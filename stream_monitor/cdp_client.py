"""Minimal Chromium DevTools Protocol client for Twitch page assist.

Source-run only. Packaged builds must not import the execution path that
attaches to a live browser; helpers that allocate a port remain safe to call
from tests.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_CDP_MODIFIER_ALT = 1
_CDP_VK_ALT = 18
_CDP_VK_T = 84


def allocate_debugging_port() -> int:
    """Bind an ephemeral localhost port and return its number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _list_targets(port: int, timeout: float = 2.0) -> list[dict[str, Any]]:
    url = f"http://127.0.0.1:{port}/json/list"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _pick_page_ws(port: int, url_hint: str) -> str | None:
    hint = (url_hint or "").strip().lower()
    targets = _list_targets(port)
    pages = [
        item
        for item in targets
        if str(item.get("type") or "") == "page"
        and isinstance(item.get("webSocketDebuggerUrl"), str)
    ]
    if not pages:
        return None

    def score(item: dict[str, Any]) -> tuple[int, int]:
        target_url = str(item.get("url") or "").lower()
        exact = 1 if hint and hint in target_url else 0
        twitch = 1 if "twitch.tv" in target_url else 0
        return (exact, twitch)

    pages.sort(key=score, reverse=True)
    return str(pages[0]["webSocketDebuggerUrl"])


class CdpClient:
    """Synchronous CDP session over a single page WebSocket."""

    def __init__(self) -> None:
        self._ws: Any | None = None
        self._next_id = 1
        self._lock = threading.Lock()
        self._pending: dict[int, dict[str, Any]] = {}
        self._reader: threading.Thread | None = None
        self._closed = threading.Event()

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._closed.is_set()

    def connect(
        self,
        port: int,
        url_hint: str = "",
        *,
        timeout: float = 45.0,
        poll_interval: float = 0.5,
    ) -> None:
        """Wait for Chrome's debug endpoint and attach to a matching page."""
        if self._ws is not None:
            raise RuntimeError("CdpClient is already connected")
        try:
            import websocket  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - env missing dep
            raise RuntimeError(
                "websocket-client is required for CDP page assist"
            ) from exc

        deadline = time.monotonic() + max(1.0, timeout)
        ws_url: str | None = None
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                ws_url = _pick_page_ws(port, url_hint)
                if ws_url:
                    break
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last_error = exc
            time.sleep(poll_interval)
        if not ws_url:
            raise TimeoutError(
                f"CDP page target not ready on port {port}: {last_error}"
            )

        ws = websocket.create_connection(ws_url, timeout=10)
        self._ws = ws
        self._closed.clear()
        self._reader = threading.Thread(
            target=self._read_loop,
            name=f"cdp-reader-{port}",
            daemon=True,
        )
        self._reader.start()
        self.call("Page.enable")
        self.call("Runtime.enable")

    def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            while not self._closed.is_set():
                raw = self._ws.recv()
                if not raw:
                    break
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                msg_id = message.get("id")
                if isinstance(msg_id, int):
                    with self._lock:
                        self._pending[msg_id] = message
        except Exception:
            logger.debug("CDP reader stopped", exc_info=True)
        finally:
            self._closed.set()

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("CdpClient is not connected")
        with self._lock:
            msg_id = self._next_id
            self._next_id += 1
            payload = {"id": msg_id, "method": method}
            if params is not None:
                payload["params"] = params
            self._ws.send(json.dumps(payload))
        deadline = time.monotonic() + max(0.5, timeout)
        while time.monotonic() < deadline:
            with self._lock:
                message = self._pending.pop(msg_id, None)
            if message is not None:
                if "error" in message:
                    raise RuntimeError(f"CDP {method} failed: {message['error']}")
                result = message.get("result")
                return result if isinstance(result, dict) else {}
            if self._closed.is_set():
                raise RuntimeError(f"CDP connection closed during {method}")
            time.sleep(0.02)
        raise TimeoutError(f"CDP call timed out: {method}")

    def evaluate(self, expression: str, *, timeout: float = 10.0) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
            timeout=timeout,
        )
        remote = result.get("result")
        if not isinstance(remote, dict):
            return None
        if remote.get("subtype") == "error":
            raise RuntimeError(f"Runtime.evaluate error: {remote}")
        return remote.get("value")

    def click_at(self, x: float, y: float) -> None:
        for event_type in ("mousePressed", "mouseReleased"):
            self.call(
                "Input.dispatchMouseEvent",
                {
                    "type": event_type,
                    "x": float(x),
                    "y": float(y),
                    "button": "left",
                    "clickCount": 1,
                },
            )

    def send_alt_t(self) -> None:
        """Dispatch Alt+T (Twitch theater mode shortcut)."""
        for phase, key, vk, text in (
            ("keyDown", "Alt", _CDP_VK_ALT, ""),
            ("keyDown", "t", _CDP_VK_T, "t"),
            ("keyUp", "t", _CDP_VK_T, "t"),
            ("keyUp", "Alt", _CDP_VK_ALT, ""),
        ):
            params: dict[str, Any] = {
                "type": phase,
                "modifiers": _CDP_MODIFIER_ALT,
                "windowsVirtualKeyCode": vk,
                "code": "AltLeft" if key == "Alt" else "KeyT",
                "key": key,
            }
            if text:
                params["text"] = text
            self.call("Input.dispatchKeyEvent", params)

    def reload(self, *, ignore_cache: bool = False) -> None:
        self.call("Page.reload", {"ignoreCache": bool(ignore_cache)})

    def close(self) -> None:
        self._closed.set()
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                logger.debug("CDP websocket close failed", exc_info=True)
        reader = self._reader
        if reader is not None and reader.is_alive():
            reader.join(timeout=1.0)
        self._reader = None
