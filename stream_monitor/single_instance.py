"""單一執行個體鎖定 — 透過 localhost socket 確保只有一個程式在執行。

若偵測到已有實例在運行，發送 SHOW 指令喚醒舊視窗。
"""

from __future__ import annotations

import hashlib
import logging
import socket
import threading
from pathlib import Path
from typing import Callable

from stream_monitor.portable_storage import portable_paths

logger = logging.getLogger(__name__)

_PORT = 47201  # legacy/default value kept for integrations that import it
_HOST = "127.0.0.1"
_MSG_SHOW = b"SHOW"


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
        try:
            self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            self._server.bind((_HOST, self._port))
            self._server.listen(1)
            self._thread = threading.Thread(target=self._listen, daemon=True)
            self._thread.start()
            return True
        except OSError:
            self._server = None
            self._signal_existing()
            return False

    def _listen(self) -> None:
        while self._server is not None:
            try:
                conn, _ = self._server.accept()
                data = conn.recv(64)
                conn.close()
                if data == _MSG_SHOW and self._on_show:
                    self._on_show()
            except OSError:
                break

    def _signal_existing(self) -> None:
        """Tell the already-running instance to show its window."""
        try:
            with socket.create_connection((_HOST, self._port), timeout=2) as s:
                s.sendall(_MSG_SHOW)
            logger.info("Signalled existing instance to show")
        except OSError:
            logger.warning("Failed to signal existing instance")

    def release(self) -> None:
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
