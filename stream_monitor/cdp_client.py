"""Minimal Chromium DevTools Protocol client for Twitch page assist.

Used by source and packaged builds when the user enables page assist for a
dedicated Chromium profile and a managed browser window.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_CDP_MODIFIER_ALT = 1
_CDP_VK_ALT = 18
_CDP_VK_T = 84

# Bound attach waits so a missing endpoint fails promptly instead of hanging.
DEFAULT_CDP_ATTACH_TIMEOUT_S = 12.0
# Keep GUI launch paths snappy; full page attach still uses DEFAULT_CDP_ATTACH_TIMEOUT_S.
DEFAULT_CDP_DISCOVERY_TIMEOUT_S = 2.5

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


@dataclass(frozen=True)
class CdpAttachResult:
    """Observable outcome of CDP port resolve / attach preflight."""

    ok: bool
    port: int | None = None
    reason: str = ""
    message: str = ""
    lease: DebuggingPortLease | None = None
    pass_debugging_flag: bool = False


@dataclass
class DebuggingPortLease:
    """Hold an ephemeral localhost port until Chrome is about to bind it."""

    port: int
    _sock: socket.socket | None = field(default=None, repr=False)

    def release(self) -> int:
        """Close the binder so the browser can claim *port*. Returns the port."""
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                logger.debug("CDP port lease close failed", exc_info=True)
        return self.port


def lease_debugging_port() -> DebuggingPortLease:
    """Bind and hold an ephemeral port to shrink the cold-start race window."""
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


def normalize_profile_key(user_data_dir: str) -> str:
    """Stable key for app-owned profile paths (case-normalized on Windows)."""
    raw = (user_data_dir or "").strip()
    if not raw:
        return ""
    expanded = os.path.expandvars(os.path.expanduser(raw))
    try:
        resolved = str(Path(expanded).resolve())
    except OSError:
        resolved = str(Path(expanded))
    if os.name == "nt":
        return resolved.replace("/", "\\").lower()
    return resolved


def normalize_page_url_key(url: str) -> str:
    """Normalize a page URL to ``scheme://netloc/path`` (no query/fragment)."""
    parts = urlsplit((url or "").strip())
    scheme = (parts.scheme or "https").lower()
    netloc = (parts.netloc or "").lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path.rstrip("/") or "/"
    return f"{scheme}://{netloc}{path.lower()}"


def page_urls_match(requested: str, candidate: str) -> bool:
    """True when *candidate* is the exact normalized page for *requested*."""
    hint = (requested or "").strip()
    if not hint:
        return False
    return normalize_page_url_key(hint) == normalize_page_url_key(candidate)


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
    except (OSError, UnicodeDecodeError):
        return None
    first = (text.splitlines() or [""])[0].strip()
    if not first.isascii() or not first.isdigit() or len(first) > 5:
        return None
    port = int(first)
    return port if 0 < port <= 65535 else None


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
    interval = max(0.05, poll_interval)
    # Guard frozen test clocks / no-op sleep so this never spins forever.
    max_iterations = max(1, int(max(0.5, timeout) / interval) + 3)
    for _ in range(max_iterations):
        if clock() >= deadline:
            break
        port = reader(user_data_dir)
        if port is not None:
            return CdpAttachResult(ok=True, port=port, reason="devtools_file")
        sleep(interval)
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


def endpoint_belongs_to_profile(
    port: int,
    user_data_dir: str,
    *,
    reader: Callable[[str], int | None] = read_devtools_active_port,
    endpoint_probe: Callable[..., bool] = probe_cdp_endpoint,
) -> bool:
    """True only when *port* is proven to be this profile's DevTools endpoint."""
    if port <= 0 or not (user_data_dir or "").strip():
        return False
    file_port = reader(user_data_dir)
    if file_port is None or file_port != port:
        return False
    return bool(endpoint_probe(port, timeout=1.0))


