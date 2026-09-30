"""Twitch in-page assist orchestrator driven by Chromium CDP.

Runs only for source builds with viewer_engagement.page_assist_enabled.
Each managed Twitch window gets one background worker that:
  1. accepts content-classification / Start Watching gates
  2. optionally toggles theater mode (Alt+T)
  3. optionally claims Channel Points with fuzzy delay / click offset
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
from stream_monitor.viewer_engagement_model import (
    ViewerEngagementSettings,
    coerce_viewer_engagement,
    is_twitch_url,
    page_assist_runtime_allowed,
)

logger = logging.getLogger(__name__)

_GATE_TIMEOUT_S = 60.0
_GATE_POLL_S = 1.5
_CLAIM_COOLDOWN_MIN_S = 12 * 60
_CLAIM_COOLDOWN_MAX_S = 18 * 60

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

_FIND_CLAIM_JS = r"""
(() => {
  const selectors = [
    '[data-a-target="community-points-summary"] button',
    'button[aria-label*="Claim" i]',
    'button[aria-label*="領取"]',
    'button[aria-label*="领取"]',
    'button[aria-label*="受け取る"]',
    'button[aria-label*="받기"]',
    '.claimable-bonus__icon'
  ];
  for (const sel of selectors) {
    const node = document.querySelector(sel);
    if (!node) continue;
    const el = node.closest ? (node.closest("button") || node) : node;
    if (!(el instanceof HTMLElement)) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) continue;
    return {found: true, x: r.left + r.width / 2, y: r.top + r.height / 2};
  }
  return {found: false};
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
            logger.info("Twitch page assist attached via CDP for %s", self.url)
            next_refresh_at = self._schedule_refresh()
            while not self._stop.is_set():
                self._run_gate_and_theater(client)
                claim_deadline = time.monotonic() + fuzzy_uniform(
                    _CLAIM_COOLDOWN_MIN_S, _CLAIM_COOLDOWN_MAX_S
                )
                while not self._stop.is_set():
                    now = time.monotonic()
                    if (
                        self.settings.auto_refresh
                        and next_refresh_at is not None
                        and now >= next_refresh_at
                    ):
                        logger.info("Twitch page assist reloading %s", self.url)
                        try:
                            client.reload()
                        except Exception:
                            logger.exception("Page.reload failed for %s", self.url)
                            return
                        if not self._sleep(3.0):
                            return
                        next_refresh_at = self._schedule_refresh()
                        break  # restart gate → theater after reload

                    if self.settings.claim_channel_points and now >= claim_deadline:
                        if self._try_claim(client):
                            claim_deadline = now + fuzzy_uniform(
                                _CLAIM_COOLDOWN_MIN_S, _CLAIM_COOLDOWN_MAX_S
                            )
                        else:
                            claim_deadline = now + fuzzy_uniform(
                                self.settings.claim_poll_seconds_min,
                                self.settings.claim_poll_seconds_max,
                            )
                    wait_s = fuzzy_uniform(
                        self.settings.claim_poll_seconds_min,
                        self.settings.claim_poll_seconds_max,
                    )
                    if next_refresh_at is not None:
                        wait_s = min(wait_s, max(1.0, next_refresh_at - time.monotonic()))
                    if not self._sleep(wait_s):
                        return
        except Exception:
            logger.exception("Twitch page assist stopped for %s", self.url)
        finally:
            client.close()
            with _assist_lock:
                current = _assist_by_url.get(_url_key(self.url))
                if current is self:
                    _assist_by_url.pop(_url_key(self.url), None)

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
            deadline = time.monotonic() + _GATE_TIMEOUT_S
            while not self._stop.is_set() and time.monotonic() < deadline:
                if self._try_accept_gate(client):
                    break
                if not self._sleep(_GATE_POLL_S):
                    return
        if self.settings.theater_mode and not self._stop.is_set():
            if not self._sleep(float(self.settings.theater_delay_seconds)):
                return
            try:
                client.send_alt_t()
                logger.info("Twitch page assist sent Alt+T for %s", self.url)
            except Exception:
                logger.exception("Failed to send theater Alt+T for %s", self.url)

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

    def _try_claim(self, client: CdpClient) -> bool:
        try:
            hit = client.evaluate(_FIND_CLAIM_JS)
        except Exception:
            logger.debug("Claim probe failed for %s", self.url, exc_info=True)
            return False
        if not isinstance(hit, dict) or not hit.get("found"):
            return False
        delay = fuzzy_uniform(
            self.settings.claim_delay_seconds_min,
            self.settings.claim_delay_seconds_max,
        )
        if not self._sleep(delay):
            return False
        offset = max(0, int(self.settings.claim_click_offset_px))
        dx = random.randint(-offset, offset) if offset else 0
        dy = random.randint(-offset, offset) if offset else 0
        x = float(hit.get("x", 0)) + dx
        y = float(hit.get("y", 0)) + dy
        try:
            client.click_at(x, y)
            logger.info(
                "Twitch page assist claimed channel points for %s (offset=%s,%s)",
                self.url,
                dx,
                dy,
            )
            return True
        except Exception:
            logger.exception("Claim click failed for %s", self.url)
            return False


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
