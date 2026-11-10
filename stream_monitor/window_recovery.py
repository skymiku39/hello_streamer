"""Recover only windows carrying this portable copy's ownership marker."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_BROWSER_CLASSES = {"Chrome_WidgetWin_1", "MozillaWindowClass"}


class WindowIdentity:
    """A window property survives our exit, but not destruction of the HWND.

    A saved HWND/PID or matching title alone is insufficient: Windows can reuse
    handles, and raids change titles. The random marker is not a memory pointer.
    """

    def __init__(self, root: Path) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        digest = hashlib.sha256(os.path.normcase(str(root.resolve())).encode()).hexdigest()[:24]
        self.property_name = "HelloStreamer.Owner." + digest
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.api.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
        self.api.SetPropW.restype = wintypes.BOOL
        self.api.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        self.api.GetPropW.restype = wintypes.HANDLE
        self.api.RemovePropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        self.api.RemovePropW.restype = wintypes.HANDLE
        self.api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.api.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.api.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.api.GetClassNameW.restype = ctypes.c_int

    def _owner(self, hwnd: int) -> tuple[int, str]:
        pid = self.wintypes.DWORD()
        self.api.GetWindowThreadProcessId(hwnd, self.ctypes.byref(pid))
        name = self.ctypes.create_unicode_buffer(256)
        self.api.GetClassNameW(hwnd, name, len(name))
        return pid.value, name.value

    def capture(self, hwnd: int) -> dict[str, Any] | None:
        pid, class_name = self._owner(hwnd)
        if not pid or class_name not in _BROWSER_CLASSES:
            return None
        token = secrets.randbits(31) + 1
        if not self.api.SetPropW(hwnd, self.property_name, token):
            logger.warning("Cannot mark managed window HWND=%s for restart recovery", hwnd)
            return None
        return {"hwnd": hwnd, "pid": pid, "class_name": class_name, "token": token}

    def matches(self, record: dict[str, Any]) -> bool:
        hwnd, token, pid = (record.get(k) for k in ("hwnd", "token", "pid"))
        if not all(type(value) is int and value > 0 for value in (hwnd, token, pid)):
            return False
        if record.get("class_name") not in _BROWSER_CLASSES:
            return False
        return (
            self.api.GetPropW(hwnd, self.property_name) == token
            and self._owner(hwnd) == (pid, record["class_name"])
        )

    def release(self, record: dict[str, Any]) -> None:
        if self.matches(record):
            self.api.RemovePropW(record["hwnd"], self.property_name)


class WindowRecovery:
    """An atomic journal; callers serialize operations with the registry lock."""

    def __init__(self, path: Path, identity: Any) -> None:
        self.path = path
        self.identity = identity
        self.records: dict[tuple[str, int], dict[str, Any]] = {}
        self.known_urls: set[str] = set()

    def restore(self) -> list[dict[str, Any]]:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(document, dict) or document.get("version") != 1:
                raise ValueError("unsupported managed-window journal")
            rows = document.get("windows")
            if not isinstance(rows, list):
                raise ValueError("invalid managed-window list")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                url, hwnd = row.get("url"), row.get("hwnd")
                keywords = row.get("keywords", [])
                if not isinstance(url, str) or not url or type(hwnd) is not int:
                    continue
                self.known_urls.add(url)
                if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
                    continue
                if self.identity.matches(row):
                    row["keywords"] = keywords
                    self.records[(url, hwnd)] = row
                    logger.info("Recovered managed window HWND=%s url=%s", hwnd, url)
                else:
                    logger.info("Discarded stale managed-window record HWND=%s url=%s", hwnd, url)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, OverflowError):
            logger.exception("Could not restore managed windows; refusing unverified ownership")
        return list(self.records.values())

    def save(self, windows: dict[str, list[Any]]) -> None:
        wanted = {(url, w.hwnd): w for url, bucket in windows.items() for w in bucket}
        self.known_urls.update(url for url, _hwnd in wanted)
        for key in self.records.keys() - wanted.keys():
            self.identity.release(self.records.pop(key))
        for (url, hwnd), window in wanted.items():
            key = (url, hwnd)
            record = self.records.get(key)
            if record is None:
                record = self.identity.capture(hwnd)
                if record is None:
                    continue
                self.records[key] = record
            record.update(url=url, keywords=list(window.keywords))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as stream:
            json.dump({"version": 1, "windows": list(self.records.values())}, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(self.path)