class CdpPortRegistry:
    """Thread-safe cache of app-owned profile path → debugging port."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._ports: dict[str, int] = {}
        self._conditions: dict[str, threading.Condition] = {}

    def _condition_for(self, key: str) -> threading.Condition:
        cond = self._conditions.get(key)
        if cond is None:
            cond = threading.Condition(self._lock)
            self._conditions[key] = cond
        return cond

    def get(self, profile_key: str) -> int | None:
        key = (profile_key or "").strip()
        if not key:
            return None
        with self._lock:
            port = self._ports.get(key)
            return int(port) if isinstance(port, int) and port > 0 else None

    def put(self, profile_key: str, port: int) -> None:
        key = (profile_key or "").strip()
        if not key or port <= 0:
            return
        with self._lock:
            self._ports[key] = int(port)
            self._condition_for(key).notify_all()

    def clear(self, profile_key: str | None = None) -> None:
        with self._lock:
            if profile_key is None:
                self._ports.clear()
                for cond in self._conditions.values():
                    cond.notify_all()
                return
            key = profile_key.strip()
            self._ports.pop(key, None)
            cond = self._conditions.get(key)
            if cond is not None:
                cond.notify_all()

    def remember_if_absent(self, profile_key: str, port: int) -> int:
        """Register *port* unless another winner already claimed the key."""
        key = (profile_key or "").strip()
        if not key or port <= 0:
            return port
        with self._lock:
            existing = self._ports.get(key)
            if isinstance(existing, int) and existing > 0:
                return existing
            self._ports[key] = int(port)
            self._condition_for(key).notify_all()
            return int(port)

    def wait_for_port(
        self,
        profile_key: str,
        *,
        timeout: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> int | None:
        """Wait until a port is registered for *profile_key* or timeout."""
        key = (profile_key or "").strip()
        if not key:
            return None
        deadline = clock() + max(0.0, timeout)
        with self._lock:
            cond = self._condition_for(key)
            while True:
                existing = self._ports.get(key)
                if isinstance(existing, int) and existing > 0:
                    return existing
                remaining = deadline - clock()
                if remaining <= 0:
                    return None
                cond.wait(timeout=remaining)


_PORT_REGISTRY = CdpPortRegistry()


def get_cdp_port_registry() -> CdpPortRegistry:
    return _PORT_REGISTRY


def reset_cdp_port_registry() -> None:
    """Clear the process-wide registry (tests / recovery)."""
    _PORT_REGISTRY.clear()


def _windows_profile_debug_info(
    user_data_dir: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> tuple[bool, int | None]:
    """Hidden lookup: ``(profile_process_seen, debug_port_or_none)``.

    Never logs raw command lines — only a concise match summary.
    """
    if os.name != "nt":
        return False, None
    key = normalize_profile_key(user_data_dir)
    if not key:
        return False, None
    needle = key.replace("'", "''")
    script = (
        "$seen = $false; $port = $null; "
        "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue "
        "-Filter \"Name='chrome.exe' OR Name='msedge.exe' OR "
        "Name='chromium.exe' OR Name='brave.exe' OR Name='vivaldi.exe'\" | "
        "ForEach-Object { "
        "$cl = $_.CommandLine; "
        "if (-not $cl) { return }; "
        f"$n = '{needle}'; "
        "$profileMatch = [regex]::Match($cl, "
        "'(?i)(?:^|\\s)--user-data-dir(?:=|\\s+)(?:\"([^\"]+)\"|([^\\s\"]+))'); "
        "if (-not $profileMatch.Success) { return }; "
        "$profileArg = $profileMatch.Groups[1].Value; "
        "if (-not $profileArg) { $profileArg = $profileMatch.Groups[2].Value }; "
        "try { $profileKey = [IO.Path]::GetFullPath($profileArg)"
        ".TrimEnd([char]92).ToLowerInvariant() } catch { return }; "
        "if ($profileKey -eq $n) { "
        "$seen = $true; "
        "if ($null -eq $port -and $cl -match '--remote-debugging-port=(\\d+)') { "
        "$port = [int]$Matches[1] "
        "} "
        "} "
        "}; "
        "if ($seen) { "
        "if ($null -ne $port) { Write-Output \"1 $port\" } "
        "else { Write-Output '1' } "
        "} else { Write-Output '0' }"
    )
    run = runner or subprocess.run
    try:
        completed = run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
            creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.debug("CDP profile process lookup unavailable")
        return False, None
    parts = (completed.stdout or "").strip().split()
    if not parts or parts[0] != "1":
        logger.debug("CDP profile process lookup found no matching process")
        return False, None
    port: int | None = None
    if len(parts) >= 2 and parts[1].isdigit():
        value = int(parts[1])
        port = value if value > 0 else None
    logger.debug(
        "CDP profile process lookup seen=1 port=%s",
        port if port is not None else "none",
    )
    return True, port


def _windows_debug_port_for_profile(
    user_data_dir: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> int | None:
    """Best-effort hidden lookup of an app-owned Chrome debug port."""
    _seen, port = _windows_profile_debug_info(user_data_dir, runner=runner)
    return port


def acquire_cdp_debugging_port(
    user_data_dir: str,
    *,
    registry: CdpPortRegistry | None = None,
    timeout: float = DEFAULT_CDP_DISCOVERY_TIMEOUT_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    endpoint_probe: Callable[..., bool] | None = None,
    reader: Callable[[str], int | None] | None = None,
    busy_check: Callable[[str], bool] | None = None,
    process_lookup: Callable[[str], int | None] | None = None,
    profile_running: Callable[[str], bool] | None = None,
    leaser: Callable[[], DebuggingPortLease] | None = None,
) -> CdpAttachResult:
    """Lease or safely reuse a CDP port for the exact configured profile.

    Only endpoints proven to belong to *user_data_dir* (or ports this process
    previously leased into the registry) are reused. Unrelated debug ports are
    never guessed. Stale lock markers are tolerated when no live endpoint exists.
    Discovery waits stay bounded so GUI launches are not blocked.
    """
    reg = registry if registry is not None else _PORT_REGISTRY
    key = normalize_profile_key(user_data_dir)
    if not key:
        return CdpAttachResult(
            ok=False,
            reason="no_profile",
            message="CDP requires a dedicated user_data_dir",
        )

    endpoint_probe = endpoint_probe or probe_cdp_endpoint
    reader = reader or read_devtools_active_port
    busy_check = busy_check or profile_looks_busy
    leaser = leaser or lease_debugging_port
    lookup = process_lookup
    if lookup is None:
        lookup = _windows_debug_port_for_profile

    def _default_profile_running(path: str) -> bool:
        seen, _port = _windows_profile_debug_info(path)
        return seen

    running_check = (
        profile_running if profile_running is not None else _default_profile_running
    )

    deadline = clock() + max(0.2, timeout)
    probe_timeout = min(0.8, timeout)

    def _try_proven(
        port: int | None,
        reason: str,
        *,
        require_devtools_file: bool = True,
    ) -> CdpAttachResult | None:
        if not isinstance(port, int) or port <= 0:
            return None
        if not endpoint_probe(port, timeout=probe_timeout):
            return None
        file_port = reader(user_data_dir)
        if require_devtools_file:
            if not endpoint_belongs_to_profile(
                port,
                user_data_dir,
                reader=reader,
                endpoint_probe=endpoint_probe,
            ):
                return None
        elif file_port is not None and file_port != port:
            # Process claimed a port that conflicts with this profile's
            # DevToolsActivePort — treat as unrelated, never attach.
            return None
        reg.put(key, port)
        return CdpAttachResult(
            ok=True,
            port=port,
            reason=reason,
            pass_debugging_flag=False,
        )

    cached = reg.get(key)
    if isinstance(cached, int) and cached > 0 and endpoint_probe(
        cached, timeout=probe_timeout
    ):
        return CdpAttachResult(
            ok=True,
            port=cached,
            reason="registry",
            pass_debugging_flag=True,
        )

    proven = _try_proven(reader(user_data_dir), "devtools_file")
    if proven is not None:
        return proven

    busy = bool(busy_check(user_data_dir))
    if busy:
        # Process cmdline match for this exact profile is sufficient proof.
        proven = _try_proven(
            lookup(user_data_dir),
            "process",
            require_devtools_file=False,
        )
        if proven is not None:
            return proven

        wait_budget = max(0.0, deadline - clock())
        if wait_budget > 0:
            waited_dev = wait_for_devtools_active_port(
                user_data_dir,
                timeout=wait_budget,
                clock=clock,
                sleep=sleep,
                reader=reader,
            )
            if waited_dev.ok:
                proven = _try_proven(waited_dev.port, "devtools_file")
                if proven is not None:
                    return proven

        reserved = reg.get(key)
        if isinstance(reserved, int) and reserved > 0:
            # App-owned reservation (cold multi-window / pending bind).
            return CdpAttachResult(
                ok=True,
                port=reserved,
                reason="registry",
                pass_debugging_flag=True,
            )

        if running_check(user_data_dir):
            return CdpAttachResult(
                ok=False,
                reason="profile_nondebug",
                message=(
                    "Existing browser/profile is running without a proven "
                    "remote-debugging endpoint for this user_data_dir"
                ),
            )
        logger.info(
            "CDP treating stale profile lock markers as free for %s",
            key,
        )

    reserved = reg.get(key)
    if isinstance(reserved, int) and reserved > 0:
        # Keep the app-owned reservation for cold multi-window attach even when
        # /json/list is not up yet. Replacement happens only via proven discovery
        # above (DevToolsActivePort / process), never by guessing another port.
        return CdpAttachResult(
            ok=True,
            port=reserved,
            reason="registry",
            pass_debugging_flag=True,
        )

    # Cold lease with a short critical section so concurrent openers share one port.
    with reg._lock:  # noqa: SLF001 — cold-start race winner
        existing = reg._ports.get(key)  # noqa: SLF001
        if isinstance(existing, int) and existing > 0:
            return CdpAttachResult(
                ok=True,
                port=existing,
                reason="registry",
                pass_debugging_flag=True,
            )
        lease = leaser()
        reg._ports[key] = int(lease.port)  # noqa: SLF001
        reg._condition_for(key).notify_all()  # noqa: SLF001
        return CdpAttachResult(
            ok=True,
            port=lease.port,
            reason="leased",
            lease=lease,
            pass_debugging_flag=True,
        )


def resolve_cdp_attach(
    user_data_dir: str,
    *,
    preferred_port: int | None = None,
    profile_was_busy: bool = False,
    timeout: float = DEFAULT_CDP_ATTACH_TIMEOUT_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    endpoint_probe: Callable[..., bool] = probe_cdp_endpoint,
    wait_devtools: Callable[..., CdpAttachResult] = wait_for_devtools_active_port,
    reader: Callable[[str], int | None] = read_devtools_active_port,
) -> CdpAttachResult:
    """Resolve a CDP port after launch without unbounded waiting.

    Fresh profiles typically expose ``preferred_port`` or DevToolsActivePort.
    Preferred ports are accepted only when proven to belong to *user_data_dir*
    (or when the profile has not yet written DevToolsActivePort during cold start).
    An already-running shared profile without debugging surfaces as a timely
    ``profile_busy`` / ``profile_nondebug`` / ``timeout`` failure instead of hang.
    """
    deadline = clock() + max(0.5, timeout)
    if preferred_port and preferred_port > 0:
        interval = 0.2
        max_iterations = max(1, int(max(0.5, timeout) / interval) + 3)
        for _ in range(max_iterations):
            if clock() >= deadline:
                break
            if endpoint_probe(preferred_port, timeout=min(1.0, timeout)):
                file_port = reader(user_data_dir) if user_data_dir else None
                if file_port is None or file_port == preferred_port:
                    return CdpAttachResult(
                        ok=True,
                        port=preferred_port,
                        reason="preferred_port",
                    )
                return CdpAttachResult(
                    ok=False,
                    reason="unrelated_endpoint",
                    message=(
                        f"Port {preferred_port} is live but DevToolsActivePort "
                        f"for this profile is {file_port}"
                    ),
                )
            sleep(interval)
        if profile_was_busy:
            return CdpAttachResult(
                ok=False,
                reason="profile_busy",
                message=(
                    "Existing browser/profile did not expose CDP on "
                    f"port {preferred_port}"
                ),
            )
        return CdpAttachResult(
            ok=False,
            reason="timeout",
            message=f"CDP endpoint not ready on port {preferred_port}",
        )

    remaining = max(0.5, deadline - clock())
    result = wait_devtools(user_data_dir, timeout=remaining, clock=clock, sleep=sleep)
    if result.ok:
        return result
    if profile_was_busy:
        return CdpAttachResult(
            ok=False,
            reason="profile_busy",
            message=(
                "Existing browser/profile did not write a usable "
                "DevToolsActivePort endpoint"
            ),
        )
    return result


def _list_targets(port: int, timeout: float = 2.0) -> list[dict[str, Any]]:
    url = f"http://127.0.0.1:{port}/json/list"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _pick_page_ws(port: int, url_hint: str) -> str | None:
    """Return the websocket URL for the exact requested page, or None.

    When *url_hint* is supplied, never fall back to a different Twitch channel.
    """
    hint = (url_hint or "").strip()
    targets = _list_targets(port)
    pages = [
        item
        for item in targets
        if str(item.get("type") or "") == "page"
        and isinstance(item.get("webSocketDebuggerUrl"), str)
    ]
    if not pages:
        return None

    if hint:
        for item in pages:
            target_url = str(item.get("url") or "")
            if page_urls_match(hint, target_url):
                return str(item["webSocketDebuggerUrl"])
        return None

    # No hint: prefer any twitch page, else first page (legacy/test helper).
    for item in pages:
        if "twitch.tv" in str(item.get("url") or "").lower():
            return str(item["webSocketDebuggerUrl"])
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
        """Wait for Chrome's debug endpoint and attach to the exact page."""
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
        hint = (url_hint or "").strip()
        while time.monotonic() < deadline:
            try:
                ws_url = _pick_page_ws(port, hint)
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
            detail = f": {last_error}" if last_error else ""
            if hint:
                raise TimeoutError(
                    f"CDP page target for {hint!r} not ready on port {port}{detail}"
                )
            raise TimeoutError(
                f"CDP page target not ready on port {port}{detail}"
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
        from websocket import WebSocketTimeoutException

        assert self._ws is not None
        ws = self._ws
        try:
            while not self._closed.is_set():
                try:
                    raw = ws.recv()
                except WebSocketTimeoutException:
                    # A quiet page is still connected. Claim polling and delays
                    # can legitimately exceed the socket's read timeout.
                    continue
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
