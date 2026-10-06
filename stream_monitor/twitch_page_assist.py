"""Twitch in-page assist orchestrator driven by Chromium CDP.

Runs only for source builds with viewer_engagement.page_assist_enabled.
Each managed Twitch window gets one background worker that:
  1. accepts content-classification / Start Watching gates
  2. optionally enables theater mode (verified; idempotent; never blind-toggle)
  3. ensures chat is visible, then optionally claims Channel Points bonus
     with fuzzy delay / bounded click offset and post-click verification
  4. optionally reloads on a fuzzy interval and re-runs (1)+(2)
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from stream_monitor.cdp_client import DEFAULT_CDP_ATTACH_TIMEOUT_S, CdpClient
from stream_monitor.page_assist_status import publish_page_assist_status
from stream_monitor.viewer_engagement_model import (
    ViewerEngagementSettings,
    coerce_viewer_engagement,
    is_twitch_url,
    page_assist_runtime_allowed,
)

logger = logging.getLogger(__name__)

_GATE_TIMEOUT_S = 60.0
_GATE_POLL_S = 1.0
_READY_CONFIRM_POLLS = 2
_THEATER_VERIFY_WAIT_S = 0.6
_CLAIM_VERIFY_WAIT_S = 0.8
_PAGE_CHECK_INTERVAL_S = 30.0

# Best-effort DOM helpers. Twitch UI changes; failures are non-fatal.
_FIND_GATE_JS = r"""
(() => {
  const texts = [
    "start watching",
    "開始觀看",
    "开始观看",
    "視聴を開始",
    "시청 시작",
    "regarder",
    "ver ahora",
    "assistir"
  ];
  const prefer = document.querySelector(
    '[data-a-target="content-classification-gate-overlay-start-watching-button"]'
  );
  const candidates = prefer
    ? [prefer]
    : Array.from(document.querySelectorAll('button, [role="button"]'));
  for (const el of candidates) {
    if (!(el instanceof HTMLElement)) continue;
    const label = ((el.innerText || el.textContent || "") + " " +
      (el.getAttribute("aria-label") || "")).trim().toLowerCase();
    const matched = prefer === el || texts.some((t) => label.includes(t));
    if (!matched) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) continue;
    return {found: true, x: r.left + r.width / 2, y: r.top + r.height / 2};
  }
  return {found: false};
})()
"""

_PAGE_READY_JS = r"""
(() => {
  const gateTexts = [
    "start watching",
    "開始觀看",
    "开始观看",
    "視聴を開始",
    "시청 시작",
    "regarder",
    "ver ahora",
    "assistir"
  ];
  const prefer = document.querySelector(
    '[data-a-target="content-classification-gate-overlay-start-watching-button"]'
  );
  let gatePresent = false;
  if (prefer instanceof HTMLElement) {
    const r = prefer.getBoundingClientRect();
    gatePresent = r.width > 1 && r.height > 1;
  } else {
    for (const el of document.querySelectorAll('button, [role="button"]')) {
      if (!(el instanceof HTMLElement)) continue;
      const label = ((el.innerText || el.textContent || "") + " " +
        (el.getAttribute("aria-label") || "")).trim().toLowerCase();
      if (!gateTexts.some((t) => label.includes(t))) continue;
      const r = el.getBoundingClientRect();
      if (r.width > 1 && r.height > 1) { gatePresent = true; break; }
    }
  }
  let readyState = 0;
  let paused = true;
  let hasVideo = false;
  for (const v of document.querySelectorAll("video")) {
    if (!(v instanceof HTMLVideoElement)) continue;
    const r = v.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) continue;
    hasVideo = true;
    if (v.readyState >= readyState) {
      readyState = v.readyState;
      paused = !!v.paused;
    }
  }
  return {
    gatePresent,
    hasVideo,
    readyState,
    paused,
    playbackReady: hasVideo && readyState >= 3
  };
})()
"""

_CHAT_STATE_JS = r"""
(() => {
  const visible = (el) => {
    if (!(el instanceof HTMLElement)) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) return false;
    const st = window.getComputedStyle(el);
    if (st.visibility === "hidden" || st.display === "none" || Number(st.opacity) === 0) {
      return false;
    }
    return true;
  };
  const labelOf = (el) => ((el.getAttribute("aria-label") || "") + " " +
    (el.innerText || el.textContent || "")).trim().toLowerCase();
  const expandHints = [
    "expand chat",
    "show chat",
    "expand",
    "展開聊天",
    "展开聊天",
    "顯示聊天",
    "显示聊天",
    "チャットを表示",
    "채팅 펼치기",
    "채팅 표시"
  ];
  const collapseHints = [
    "collapse chat",
    "hide chat",
    "collapse",
    "收合聊天",
    "折叠聊天",
    "隱藏聊天",
    "隐藏聊天",
    "チャットを非表示",
    "채팅 접기",
    "채팅 숨기기"
  ];
  const chatShells = [
    document.querySelector('[data-test-selector="chat-room-component-layout"]'),
    document.querySelector('section[data-a-target="chat-room-header-label"]') &&
      document.querySelector('[data-a-target="chat-input"]')?.closest("section"),
    document.querySelector(".chat-room"),
    document.querySelector('[data-a-target="right-column-chat-bar"]')
  ].filter(Boolean);
  let chatVisible = false;
  for (const shell of chatShells) {
    if (visible(shell)) { chatVisible = true; break; }
  }
  if (!chatVisible) {
    const input = document.querySelector(
      '[data-a-target="chat-input"], textarea[data-a-target="chat-input"], ' +
      '[data-a-target="chat-input-text"]'
    );
    chatVisible = visible(input);
  }
  const loginHints = [
    "log in",
    "sign in",
    "登入",
    "登录",
    "ログイン",
    "로그인"
  ];
  let notLoggedIn = false;
  for (const el of document.querySelectorAll("button, a, [role='button']")) {
    if (!(el instanceof HTMLElement) || !visible(el)) continue;
    const label = labelOf(el);
    if (loginHints.some((h) => label.includes(h)) &&
        (label.includes("chat") || label.includes("聊天") ||
         label.includes("チャット") || label.includes("채팅") ||
         !!el.closest('[data-a-target="right-column-chat-bar"], .chat-room'))) {
      notLoggedIn = true;
      break;
    }
  }
  const toggleSelectors = [
    '[data-a-target="right-column__toggle-collapse-btn"]',
    'button[aria-label*="Expand Chat" i]',
    'button[aria-label*="Collapse Chat" i]',
    'button[aria-label*="Show Chat" i]',
    'button[aria-label*="Hide Chat" i]',
    'button[aria-label*="展開聊天"]',
    'button[aria-label*="收合聊天"]',
    'button[aria-label*="展开聊天"]',
    'button[aria-label*="折叠聊天"]'
  ];
  const seen = new Set();
  let expandControl = null;
  let collapsed = false;
  for (const sel of toggleSelectors) {
    for (const node of document.querySelectorAll(sel)) {
      if (!(node instanceof HTMLElement) || seen.has(node) || !visible(node)) continue;
      seen.add(node);
      const label = labelOf(node);
      const isExpand = expandHints.some((h) => label.includes(h)) &&
        !collapseHints.some((h) => label.includes(h));
      const isCollapse = collapseHints.some((h) => label.includes(h));
      const pressed = node.getAttribute("aria-pressed");
      const expanded = node.getAttribute("aria-expanded");
      if (isExpand || pressed === "false" || expanded === "false") {
        collapsed = true;
        if (!expandControl) {
          const r = node.getBoundingClientRect();
          expandControl = {
            found: true,
            x: r.left + r.width / 2,
            y: r.top + r.height / 2,
            label: label.slice(0, 80)
          };
        }
      } else if (isCollapse || pressed === "true" || expanded === "true") {
        chatVisible = chatVisible || true;
      }
    }
  }
  if (chatVisible && !collapsed) {
    return {status: "visible", chatVisible: true, collapsed: false, expandControl: null, notLoggedIn};
  }
  if (collapsed && expandControl) {
    return {status: "collapsed", chatVisible: false, collapsed: true, expandControl, notLoggedIn};
  }
  if (notLoggedIn) {
    return {status: "not_logged_in", chatVisible, collapsed, expandControl, notLoggedIn: true};
  }
  return {
    status: chatVisible ? "visible" : "hidden",
    chatVisible,
    collapsed: !!collapsed,
    expandControl,
    notLoggedIn
  };
})()
"""

_FIND_CLAIM_JS = r"""
(() => {
  const visible = (el) => {
    if (!(el instanceof HTMLElement)) return false;
    if (el.hasAttribute("disabled") || el.getAttribute("aria-disabled") === "true") {
      return false;
    }
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) return false;
    const st = window.getComputedStyle(el);
    if (st.visibility === "hidden" || st.display === "none" || Number(st.opacity) === 0) {
      return false;
    }
    if (st.pointerEvents === "none") return false;
    return true;
  };
  const hitTestable = (el) => {
    const r = el.getBoundingClientRect();
    const x = r.left + Math.min(Math.max(r.width / 2, 1), r.width - 1);
    const y = r.top + Math.min(Math.max(r.height / 2, 1), r.height - 1);
    const top = document.elementFromPoint(x, y);
    if (!top) return false;
    return el === top || el.contains(top) || top.contains(el);
  };
  const labelOf = (el) => ((el.getAttribute("aria-label") || "") + " " +
    (el.innerText || el.textContent || "")).trim().toLowerCase();
  const bonusHints = [
    "claim bonus",
    "claim a bonus",
    "click to claim",
    "領取獎勵",
    "领取奖励",
    "ボーナスを受け取る",
    "보너스 받기",
    "claim",
    "領取",
    "领取",
    "受け取る",
    "받기"
  ];
  const excludeHints = [
    "community points",
    "channel points",
    "points menu",
    "balance",
    "預測",
    "预测",
    "prediction",
    "drops",
    "redeem",
    "兌換",
    "兑换"
  ];
  const roots = [];
  const summary = document.querySelector(
    '[data-a-target="community-points-summary"], [data-test-selector="community-points-summary"]'
  );
  if (summary) roots.push(summary);
  const chat = document.querySelector(
    '[data-test-selector="chat-room-component-layout"], .chat-room, ' +
    '[data-a-target="right-column-chat-bar"]'
  );
  if (chat) roots.push(chat);
  if (!roots.length) roots.push(document);

  const isBonusCue = (el) => {
    const label = labelOf(el);
    if (el.closest(".claimable-bonus") || el.querySelector(".claimable-bonus__icon") ||
        el.classList.contains("claimable-bonus") ||
        el.closest('[data-test-selector="community-points-summary-claimable-bonus"]')) {
      return true;
    }
    // Chat may contain extensions, Drops or other reward buttons. Localized
    // reward text alone is only evidence inside the channel-points summary.
    if (!el.closest('[data-a-target="community-points-summary"], ' +
      '[data-test-selector="community-points-summary"], .community-points-summary')) return false;
    return (
      label.includes("bonus") ||
      label.includes("獎勵") ||
      label.includes("奖励") ||
      label.includes("ボーナス") ||
      label.includes("보너스")
    );
  };
  const candidates = [];
  const pushCandidate = (el, reason) => {
    if (!(el instanceof HTMLElement) || !visible(el) || !hitTestable(el)) return;
    // Require explicit bonus cues; never treat summary/menu openers as claim.
    if (!isBonusCue(el)) return;
    const label = labelOf(el);
    if (excludeHints.some((h) => label.includes(h))) return;
    const r = el.getBoundingClientRect();
    candidates.push({
      found: true,
      reason,
      x: r.left + r.width / 2,
      y: r.top + r.height / 2,
      left: r.left,
      top: r.top,
      width: r.width,
      height: r.height,
      label: label.slice(0, 120),
      key: [
        Math.round(r.left), Math.round(r.top),
        Math.round(r.width), Math.round(r.height),
        label.slice(0, 40)
      ].join("|")
    });
  };

  for (const root of roots) {
    for (const icon of root.querySelectorAll(".claimable-bonus__icon, .claimable-bonus")) {
      const btn = icon.closest("button, [role='button']") || icon;
      pushCandidate(btn, "claimable-bonus");
    }
    for (const el of root.querySelectorAll(
      'button[aria-label*="Claim Bonus" i], button[aria-label*="claim a bonus" i], ' +
      'button[aria-label*="領取獎勵"], button[aria-label*="领取奖励"], ' +
      'button[aria-label*="ボーナス"], button[aria-label*="보너스"], ' +
      '[data-test-selector="community-points-summary-claimable-bonus"] button, ' +
      'button.claimable-bonus, .community-points-summary .claimable-bonus button'
    )) {
      pushCandidate(el, "bonus-selector");
    }
  }

  // Localized claim labels only inside the points/chat area — never document-wide.
  for (const root of roots) {
    if (root === document) continue;
    for (const el of root.querySelectorAll("button, [role='button']")) {
      const label = labelOf(el);
      if (!bonusHints.some((h) => label.includes(h))) continue;
      if (!label.includes("bonus") && !label.includes("獎勵") &&
          !label.includes("奖励") && !label.includes("ボーナス") &&
          !label.includes("보너스") && !el.closest(".claimable-bonus") &&
          !el.querySelector(".claimable-bonus__icon")) {
        // Bare "Claim"/"領取" without bonus cue: only accept inside claimable-bonus.
        continue;
      }
      pushCandidate(el, "localized-bonus");
    }
  }

  if (!candidates.length) {
    const pointsPresent = [...document.querySelectorAll(
      '[data-a-target="community-points-summary"], ' +
      '[data-test-selector="community-points-summary"], .community-points-summary'
    )].some(visible);
    return {found: false, pointsPresent};
  }
  // Prefer smallest hit target (actual bonus chest over large wrappers).
  candidates.sort((a, b) => (a.width * a.height) - (b.width * b.height));
  return candidates[0];
})()
"""

_THEATER_STATE_JS = r"""
(() => {
  const labelOf = (el) => ((el.getAttribute("aria-label") || "") + " " +
    (el.innerText || el.textContent || "")).trim().toLowerCase();
  const enterHints = [
    "enter theatre",
    "enter theater",
    "開啟劇院",
    "开启剧院",
    "進入劇院",
    "进入剧院",
  ];
  const exitHints = [
    "exit theatre",
    "exit theater",
    "leave theatre",
    "leave theater",
    "關閉劇院",
    "关闭剧院",
    "離開劇院",
    "离开剧院",
    "シアターモードを終了",
    "극장 모드 종료"
  ];
  const genericHints = [
    "theatre",
    "theater",
    "劇院",
    "剧院",
    "劇場",
    "剧场",
    "シアター",
    "극장"
  ];
  const selectors = [
    '[data-a-target="player-theatre-mode-button"]',
    '[data-a-target="player-theater-mode-button"]',
    'button[aria-label*="theatre" i]',
    'button[aria-label*="theater" i]',
    'button[aria-label*="劇院"]',
    'button[aria-label*="剧院"]',
    'button[aria-label*="劇場"]',
    'button[aria-label*="剧场"]',
    'button[aria-label*="シアター"]',
    'button[aria-label*="극장"]'
  ];
  const visible = (el) => {
    if (!(el instanceof HTMLElement)) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) return false;
    const st = window.getComputedStyle(el);
    if (st.visibility === "hidden" || st.display === "none" || Number(st.opacity) === 0) return false;
    return true;
  };
  const seen = new Set();
  const controls = [];
  for (const sel of selectors) {
    for (const node of document.querySelectorAll(sel)) {
      if (!(node instanceof HTMLElement) || seen.has(node) || !visible(node)) continue;
      seen.add(node);
      controls.push(node);
    }
  }
  for (const el of document.querySelectorAll("button, [role='button']")) {
    if (!(el instanceof HTMLElement) || seen.has(el) || !visible(el)) continue;
    const label = labelOf(el);
    if (!genericHints.some((h) => label.includes(h))) continue;
    seen.add(el);
    controls.push(el);
  }
  const inferControlState = (el) => {
    const pressed = el.getAttribute("aria-pressed");
    const label = labelOf(el);
    if (pressed === "true") return "on";
    if (pressed === "false") return "off";
    if (exitHints.some((h) => label.includes(h))) return "on";
    if (enterHints.some((h) => label.includes(h))) return "off";
    // Current Twitch uses a bare mode label when OFF, including localized
    // labels and an optional shortcut. Scope this evidence to player controls;
    // aria-expanded describes a popup and is not theater state.
    const modeLabel = label.replace(/\s*[（(]\s*alt\s*\+\s*t\s*[）)]\s*$/i, "").trim();
    const defaultLabels = ["theatre mode", "theater mode", "劇院模式", "剧院模式",
      "シアターモード", "극장 모드"];
    if (defaultLabels.includes(modeLabel) && (
      el.matches('[data-a-target="player-theatre-mode-button"], [data-a-target="player-theater-mode-button"]') ||
      el.closest('[data-a-target="player-controls"], .player-controls')
    )) return "off";
    // Other generic labels are insufficient evidence to toggle.
    return "unknown";
  };
  let control = null;
  let controlState = "unknown";
  for (const el of controls) {
    const r = el.getBoundingClientRect();
    const state = inferControlState(el);
    const candidate = {
      found: true,
      x: r.left + r.width / 2,
      y: r.top + r.height / 2,
      state,
      label: labelOf(el).slice(0, 80)
    };
    if (state !== "unknown") {
      control = candidate;
      controlState = state;
      break;
    }
    if (!control) control = candidate;
  }
  const root = document.documentElement;
  const body = document.body;
  const theaterLayout = !!(
    document.querySelector(".video-player--theatre, .video-player--theater") ||
    document.querySelector('[data-a-target="player-overlay-theater-mode"]') ||
    root.classList.contains("theatreMode") ||
    root.classList.contains("theaterMode") ||
    root.classList.contains("theatre-mode") ||
    root.classList.contains("theater-mode") ||
    (body && (
      body.classList.contains("theatre-mode") ||
      body.classList.contains("theater-mode") ||
      body.classList.contains("theatreMode") ||
      body.classList.contains("theaterMode")
    )) ||
    !!document.querySelector('.persistent-player[data-theatre="true"], ' +
      '.persistent-player[data-theater="true"]')
  );
  let state = "unknown";
  if (controlState === "on" || (theaterLayout && controlState !== "off")) {
    state = "on";
  } else if (controlState === "off" && !theaterLayout) {
    state = "off";
  } else if (controlState === "off" && theaterLayout) {
    // Conflicting signals — do not toggle.
    state = "unknown";
  } else if (theaterLayout) {
    state = "on";
  }
  // Only expose clickable control when we know it is OFF (safe to enable).
  const clickable = control && controls.some(el => {
    const r = el.getBoundingClientRect();
    if (r.left + r.width / 2 !== control.x || r.top + r.height / 2 !== control.y) return false;
    if (el.hasAttribute("disabled") || el.getAttribute("aria-disabled") === "true") return false;
    if (getComputedStyle(el).pointerEvents === "none") return false;
    const top = document.elementFromPoint(control.x, control.y);
    return !!top && (top === el || el.contains(top));
  });
  const safeControl = (clickable && controlState === "off" && state === "off")
    ? control
    : (control && controlState === "on" ? control : null);
  return {state, control: safeControl, controlState, theaterLayout: !!theaterLayout};
})()
"""

_FOCUS_PLAYER_JS = r"""
(() => {
  const targets = [
    '[data-a-target="player-controls"]',
    '.player-controls',
    '.video-player__container',
    '.video-player',
    'main'
  ];
  for (const sel of targets) {
    const el = document.querySelector(sel);
    if (!(el instanceof HTMLElement)) continue;
    try { el.focus({preventScroll: true}); } catch (_) { try { el.focus(); } catch (_) {} }
    return {focused: true, selector: sel};
  }
  return {focused: false};
})()
"""

_PLAYER_HOVER_JS = r"""
(() => {
  for (const video of document.querySelectorAll("video")) {
    const r = video.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) continue;
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) continue;
    const top = document.elementFromPoint(x, y);
    if (!top || !(top === video || top.closest('.video-player, [data-a-target="video-player"]'))) continue;
    return {x, y};
  }
  return null;
})()
"""

_assist_lock = threading.Lock()
_assist_by_url: dict[str, "_PageAssistWorker"] = {}


def _url_key(url: str) -> str:
    parts = urlsplit((url or "").strip())
    path = parts.path.rstrip("/") or "/"
    return f"{parts.scheme}://{parts.netloc}{path}".lower()


def fuzzy_uniform(low: float, high: float) -> float:
    """Sample inclusive-ish range with ordered bounds."""
    a = float(low)
    b = float(high)
    if b < a:
        a, b = b, a
    if a == b:
        return a
    return random.uniform(a, b)


def is_standalone_managed_window(
    *,
    managed: bool,
    app_mode: bool,
    new_window: bool,
) -> bool:
    """True when Twitch opens in a managed separate/solo window (UI docs).

    Regular tabs (``managed`` with neither app mode nor new window) are not
    standalone; tab management disabled (``managed=False``) also fails.
    """
    return bool(managed and (app_mode or new_window))


def should_start_page_assist(
    url: str,
    settings: ViewerEngagementSettings | dict[str, Any] | None,
    *,
    managed: bool,
    isolated_profile: bool,
    chromium_family: bool,
    standalone_window: bool,
) -> bool:
    engagement = coerce_viewer_engagement(settings)
    if engagement is None or not engagement.page_assist_active():
        return False
    if not page_assist_runtime_allowed():
        return False
    if not (
        managed
        and isolated_profile
        and chromium_family
        and standalone_window
    ):
        return False
    return is_twitch_url(url)


def _bounded_jitter(
    x: float,
    y: float,
    *,
    left: float,
    top: float,
    width: float,
    height: float,
    offset: int,
) -> tuple[float, float, int, int]:
    """Apply click jitter clamped inside the hit-tested button rect."""
    if offset <= 0 or width <= 2 or height <= 2:
        return x, y, 0, 0
    max_dx = max(0, int(min(offset, (width / 2) - 1)))
    max_dy = max(0, int(min(offset, (height / 2) - 1)))
    dx = random.randint(-max_dx, max_dx) if max_dx else 0
    dy = random.randint(-max_dy, max_dy) if max_dy else 0
    nx = min(max(x + dx, left + 1.0), left + width - 1.0)
    ny = min(max(y + dy, top + 1.0), top + height - 1.0)
    return nx, ny, int(round(nx - x)), int(round(ny - y))


class _PageAssistWorker:
    def __init__(
        self,
        url: str,
        port: int,
        settings: ViewerEngagementSettings,
    ) -> None:
        self.url = url
        self.port = port
        self.settings = settings
        self._theater_confirmed_once = False
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"twitch-page-assist-{_url_key(url)[-24:]}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float = 2.0) -> None:
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _sleep(self, seconds: float) -> bool:
        """Sleep interruptibly; return False when stop was requested."""
        return not self._stop.wait(timeout=max(0.0, seconds))

    def _run(self) -> None:
        client = CdpClient()
        try:
            client.connect(self.port, self.url, timeout=DEFAULT_CDP_ATTACH_TIMEOUT_S)
            if self._stop.is_set():
                return
            publish_page_assist_status(self.url, "")
            logger.info("Twitch page assist attached via CDP for %s", self.url)
            next_refresh_at = self._schedule_refresh()
            claim_deadline = time.monotonic()
            while not self._stop.is_set():
                self._run_gate_and_theater(client)
                next_page_check_at = time.monotonic() + _PAGE_CHECK_INTERVAL_S
                while not self._stop.is_set():
                    now = time.monotonic()
                    if not client.connected:
                        client.close()
                        client = CdpClient()
                        try:
                            client.connect(self.port, self.url,
                                           timeout=DEFAULT_CDP_ATTACH_TIMEOUT_S)
                        except Exception:
                            client.close()
                            publish_page_assist_status(self.url, "unavailable")
                            logger.warning("Twitch page assist reconnect failed for %s",
                                           self.url, exc_info=True)
                            if not self._sleep(_PAGE_CHECK_INTERVAL_S):
                                return
                            continue
                        publish_page_assist_status(self.url, "")
                        logger.info("Twitch page assist reconnected for %s", self.url)
                        next_page_check_at = now
                    if (
                        self.settings.auto_refresh
                        and next_refresh_at is not None
                        and now >= next_refresh_at
                    ):
                        logger.info("Twitch page assist reloading %s", self.url)
                        try:
                            client.reload()
                        except Exception:
                            publish_page_assist_status(self.url, "unavailable")
                            logger.exception("Page.reload failed for %s", self.url)
                            return
                        if not self._sleep(3.0):
                            return
                        next_refresh_at = self._schedule_refresh()
                        break  # restart gate → theater after reload

                    if now >= next_page_check_at:
                        self._check_page(client)
                        next_page_check_at = time.monotonic() + _PAGE_CHECK_INTERVAL_S

                    if self.settings.claim_channel_points and now >= claim_deadline:
                        self._try_claim(client)
                        # Continue observing at the configured interval after
                        # success too; a new visible bonus must not be held by
                        # an unrelated fixed 12–18 minute cooldown.
                        claim_deadline = time.monotonic() + fuzzy_uniform(
                            self.settings.claim_poll_seconds_min,
                            self.settings.claim_poll_seconds_max,
                        )
                    wait_s = fuzzy_uniform(
                        self.settings.claim_poll_seconds_min,
                        self.settings.claim_poll_seconds_max,
                    )
                    wait_s = min(wait_s, max(0.1, next_page_check_at - time.monotonic()))
                    if self.settings.claim_channel_points:
                        wait_s = min(wait_s, max(0.1, claim_deadline - time.monotonic()))
                    if next_refresh_at is not None:
                        wait_s = min(wait_s, max(1.0, next_refresh_at - time.monotonic()))
                    if not self._sleep(wait_s):
                        return
        except Exception:
            if not self._stop.is_set():
                publish_page_assist_status(self.url, "unavailable")
            logger.exception("Twitch page assist stopped for %s", self.url)
        finally:
            client.close()
            with _assist_lock:
                current = _assist_by_url.get(_url_key(self.url))
                if current is self:
                    _assist_by_url.pop(_url_key(self.url), None)

    def _check_page(self, client: CdpClient) -> None:
        """Periodically reconcile this page without reloading or blind toggles."""
        if self._stop.is_set():
            return
        ready = self._probe_page_ready(client)
        if ready.get("gatePresent") and self.settings.accept_content_gate:
            self._try_accept_gate(client)
            if not self._sleep(_THEATER_VERIFY_WAIT_S):
                return
            ready = self._probe_page_ready(client)
        if ready.get("gatePresent"):
            logger.info("Twitch page assist periodic page check for %s: content gate present",
                        self.url)
            return
        if self.settings.theater_mode:
            self._ensure_theater(client)
        if self._stop.is_set():
            return
        chat = (self._ensure_chat_visible(client) if self.settings.claim_channel_points
                else self._chat_snapshot(client).get("status", "hidden"))
        theater = self._theater_snapshot(client).get("state", "unknown")
        if not ready:
            playback = "unavailable"
        elif not ready.get("hasVideo") or ready.get("readyState", 0) < 3:
            playback = "loading"
        elif ready.get("paused"):
            playback = "paused"
        else:
            playback = "playing"
        logger.info("Twitch page assist periodic page check for %s: "
                    "playback=%s theater=%s chat=%s", self.url, playback, theater, chat)

    def _schedule_refresh(self) -> float | None:
        if not self.settings.auto_refresh:
            return None
        minutes = fuzzy_uniform(
            self.settings.refresh_minutes_min,
            self.settings.refresh_minutes_max,
        )
        return time.monotonic() + minutes * 60.0

    def _run_gate_and_theater(self, client: CdpClient) -> None:
        if self.settings.accept_content_gate:
            if not self._wait_for_gate_or_ready(client):
                return
        if self.settings.theater_mode and not self._stop.is_set():
            if not self._sleep(float(self.settings.theater_delay_seconds)):
                return
            self._ensure_theater(client)
        if self.settings.claim_channel_points and not self._stop.is_set():
            self._ensure_chat_visible(client)

    def _wait_for_gate_or_ready(self, client: CdpClient) -> bool:
        """Accept a gate when present; advance promptly once playback is ready.

        Gate overlays may appear slightly after the player is ready, so a short
        confirmation window is kept after the first ready reading. Stop remains
        cooperative throughout.
        """
        deadline = time.monotonic() + _GATE_TIMEOUT_S
        ready_streak = 0
        while not self._stop.is_set() and time.monotonic() < deadline:
            status = self._probe_page_ready(client)
            if status.get("gatePresent"):
                if self._try_accept_gate(client):
                    return True
            elif status.get("playbackReady"):
                ready_streak += 1
                if ready_streak >= _READY_CONFIRM_POLLS:
                    logger.info(
                        "Twitch page assist playback ready without content gate for %s",
                        self.url,
                    )
                    return True
            else:
                ready_streak = 0
            if not self._sleep(_GATE_POLL_S):
                return False
        if self._stop.is_set():
            return False
        logger.info(
            "Twitch page assist gate wait finished without gate for %s",
            self.url,
        )
        return True

    def _probe_page_ready(self, client: CdpClient) -> dict[str, Any]:
        try:
            hit = client.evaluate(_PAGE_READY_JS)
        except Exception:
            logger.debug("Page ready probe failed for %s", self.url, exc_info=True)
            return {}
        return hit if isinstance(hit, dict) else {}

    def _theater_snapshot(self, client: CdpClient) -> dict[str, Any]:
        try:
            hit = client.evaluate(_THEATER_STATE_JS)
        except Exception:
            logger.debug("Theater probe failed for %s", self.url, exc_info=True)
            return {"state": "unknown", "control": None}
        if not isinstance(hit, dict):
            return {"state": "unknown", "control": None}
        state = str(hit.get("state") or "unknown")
        if state not in {"on", "off", "unknown"}:
            state = "unknown"
        control = hit.get("control")
        return {
            "state": state,
            "control": control if isinstance(control, dict) else None,
            "controlState": hit.get("controlState"),
            "theaterLayout": bool(hit.get("theaterLayout")),
        }

    def _focus_player(self, client: CdpClient) -> None:
        try:
            client.evaluate(_FOCUS_PLAYER_JS)
        except Exception:
            logger.debug("Player focus failed for %s", self.url, exc_info=True)

    def _ensure_theater(self, client: CdpClient) -> None:
        """Initialize theater once per worker; preserve subsequent user choices."""
        if self._stop.is_set() or self._theater_confirmed_once:
            return
        # Twitch hides its controls until the pointer enters the player.
        try:
            hover = client.evaluate(_PLAYER_HOVER_JS)
            if isinstance(hover, dict):
                client.call("Input.dispatchMouseEvent", {"type": "mouseMoved", **hover})
                if not self._sleep(_THEATER_VERIFY_WAIT_S):
                    return
        except Exception:
            logger.debug("Player hover failed for %s", self.url, exc_info=True)
        snap = self._theater_snapshot(client)
        state = str(snap["state"])
        if state == "on":
            self._theater_confirmed_once = True
            logger.info(
                "Twitch page assist theater already active for %s", self.url
            )
            return
        if state != "off":
            logger.warning(
                "Twitch page assist theater skipped for %s "
                "(state=%s; unknown must not toggle)",
                self.url,
                state,
            )
            return

        self._focus_player(client)
        control = snap.get("control") if isinstance(snap.get("control"), dict) else None

        # Prefer exact DOM control when we have explicit OFF evidence.
        if (
            isinstance(control, dict)
            and control.get("found")
            and not self._stop.is_set()
        ):
            try:
                client.click_at(float(control.get("x", 0)), float(control.get("y", 0)))
            except Exception:
                logger.exception("Theater control click failed for %s", self.url)
                return
            if not self._sleep(_THEATER_VERIFY_WAIT_S):
                return
            snap = self._theater_snapshot(client)
            state = str(snap["state"])
            if state == "on":
                self._theater_confirmed_once = True
                logger.info(
                    "Twitch page assist theater enabled via control for %s",
                    self.url,
                )
                return
            if state != "off":
                logger.warning(
                    "Twitch page assist theater unverified after control for %s "
                    "(state=%s; no further toggles)",
                    self.url,
                    state,
                )
                return

        # Optional single Alt+T only while still known off after DOM attempt.
        if state == "off" and not self._stop.is_set():
            try:
                client.send_alt_t()
            except Exception:
                logger.exception("Failed to send theater Alt+T for %s", self.url)
                return
            if not self._sleep(_THEATER_VERIFY_WAIT_S):
                return
            snap = self._theater_snapshot(client)
            state = str(snap["state"])
            if state == "on":
                self._theater_confirmed_once = True
                logger.info(
                    "Twitch page assist theater enabled via keyboard for %s",
                    self.url,
                )
                return
            if state != "off":
                logger.warning(
                    "Twitch page assist theater unverified after Alt+T for %s "
                    "(state=%s; no repeated toggle)",
                    self.url,
                    state,
                )
                return

        if state == "on":
            self._theater_confirmed_once = True
            logger.info("Twitch page assist theater verified for %s", self.url)
        else:
            logger.warning(
                "Twitch page assist theater failed for %s (state=%s)",
                self.url,
                state,
            )

    def _chat_snapshot(self, client: CdpClient) -> dict[str, Any]:
        try:
            hit = client.evaluate(_CHAT_STATE_JS)
        except Exception:
            logger.debug("Chat probe failed for %s", self.url, exc_info=True)
            return {"status": "hidden", "chatVisible": False}
        if not isinstance(hit, dict):
            return {"status": "hidden", "chatVisible": False}
        status = str(hit.get("status") or "hidden")
        if status not in {"visible", "collapsed", "hidden", "not_logged_in"}:
            status = "hidden"
        return {
            "status": status,
            "chatVisible": bool(hit.get("chatVisible")),
            "collapsed": bool(hit.get("collapsed")),
            "expandControl": (
                hit.get("expandControl")
                if isinstance(hit.get("expandControl"), dict)
                else None
            ),
            "notLoggedIn": bool(hit.get("notLoggedIn")),
        }

    def _ensure_chat_visible(self, client: CdpClient) -> str:
        """Make chat visible before points; return status for logging/claim gate.

        Returns one of: visible, hidden, collapsed, not_logged_in, unavailable.
        """
        snap = self._chat_snapshot(client)
        status = str(snap.get("status") or "hidden")
        if status == "not_logged_in" or snap.get("notLoggedIn"):
            logger.info(
                "Twitch page assist chat/points unavailable (not logged in) for %s",
                self.url,
            )
            return "not_logged_in"
        if status == "visible":
            return "visible"
        if status == "collapsed":
            control = snap.get("expandControl")
            if not isinstance(control, dict) or not control.get("found"):
                logger.warning(
                    "Twitch page assist chat collapsed but no expand control for %s",
                    self.url,
                )
                return "hidden"
            try:
                client.click_at(float(control.get("x", 0)), float(control.get("y", 0)))
            except Exception:
                logger.exception("Chat expand click failed for %s", self.url)
                return "hidden"
            if not self._sleep(_THEATER_VERIFY_WAIT_S):
                return "hidden"
            snap = self._chat_snapshot(client)
            if snap.get("notLoggedIn") or snap.get("status") == "not_logged_in":
                return "not_logged_in"
            if snap.get("status") == "visible" or snap.get("chatVisible"):
                logger.info("Twitch page assist chat expanded for %s", self.url)
                return "visible"
            logger.warning(
                "Twitch page assist chat still hidden after expand for %s",
                self.url,
            )
            return "hidden"
        logger.warning(
            "Twitch page assist chat unavailable/hidden for %s (status=%s)",
            self.url,
            status,
        )
        return "hidden"

    def _try_accept_gate(self, client: CdpClient) -> bool:
        try:
            hit = client.evaluate(_FIND_GATE_JS)
        except Exception:
            logger.debug("Gate probe failed for %s", self.url, exc_info=True)
            return False
        if not isinstance(hit, dict) or not hit.get("found"):
            return False
        x = float(hit.get("x", 0))
        y = float(hit.get("y", 0))
        try:
            client.click_at(x, y)
            logger.info("Twitch page assist accepted content gate for %s", self.url)
            return True
        except Exception:
            logger.exception("Gate click failed for %s", self.url)
            return False

    def _probe_claim(self, client: CdpClient) -> dict[str, Any]:
        try:
            hit = client.evaluate(_FIND_CLAIM_JS)
        except Exception:
            logger.debug("Claim probe failed for %s", self.url, exc_info=True)
            return {"found": False, "probeError": True}
        return hit if isinstance(hit, dict) else {"found": False, "probeError": True}

    def _claim_still_present(self, client: CdpClient, key: str) -> dict[str, Any]:
        # Re-run the finder; absence of any eligible bonus = verified disappearance.
        hit = self._probe_claim(client)
        if hit.get("probeError"):
            return {"present": True, "verified": False}
        if not hit.get("found"):
            if not hit.get("pointsPresent"):
                # Loading/navigation or hidden points UI is not claim evidence.
                return {"present": True, "verified": False}
            return {"present": False, "found": False, "same": False}
        return {
            "present": True,
            "found": True,
            "same": (not key) or hit.get("key") == key,
            "key": hit.get("key"),
        }

    def _try_claim(self, client: CdpClient) -> bool:
        """Attempt bonus claim. True only when bonus disappearance is verified."""
        chat_status = self._ensure_chat_visible(client)
        if chat_status == "not_logged_in":
            return False
        if chat_status != "visible":
            logger.info(
                "Twitch page assist skip claim; chat not visible for %s",
                self.url,
            )
            return False

        hit = self._probe_claim(client)
        if not hit.get("found"):
            if hit.get("pointsPresent"):
                logger.debug(
                    "Twitch page assist no claimable bonus for %s", self.url
                )
            else:
                logger.debug(
                    "Twitch page assist no channel-points bonus UI for %s", self.url
                )
            return False

        delay = fuzzy_uniform(
            self.settings.claim_delay_seconds_min,
            self.settings.claim_delay_seconds_max,
        )
        if not self._sleep(delay):
            return False

        # DOM may change during fuzzy delay — re-probe exact eligible button.
        hit = self._probe_claim(client)
        if not hit.get("found"):
            logger.info(
                "Twitch page assist claim candidate stale/missing after delay for %s",
                self.url,
            )
            return False

        offset = max(0, int(self.settings.claim_click_offset_px))
        left = float(hit.get("left", hit.get("x", 0)))
        top = float(hit.get("top", hit.get("y", 0)))
        width = float(hit.get("width", 0))
        height = float(hit.get("height", 0))
        cx = float(hit.get("x", left + width / 2))
        cy = float(hit.get("y", top + height / 2))
        if width <= 0 or height <= 0:
            width = 2.0
            height = 2.0
            left = cx - 1.0
            top = cy - 1.0
        x, y, dx, dy = _bounded_jitter(
            cx, cy, left=left, top=top, width=width, height=height, offset=offset
        )
        key = str(hit.get("key") or "")
        try:
            client.click_at(x, y)
        except Exception:
            logger.exception("Claim click failed for %s", self.url)
            return False

        if not self._sleep(_CLAIM_VERIFY_WAIT_S):
            return False
        after = self._claim_still_present(client, key)
        if after.get("present"):
            logger.warning(
                "Twitch page assist claim click unverified for %s "
                "(bonus still present; offset=%s,%s)",
                self.url,
                dx,
                dy,
            )
            return False

        logger.info(
            "Twitch page assist channel-points bonus disappeared after click "
            "for %s (verified; offset=%s,%s; not asserting server balance)",
            self.url,
            dx,
            dy,
        )
        return True


def start_page_assist(
    url: str,
    port: int,
    settings: ViewerEngagementSettings | dict[str, Any],
) -> bool:
    """Start (or replace) a page-assist worker for *url*. Returns True if started."""
    engagement = coerce_viewer_engagement(settings)
    if engagement is None or not engagement.page_assist_active():
        return False
    if not is_twitch_url(url) or port <= 0:
        return False
    key = _url_key(url)
    worker = _PageAssistWorker(url, port, engagement)
    with _assist_lock:
        previous = _assist_by_url.pop(key, None)
        _assist_by_url[key] = worker
    if previous is not None:
        previous.stop()
        previous.join(timeout=1.0)
    worker.start()
    return True


def stop_page_assist(url: str) -> None:
    key = _url_key(url)
    with _assist_lock:
        worker = _assist_by_url.pop(key, None)
    if worker is not None:
        worker.stop()
        worker.join(timeout=2.0)


def stop_all_page_assist() -> None:
    with _assist_lock:
        workers = list(_assist_by_url.values())
        _assist_by_url.clear()
    for worker in workers:
        worker.stop()
    for worker in workers:
        worker.join(timeout=2.0)


def active_page_assist_urls() -> set[str]:
    with _assist_lock:
        return set(_assist_by_url.keys())
