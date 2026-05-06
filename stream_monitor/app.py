"""CustomTkinter 主視窗 — 開播監聽器 GUI + 系統匣常駐 + 單一執行個體。"""

from __future__ import annotations

import logging
import platform
import queue
import re
import sys
import threading
from typing import Any

import customtkinter as ctk

from stream_monitor import config_manager
from stream_monitor.db import SeenVideoDB
from stream_monitor.fetcher import get_fetcher
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.monitor import ChannelEntry, Monitor
from stream_monitor.notifier import execute_action, open_url
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
    _FONT_FAMILY = "Noto Sans TC"


def _font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=_FONT_FAMILY, size=size, weight=weight)


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
        self.grab_set()
        self.configure(fg_color=_CLR_BG_DARK)

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
    ) -> None:
        super().__init__(parent, corner_radius=10, fg_color=_CLR_CARD, height=58)
        self.channel = channel
        self._on_toggle_enabled = on_toggle_enabled

        color = _CLR_TWITCH if channel["platform"] == "twitch" else _CLR_YOUTUBE
        self._platform_color = color

        self.platform_label = ctk.CTkLabel(
            self,
            text=channel["platform"].upper(),
            width=78,
            fg_color=color,
            corner_radius=6,
            text_color="white",
            font=_font(11, "bold"),
        )
        self.platform_label.pack(side="left", padx=(10, 6), pady=8)

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

        self.status_label = ctk.CTkLabel(
            self,
            text="  --  ",
            width=80,
            font=_font(12, "bold"),
            corner_radius=6,
        )
        self.status_label.pack(side="left", padx=6, pady=8)

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

        self.down_btn = ctk.CTkButton(
            self,
            text="↓",
            width=30,
            height=30,
            corner_radius=6,
            fg_color="transparent",
            border_width=1,
            border_color="#3c4566",
            hover_color="#243052",
            font=_font(13, "bold"),
            command=on_move_down,
        )
        self.down_btn.pack(side="right", padx=(0, 4), pady=8)

        self.up_btn = ctk.CTkButton(
            self,
            text="↑",
            width=30,
            height=30,
            corner_radius=6,
            fg_color="transparent",
            border_width=1,
            border_color="#3c4566",
            hover_color="#243052",
            font=_font(13, "bold"),
            command=on_move_up,
        )
        self.up_btn.pack(side="right", padx=(0, 4), pady=8)

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
            command=self._open_channel_page,
        )
        self.link_btn.pack(side="right", padx=(0, 4), pady=8)

        _tooltip(self.link_btn, "開啟頻道首頁")
        self._toggle_tip = _tooltip(self.toggle_btn, "")
        _tooltip(self.up_btn, "上移")
        _tooltip(self.down_btn, "下移")
        _tooltip(self.delete_btn, "刪除頻道")
        self._platform_tip = _tooltip(self.platform_label, channel["platform"].upper())

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
        open_url(self._channel_url())

    def _on_toggle_click(self) -> None:
        enabled = not self.channel.get("enabled", True)
        self.channel["enabled"] = enabled
        self._apply_enabled_visual()
        self._on_toggle_enabled()

    def _apply_enabled_visual(self) -> None:
        enabled = self.channel.get("enabled", True)
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
            if hasattr(self, "_toggle_tip"):
                self._toggle_tip.text = "恢復監聽此頻道"

    def set_status(self, status: bool | str | None) -> None:
        if not self.channel.get("enabled", True):
            return
        if status is None:
            self.status_label.configure(
                text="  --  ", text_color="#666677", fg_color="transparent"
            )
        elif status == "upcoming":
            self.status_label.configure(
                text=" UPCOMING ", text_color="white", fg_color="#e65100"
            )
        elif status is True or status == "live":
            self.status_label.configure(
                text=" ● LIVE ", text_color="white", fg_color="#1b5e20"
            )
        else:
            self.status_label.configure(
                text=" OFFLINE ", text_color="#999999", fg_color="transparent"
            )

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
            on_quit=self._quit_app,
            is_monitoring=lambda: self._monitor is not None and self._monitor.is_running,
        )
        self._tray.start()

        if silent:
            self.withdraw()
            channels = self.config.get("channels", [])
            if channels:
                self.after(500, self._on_start)

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
        """X button hides to tray instead of quitting."""
        self._hide_window()

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
            text="▶  開始監聽",
            width=130,
            height=38,
            corner_radius=8,
            fg_color=_CLR_START,
            hover_color=_CLR_START_HOVER,
            font=_font(14, "bold"),
            command=self._on_start,
        )
        self.start_btn.pack(side="left", padx=(0, 8))
        _tooltip(self.start_btn, "開始監聽所有已啟用的頻道")

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

        self.startup_var = ctk.BooleanVar(value=is_startup_enabled())
        self.startup_switch = ctk.CTkSwitch(
            toolbar,
            text="開機啟動",
            variable=self.startup_var,
            command=self._on_startup_toggle,
            font=_font(12),
        )
        self.startup_switch.grid(row=0, column=5, sticky="e", padx=(18, 0))
        _tooltip(self.startup_switch, "系統開機時自動啟動程式（僅限封裝 exe）")

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
    def _on_start(self) -> None:
        channels = self.config.get("channels", [])
        if not channels:
            return

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
        self._save_config()

        self._monitor = Monitor(
            channels=channels,
            interval=interval,
            on_status_change=self._on_channel_live,
            on_poll_complete=self._on_poll_done,
            db=self._db,
        )
        self._monitor.start()

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status_text.configure(text="監聽中…", text_color=_CLR_LIVE)
        self._tray.update_tooltip("哈嘍主播 — 監聽中")

    def _on_stop(self) -> None:
        if self._monitor:
            self._monitor.stop()
            self._monitor = None
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status_text.configure(text="已停止", text_color=_CLR_OFFLINE)
        self._tray.update_tooltip("哈嘍主播 — 已停止")

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

        action = self.config.get("action", "open_and_stop")
        should_stop = False
        should_exit = False

        for entry, info in live_events:
            if info.display_name:
                self._apply_display_names({entry.key: info.display_name})

            noop = lambda: None  # noqa: E731
            execute_action(action, info, stop_fn=noop, exit_fn=noop)

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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    silent = "--silent" in sys.argv

    lock = SingleInstance()

    def on_show_request() -> None:
        if app:
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
