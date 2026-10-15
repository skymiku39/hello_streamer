"""Durable exit evidence, including windowed-build native crashes."""

from __future__ import annotations

import faulthandler
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

logger = logging.getLogger(__name__)


def process_job_membership() -> bool | None:
    """Report Windows job membership without changing process/job ownership."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.IsProcessInJob.argtypes = [
            wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
        ]
        kernel32.IsProcessInJob.restype = wintypes.BOOL
        result = wintypes.BOOL()
        if kernel32.IsProcessInJob(kernel32.GetCurrentProcess(), None, ctypes.byref(result)):
            return bool(result.value)
    except (AttributeError, OSError):
        logger.debug("Could not read Windows job membership", exc_info=True)
    return None


class RuntimeDiagnostics:
    """Start only after the single-instance lock; retain unfinished sessions."""

    def __init__(self, log_dir: Path) -> None:
        self.path = log_dir / "runtime-session.json"
        self.crash_path = log_dir / "native-crash.log"
        self.session: dict[str, Any] = {}
        self._crash_file: IO[str] | None = None

    def start(self, version: str) -> None:
        try:
            previous = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(previous, dict) and previous.get("state") == "running":
                logger.warning(
                    "Previous application session ended without an exit record: "
                    "pid=%s started_at=%s (external termination or native crash possible)",
                    previous.get("pid"), previous.get("started_at"),
                )
                self.path.with_name("runtime-session.previous.json").write_text(
                    json.dumps(previous, ensure_ascii=False, indent=2), encoding="utf-8",
                )
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            logger.exception("Could not read previous application session")
        self.session = {
            "state": "running", "pid": os.getpid(), "parent_pid": os.getppid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "version": version, "executable": sys.executable,
            "windows_job_member": process_job_membership(),
        }
        self._write()
        logger.info("Application session started: %s", json.dumps(self.session))
        # Do not replace a debugger/test runner's existing fault handler.
        if not faulthandler.is_enabled():
            try:
                self._crash_file = self.crash_path.open("a", encoding="utf-8")
                self._crash_file.write("\nSession " + json.dumps(self.session) + "\n")
                self._crash_file.flush()
                faulthandler.enable(file=self._crash_file, all_threads=True)
            except (OSError, RuntimeError):
                logger.exception("Could not enable native crash logging")
                if self._crash_file is not None:
                    self._crash_file.close()
                    self._crash_file = None

    def finish(self, reason: str) -> None:
        self.session.update(
            state="exited", exit_reason=reason,
            ended_at=datetime.now(timezone.utc).isoformat(),
        )
        self._write()
        logger.info("Application session ended: reason=%s pid=%s", reason, os.getpid())
        if self._crash_file is not None:
            faulthandler.disable()
            self._crash_file.close()
            self._crash_file = None

    def _write(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            with temp.open("w", encoding="utf-8") as stream:
                json.dump(self.session, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            temp.replace(self.path)
        except OSError:
            logger.exception("Could not persist application session")
