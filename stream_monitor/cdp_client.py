"""Minimal Chromium DevTools Protocol client for Twitch page assist.

Source-run only. Packaged builds must not import the execution path that
attaches to a live browser. Production cold-start uses Chromium's
``--remote-debugging-port=0`` plus ``DevToolsActivePort`` resolution;
port-lease helpers remain only for tests / legacy callers.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_CDP_MODIFIER_ALT = 1
_CDP_VK_ALT = 18
_CDP_VK_T = 84

# Bound attach waits so a missing endpoint fails promptly instead of hanging.
DEFAULT_CDP_ATTACH_TIMEOUT_S = 12.0
# Already-running profiles either expose CDP immediately or never will.
BUSY_CDP_ATTACH_TIMEOUT_S = 1.5


@dataclass(frozen=True)
class CdpAttachResult:
    """Observable outcome of CDP port resolve / attach preflight."""

    ok: bool
    port: int | None = None
    reason: str = ""
    message: str = ""


@dataclass
class DebuggingPortLease:
    """Test helper: hold an ephemeral localhost port (not used in production)."""

    port: int
    _sock: socket.socket | None = field(default=None, repr=False)

    def release(self) -> int:
        """Close the binder so a caller can claim *port*. Returns the port."""
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                logger.debug("CDP port lease close failed", exc_info=True)
        return self.port


def lease_debugging_port() -> DebuggingPortLease:
    """Bind and hold an ephemeral port (tests / legacy only)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
    except OSError:
        sock.close()
        raise
    return DebuggingPortLease(port=int(sock.getsockname()[1]), _sock=sock)


def allocate_debugging_port() -> int:
    """Return an ephemeral localhost port (releases the binder immediately)."""
    return lease_debugging_port().release()


def profile_looks_busy(user_data_dir: str) -> bool:
    """True when Chromium profile lock files suggest a master process is live."""
    root = (user_data_dir or "").strip()
    if not root:
        return False
    base = Path(root)
    for name in ("SingletonLock", "SingletonCookie", "lockfile"):
        if (base / name).exists():
            return True
    return False


def read_devtools_active_port(user_data_dir: str) -> int | None:
    """Parse Chrome's DevToolsActivePort file; return port or None."""
    root = (user_data_dir or "").strip()
    if not root:
        return None
    path = Path(root) / "DevToolsActivePort"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    first = (text.splitlines() or [""])[0].strip()
    if not first.isdigit():
        return None
    port = int(first)
    return port if port > 0 else None


def wait_for_devtools_active_port(
    user_data_dir: str,
    *,
    timeout: float = DEFAULT_CDP_ATTACH_TIMEOUT_S,
    poll_interval: float = 0.2,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    reader: Callable[[str], int | None] = read_devtools_active_port,
) -> CdpAttachResult:
    """Poll DevToolsActivePort until ready or the bounded timeout elapses."""
    deadline = clock() + max(0.5, timeout)
    while clock() < deadline:
        port = reader(user_data_dir)
        if port is not None:
            return CdpAttachResult(ok=True, port=port, reason="devtools_file")
        sleep(max(0.05, poll_interval))
    return CdpAttachResult(
        ok=False,
        reason="timeout",
        message=(
            f"DevToolsActivePort not ready within {timeout:.1f}s "
            f"under {user_data_dir!r}"
        ),
    )


def probe_cdp_endpoint(port: int, *, timeout: float = 1.5) -> bool:
    """True when ``/json/list`` responds on *port*."""
    if port <= 0:
        return False
    try:
        _list_targets(port, timeout=timeout)
        return True
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return False


def resolve_cdp_attach(
    user_data_dir: str,
    *,
    preferred_port: int | None = None,
    profile_was_busy: bool = False,
    timeout: float = DEFAULT_CDP_ATTACH_TIMEOUT_S,
    poll_interval: float = 0.2,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    endpoint_probe: Callable[..., bool] | None = None,
    port_reader: Callable[[str], int | None] | None = None,
) -> CdpAttachResult:
    """Resolve a usable CDP port without unbounded waiting.

    Production cold-start launches with ``--remote-debugging-port=0`` and then
    polls + validates ``DevToolsActivePort``. An already-running profile is
    attached only when an existing endpoint is already usable; otherwise this
    returns promptly with ``reason=profile_busy``.
    """
    tick = clock or time.monotonic
    pause = sleep or time.sleep
    probe = endpoint_probe or probe_cdp_endpoint
    reader = port_reader or read_devtools_active_port

    effective_timeout = float(timeout)
    if profile_was_busy:
        effective_timeout = min(effective_timeout, BUSY_CDP_ATTACH_TIMEOUT_S)
    deadline = tick() + max(0.05, effective_timeout)
    probe_timeout = min(1.0, max(0.2, effective_timeout))

    while tick() < deadline:
        if preferred_port and preferred_port > 0:
            if probe(preferred_port, timeout=probe_timeout):
                return CdpAttachResult(
                    ok=True, port=preferred_port, reason="preferred_port"
                )
        port = reader(user_data_dir)
        if port is not None and probe(port, timeout=probe_timeout):
            return CdpAttachResult(ok=True, port=port, reason="devtools_file")
        pause(max(0.05, poll_interval))

    if profile_was_busy:
        detail = (
            f"on port {preferred_port}"
            if preferred_port and preferred_port > 0
            else "via DevToolsActivePort"
        )
        return CdpAttachResult(
            ok=False,
            reason="profile_busy",
            message=(
                "Existing browser/profile did not expose a usable CDP endpoint "
                f"{detail}"
            ),
        )
    if preferred_port and preferred_port > 0:
        return CdpAttachResult(
            ok=False,
            reason="timeout",
            message=f"CDP endpoint not ready on port {preferred_port}",
        )
    return CdpAttachResult(
        ok=False,
        reason="timeout",
        message=(
            f"DevToolsActivePort / CDP endpoint not ready within "
            f"{effective_timeout:.1f}s under {user_data_dir!r}"
        ),
    )


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
        timeout: float = DEFAULT_CDP_ATTACH_TIMEOUT_S,
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
            except (
                urllib.error.URLError,
                TimeoutError,
                OSError,
                json.JSONDecodeError,
            ) as exc:
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
