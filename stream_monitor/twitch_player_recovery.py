"""Bounded, per-page recovery of an explicitly visible Twitch player #2000.

No page reload or play command is used. A retry is one guarded click of the
player's own reload button; only consecutive media-time advances prove recovery.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from typing import Any

from stream_monitor.cdp_client import CdpClient, page_urls_match
from stream_monitor.page_assist_status import publish_page_assist_status

logger = logging.getLogger(__name__)

RETRY_DELAYS_S = (5.0, 15.0, 30.0)
VERIFY_TIMEOUT_S = 12.0
SAMPLE_INTERVAL_S = 1.0
REQUIRED_ADVANCES = 2  # Three observations, separated by at least one second.
IDLE_POLL_S = 30.0

# The same function probes and revalidates a click in one renderer task. Keeping
# the exact node token avoids stale coordinates clicking a replacement control.
_PLAYER_FUNCTION_JS = r"""
({clickToken = null, expectedUrl = null} = {}) => {
  const excluded = '[data-test-selector="chat-room-component-layout"], ' +
    '[data-a-target="right-column-chat-bar"], .chat-room, .chat-line__message';
  const visible = (el) => {
    if (!(el instanceof HTMLElement) || !el.isConnected || el.closest(excluded)) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1 || r.bottom <= 0 || r.right <= 0 ||
        r.top >= innerHeight || r.left >= innerWidth) return false;
    for (let node = el; node instanceof HTMLElement; node = node.parentElement) {
      const s = getComputedStyle(node);
      if (node.hidden || s.display === 'none' || s.visibility === 'hidden' ||
          s.visibility === 'collapse' || Number(s.opacity) === 0) return false;
    }
    return true;
  };
  const visibleText = (root) => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const parts = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!visible(node.parentElement)) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      if ([...range.getClientRects()].some(r => r.width > 0 && r.height > 0 &&
          r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth)) {
        parts.push(node.textContent);
      }
    }
    return parts.join(' ').replace(/\s+/g, ' ').trim();
  };
  const roots = [...document.querySelectorAll(
    '[data-a-target="video-player"], [data-test-selector="video-player"], .video-player'
  )].filter(visible);
  const player = roots[0];
  const result = {url: location.href, playerPresent: !!player, errorCode: null,
    offline: false, gatePresent: false, hasVideo: false, readyState: 0,
    paused: true, ended: false, mediaError: false, currentTime: null,
    videoId: null, retryToken: null, clicked: false};
  if (!player) return result;
  const text = visibleText(player);
  const error = text.match(/(?:#\s*|error\s*(?:code\s*)?[:：]?\s*|錯誤代碼\s*[:：]?\s*|错误代码\s*[:：]?\s*)(\d{4})(?!\d)/i);
  result.errorCode = error ? error[1] : null;
  result.offline = [...document.querySelectorAll(
    '[data-a-target="offline-channel-main-content"], ' +
    '[data-test-selector="channel-offline-overlay"], .channel-status-info--offline'
  )].some(visible) || /(?:channel is offline|stream is offline|目前離線|目前离线|目前未開播|目前未开播)/i.test(text);
  result.gatePresent = [...player.querySelectorAll(
    '[data-a-target="content-classification-gate-overlay-start-watching-button"]'
  )].some(visible);
  const key = '__helloStreamerPlayerRecovery';
  const state = window[key] || (window[key] = {
    ids: new WeakMap(), serial: 0, documentId: String(Math.random())
  });
  const id = (node) => {
    if (!state.ids.has(node)) state.ids.set(node, state.documentId + ':' + ++state.serial);
    return state.ids.get(node);
  };
  const videos = [...player.querySelectorAll('video')].filter(visible);
  // Multiple visible videos (e.g. an ad transition) are ambiguous evidence.
  if (videos.length === 1) {
    const video = videos[0];
    Object.assign(result, {hasVideo: true, readyState: video.readyState,
      paused: video.paused, ended: video.ended, mediaError: !!video.error,
      currentTime: Number.isFinite(video.currentTime) ? video.currentTime : null,
      videoId: id(video)});
  }
  const playablePause = result.hasVideo && result.readyState >= 3 && result.paused && !result.mediaError;
  if (result.errorCode !== '2000' || result.offline || result.gatePresent || playablePause) return result;
  const buttons = [...player.querySelectorAll('button, [role="button"]')].filter(el => {
    if (!visible(el) || el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true') return false;
    const label = visibleText(el) + ' ' + (el.getAttribute('aria-label') || '');
    if (!/(?:reload\s+(?:the\s+)?player|重新載入播放器|重新加载播放器|重新載入|重新加载)/i.test(label)) return false;
    const r = el.getBoundingClientRect();
    const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return getComputedStyle(el).pointerEvents !== 'none' && (top === el || el.contains(top));
  });
  if (buttons.length !== 1) return result;
  const button = buttons[0];
  result.retryToken = id(button);
  if (clickToken && result.retryToken === clickToken && location.href === expectedUrl) {
    button.click();
    result.clicked = true;
  }
  return result;
}
"""

PLAYER_SNAPSHOT_JS = f"({_PLAYER_FUNCTION_JS})()"


class PlayerRecovery:
    """Worker-owned state; reconnects and inconclusive probes never reset budget."""

    def __init__(self, url: str, stop: threading.Event, offline: threading.Event) -> None:
        self.url = url
        self.stop = stop
        self.offline = offline
        self.active = False
        self.attempts = 0
        self.retry_at: float | None = None
        self.verify_until: float | None = None
        self.next_check_at = 0.0
        self._last_sample: tuple[str, float, float] | None = None
        self._advances = 0
        self._paused_before_error = False
        self.notice = ""

    def _report(self, reason: str, action: str) -> None:
        if reason == self.notice:
            return
        self.notice = reason
        publish_page_assist_status(self.url, reason)
        logger.info("Twitch player recovery url=%s code=2000 attempt=%d/%d action=%s result=%s",
                    self.url, self.attempts, len(RETRY_DELAYS_S), action, reason or "recovered")

    def _reset_samples(self) -> None:
        self._last_sample = None
        self._advances = 0

    def disconnected(self, *, action: str = "connection_lost") -> None:
        """Cancel this operation; a fresh probe must schedule any later retry."""
        self.retry_at = None
        self.verify_until = None
        self._reset_samples()
        if self.active:
            self._report("player_2000_unverified", action)

    def _progress(self, snap: dict[str, Any], now: float) -> bool:
        value = snap.get("currentTime")
        if (not snap.get("playerPresent") or snap.get("errorCode") or snap.get("offline")
                or snap.get("gatePresent") or not snap.get("hasVideo")
                or snap.get("readyState", 0) < 3 or snap.get("paused")
                or snap.get("ended") or snap.get("mediaError") or not snap.get("videoId")
                or isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)):
            self._reset_samples()
            return False
        sample = (snap["videoId"], float(value), now)
        previous = self._last_sample
        if previous is not None and now - previous[2] < SAMPLE_INTERVAL_S:
            return False
        if previous and previous[0] == sample[0] and sample[1] - previous[1] >= 0.1:
            self._advances += 1
        else:
            self._advances = 0
        self._last_sample = sample
        return self._advances >= REQUIRED_ADVANCES

    def step(self, client: CdpClient) -> None:
        now = time.monotonic()
        self.next_check_at = now + IDLE_POLL_S
        if self.stop.is_set():
            return
        if self.offline.is_set():
            self.disconnected(action="confirmed_offline")
            return
        try:
            snap = client.evaluate(PLAYER_SNAPSHOT_JS)
        except Exception:
            self.disconnected()
            logger.debug("Player recovery probe unavailable for %s", self.url, exc_info=True)
            return
        if self.stop.is_set() or self.offline.is_set():
            return
        if not isinstance(snap, dict) or not page_urls_match(self.url, str(snap.get("url", ""))):
            self.disconnected(action="page_unavailable_or_changed")
            return
        if snap.get("offline") or snap.get("gatePresent") or not snap.get("playerPresent"):
            self.disconnected(action="offline_or_player_unavailable")
            return
        error = snap.get("errorCode") == "2000"
        paused = (snap.get("hasVideo") and snap.get("readyState", 0) >= 3
                  and snap.get("paused") and not snap.get("mediaError"))
        if not self.active:
            if not error:
                if snap.get("hasVideo") and snap.get("readyState", 0) >= 3:
                    self._paused_before_error = bool(snap.get("paused"))
                return
            self.active = True
            self._report("player_2000_retrying", "detected")
        if self._progress(snap, now):
            self._report("", "media_time_advanced")
            self.active = False
            self.attempts = 0
            self.retry_at = self.verify_until = None
            self._paused_before_error = False
            self._reset_samples()
            return
        if paused or self._paused_before_error:
            # Never resume a user's paused video. Observe only until they play.
            self.retry_at = self.verify_until = None
            self._report("player_2000_unverified", "paused_no_action")
            return
        if not error:
            # An absent error is not success, and is not permission to click.
            self.retry_at = None
            if self.verify_until is None:
                self.verify_until = now + VERIFY_TIMEOUT_S
        if self.verify_until is not None:
            if now < self.verify_until:
                self.next_check_at = now + SAMPLE_INTERVAL_S
                return
            self.verify_until = None
            self._report("player_2000_unverified", "verification_timeout")
        if self.attempts >= len(RETRY_DELAYS_S):
            self._report("player_2000_exhausted", "stop_retries")
            return
        if not error:
            return
        if not snap.get("retryToken"):
            self.retry_at = None
            self._report("player_2000_unverified", "reload_button_unavailable")
            return
        if self.retry_at is None:
            self.retry_at = now + RETRY_DELAYS_S[self.attempts]
            self._report("player_2000_retrying", "cooldown")
        self.next_check_at = self.retry_at
        if now < self.retry_at:
            return
        if self.stop.is_set() or self.offline.is_set() or not client.connected:
            self.disconnected()
            return
        self.attempts += 1  # Ambiguous transport outcomes consume the budget too.
        self.retry_at = None
        self.verify_until = now + VERIFY_TIMEOUT_S
        self.next_check_at = now + SAMPLE_INTERVAL_S
        self._reset_samples()
        args = json.dumps({"clickToken": snap["retryToken"], "expectedUrl": snap["url"]})
        try:
            result = client.evaluate(f"({_PLAYER_FUNCTION_JS})({args})")
            clicked = isinstance(result, dict) and result.get("clicked") is True
        except Exception:
            clicked = False
            self.disconnected()
        logger.info("Twitch player recovery url=%s code=2000 attempt=%d/%d "
                    "action=reload_player result=%s", self.url, self.attempts,
                    len(RETRY_DELAYS_S), "clicked_unverified" if clicked else "cancelled_or_unavailable")
        self._report("player_2000_retrying" if clicked else "player_2000_unverified",
                     "verify_playback" if clicked else "cancel_click")
