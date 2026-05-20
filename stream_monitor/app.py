"""CustomTkinter 主視窗 — 開播監聽器 GUI + 系統匣常駐 + 單一執行個體。"""

from __future__ import annotations

import logging
import logging.handlers
import platform
import queue
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import customtkinter as ctk

from stream_monitor import base_dir, config_manager
from stream_monitor.config_manager import DEFAULT_BROWSER_SETTINGS
from stream_monitor.db import SeenVideoDB
from stream_monitor.fetcher import get_fetcher
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.monitor import ChannelEntry, ChannelStatus, Monitor
from stream_monitor.notifier import (
    action_for_stream_status,
    detect_browser_family,
    execute_action,
    open_url,
)
from stream_monitor.single_instance import SingleInstance
from stream_monitor.startup import disable_startup, enable_startup, is_startup_enabled
from stream_monitor.tray import TrayIcon
from stream_monitor.url_parser import parse_url

logger = logging.getLogger(__name__)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------
_FONT_FAMILY = "Microsoft JhengHei UI"
if platform.system() != "Windows":
    _FONT_FAMILY = "sans-serif"


def _font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=_FONT_FAMILY, size=size, weight=weight)


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_minutes_delta(total_seconds: float) -> str:
    minutes = max(0, int(total_seconds // 60))
    days, rem = divmod(minutes, 24 * 60)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def _format_countdown(target: str) -> str:
    dt = _parse_iso_datetime(target)
    if dt is None:
        return ""
    return _format_minutes_delta((dt - datetime.now(timezone.utc)).total_seconds())


def _format_elapsed(started_at: str) -> str:
    dt = _parse_iso_datetime(started_at)
    if dt is None:
        return ""
    return _format_minutes_delta((datetime.now(timezone.utc) - dt).total_seconds())


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PLATFORM_OPTIONS = ["twitch", "youtube"]
ACTION_LABELS: dict[str, str] = {
    "open_and_stop": "開啟網頁並停止監聽",
    "open_and_keep": "開啟網頁並保持監聽",
    "notify_only": "僅跳出系統通知",
    "open_and_exit": "開啟網頁後關閉程式",
}
ACTION_DISPLAY = list(ACTION_LABELS.values())
ACTION_BY_DISPLAY = {label: key for key, label in ACTION_LABELS.items()}

_CLR_BG_DARK = "#1a1a2e"
_CLR_CARD = "#16213e"
_CLR_ACCENT = "#0f3460"
_CLR_LIVE = "#00e676"
_CLR_OFFLINE = "#666677"
_CLR_TWITCH = "#9146FF"
_CLR_YOUTUBE = "#FF0000"
_CLR_START = "#2e7d32"
_CLR_START_HOVER = "#1b5e20"
_CLR_STOP = "#c62828"
_CLR_STOP_HOVER = "#8e0000"
_CLR_ADD = "#0f3460"
_CLR_ADD_HOVER = "#1a4a7a"
_CLR_DELETE_HOVER = "#c62828"
_CLR_CARD_DISABLED = "#0e1528"
_CLR_TEXT_DISABLED = "#3a3a4a"
_CLR_LINK = "#2196F3"
_CLR_LINK_HOVER = "#1769aa"

_MIN_WINDOW_WIDTH = 860
_MIN_WINDOW_HEIGHT = 560
_DEFAULT_WINDOW_GEOMETRY = f"{_MIN_WINDOW_WIDTH}x580"


# ---------------------------------------------------------------------------
# Tooltip
# ---------------------------------------------------------------------------
class _Tooltip:
    """Lightweight hover tooltip for any tkinter/CTk widget."""

    _DELAY_MS = 400
    _BG = "#2a2a3e"
    _FG = "#e0e0ee"
    _BORDER = "#555566"

    def __init__(self, widget: Any, text: str) -> None:
        self._widget = widget
        self.text = text
        self._tip_window: Any | None = None
        self._after_id: str | None = None

        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._cancel, add="+")
        widget.bind("<ButtonPress>", self._cancel, add="+")

    def _schedule(self, _event: Any = None) -> None:
        self._cancel()
        self._after_id = self._widget.after(self._DELAY_MS, self._show)

    def _cancel(self, _event: Any = None) -> None:
        if self._after_id:
            self._widget.after_cancel(self._after_id)
            self._after_id = None
        self._hide()

    def _show(self) -> None:
        if self._tip_window or not self.text:
            return
        import tkinter as tk

        x = self._widget.winfo_rootx() + self._widget.winfo_width() // 2
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4

        tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        tw.wm_attributes("-topmost", True)

        label = tk.Label(
            tw,
            text=self.text,
            background=self._BG,
            foreground=self._FG,
            relief="solid",
            borderwidth=1,
            highlightbackground=self._BORDER,
            font=(_FONT_FAMILY, 10),
            padx=8,
            pady=4,
        )
        label.pack()

        tw.update_idletasks()
        tip_w = tw.winfo_width()
        screen_w = tw.winfo_screenwidth()
        if x + tip_w > screen_w - 8:
            x = screen_w - tip_w - 8
        if x < 8:
            x = 8
        tw.wm_geometry(f"+{x}+{y}")

        self._tip_window = tw

    def _hide(self) -> None:
        if self._tip_window:
            self._tip_window.destroy()
            self._tip_window = None


def _tooltip(widget: Any, text: str) -> _Tooltip:
    """Attach a hover tooltip to *widget* and return the ``_Tooltip`` handle."""
    return _Tooltip(widget, text)


def _clamped_window_geometry(saved_geometry: str | None) -> str:
    """Keep older saved window sizes from squeezing fixed-width controls."""
    if not saved_geometry:
        return _DEFAULT_WINDOW_GEOMETRY

    match = re.match(r"^(\d+)x(\d+)((?:[+-]\d+){2})?$", saved_geometry)
    if not match:
        return _DEFAULT_WINDOW_GEOMETRY

    width = max(int(match.group(1)), _MIN_WINDOW_WIDTH)
    height = max(int(match.group(2)), _MIN_WINDOW_HEIGHT)
    position = match.group(3) or ""
    return f"{width}x{height}{position}"


# ═══════════════════════════════════════════════════════════════════════════
# Add Channel Dialog
# ═══════════════════════════════════════════════════════════════════════════
class AddChannelDialog(ctk.CTkToplevel):
    """Modal dialog — supports both URL paste and manual input."""

    def __init__(self, parent: ctk.CTk) -> None:
        super().__init__(parent)
        self.title("新增頻道")
        self.geometry("680x430")
        self.resizable(False, False)
        self.transient(parent)
        self.configure(fg_color=_CLR_BG_DARK)

        if sys.platform != "win32":
            self.update()
        self.grab_set()

        self.result: dict[str, str] | None = None

        ctk.CTkLabel(
            self,
            text="貼上頻道連結（自動偵測平台）",
            font=_font(13, "bold"),
            anchor="w",
        ).pack(padx=24, pady=(20, 4), fill="x")

        url_frame = ctk.CTkFrame(self, fg_color="transparent")
        url_frame.pack(padx=24, fill="x")

        self.url_entry = ctk.CTkEntry(
            url_frame,
            placeholder_text="貼上頻道連結",
            font=_font(13),
            height=38,
        )
        self.url_entry.pack(fill="x")
        self.url_entry.bind("<KeyRelease>", self._on_url_change)

        ctk.CTkLabel(
            self,
            text="Twitch: twitch.tv/channel_name    YouTube: youtube.com/@channel_name",
            font=_font(12),
            text_color="#aaaabb",
            anchor="w",
            wraplength=620,
        ).pack(padx=24, pady=(6, 0), fill="x")

        ctk.CTkLabel(
            self,
            text="YouTube 連結只要包含 @handle 就能辨識；不含 @handle 的觀看頁、Shorts 或 /live 不支援。",
            font=_font(12, "bold"),
            text_color="#ffb74d",
            anchor="w",
            wraplength=620,
        ).pack(padx=24, pady=(4, 0), fill="x")

        self.message_label = ctk.CTkLabel(
            self, text="", font=_font(12), height=24, anchor="w", wraplength=620
        )
        self.message_label.pack(padx=24, pady=(4, 0), fill="x")

        sep = ctk.CTkFrame(self, height=1, fg_color="#333355")
        sep.pack(padx=24, pady=12, fill="x")

        ctk.CTkLabel(
            self,
            text="或手動輸入頻道名稱",
            font=_font(12),
            text_color="#888899",
            anchor="w",
        ).pack(padx=24, fill="x")

        manual_frame = ctk.CTkFrame(self, fg_color="transparent")
        manual_frame.pack(padx=24, pady=(6, 0), fill="x")
        manual_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(manual_frame, text="平台", font=_font(13), anchor="w").grid(
            row=0, column=0, padx=(0, 10), sticky="w"
        )
        self.platform_var = ctk.StringVar(value="twitch")
        self.platform_menu = ctk.CTkOptionMenu(
            manual_frame,
            variable=self.platform_var,
            values=PLATFORM_OPTIONS,
            font=_font(13),
            dropdown_font=_font(13),
            width=120,
            height=34,
        )
        self.platform_menu.grid(row=0, column=1, sticky="w")

        ctk.CTkLabel(manual_frame, text="頻道名稱", font=_font(13), anchor="w").grid(
            row=1, column=0, padx=(0, 10), pady=(8, 0), sticky="w"
        )
        self.name_entry = ctk.CTkEntry(
            manual_frame,
            placeholder_text="例如 kaicenat、channel_name 或 UC 開頭頻道 ID",
            font=_font(13),
            height=34,
        )
        self.name_entry.grid(row=1, column=1, pady=(8, 0), sticky="ew")

        ctk.CTkLabel(
            self,
            text="手動輸入 YouTube 時，請填 @handle 後面的名稱，或 UC 開頭的頻道 ID。",
            font=_font(12),
            text_color="#888899",
            anchor="w",
            wraplength=620,
        ).pack(padx=24, pady=(6, 0), fill="x")

        btn_frame = ctk.CTkFrame(self, fg_color="transparent", height=48)
        btn_frame.pack(padx=24, pady=(14, 22), fill="x")
        btn_frame.pack_propagate(False)

        ctk.CTkButton(
            btn_frame,
            text="取消",
            width=104,
            height=40,
            fg_color="transparent",
            border_width=1,
            border_color="#555566",
            hover_color="#333344",
            font=_font(13),
            command=self.destroy,
        ).pack(side="right", padx=(8, 0), pady=4)

        ctk.CTkButton(
            btn_frame,
            text="新增",
            width=104,
            height=40,
            fg_color=_CLR_ADD,
            hover_color=_CLR_ADD_HOVER,
            font=_font(13, "bold"),
            command=self._on_add,
        ).pack(side="right", pady=4)

        self.url_entry.bind("<Return>", lambda _: self._on_add())
        self.name_entry.bind("<Return>", lambda _: self._on_add())

    def _on_url_change(self, _event: Any = None) -> None:
        text = self.url_entry.get()
        parsed = parse_url(text)
        if parsed:
            self.message_label.configure(
                text=f"{parsed.platform.upper()} : {parsed.name}",
                text_color=_CLR_LIVE,
            )
            self.platform_var.set(parsed.platform)
            self.name_entry.delete(0, "end")
            self.name_entry.insert(0, parsed.name)
        else:
            if text.strip():
                self.message_label.configure(
                    text="無法辨識。YouTube 連結需包含 @handle，或手動輸入 @ 後面的名稱。",
                    text_color="#ffb74d",
                )
            else:
                self.message_label.configure(text="", text_color="gray")

    def _on_add(self) -> None:
        url_text = self.url_entry.get().strip()
        parsed = parse_url(url_text)

        if parsed:
            plat, name = parsed.platform, parsed.name
        elif url_text:
            self.message_label.configure(
                text="網址格式不支援。YouTube 請貼含 @handle 的連結，例如 https://www.youtube.com/@channel_name/live。",
                text_color="#ffb74d",
            )
            self.url_entry.focus_set()
            return
        else:
            name = self.name_entry.get().strip()
            plat = self.platform_var.get()
            if not name:
                self.message_label.configure(
                    text="請貼上頻道連結，或手動輸入頻道名稱。",
                    text_color="#ffb74d",
                )
                self.name_entry.focus_set()
                return

        self.message_label.configure(text="驗證中…", text_color="#64b5f6")
        self._set_inputs_enabled(False)
        self._pending_platform = plat
        self._pending_name = name

        threading.Thread(
            target=self._validate_channel, args=(plat, name), daemon=True
        ).start()

    def _set_inputs_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.url_entry.configure(state=state)
        self.name_entry.configure(state=state)
        self.platform_menu.configure(state=state)
        for w in self.winfo_children():
            if isinstance(w, ctk.CTkFrame):
                for child in w.winfo_children():
                    if isinstance(child, ctk.CTkButton):
                        child.configure(state=state)

    def _validate_channel(self, plat: str, name: str) -> None:
        try:
            fetcher = get_fetcher(plat)
            info = fetcher.get_stream_info(name)
        except Exception:
            info = None
        self.after(0, self._on_validate_done, info)

    def _on_validate_done(self, info: StreamInfo | None) -> None:
        plat = self._pending_platform
        name = self._pending_name

        if info is None:
            self.message_label.configure(
                text=f"找不到此帳號：{plat.upper()} / {name}。請確認名稱是否正確。",
                text_color="#ef5350",
            )
            self._set_inputs_enabled(True)
            return

        self.result = {"platform": plat, "name": name}
        if info.display_name:
            self.result["display_name"] = info.display_name
        self.destroy()


# ═══════════════════════════════════════════════════════════════════════════
# Browser Settings Dialog
# ═══════════════════════════════════════════════════════════════════════════
class BrowserSettingsDialog(ctk.CTkToplevel):
    """Modal dialog for configuring how stream pages are opened in the browser."""

    def __init__(self, parent: ctk.CTk, current: dict[str, Any]) -> None:
        super().__init__(parent)
        self.title("瀏覽器設定")
        self.geometry("580x720")
        self.resizable(False, False)
        self.transient(parent)
        self.configure(fg_color=_CLR_BG_DARK)

        if sys.platform != "win32":
            self.update()
        self.grab_set()

        self.result: dict[str, Any] | None = None

        settings = {**DEFAULT_BROWSER_SETTINGS, **(current or {})}

        ctk.CTkLabel(
            self,
            text="瀏覽器開啟方式",
            font=_font(13, "bold"),
            anchor="w",
        ).pack(padx=24, pady=(20, 4), fill="x")

        ctk.CTkLabel(
            self,
            text="關閉自訂模式時，將使用系統預設瀏覽器（無法控制座標／視窗）。",
            font=_font(12),
            text_color="#aaaabb",
            anchor="w",
            wraplength=512,
        ).pack(padx=24, pady=(0, 8), fill="x")

        self.enabled_var = ctk.BooleanVar(value=bool(settings.get("enabled", False)))
        self.enabled_switch = ctk.CTkSwitch(
            self,
            text="啟用自訂瀏覽器開啟（subprocess 模式）",
            variable=self.enabled_var,
            command=self._refresh_enabled_state,
            font=_font(12),
        )
        self.enabled_switch.pack(padx=24, anchor="w")

        # ── Browser path
        path_frame = ctk.CTkFrame(self, fg_color="transparent")
        path_frame.pack(padx=24, pady=(12, 0), fill="x")
        path_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            path_frame, text="瀏覽器指令", font=_font(13), anchor="w", width=104
        ).grid(row=0, column=0, sticky="w")

        self.path_entry = ctk.CTkEntry(
            path_frame,
            placeholder_text="chrome / msedge / 或瀏覽器執行檔絕對路徑",
            font=_font(13),
            height=34,
        )
        self.path_entry.insert(0, settings.get("browser_path", "chrome"))
        self.path_entry.grid(row=0, column=1, sticky="ew")
        self.path_entry.bind("<KeyRelease>", self._on_path_change)

        ctk.CTkLabel(
            self,
            text="提示：chrome、msedge 可直接輸入；若未在 PATH 內請填完整 .exe 路徑。",
            font=_font(11),
            text_color="#888899",
            anchor="w",
            wraplength=512,
        ).pack(padx=24, pady=(4, 0), fill="x")

        self.compat_label = ctk.CTkLabel(
            self,
            text="",
            font=_font(11, "bold"),
            text_color="#ffb74d",
            anchor="w",
            wraplength=512,
            height=20,
        )
        self.compat_label.pack(padx=24, pady=(2, 0), fill="x")

        # ── Window mode toggles
        toggle_frame = ctk.CTkFrame(self, fg_color="transparent")
        toggle_frame.pack(padx=24, pady=(10, 0), fill="x")

        self.new_window_var = ctk.BooleanVar(
            value=bool(settings.get("new_window", True))
        )
        self.new_window_cb = ctk.CTkCheckBox(
            toggle_frame,
            text="強制獨立新視窗 (--new-window)",
            variable=self.new_window_var,
            font=_font(12),
        )
        self.new_window_cb.pack(anchor="w", pady=(0, 4))

        self.app_mode_var = ctk.BooleanVar(value=bool(settings.get("app_mode", False)))
        self.app_mode_cb = ctk.CTkCheckBox(
            toggle_frame,
            text="App Mode：純淨播放器 (--app=URL；已隱含獨立視窗)",
            variable=self.app_mode_var,
            font=_font(12),
        )
        self.app_mode_cb.pack(anchor="w", pady=(0, 4))

        self.minimized_var = ctk.BooleanVar(
            value=bool(settings.get("minimized", False))
        )
        self.minimized_cb = ctk.CTkCheckBox(
            toggle_frame,
            text="最小化啟動（不搶焦點；由本程式於視窗出現後自動 Win32 縮小）",
            variable=self.minimized_var,
            font=_font(12),
        )
        self.minimized_cb.pack(anchor="w")

        # ── Isolated profile (forces a fresh Chrome master process so
        # --app= / --window-position actually take effect)
        profile_frame = ctk.CTkFrame(self, fg_color=_CLR_CARD, corner_radius=10)
        profile_frame.pack(padx=24, pady=(14, 0), fill="x")
        profile_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            profile_frame,
            text="獨立瀏覽器 Profile（建議啟用）",
            font=_font(12, "bold"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=3, padx=12, pady=(10, 2), sticky="w")

        ctk.CTkLabel(
            profile_frame,
            text="Chrome / Edge 已開啟時，--app= 與座標/大小會被忽略。設定獨立 Profile "
                 "可強迫瀏覽器以新 master process 開啟，所有設定才會生效。\n"
                 "代價：此 profile 沒有你主瀏覽器的書籤/登入/外掛（彈窗純粹播片用）。",
            font=_font(11),
            text_color="#9aa0b4",
            anchor="w",
            wraplength=500,
            justify="left",
        ).grid(row=1, column=0, columnspan=3, padx=12, pady=(0, 6), sticky="w")

        try:
            default_profile_dir = str(base_dir() / "browser_profile")
        except Exception:
            default_profile_dir = ""

        saved_profile_dir = (settings.get("user_data_dir") or "").strip()
        self.user_data_dir_enabled_var = ctk.BooleanVar(value=bool(saved_profile_dir))

        self.user_data_dir_cb = ctk.CTkCheckBox(
            profile_frame,
            text="啟用獨立 Profile",
            variable=self.user_data_dir_enabled_var,
            command=self._refresh_user_data_dir_state,
            font=_font(12),
        )
        self.user_data_dir_cb.grid(row=2, column=0, padx=12, pady=(0, 8), sticky="w")

        self.user_data_dir_entry = ctk.CTkEntry(
            profile_frame,
            placeholder_text=default_profile_dir or "C:\\Path\\To\\Profile\\Folder",
            font=_font(12),
            height=30,
        )
        self.user_data_dir_entry.insert(0, saved_profile_dir or default_profile_dir)
        self.user_data_dir_entry.grid(
            row=2, column=1, columnspan=2, padx=(0, 12), pady=(0, 8), sticky="ew"
        )

        # ── Position / size
        pos_frame = ctk.CTkFrame(self, fg_color=_CLR_CARD, corner_radius=10)
        pos_frame.pack(padx=24, pady=(14, 0), fill="x")
        pos_frame.grid_columnconfigure((1, 3), weight=1)

        ctk.CTkLabel(
            pos_frame, text="視窗位置 / 大小", font=_font(12, "bold"), anchor="w"
        ).grid(row=0, column=0, columnspan=4, padx=12, pady=(10, 6), sticky="w")

        def _make_int_entry(parent: ctk.CTkFrame, value: int) -> ctk.CTkEntry:
            entry = ctk.CTkEntry(parent, width=84, height=30, font=_font(13), justify="center")
            entry.insert(0, str(value))
            return entry

        ctk.CTkLabel(pos_frame, text="X", font=_font(12)).grid(
            row=1, column=0, padx=(14, 4), pady=4, sticky="e"
        )
        self.x_entry = _make_int_entry(pos_frame, int(settings.get("x", 0)))
        self.x_entry.grid(row=1, column=1, padx=(0, 14), pady=4, sticky="w")

        ctk.CTkLabel(pos_frame, text="Y", font=_font(12)).grid(
            row=1, column=2, padx=(14, 4), pady=4, sticky="e"
        )
        self.y_entry = _make_int_entry(pos_frame, int(settings.get("y", 0)))
        self.y_entry.grid(row=1, column=3, padx=(0, 14), pady=4, sticky="w")

        ctk.CTkLabel(pos_frame, text="寬度", font=_font(12)).grid(
            row=2, column=0, padx=(14, 4), pady=(4, 10), sticky="e"
        )
        self.w_entry = _make_int_entry(pos_frame, int(settings.get("width", 1280)))
        self.w_entry.grid(row=2, column=1, padx=(0, 14), pady=(4, 10), sticky="w")

        ctk.CTkLabel(pos_frame, text="高度", font=_font(12)).grid(
            row=2, column=2, padx=(14, 4), pady=(4, 10), sticky="e"
        )
        self.h_entry = _make_int_entry(pos_frame, int(settings.get("height", 720)))
        self.h_entry.grid(row=2, column=3, padx=(0, 14), pady=(4, 10), sticky="w")

        self.message_label = ctk.CTkLabel(
            self, text="", font=_font(12), height=22, anchor="w", wraplength=512
        )
        self.message_label.pack(padx=24, pady=(8, 0), fill="x")

        # ── Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent", height=52)
        btn_frame.pack(padx=24, pady=(8, 18), fill="x")
        btn_frame.pack_propagate(False)

        ctk.CTkButton(
            btn_frame,
            text="取消",
            width=104,
            height=40,
            fg_color="transparent",
            border_width=1,
            border_color="#555566",
            hover_color="#333344",
            font=_font(13),
            command=self.destroy,
        ).pack(side="right", padx=(8, 0), pady=4)

        ctk.CTkButton(
            btn_frame,
            text="測試開啟",
            width=104,
            height=40,
            fg_color="transparent",
            border_width=1,
            border_color=_CLR_LINK,
            hover_color=_CLR_LINK_HOVER,
            text_color=_CLR_LINK,
            font=_font(13),
            command=self._on_test,
        ).pack(side="right", padx=(8, 0), pady=4)

        ctk.CTkButton(
            btn_frame,
            text="儲存",
            width=104,
            height=40,
            fg_color=_CLR_ADD,
            hover_color=_CLR_ADD_HOVER,
            font=_font(13, "bold"),
            command=self._on_save,
        ).pack(side="right", pady=4)

        self._all_inputs: list[Any] = [
            self.path_entry,
            self.x_entry,
            self.y_entry,
            self.w_entry,
            self.h_entry,
        ]
        self._family_dependent: list[Any] = [
            self.x_entry,
            self.y_entry,
            self.w_entry,
            self.h_entry,
            self.app_mode_cb,
        ]
        self._refresh_enabled_state()
        self._refresh_user_data_dir_state()
        self._on_path_change()

    def _refresh_enabled_state(self) -> None:
        state = "normal" if self.enabled_var.get() else "disabled"
        for widget in self._all_inputs:
            widget.configure(state=state)
        for widget in (
            self.new_window_cb,
            self.app_mode_cb,
            self.minimized_cb,
            self.user_data_dir_cb,
        ):
            widget.configure(state=state)
        self._refresh_user_data_dir_state()
        self._on_path_change()

    def _refresh_user_data_dir_state(self) -> None:
        if not self.enabled_var.get():
            self.user_data_dir_entry.configure(state="disabled")
            return
        self.user_data_dir_entry.configure(
            state="normal" if self.user_data_dir_enabled_var.get() else "disabled"
        )

    def _on_path_change(self, _event: Any = None) -> None:
        if not self.enabled_var.get():
            self.compat_label.configure(text="（自訂模式已停用 — 將使用系統預設瀏覽器）")
            return

        executable = self.path_entry.get().strip() or "chrome"
        family = detect_browser_family(executable)

        if family == "firefox":
            self.compat_label.configure(
                text="⚠ 偵測到 Firefox：座標 / 大小 / App Mode 都無法控制（CLI 不支援），相關欄位已停用。",
                text_color="#ffb74d",
            )
            for widget in self._family_dependent:
                widget.configure(state="disabled")
        elif family == "chromium":
            self.compat_label.configure(
                text="✓ Chromium 系列瀏覽器：所有參數都可使用。",
                text_color="#81c784",
            )
            for widget in self._family_dependent:
                widget.configure(state="normal")
        else:
            self.compat_label.configure(
                text="ℹ 未知瀏覽器類型 — 仍會嘗試送出 Chromium 參數，若無效請改用 chrome / msedge。",
                text_color="#90caf9",
            )
            for widget in self._family_dependent:
                widget.configure(state="normal")

    def _collect(self) -> dict[str, Any] | None:
        try:
            x = int(self.x_entry.get())
            y = int(self.y_entry.get())
            width = int(self.w_entry.get())
            height = int(self.h_entry.get())
        except ValueError:
            self.message_label.configure(
                text="座標與大小必須為整數。", text_color="#ef5350"
            )
            return None

        if width < 100 or height < 100:
            self.message_label.configure(
                text="寬度與高度至少需 100 像素。", text_color="#ef5350"
            )
            return None

        browser_path = self.path_entry.get().strip() or "chrome"

        if self.user_data_dir_enabled_var.get():
            user_data_dir = self.user_data_dir_entry.get().strip()
            if not user_data_dir:
                self.message_label.configure(
                    text="獨立 Profile 已啟用但路徑為空，請填入資料夾路徑。",
                    text_color="#ef5350",
                )
                return None
        else:
            user_data_dir = ""

        return {
            "enabled": bool(self.enabled_var.get()),
            "browser_path": browser_path,
            "new_window": bool(self.new_window_var.get()),
            "app_mode": bool(self.app_mode_var.get()),
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "minimized": bool(self.minimized_var.get()),
            "user_data_dir": user_data_dir,
        }

    def _on_test(self) -> None:
        data = self._collect()
        if data is None:
            return
        self.message_label.configure(
            text="已嘗試開啟測試頁面（about:blank）。",
            text_color="#64b5f6",
        )
        open_url("about:blank", data if data["enabled"] else None)

    def _on_save(self) -> None:
        data = self._collect()
        if data is None:
            return
        self.result = data
        self.destroy()


