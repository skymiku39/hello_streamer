"""Capture the portable app while rapidly scrolling its native viewport.

Run the built portable executable first, then:
    uv run --no-sync python scripts/verify_scroll_repaint.py
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import threading
import time
from pathlib import Path

from PIL import ImageGrab

_GWL_EXSTYLE = -20
_WS_EX_COMPOSITED = 0x02000000
_SWP_FRAMECHANGED = 0x0020


def _set_composited(user32: ctypes.CDLL, hwnd: int, enabled: bool) -> int:
    """Temporarily toggle Windows child-window compositing for diagnosis."""
    get_style = user32.GetWindowLongPtrW
    set_style = user32.SetWindowLongPtrW
    get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
    get_style.restype = ctypes.c_ssize_t
    set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    set_style.restype = ctypes.c_ssize_t
    before = int(get_style(hwnd, _GWL_EXSTYLE))
    after = (
        before | _WS_EX_COMPOSITED
        if enabled
        else before & ~_WS_EX_COMPOSITED
    )
    if after != before:
        set_style(hwnd, _GWL_EXSTYLE, after)
        user32.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            0x0001 | 0x0002 | _SWP_FRAMECHANGED,
        )
    return before


def _activate_window(user32: ctypes.CDLL, hwnd: int) -> bool:
    """Activate a test window even when a browser process owns the foreground."""
    get_foreground = user32.GetForegroundWindow
    get_thread = user32.GetWindowThreadProcessId
    attach_threads = user32.AttachThreadInput
    get_current_thread = ctypes.windll.kernel32.GetCurrentThreadId
    get_foreground.restype = ctypes.c_void_p
    get_thread.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    get_thread.restype = ctypes.c_ulong
    get_current_thread.restype = ctypes.c_ulong
    attach_threads.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_bool]
    attach_threads.restype = ctypes.c_bool

    foreground_hwnd = get_foreground()
    foreground_pid = ctypes.c_ulong()
    target_pid = ctypes.c_ulong()
    foreground_thread = get_thread(foreground_hwnd, ctypes.byref(foreground_pid))
    target_thread = get_thread(hwnd, ctypes.byref(target_pid))
    current_thread = get_current_thread()
    attached: list[tuple[int, int]] = []
    for other_thread in (foreground_thread, target_thread):
        if other_thread and other_thread != current_thread:
            pair = (current_thread, other_thread)
            if pair not in attached and attach_threads(*pair, True):
                attached.append(pair)
    try:
        user32.ShowWindow(hwnd, 9)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
        return bool(get_foreground() == hwnd)
    finally:
        for first, second in reversed(attached):
            attach_threads(first, second, False)


class Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def _main_window() -> tuple[int, Rect]:
    expected = os.path.normcase(
        r"D:\Tools\HelloStreamer-HelloStreamer\HelloStreamer.exe"
    )
    command = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.ExecutablePath -eq '{expected}' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    processes = subprocess.check_output(
        ["powershell", "-NoProfile", "-Command", command], text=True
    )
    user32 = ctypes.windll.user32
    for value in processes.split():
        hwnd = ctypes.c_void_p()
        enum_proc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
        )

        @enum_proc
        def callback(candidate: ctypes.c_void_p, _lparam: ctypes.c_void_p) -> bool:
            process_id = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(candidate, ctypes.byref(process_id))
            if process_id.value == int(value) and user32.IsWindowVisible(candidate):
                hwnd.value = int(candidate)
                return False
            return True

        user32.EnumWindows(callback, 0)
        if hwnd.value:
            rect = Rect()
            if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return hwnd.value, rect
    raise RuntimeError("Portable HelloStreamer main window not found")


def main() -> None:
    hwnd, rect = _main_window()
    user32 = ctypes.windll.user32
    moved = user32.SetWindowPos(
        hwnd, 0, 80, 80, 0, 0, 0x0001 | 0x0004 | 0x0010
    )
    # The app can launch a live-browser window during startup. Temporarily put
    # the main window above it so the screen capture is deterministic.
    user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    foreground = _activate_window(user32, hwnd)
    time.sleep(0.3)
    print(
        "window=",
        hwnd,
        "rect=",
        (rect.left, rect.top, rect.right, rect.bottom),
        "moved=",
        moved,
        "foreground=",
        foreground,
        "active=",
        user32.GetForegroundWindow(),
    )

    output_dir = Path(os.environ.get("TEMP", ".")) / "hello_streamer_scroll_verify_latest"
    output_dir.mkdir(parents=True, exist_ok=True)
    for old_file in output_dir.glob("frame_*.png"):
        old_file.unlink()

    x = (rect.left + rect.right) // 2
    y = rect.top + 330
    user32.SetCursorPos(x, y)
    # Return to the top before the burst so every run has the same starting
    # state. Positive wheel data scrolls upward on Windows.
    for _ in range(30):
        user32.mouse_event(0x0800, 0, 0, 120, 0)
    time.sleep(0.4)

    stop_capture = threading.Event()

    def send_burst() -> None:
        for _ in range(32):
            user32.mouse_event(0x0800, 0, 0, 0xFFFFFF88, 0)
            time.sleep(0.025)
        stop_capture.set()

    sender = threading.Thread(target=send_burst, daemon=True)
    sender.start()
    frame_index = 0
    while not stop_capture.is_set() or sender.is_alive():
        image = ImageGrab.grab(
            bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True
        )
        image.save(output_dir / f"frame_{frame_index:03d}.png")
        frame_index += 1
        time.sleep(0.025)
    sender.join()
    # Capture one settled frame as well; the last wheel event may have queued
    # a Canvas invalidation after the sender thread exits.
    time.sleep(0.8)
    image = ImageGrab.grab(
        bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True
    )
    image.save(output_dir / f"frame_{frame_index:03d}_settled.png")
    # Exercise the scrollbar thumb itself.  This is the interaction that
    # previously exposed the worst child-window overlap.
    for _ in range(30):
        user32.mouse_event(0x0800, 0, 0, 120, 0)
    time.sleep(0.3)
    drag_x = rect.right - 34
    drag_start = rect.top + 170
    drag_end = rect.top + 650
    user32.SetCursorPos(drag_x, drag_start)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    for step in range(31):
        drag_y = drag_start + int((drag_end - drag_start) * step / 30)
        user32.SetCursorPos(drag_x, drag_y)
        image = ImageGrab.grab(
            bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True
        )
        image.save(output_dir / f"frame_{frame_index:03d}_drag.png")
        frame_index += 1
        time.sleep(0.025)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.3)
    user32.SetWindowPos(hwnd, ctypes.c_void_p(-2), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
    print(output_dir)
    for image_path in sorted(output_dir.glob("frame_*.png")):
        print(image_path, image_path.stat().st_size)


if __name__ == "__main__":
    main()
