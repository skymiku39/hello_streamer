"""單一執行個體鎖定 — 透過 localhost socket 確保只有一個程式在執行。

若偵測到已有實例在運行，發送 SHOW 指令喚醒舊視窗。
"""

from __future__ import annotations

import hashlib
import logging
import socket
import threading
import time
from pathlib import Path
from typing import Callable

from stream_monitor.portable_storage import portable_paths

logger = logging.getLogger(__name__)

_PORT = 47201  # legacy/default value kept for integrations that import it
_HOST = "127.0.0.1"
_MSG_SHOW = b"SHOW"
_SOCKET_TIMEOUT = 0.2
_REQUEST_TIMEOUT = 1.0


def port_for_root(root: Path | None = None) -> int:
    """Return a stable localhost IPC port for one portable app copy.

    A fixed global port makes two independent portable copies interfere with
    each other.  Hashing the resolved portable root keeps the single-instance
    boundary local to that copy while remaining stable across restarts.
    """
    resolved = (root or portable_paths().root).resolve()
    digest = hashlib.blake2s(str(resolved).encode("utf-8"), digest_size=4).digest()
    return 41000 + (int.from_bytes(digest, "big") % 18000)


class SingleInstance:
    """Acquire a single-instance lock via a localhost TCP port."""

    def __init__(
        self,
        on_show_request: Callable[[], None] | None = None,
        *,
        root: Path | None = None,
        port: int | None = None,
    ) -> None:
        self._server: socket.socket | None = None
        self._on_show = on_show_request
        self._thread: threading.Thread | None = None
        self._port = port if port is not None else port_for_root(root)

    def try_lock(self) -> bool:
        """Return True if this is the first instance; False if another is running."""
        if self._server is not None:
            return True
        server: socket.socket | None = None
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            server.bind((_HOST, self._port))
            server.listen(1)
            server.settimeout(_SOCKET_TIMEOUT)
            self._server = server
            self._thread = threading.Thread(
                target=self._listen, args=(server,), daemon=True,
                name="single-instance-listener",
            )
            self._thread.start()
            return True
        except OSError:
            self._server = None
            if server is not None:
                server.close()
            self._signal_existing()
            return False

    def _listen(self, server: socket.socket) -> None:
        while self._server is server:
            try:
                conn, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                with conn:
                    data = self._read_request(conn, server)
            except OSError:
                # A reset or incomplete client must not disable the listener.
                logger.debug("Single-instance client disconnected", exc_info=True)
                continue
            if self._server is server and data == _MSG_SHOW and self._on_show:
                try:
                    self._on_show()
                except Exception:
                    logger.exception("Could not show the existing instance")

    def _read_request(self, conn: socket.socket, server: socket.socket) -> bytes:
        conn.settimeout(_SOCKET_TIMEOUT)
        deadline = time.monotonic() + _REQUEST_TIMEOUT
        data = b""
        while self._server is server and len(data) < len(_MSG_SHOW):
            if time.monotonic() >= deadline:
                break
            try:
                chunk = conn.recv(len(_MSG_SHOW) - len(data))
            except TimeoutError:
                continue
            if not chunk:
                break
            data += chunk
            if not _MSG_SHOW.startswith(data):
                break
        return data

    def _signal_existing(self) -> None:
        """Tell the already-running instance to show its window."""
        try:
            with socket.create_connection((_HOST, self._port), timeout=2) as s:
                s.sendall(_MSG_SHOW)
            logger.info("Signalled existing instance to show")
        except OSError:
            logger.warning("Failed to signal existing instance")

    def release(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            try:
                server.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            server.close()