# ═══════════════════════════════════════════════════════════════════════════
# Channel Row
# ═══════════════════════════════════════════════════════════════════════════
class ChannelRow(ctk.CTkFrame):
    """Single row in the channel list."""

    def __init__(
        self,
        parent: ctk.CTkFrame,
        channel: dict[str, str],
        on_delete: callable,
        on_move_up: callable,
        on_move_down: callable,
        on_toggle_enabled: callable,
        get_browser_settings: Callable[[], dict[str, Any] | None] | None = None,
    ) -> None:
        super().__init__(parent, corner_radius=10, fg_color=_CLR_CARD, height=58)
        self.channel = channel
        self._on_toggle_enabled = on_toggle_enabled
        self._get_browser_settings = get_browser_settings or (lambda: None)
        self._active_url = ""
        self._status_title = ""

        color = _CLR_TWITCH if channel["platform"] == "twitch" else _CLR_YOUTUBE
        self._platform_color = color

        move_frame = ctk.CTkFrame(self, fg_color="transparent", width=30, height=42)
        move_frame.pack(side="left", padx=(6, 0), pady=8)
        move_frame.pack_propagate(False)

        self.up_btn = ctk.CTkButton(
            move_frame,
            text="▲",
            width=30,
            height=20,
            corner_radius=4,
            fg_color="transparent",
            hover_color="#243052",
            font=_font(10),
            command=on_move_up,
        )
        self.up_btn.pack(anchor="n")

        self.down_btn = ctk.CTkButton(
            move_frame,
            text="▼",
            width=30,
            height=20,
            corner_radius=4,
            fg_color="transparent",
            hover_color="#243052",
            font=_font(10),
            command=on_move_down,
        )
        self.down_btn.pack(anchor="s", side="bottom")

        self.platform_label = ctk.CTkLabel(
            self,
            text=channel["platform"].upper(),
            width=78,
            fg_color=color,
            corner_radius=6,
            text_color="white",
            font=_font(11, "bold"),
            cursor="hand2",
        )
        self.platform_label.pack(side="left", padx=(4, 6), pady=8)
        self.platform_label.bind("<Button-1>", lambda _e: self._open_channel_page())

        name_frame = ctk.CTkFrame(self, fg_color="transparent")
        name_frame.pack(side="left", padx=6, pady=7, fill="x", expand=True)

        self.name_label = ctk.CTkLabel(
            name_frame,
            text="",
            anchor="w",
            font=_font(15, "bold"),
        )
        self.name_label.pack(anchor="w", fill="x")

        self.id_label = ctk.CTkLabel(
            name_frame,
            text="",
            anchor="w",
            font=_font(11),
            text_color="#9aa0b4",
        )
        self.id_label.pack(anchor="w", fill="x", pady=(1, 0))
        self._refresh_name_labels()

        self.time_label = ctk.CTkLabel(
            self,
            text="",
            width=72,
            anchor="e",
            font=_font(12, "bold"),
            text_color="#aab3d5",
        )
        self.time_label.pack(side="left", padx=(6, 0), pady=8)

        self.status_label = ctk.CTkLabel(
            self,
            text="  --  ",
            width=80,
            font=_font(12, "bold"),
            corner_radius=6,
        )
        self.status_label.pack(side="left", padx=6, pady=8)
        self.status_label.bind("<Button-1>", lambda _event: self._open_active_page())

        self.delete_btn = ctk.CTkButton(
            self,
            text="✕",
            width=32,
            height=32,
            corner_radius=6,
            fg_color="transparent",
            hover_color=_CLR_DELETE_HOVER,
            font=_font(14),
            command=on_delete,
        )
        self.delete_btn.pack(side="right", padx=(0, 10), pady=8)

        self.toggle_btn = ctk.CTkButton(
            self,
            text="⏸",
            width=30,
            height=30,
            corner_radius=6,
            fg_color="transparent",
            border_width=1,
            border_color="#3c4566",
            hover_color="#243052",
            font=_font(13, "bold"),
            command=self._on_toggle_click,
        )
        self.toggle_btn.pack(side="right", padx=(0, 4), pady=8)

        self.link_btn = ctk.CTkButton(
            self,
            text="🔗",
            width=30,
            height=30,
            corner_radius=6,
            fg_color="transparent",
            hover_color=_CLR_LINK_HOVER,
            font=_font(12),
            command=self._open_current_page,
        )
        self.link_btn.pack(side="right", padx=(0, 4), pady=8)

        self._link_tip = _tooltip(self.link_btn, "開啟頻道首頁")
        self._toggle_tip = _tooltip(self.toggle_btn, "")
        _tooltip(self.up_btn, "上移")
        _tooltip(self.down_btn, "下移")
        _tooltip(self.delete_btn, "刪除頻道")
        self._platform_tip = _tooltip(self.platform_label, "開啟頻道首頁")
        self._status_tip = _tooltip(self.status_label, "")

        self._apply_enabled_visual()

    def _channel_url(self) -> str:
        plat = self.channel["platform"]
        name = self.channel["name"]
        if plat == "twitch":
            return f"https://www.twitch.tv/{name}"
        if name.startswith("UC"):
            return f"https://www.youtube.com/channel/{name}"
        return f"https://www.youtube.com/@{name}"

    def _open_channel_page(self) -> None:
        open_url(self._channel_url(), self._get_browser_settings())

    def _open_current_page(self) -> None:
        open_url(self._active_url or self._channel_url(), self._get_browser_settings())

    def _open_active_page(self) -> None:
        if self._active_url:
            open_url(self._active_url, self._get_browser_settings())

    def _set_link_tip(self, text: str) -> None:
        if hasattr(self, "_link_tip"):
            self._link_tip.text = text

    def _on_toggle_click(self) -> None:
        enabled = not self.channel.get("enabled", True)
        self.channel["enabled"] = enabled
        self._apply_enabled_visual()
        self._on_toggle_enabled()

    def _apply_enabled_visual(self) -> None:
        enabled = self.channel.get("enabled", True)
        self._active_url = ""
        self._status_title = ""
        self.time_label.configure(text="")
        self._set_link_tip("開啟頻道首頁")
        if enabled:
            self.configure(fg_color=_CLR_CARD)
            self.platform_label.configure(
                fg_color=self._platform_color, text_color="white"
            )
            self.name_label.configure(text_color=("gray10", "gray90"))
            self.id_label.configure(text_color="#9aa0b4")
            self.status_label.configure(
                text="  --  ", text_color="#666677", fg_color="transparent"
            )
            self.toggle_btn.configure(text="⏸")
            if hasattr(self, "_toggle_tip"):
                self._toggle_tip.text = "暫停監聽此頻道"
        else:
            self.configure(fg_color=_CLR_CARD_DISABLED)
            self.platform_label.configure(
                fg_color="#2a2a3a", text_color=_CLR_TEXT_DISABLED
            )
            self.name_label.configure(text_color=_CLR_TEXT_DISABLED)
            self.id_label.configure(text_color=_CLR_TEXT_DISABLED)
            self.status_label.configure(
                text=" 已暫停 ", text_color=_CLR_TEXT_DISABLED, fg_color="transparent"
            )
            self.toggle_btn.configure(text="▶")
            self._set_link_tip("頻道已暫停；開啟頻道首頁")
            if hasattr(self, "_toggle_tip"):
                self._toggle_tip.text = "恢復監聽此頻道"

    def set_status(self, status: bool | str | ChannelStatus | None) -> None:
        if not self.channel.get("enabled", True):
            return

        detail = status if isinstance(status, ChannelStatus) else None
        state = detail.status if detail else status
        self._active_url = detail.url if detail else ""
        self._status_title = detail.title if detail else ""

        if state is None:
            self.time_label.configure(text="")
            self.status_label.configure(
                text="  --  ", text_color="#666677", fg_color="transparent"
            )
            self.status_label.configure(cursor="")
            self._status_tip.text = ""
            self._set_link_tip("尚未更新；開啟頻道首頁")
        elif state == "upcoming":
            countdown = _format_countdown(detail.scheduled_start if detail else "")
            self.time_label.configure(text=countdown)
            self.status_label.configure(
                text=" UPCOMING ", text_color="white", fg_color="#e65100"
            )
            self.status_label.configure(cursor="hand2")
            tip = f"📺 {detail.title}" if detail and detail.title else ""
            if countdown:
                tip += f"\n⏱ {countdown} 後開始" if tip else f"⏱ {countdown} 後開始"
            self._status_tip.text = tip or "待機中"
            link_tip = "開啟待機間"
            if detail and detail.title:
                link_tip += f"：{detail.title}"
            self._set_link_tip(link_tip)
        elif state is True or state == "live":
            elapsed = _format_elapsed(detail.started_at if detail else "")
            self.time_label.configure(text=elapsed)
            self.status_label.configure(
                text=" ● LIVE ", text_color="white", fg_color="#1b5e20"
            )
            self.status_label.configure(cursor="hand2")
            tip = f"📺 {detail.title}" if detail and detail.title else ""
            if elapsed:
                tip += f"\n⏱ 已開播 {elapsed}" if tip else f"⏱ 已開播 {elapsed}"
            self._status_tip.text = tip or "直播中"
            link_tip = "開啟直播間"
            if detail and detail.title:
                link_tip += f"：{detail.title}"
            self._set_link_tip(link_tip)
        else:
            self._active_url = ""
            self._status_title = ""
            self.time_label.configure(text="")
            self.status_label.configure(
                text=" OFFLINE ", text_color="#999999", fg_color="transparent"
            )
            self.status_label.configure(cursor="")
            self._status_tip.text = ""
            self._set_link_tip("目前離線；開啟頻道首頁")

    @property
    def key(self) -> str:
        return f"{self.channel['platform']}:{self.channel['name']}"

    def set_display_name(self, display_name: str | None) -> bool:
        display_name = (display_name or "").strip()
        if not display_name or display_name == self.channel.get("display_name"):
            return False
        self.channel["display_name"] = display_name
        self._refresh_name_labels()
        return True

    def _refresh_name_labels(self) -> None:
        channel_id = self.channel["name"]
        display_name = self.channel.get("display_name", "").strip()
        if display_name and display_name != channel_id:
            self.name_label.configure(text=display_name)
            self.id_label.configure(text=f"ID: {channel_id}")
        else:
            self.name_label.configure(text=channel_id)
            self.id_label.configure(text="")

    def set_move_state(self, can_move_up: bool, can_move_down: bool) -> None:
        self.up_btn.configure(state="normal" if can_move_up else "disabled")
        self.down_btn.configure(state="normal" if can_move_down else "disabled")


