"""CustomTkinter 主視窗 — 開播監聽器 GUI 與 Monitor 整合。"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

import customtkinter as ctk

from stream_monitor import config_manager
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.monitor import ChannelEntry, Monitor
from stream_monitor.notifier import execute_action
from stream_monitor.startup import disable_startup, enable_startup, is_startup_enabled

logger = logging.getLogger(__name__)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

PLATFORM_OPTIONS = ["twitch", "youtube"]
ACTION_LABELS: dict[str, str] = {
    "open_and_stop": "開啟網頁並停止監聽",
    "open_and_keep": "開啟網頁並保持監聽",
    "notify_only": "僅跳出系統通知",
    "open_and_exit": "開啟網頁後關閉程式",
}
ACTION_KEYS = list(ACTION_LABELS.keys())
ACTION_DISPLAY = list(ACTION_LABELS.values())


class AddChannelDialog(ctk.CTkToplevel):
    """Modal dialog for adding a new channel."""

    def __init__(self, parent: ctk.CTk) -> None:
        super().__init__(parent)
        self.title("新增頻道")
        self.geometry("360x200")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.result: dict[str, str] | None = None

        ctk.CTkLabel(self, text="平台：", anchor="w").pack(
            padx=20, pady=(20, 0), fill="x"
        )
        self.platform_var = ctk.StringVar(value="twitch")
        self.platform_menu = ctk.CTkOptionMenu(
            self, variable=self.platform_var, values=PLATFORM_OPTIONS
        )
        self.platform_menu.pack(padx=20, fill="x")

        ctk.CTkLabel(self, text="頻道名稱：", anchor="w").pack(
            padx=20, pady=(10, 0), fill="x"
        )
        self.name_entry = ctk.CTkEntry(self, placeholder_text="例如 kaicenat")
        self.name_entry.pack(padx=20, fill="x")

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(padx=20, pady=15, fill="x")
        ctk.CTkButton(btn_frame, text="取消", width=80, command=self.destroy).pack(
            side="right", padx=(5, 0)
        )
        ctk.CTkButton(btn_frame, text="新增", width=80, command=self._on_add).pack(
            side="right"
        )

        self.name_entry.bind("<Return>", lambda _: self._on_add())

    def _on_add(self) -> None:
        name = self.name_entry.get().strip()
        if name:
            self.result = {"platform": self.platform_var.get(), "name": name}
        self.destroy()


class ChannelRow(ctk.CTkFrame):
    """Single row in the channel list showing platform, name, status."""

    def __init__(
        self,
        parent: ctk.CTkFrame,
        channel: dict[str, str],
        on_delete: callable,
    ) -> None:
        super().__init__(parent, corner_radius=8)
        self.channel = channel

        platform_colors = {"twitch": "#9146FF", "youtube": "#FF0000"}
        color = platform_colors.get(channel["platform"], "#555555")

        self.platform_label = ctk.CTkLabel(
            self,
            text=channel["platform"].upper(),
            width=70,
            fg_color=color,
            corner_radius=4,
            text_color="white",
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self.platform_label.pack(side="left", padx=(8, 4), pady=6)

        self.name_label = ctk.CTkLabel(
            self,
            text=channel["name"],
            anchor="w",
            font=ctk.CTkFont(size=14),
        )
        self.name_label.pack(side="left", padx=4, pady=6, fill="x", expand=True)

        self.status_label = ctk.CTkLabel(
            self,
            text="--",
            width=70,
            font=ctk.CTkFont(size=12),
        )
        self.status_label.pack(side="left", padx=4, pady=6)

        self.delete_btn = ctk.CTkButton(
            self,
            text="✕",
            width=30,
            height=30,
            fg_color="transparent",
            hover_color="#FF4444",
            command=on_delete,
            font=ctk.CTkFont(size=14),
        )
        self.delete_btn.pack(side="right", padx=(0, 8), pady=6)

    def set_status(self, is_live: bool | None) -> None:
        if is_live is None:
            self.status_label.configure(text="--", text_color="gray")
        elif is_live:
            self.status_label.configure(text="● LIVE", text_color="#00FF88")
        else:
            self.status_label.configure(text="OFFLINE", text_color="gray")

    @property
    def key(self) -> str:
        return f"{self.channel['platform']}:{self.channel['name']}"


class App(ctk.CTk):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()

        self.config = config_manager.load()
        self._event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._monitor: Monitor | None = None
        self._channel_rows: list[ChannelRow] = []

        self.title("開播監聽器  Stream Monitor")
        self.geometry(self.config.get("window_geometry") or "720x520")
        self.minsize(600, 400)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._populate_channels()
        self._poll_events()

    def _build_ui(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        main.grid_rowconfigure(0, weight=1)
        main.grid_columnconfigure(0, weight=1)

        # ---- Channel list panel ----
        list_frame = ctk.CTkFrame(main)
        list_frame.grid(row=0, column=0, sticky="nsew")
        list_frame.grid_rowconfigure(1, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(list_frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))
        ctk.CTkLabel(
            header,
            text="頻道清單",
            font=ctk.CTkFont(size=18, weight="bold"),
            anchor="w",
        ).pack(side="left")

        self.add_btn = ctk.CTkButton(
            header, text="＋ 新增頻道", width=110, command=self._on_add_channel
        )
        self.add_btn.pack(side="right")

        self.scroll_frame = ctk.CTkScrollableFrame(list_frame, corner_radius=0)
        self.scroll_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        self.scroll_frame.grid_columnconfigure(0, weight=1)

        # ---- Bottom control bar ----
        ctrl = ctk.CTkFrame(main)
        ctrl.grid(row=1, column=0, sticky="ew", pady=(10, 0))

        left_ctrl = ctk.CTkFrame(ctrl, fg_color="transparent")
        left_ctrl.pack(side="left", padx=12, pady=10)

        self.start_btn = ctk.CTkButton(
            left_ctrl,
            text="▶  開始監聽",
            width=130,
            fg_color="#2B8C3E",
            hover_color="#237332",
            command=self._on_start,
        )
        self.start_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = ctk.CTkButton(
            left_ctrl,
            text="■  停止",
            width=80,
            fg_color="#C0392B",
            hover_color="#962D22",
            state="disabled",
            command=self._on_stop,
        )
        self.stop_btn.pack(side="left")

        self.status_text = ctk.CTkLabel(
            left_ctrl,
            text="未啟動",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        )
        self.status_text.pack(side="left", padx=12)

        right_ctrl = ctk.CTkFrame(ctrl, fg_color="transparent")
        right_ctrl.pack(side="right", padx=12, pady=10)

        ctk.CTkLabel(right_ctrl, text="間隔(秒)：").pack(side="left")
        self.interval_var = ctk.StringVar(
            value=str(self.config.get("check_interval", 60))
        )
        self.interval_entry = ctk.CTkEntry(
            right_ctrl, width=55, textvariable=self.interval_var
        )
        self.interval_entry.pack(side="left", padx=(0, 12))

        ctk.CTkLabel(right_ctrl, text="觸發行為：").pack(side="left")
        current_action = self.config.get("action", "open_and_stop")
        display = ACTION_LABELS.get(current_action, ACTION_DISPLAY[0])
        self.action_var = ctk.StringVar(value=display)
        self.action_menu = ctk.CTkOptionMenu(
            right_ctrl,
            variable=self.action_var,
            values=ACTION_DISPLAY,
            width=180,
        )
        self.action_menu.pack(side="left", padx=(0, 12))

        self.startup_var = ctk.BooleanVar(value=is_startup_enabled())
        self.startup_switch = ctk.CTkSwitch(
            right_ctrl,
            text="開機啟動",
            variable=self.startup_var,
            command=self._on_startup_toggle,
        )
        self.startup_switch.pack(side="left")

    def _populate_channels(self) -> None:
        for ch in self.config.get("channels", []):
            self._add_channel_row(ch)

    def _add_channel_row(self, channel: dict[str, str]) -> None:
        def on_delete(ch=channel):
            self._remove_channel(ch)

        row = ChannelRow(self.scroll_frame, channel, on_delete=on_delete)
        row.pack(fill="x", pady=2)
        self._channel_rows.append(row)

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

    def _on_start(self) -> None:
        channels = self.config.get("channels", [])
        if not channels:
            return

        try:
            interval = int(self.interval_var.get())
        except ValueError:
            interval = 60

        self.config["check_interval"] = interval
        self._save_config()

        action_display = self.action_var.get()
        action_key = ACTION_KEYS[ACTION_DISPLAY.index(action_display)]
        self.config["action"] = action_key
        self._save_config()

        self._monitor = Monitor(
            channels=channels,
            interval=interval,
            on_status_change=self._on_channel_live,
            on_poll_complete=self._on_poll_done,
        )
        self._monitor.start()

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status_text.configure(text="監聽中…", text_color="#00FF88")

    def _on_stop(self) -> None:
        if self._monitor:
            self._monitor.stop()
            self._monitor = None
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status_text.configure(text="已停止", text_color="gray")

    def _on_channel_live(self, entry: ChannelEntry, info: StreamInfo) -> None:
        """Called from monitor thread — push event to UI queue."""
        self._event_queue.put(("live", (entry, info)))

    def _on_poll_done(self) -> None:
        """Called from monitor thread after each full polling round."""
        if self._monitor:
            statuses = dict(self._monitor._last_status)
            self._event_queue.put(("status_update", statuses))

    def _poll_events(self) -> None:
        """Drain the event queue from the UI thread (tkinter-safe)."""
        try:
            while True:
                kind, data = self._event_queue.get_nowait()
                if kind == "live":
                    entry, info = data
                    action = self.config.get("action", "open_and_stop")

                    if action == "open_and_keep":
                        if self._monitor and entry.key in self._monitor.triggered:
                            continue
                        if self._monitor:
                            self._monitor.mark_triggered(entry.key)

                    stop_fn = self._on_stop if action == "open_and_stop" else None
                    execute_action(action, info, stop_fn=stop_fn)

                elif kind == "status_update":
                    statuses: dict[str, bool] = data
                    for row in self._channel_rows:
                        row.set_status(statuses.get(row.key))

        except queue.Empty:
            pass

        self.after(500, self._poll_events)

    def _on_startup_toggle(self) -> None:
        if self.startup_var.get():
            enable_startup()
        else:
            disable_startup()
        self.config["run_on_startup"] = self.startup_var.get()
        self._save_config()

    def _save_config(self) -> None:
        try:
            geo = self.geometry()
            self.config["window_geometry"] = geo
        except Exception:
            pass
        config_manager.save(self.config)

    def _on_close(self) -> None:
        if self._monitor:
            self._monitor.stop()
        self._save_config()
        self.destroy()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