# ═══════════════════════════════════════════════════════════════════════════
# Main App Window
# ═══════════════════════════════════════════════════════════════════════════
class App(ctk.CTk):
    """Main application window with system tray integration."""

    def __init__(self, silent: bool = False) -> None:
        super().__init__()

        self.config = config_manager.load()
        self._event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._db = SeenVideoDB()
        self._monitor: Monitor | None = None
        self._channel_rows: list[ChannelRow] = []
        self._silent = silent
        self._truly_quitting = False
        # monitor mode: "idle" | "trigger" | "watch"
        self._monitor_mode: str = "idle"

        self.title("哈嘍主播  Hello Streamer")
        self.minsize(_MIN_WINDOW_WIDTH, _MIN_WINDOW_HEIGHT)
        self.geometry(_clamped_window_geometry(self.config.get("window_geometry")))
        self.configure(fg_color=_CLR_BG_DARK)
        self.protocol("WM_DELETE_WINDOW", self._on_close_button)

        self._build_ui()
        self._populate_channels()
        self._poll_events()

        self._tray = TrayIcon(
            on_show=self._show_window,
            on_toggle_monitor=self._tray_toggle_monitor,
            on_watch_only=lambda: self.after(0, self._on_watch),
            on_stop=lambda: self.after(0, self._on_stop),
            on_quit=self._quit_app,
            get_mode=lambda: self._monitor_mode,
        )
        self._tray.start()

        if silent:
            self.withdraw()
            channels = self.config.get("channels", [])
            if channels:
                saved_mode = self.config.get("monitor_mode", "trigger")
                starter = self._on_watch if saved_mode == "watch" else self._on_start
                self.after(500, starter)

    # ------------------------------------------------------------------
    # Window visibility
    # ------------------------------------------------------------------
    def _show_window(self) -> None:
        self.after(0, self._do_show)

    def _do_show(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def _hide_window(self) -> None:
        self._save_config()
        self.withdraw()

    def _on_close_button(self) -> None:
        """X button: hide to tray or quit based on user preference."""
        if self.minimize_to_tray_var.get():
            self._hide_window()
        else:
            self._quit_app()

    def _quit_app(self) -> None:
        """Full exit — called from tray menu or explicit quit."""
        self._truly_quitting = True
        if self._monitor:
            self._monitor.stop()
        self._tray.stop()
        self._save_config()
        self._db.close()
        self.after(0, self.destroy)

    # ------------------------------------------------------------------
    # Tray callbacks
    # ------------------------------------------------------------------
    def _tray_toggle_monitor(self) -> None:
        if self._monitor and self._monitor.is_running:
            self.after(0, self._on_stop)
        else:
            self.after(0, self._on_start)

    def _current_browser_settings(self) -> dict[str, Any] | None:
        settings = self.config.get("browser_settings")
        if isinstance(settings, dict) and settings.get("enabled"):
            return settings
        return None

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.grid(row=0, column=0, sticky="nsew", padx=16, pady=16)
        outer.grid_rowconfigure(1, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        # ── Title bar ──
        title_bar = ctk.CTkFrame(outer, fg_color="transparent")
        title_bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        ctk.CTkLabel(
            title_bar,
            text="哈嘍主播",
            font=_font(22, "bold"),
            anchor="w",
        ).pack(side="left")

        ctk.CTkLabel(
            title_bar,
            text="Hello Streamer",
            font=_font(13),
            text_color="#777788",
            anchor="w",
        ).pack(side="left", padx=(10, 0), pady=(6, 0))

        self.add_btn = ctk.CTkButton(
            title_bar,
            text="＋  新增頻道",
            width=130,
            height=36,
            corner_radius=8,
            fg_color=_CLR_ADD,
            hover_color=_CLR_ADD_HOVER,
            font=_font(14, "bold"),
            command=self._on_add_channel,
        )
        self.add_btn.pack(side="right")
        _tooltip(self.add_btn, "新增 Twitch 或 YouTube 頻道")

        self.browser_settings_btn = ctk.CTkButton(
            title_bar,
            text="⚙  瀏覽器設定",
            width=130,
            height=36,
            corner_radius=8,
            fg_color="transparent",
            border_width=1,
            border_color=_CLR_LINK,
            hover_color=_CLR_LINK_HOVER,
            text_color=_CLR_LINK,
            font=_font(13, "bold"),
            command=self._on_browser_settings,
        )
        self.browser_settings_btn.pack(side="right", padx=(0, 8))
        _tooltip(
            self.browser_settings_btn,
            "設定觸發時的瀏覽器：獨立視窗 / 座標 / 大小 / 最小化 / App Mode",
        )

        self.startup_var = ctk.BooleanVar(value=is_startup_enabled())
        self.startup_switch = ctk.CTkSwitch(
            title_bar,
            text="開機啟動",
            variable=self.startup_var,
            command=self._on_startup_toggle,
            font=_font(12),
        )
        self.startup_switch.pack(side="right", padx=(0, 14))
        _tooltip(self.startup_switch, "系統開機時自動啟動程式")

        self.minimize_to_tray_var = ctk.BooleanVar(
            value=self.config.get("minimize_to_tray", True)
        )
        self.tray_switch = ctk.CTkSwitch(
            title_bar,
            text="縮小至系統匣",
            variable=self.minimize_to_tray_var,
            command=self._on_tray_switch_toggle,
            font=_font(12),
        )
        self.tray_switch.pack(side="right", padx=(0, 14))
        _tooltip(self.tray_switch, "開啟：關閉時縮小到系統匣\n關閉：關閉時直接結束程式")

        # ── Channel list ──
        list_container = ctk.CTkFrame(outer, corner_radius=12, fg_color=_CLR_ACCENT)
        list_container.grid(row=1, column=0, sticky="nsew")
        list_container.grid_rowconfigure(0, weight=1)
        list_container.grid_columnconfigure(0, weight=1)

        self.scroll_frame = ctk.CTkScrollableFrame(
            list_container,
            corner_radius=0,
            fg_color="transparent",
            scrollbar_button_color="#333355",
            scrollbar_button_hover_color="#444466",
        )
        self.scroll_frame.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.scroll_frame.grid_columnconfigure(0, weight=1)

        self.empty_label = ctk.CTkLabel(
            self.scroll_frame,
            text="尚無頻道，請點擊「＋ 新增頻道」開始",
            font=_font(14),
            text_color="#555566",
        )

        # ── Bottom control bar ──
        ctrl = ctk.CTkFrame(outer, corner_radius=12, fg_color=_CLR_CARD)
        ctrl.grid(row=2, column=0, sticky="ew", pady=(10, 0))

        toolbar = ctk.CTkFrame(ctrl, fg_color="transparent")
        toolbar.pack(fill="x", padx=14, pady=10)
        toolbar.grid_columnconfigure(2, weight=1)

        left = ctk.CTkFrame(toolbar, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")

        self.start_btn = ctk.CTkButton(
            left,
            text="▶  監聽+觸發",
            width=132,
            height=38,
            corner_radius=8,
            fg_color=_CLR_START,
            hover_color=_CLR_START_HOVER,
            font=_font(14, "bold"),
            command=self._on_start,
        )
        self.start_btn.pack(side="left", padx=(0, 6))
        _tooltip(self.start_btn, "開始監聽並在偵測到開播時執行觸發行為")

        self.watch_btn = ctk.CTkButton(
            left,
            text="👁  只監測",
            width=108,
            height=38,
            corner_radius=8,
            fg_color="#1565c0",
            hover_color="#0d47a1",
            font=_font(14, "bold"),
            command=self._on_watch,
        )
        self.watch_btn.pack(side="left", padx=(0, 6))
        _tooltip(self.watch_btn, "只更新狀態與顯示，不執行觸發行為（不自動開網頁/不通知）")

        self.stop_btn = ctk.CTkButton(
            left,
            text="■  停止",
            width=80,
            height=38,
            corner_radius=8,
            fg_color=_CLR_STOP,
            hover_color=_CLR_STOP_HOVER,
            state="disabled",
            font=_font(14, "bold"),
            command=self._on_stop,
        )
        self.stop_btn.pack(side="left")
        _tooltip(self.stop_btn, "停止所有監聽")

        self.status_text = ctk.CTkLabel(
            toolbar,
            text="尚未啟動",
            font=_font(13),
            text_color=_CLR_OFFLINE,
            width=86,
            anchor="w",
        )
        self.status_text.grid(row=0, column=1, sticky="w", padx=(14, 8))

        interval_group = ctk.CTkFrame(toolbar, fg_color="transparent")
        interval_group.grid(row=0, column=3, sticky="w", padx=(12, 0))
        ctk.CTkLabel(
            interval_group,
            text="檢查間隔",
            font=_font(11),
            text_color="#9aa0b4",
            anchor="w",
        ).pack(anchor="w")

        self.interval_var = ctk.StringVar(
            value=str(self.config.get("check_interval", 60))
        )
        interval_line = ctk.CTkFrame(interval_group, fg_color="transparent")
        interval_line.pack(anchor="w", pady=(2, 0))
        self.interval_entry = ctk.CTkEntry(
            interval_line,
            width=78,
            height=32,
            textvariable=self.interval_var,
            font=_font(14, "bold"),
            justify="center",
        )
        self.interval_entry.pack(side="left")
        _tooltip(self.interval_entry, "每次檢查的間隔秒數（最低 10 秒）")

        ctk.CTkLabel(
            interval_line, text="秒", font=_font(12), text_color="#d8d8e5"
        ).pack(side="left", padx=(6, 0))

        action_group = ctk.CTkFrame(toolbar, fg_color="transparent")
        action_group.grid(row=0, column=4, sticky="w", padx=(18, 0))
        ctk.CTkLabel(
            action_group,
            text="觸發行為",
            font=_font(11),
            text_color="#9aa0b4",
            anchor="w",
        ).pack(anchor="w")

        current_action = self.config.get("action", "open_and_stop")
        display = ACTION_LABELS.get(current_action, ACTION_DISPLAY[0])
        self.action_var = ctk.StringVar(value=display)
        self.action_menu = ctk.CTkOptionMenu(
            action_group,
            variable=self.action_var,
            values=ACTION_DISPLAY,
            width=218,
            height=32,
            font=_font(12),
            dropdown_font=_font(12),
        )
        self.action_menu.pack(anchor="w", pady=(2, 0))
        _tooltip(self.action_menu, "偵測到開播時要執行的動作")


    # ------------------------------------------------------------------
    # Channel list operations
    # ------------------------------------------------------------------
    def _populate_channels(self) -> None:
        channels = self.config.get("channels", [])
        if not channels:
            self.empty_label.pack(pady=40)
        for ch in channels:
            self._add_channel_row(ch)
        self._refresh_move_buttons()

    def _refresh_empty_hint(self) -> None:
        if self._channel_rows:
            self.empty_label.pack_forget()
        else:
            self.empty_label.pack(pady=40)

    def _add_channel_row(self, channel: dict[str, str]) -> None:
        self.empty_label.pack_forget()

        def on_delete(ch=channel):
            self._remove_channel(ch)

        def on_move_up(ch=channel):
            self._move_channel(ch, -1)

        def on_move_down(ch=channel):
            self._move_channel(ch, 1)

        def on_toggle_enabled(ch=channel):
            self._on_channel_toggle_enabled(ch)

        row = ChannelRow(
            self.scroll_frame,
            channel,
            on_delete=on_delete,
            on_move_up=on_move_up,
            on_move_down=on_move_down,
            on_toggle_enabled=on_toggle_enabled,
            get_browser_settings=self._current_browser_settings,
        )
        row.pack(fill="x", pady=3)
        self._channel_rows.append(row)
        self._refresh_move_buttons()

    def _remove_channel(self, channel: dict[str, str]) -> None:
        for row in self._channel_rows:
            if row.channel == channel:
                row.destroy()
                self._channel_rows.remove(row)
                break
        channels = self.config.get("channels", [])
        if channel in channels:
            channels.remove(channel)
        self._save_config()
        self._refresh_empty_hint()
        self._refresh_move_buttons()
        if self._monitor and self._monitor.is_running:
            self._monitor.update_channels(channels)

    def _move_channel(self, channel: dict[str, str], offset: int) -> None:
        channels = self.config.get("channels", [])
        try:
            index = channels.index(channel)
        except ValueError:
            return

        new_index = index + offset
        if new_index < 0 or new_index >= len(channels):
            return

        channels[index], channels[new_index] = channels[new_index], channels[index]
        self._channel_rows[index], self._channel_rows[new_index] = (
            self._channel_rows[new_index],
            self._channel_rows[index],
        )

        for row in self._channel_rows:
            row.pack_forget()
        for row in self._channel_rows:
            row.pack(fill="x", pady=3)

        self._save_config()
        self._refresh_move_buttons()
        if self._monitor and self._monitor.is_running:
            self._monitor.update_channels(channels)

    def _refresh_move_buttons(self) -> None:
        last_index = len(self._channel_rows) - 1
        for index, row in enumerate(self._channel_rows):
            row.set_move_state(can_move_up=index > 0, can_move_down=index < last_index)

    def _on_channel_toggle_enabled(self, channel: dict[str, str]) -> None:
        self._save_config()
        channels = self.config.get("channels", [])
        if self._monitor and self._monitor.is_running:
            self._monitor.update_channels(channels)

    def _apply_display_names(self, display_names: dict[str, str]) -> None:
        changed = False
        for row in self._channel_rows:
            changed = row.set_display_name(display_names.get(row.key)) or changed
        if changed:
            self._save_config()

    def _on_add_channel(self) -> None:
        dialog = AddChannelDialog(self)
        self.wait_window(dialog)
        if dialog.result:
            ch = dialog.result
            channels = self.config.setdefault("channels", [])
            if ch not in channels:
                channels.append(ch)
                self._add_channel_row(ch)
                self._save_config()
                if self._monitor and self._monitor.is_running:
                    self._monitor.update_channels(channels)

    # ------------------------------------------------------------------
    # Monitor control
    # ------------------------------------------------------------------
    def _ensure_monitor_running(self) -> bool:
        """Start (or keep) the background monitor. Returns False if no channels."""
        channels = self.config.get("channels", [])
        if not channels:
            return False

        try:
            interval = int(self.interval_var.get())
        except (TypeError, ValueError):
            interval = 60
        interval = max(10, interval)
        self.interval_var.set(str(interval))
        self.config["check_interval"] = interval

        action_display = self.action_var.get()
        action_key = ACTION_BY_DISPLAY.get(action_display, "open_and_stop")
        self.config["action"] = action_key

        if self._monitor and self._monitor.is_running:
            self._monitor.update_interval(interval)
            self._monitor.update_channels(channels)
        else:
            self._monitor = Monitor(
                channels=channels,
                interval=interval,
                on_status_change=self._on_channel_live,
                on_poll_complete=self._on_poll_done,
                db=self._db,
            )
            self._monitor.start()
        return True

    def _on_start(self) -> None:
        if not self._ensure_monitor_running():
            return
        self._monitor_mode = "trigger"
        self.config["monitor_mode"] = "trigger"
        self._save_config()
        self._apply_monitor_mode_buttons()
        self.status_text.configure(text="監聽+觸發中…", text_color=_CLR_LIVE)
        self._tray.update_tooltip("哈嘍主播 — 監聽+觸發中")

    def _on_watch(self) -> None:
        if not self._ensure_monitor_running():
            return
        self._monitor_mode = "watch"
        self.config["monitor_mode"] = "watch"
        self._save_config()
        self._apply_monitor_mode_buttons()
        self.status_text.configure(text="只監測中…", text_color="#64b5f6")
        self._tray.update_tooltip("哈嘍主播 — 只監測中")

    def _on_stop(self) -> None:
        if self._monitor:
            self._monitor.stop()
            if not self._monitor.is_running:
                self._monitor = None
        try:
            while True:
                self._event_queue.get_nowait()
        except queue.Empty:
            pass
        self._monitor_mode = "idle"
        self._apply_monitor_mode_buttons()
        self.status_text.configure(text="已停止", text_color=_CLR_OFFLINE)
        self._tray.update_tooltip("哈嘍主播 — 已停止")

    def _apply_monitor_mode_buttons(self) -> None:
        mode = self._monitor_mode
        if mode == "trigger":
            self.start_btn.configure(state="disabled")
            self.watch_btn.configure(state="normal")
            self.stop_btn.configure(state="normal")
        elif mode == "watch":
            self.start_btn.configure(state="normal")
            self.watch_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
        else:
            self.start_btn.configure(state="normal")
            self.watch_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")

    def _on_browser_settings(self) -> None:
        dialog = BrowserSettingsDialog(
            self, self.config.get("browser_settings", {}) or {}
        )
        self.wait_window(dialog)
        if dialog.result is not None:
            self.config["browser_settings"] = dialog.result
            self._save_config()

    # ------------------------------------------------------------------
    # Event bridge (monitor thread -> UI thread)
    # ------------------------------------------------------------------
    def _on_channel_live(self, entry: ChannelEntry, info: StreamInfo) -> None:
        self._event_queue.put(("live", (entry, info)))

    def _on_poll_done(self) -> None:
        if self._monitor:
            statuses = self._monitor.snapshot_statuses()
            display_names = self._monitor.snapshot_display_names()
            self._event_queue.put(("status_update", (statuses, display_names)))

    def _poll_events(self) -> None:
        live_events: list[tuple[ChannelEntry, StreamInfo]] = []
        latest_status_update: tuple[dict, dict] | None = None

        try:
            while True:
                kind, data = self._event_queue.get_nowait()
                if kind == "live":
                    live_events.append(data)
                elif kind == "status_update":
                    latest_status_update = data
        except queue.Empty:
            pass

        if latest_status_update is not None:
            statuses, display_names = latest_status_update
            self._apply_display_names(display_names)
            for row in self._channel_rows:
                row.set_status(statuses.get(row.key))

        configured_action = self.config.get("action", "open_and_stop")
        browser_settings = self._current_browser_settings()
        should_stop = False
        should_exit = False
        trigger_enabled = self._monitor_mode == "trigger"

        for entry, info in live_events:
            if info.display_name:
                self._apply_display_names({entry.key: info.display_name})

            if not trigger_enabled:
                continue

            action = action_for_stream_status(configured_action, info)
            if action is None:
                continue

            noop = lambda: None  # noqa: E731
            execute_action(
                action,
                info,
                stop_fn=noop,
                exit_fn=noop,
                browser_settings=browser_settings,
            )

            if action == "open_and_stop":
                should_stop = True
            elif action == "open_and_exit":
                should_exit = True

        if should_stop:
            self._on_stop()
        elif should_exit:
            self._quit_app()

        self.after(500, self._poll_events)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def _on_tray_switch_toggle(self) -> None:
        self.config["minimize_to_tray"] = self.minimize_to_tray_var.get()
        self._save_config()

    def _on_startup_toggle(self) -> None:
        requested = self.startup_var.get()
        success = enable_startup() if requested else disable_startup()
        if not success:
            self.startup_var.set(not requested)
            logger.warning("Failed to update startup setting")
        self.config["run_on_startup"] = self.startup_var.get()
        self._save_config()

    def _save_config(self) -> None:
        try:
            self.config["window_geometry"] = self.geometry()
        except Exception:
            pass
        config_manager.save(self.config)

    def _on_close(self) -> None:
        if self._monitor:
            self._monitor.stop()
        self._tray.stop()
        self._save_config()
        self._db.close()
        self.destroy()


def _fix_linux_frozen_env() -> None:
    """Restore LD_LIBRARY_PATH for PyInstaller --onefile on Linux.

    PyInstaller overrides LD_LIBRARY_PATH to its temp extraction dir, which
    breaks DNS resolution (glibc NSS dlopen) and subprocess calls (browser,
    xdg-open).  Restoring the original value after Python is fully loaded is
    safe because all bundled .so files are already mapped into memory.
    """
    import os

    lp_key = "LD_LIBRARY_PATH"
    lp_orig = os.environ.get(lp_key + "_ORIG")
    if lp_orig is not None:
        os.environ[lp_key] = lp_orig
    elif lp_key in os.environ:
        del os.environ[lp_key]


def _check_writable(directory: Path) -> None:
    """Abort early with a user-friendly dialog if *directory* is not writable."""
    probe = directory / ".write_test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "啟動失敗",
            f"程式所在目錄無法寫入：\n{directory}\n\n"
            "請將程式移至有寫入權限的資料夾後再試。",
        )
        root.destroy()
        sys.exit(1)


def main() -> None:
    if getattr(sys, "frozen", False) and sys.platform != "win32":
        _fix_linux_frozen_env()

    log_fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    data_dir = base_dir()
    _check_writable(data_dir)

    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "stream_monitor.log"

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(log_fmt))

    logging.basicConfig(
        level=logging.INFO,
        format=log_fmt,
        handlers=[logging.StreamHandler(), file_handler],
    )

    silent = "--silent" in sys.argv

    app: App | None = None

    lock = SingleInstance()

    def on_show_request() -> None:
        if app is not None:
            app._show_window()

    lock._on_show = on_show_request

    if not lock.try_lock():
        logger.info("Another instance is already running — activating it")
        sys.exit(0)

    app = App(silent=silent)

    try:
        app.mainloop()
    finally:
        lock.release()


if __name__ == "__main__":
    main()
