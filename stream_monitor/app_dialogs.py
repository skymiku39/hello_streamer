"""Modal dialogs for channel, language, and browser settings."""

from __future__ import annotations

import logging
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import customtkinter as ctk

from stream_monitor import config_manager, default_browser_profile_dir, i18n
from stream_monitor.app_ui import (
    _CLR_ADD,
    _CLR_ADD_HOVER,
    _CLR_BG_DARK,
    _CLR_CARD,
    _CLR_LINK,
    _CLR_LINK_HOVER,
    _CLR_LIVE,
    PLATFORM_OPTIONS,
    _button_width,
    _fit_button,
    _font,
    _tooltip_tr,
)
from stream_monitor.config_manager import DEFAULT_BROWSER_SETTINGS
from stream_monitor.fetcher import get_fetcher
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.i18n import tr
from stream_monitor.notifier import (
    close_browser_window_for_url,
    detect_browser_family,
    open_browser_for_signin,
    open_url,
)
from stream_monitor.url_parser import parse_url

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# Add Channel Dialog
# ═══════════════════════════════════════════════════════════════════════════
class AddChannelDialog(ctk.CTkToplevel):
    """Modal dialog — supports both URL paste and manual input."""

    def __init__(self, parent: ctk.CTk) -> None:
        super().__init__(parent)
        self.title(tr("add.title"))
        self.geometry("680x430")
        self.resizable(False, False)
        self.transient(parent)
        self.configure(fg_color=_CLR_BG_DARK)

        if sys.platform != "win32":
            self.update()
        self.grab_set()

        self.result: dict[str, str] | None = None

        self._heading_label = ctk.CTkLabel(
            self,
            text=tr("add.heading"),
            font=_font(13, "bold"),
            anchor="w",
        )
        self._heading_label.pack(padx=24, pady=(20, 4), fill="x")

        url_frame = ctk.CTkFrame(self, fg_color="transparent")
        url_frame.pack(padx=24, fill="x")

        self.url_entry = ctk.CTkEntry(
            url_frame,
            placeholder_text=tr("add.url.placeholder"),
            font=_font(13),
            height=38,
        )
        self.url_entry.pack(fill="x")
        self.url_entry.bind("<KeyRelease>", self._on_url_change)

        self._url_hint_label = ctk.CTkLabel(
            self,
            text=tr("add.url.hint"),
            font=_font(12),
            text_color="#aaaabb",
            anchor="w",
            wraplength=620,
        )
        self._url_hint_label.pack(padx=24, pady=(6, 0), fill="x")

        self._url_warning_label = ctk.CTkLabel(
            self,
            text=tr("add.url.warning"),
            font=_font(12, "bold"),
            text_color="#ffb74d",
            anchor="w",
            wraplength=620,
        )
        self._url_warning_label.pack(padx=24, pady=(4, 0), fill="x")

        self.message_label = ctk.CTkLabel(
            self, text="", font=_font(12), height=24, anchor="w", wraplength=620
        )
        self.message_label.pack(padx=24, pady=(4, 0), fill="x")
        self._message_key: tuple[str, dict[str, Any]] | None = None

        sep = ctk.CTkFrame(self, height=1, fg_color="#333355")
        sep.pack(padx=24, pady=12, fill="x")

        self._manual_heading_label = ctk.CTkLabel(
            self,
            text=tr("add.manual.heading"),
            font=_font(12),
            text_color="#888899",
            anchor="w",
        )
        self._manual_heading_label.pack(padx=24, fill="x")

        manual_frame = ctk.CTkFrame(self, fg_color="transparent")
        manual_frame.pack(padx=24, pady=(6, 0), fill="x")
        manual_frame.grid_columnconfigure(1, weight=1)

        self._platform_label = ctk.CTkLabel(
            manual_frame, text=tr("add.manual.platform"), font=_font(13), anchor="w"
        )
        self._platform_label.grid(row=0, column=0, padx=(0, 10), sticky="w")
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

        self._name_label = ctk.CTkLabel(
            manual_frame, text=tr("add.manual.name"), font=_font(13), anchor="w"
        )
        self._name_label.grid(row=1, column=0, padx=(0, 10), pady=(8, 0), sticky="w")
        self.name_entry = ctk.CTkEntry(
            manual_frame,
            placeholder_text=tr("add.manual.name.placeholder"),
            font=_font(13),
            height=34,
        )
        self.name_entry.grid(row=1, column=1, pady=(8, 0), sticky="ew")

        self._manual_hint_label = ctk.CTkLabel(
            self,
            text=tr("add.manual.hint"),
            font=_font(12),
            text_color="#888899",
            anchor="w",
            wraplength=620,
        )
        self._manual_hint_label.pack(padx=24, pady=(6, 0), fill="x")

        btn_frame = ctk.CTkFrame(self, fg_color="transparent", height=48)
        btn_frame.pack(padx=24, pady=(14, 22), fill="x")
        btn_frame.pack_propagate(False)

        self._cancel_btn = ctk.CTkButton(
            btn_frame,
            text=tr("add.btn.cancel"),
            width=_button_width(tr("add.btn.cancel"), min_width=96),
            height=40,
            fg_color="transparent",
            border_width=1,
            border_color="#555566",
            hover_color="#333344",
            font=_font(13),
            command=self.destroy,
        )
        self._cancel_btn.pack(side="right", padx=(8, 0), pady=4)

        self._add_btn = ctk.CTkButton(
            btn_frame,
            text=tr("add.btn.add"),
            width=_button_width(tr("add.btn.add"), min_width=96, weight="bold"),
            height=40,
            fg_color=_CLR_ADD,
            hover_color=_CLR_ADD_HOVER,
            font=_font(13, "bold"),
            command=self._on_add,
        )
        self._add_btn.pack(side="right", pady=4)

        self.url_entry.bind("<Return>", lambda _: self._on_add())
        self.name_entry.bind("<Return>", lambda _: self._on_add())

        self._unsub_i18n = i18n.subscribe(self._retranslate)
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _retranslate(self) -> None:
        try:
            self.title(tr("add.title"))
        except Exception:  # noqa: BLE001
            return
        self._heading_label.configure(text=tr("add.heading"))
        self.url_entry.configure(placeholder_text=tr("add.url.placeholder"))
        self._url_hint_label.configure(text=tr("add.url.hint"))
        self._url_warning_label.configure(text=tr("add.url.warning"))
        self._manual_heading_label.configure(text=tr("add.manual.heading"))
        self._platform_label.configure(text=tr("add.manual.platform"))
        self._name_label.configure(text=tr("add.manual.name"))
        self.name_entry.configure(placeholder_text=tr("add.manual.name.placeholder"))
        self._manual_hint_label.configure(text=tr("add.manual.hint"))
        _fit_button(self._cancel_btn, tr("add.btn.cancel"), min_width=96)
        _fit_button(
            self._add_btn, tr("add.btn.add"), min_width=96, weight="bold"
        )
        if self._message_key is not None:
            key, kwargs = self._message_key
            self.message_label.configure(text=tr(key, **kwargs))

    def _on_destroy(self, event: Any = None) -> None:
        if event is not None and event.widget is not self:
            return
        if getattr(self, "_unsub_i18n", None):
            self._unsub_i18n()
            self._unsub_i18n = None

    def _set_message(
        self, key: str | None, *, color: str = "gray", **kwargs: Any
    ) -> None:
        if key is None:
            self._message_key = None
            self.message_label.configure(text="", text_color=color)
            return
        self._message_key = (key, dict(kwargs))
        self.message_label.configure(text=tr(key, **kwargs), text_color=color)

    def _on_url_change(self, _event: Any = None) -> None:
        text = self.url_entry.get()
        parsed = parse_url(text)
        if parsed:
            self._set_message(
                "add.msg.parsed",
                color=_CLR_LIVE,
                platform_upper=parsed.platform.upper(),
                name=parsed.name,
            )
            self.platform_var.set(parsed.platform)
            self.name_entry.delete(0, "end")
            self.name_entry.insert(0, parsed.name)
        else:
            if text.strip():
                self._set_message("add.msg.unparseable", color="#ffb74d")
            else:
                self._set_message(None)

    def _on_add(self) -> None:
        url_text = self.url_entry.get().strip()
        parsed = parse_url(url_text)

        if parsed:
            plat, name = parsed.platform, parsed.name
        elif url_text:
            self._set_message("add.msg.invalid_url", color="#ffb74d")
            self.url_entry.focus_set()
            return
        else:
            name = self.name_entry.get().strip()
            plat = self.platform_var.get()
            if not name:
                self._set_message("add.msg.empty", color="#ffb74d")
                self.name_entry.focus_set()
                return

        self._set_message("add.msg.validating", color="#64b5f6")
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
        # Keep the Cancel button always usable. The network validation call
        # in ``_validate_channel`` has no enforced timeout, so disabling
        # Cancel while waiting would strand the user in a modal they cannot
        # close until the fetcher returns. The Add button still gets
        # toggled — leaving Add disabled prevents double-submits.
        for w in self.winfo_children():
            if isinstance(w, ctk.CTkFrame):
                for child in w.winfo_children():
                    if isinstance(child, ctk.CTkButton) and child is not self._cancel_btn:
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
            self._set_message(
                "add.msg.not_found",
                color="#ef5350",
                platform_upper=plat.upper(),
                name=name,
            )
            self._set_inputs_enabled(True)
            return

        self.result = {"platform": plat, "name": name}
        if info.display_name:
            self.result["display_name"] = info.display_name
        self.destroy()


# ═══════════════════════════════════════════════════════════════════════════
# Language Dialog
# ═══════════════════════════════════════════════════════════════════════════
class LanguageDialog(ctk.CTkToplevel):
    """Modal picker for switching the active UI language at runtime."""

    def __init__(
        self,
        parent: ctk.CTk,
        on_apply: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title(tr("lang.title"))
        self.geometry("420x480")
        self.resizable(False, False)
        self.transient(parent)
        self.configure(fg_color=_CLR_BG_DARK)

        if sys.platform != "win32":
            self.update()
        self.grab_set()

        self._on_apply = on_apply
        self._initial_lang = i18n.current_language()
        self._selected_lang = ctk.StringVar(value=self._initial_lang)
        self._row_widgets: dict[str, tuple[ctk.CTkFrame, ctk.CTkLabel, ctk.CTkLabel]] = {}

        self._heading_label = ctk.CTkLabel(
            self,
            text=tr("lang.heading"),
            font=_font(16, "bold"),
            anchor="w",
        )
        self._heading_label.pack(padx=22, pady=(22, 4), fill="x")

        self._description_label = ctk.CTkLabel(
            self,
            text=tr("lang.description"),
            font=_font(12),
            text_color="#aaaabb",
            anchor="w",
            justify="left",
            wraplength=360,
        )
        self._description_label.pack(padx=22, pady=(0, 14), fill="x")

        for code, native_label, english_label in i18n.available_languages():
            row = ctk.CTkFrame(
                self,
                fg_color=_CLR_CARD,
                border_width=1,
                border_color="#333355",
                corner_radius=8,
                height=48,
                cursor="hand2",
            )
            row.pack(padx=22, pady=(0, 8), fill="x")
            row.pack_propagate(False)

            name_label = ctk.CTkLabel(
                row,
                text=native_label,
                font=_font(13, "bold"),
                anchor="w",
            )
            name_label.pack(side="left", padx=(14, 6), fill="x", expand=True)

            status_label = ctk.CTkLabel(
                row,
                text=english_label,
                font=_font(11),
                text_color="#888899",
                anchor="e",
            )
            status_label.pack(side="right", padx=14)

            for widget in (row, name_label, status_label):
                widget.bind("<Button-1>", lambda _e, c=code: self._select(c))

            self._row_widgets[code] = (row, name_label, status_label)

        footer = ctk.CTkFrame(self, fg_color="transparent", height=52)
        footer.pack(padx=22, pady=(6, 18), fill="x", side="bottom")
        footer.pack_propagate(False)

        self._close_btn = ctk.CTkButton(
            footer,
            text=tr("lang.btn.close"),
            width=_button_width(tr("lang.btn.close"), min_width=88),
            height=36,
            fg_color="transparent",
            border_width=1,
            border_color="#555566",
            hover_color="#333344",
            font=_font(13),
            command=self._on_close,
        )
        self._close_btn.pack(side="right", padx=(8, 0), pady=8)

        self._apply_btn = ctk.CTkButton(
            footer,
            text=tr("lang.btn.apply"),
            width=_button_width(
                tr("lang.btn.apply"), min_width=88, weight="bold"
            ),
            height=36,
            fg_color=_CLR_ADD,
            hover_color=_CLR_ADD_HOVER,
            font=_font(13, "bold"),
            command=self._on_apply_btn,
        )
        self._apply_btn.pack(side="right", pady=8)

        self._unsub_i18n = i18n.subscribe(self._retranslate)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._update_row_visuals()

    def _select(self, code: str) -> None:
        if code not in i18n.LANGUAGE_CODES:
            return
        self._selected_lang.set(code)
        self._update_row_visuals()

    def _update_row_visuals(self) -> None:
        current = i18n.current_language()
        selected = self._selected_lang.get()
        for code, (row, _name, status) in self._row_widgets.items():
            is_selected = code == selected
            is_current = code == current
            border = _CLR_LINK if is_selected else "#333355"
            row.configure(border_color=border)
            if is_current and is_selected:
                status.configure(text=tr("lang.option.current"), text_color=_CLR_LIVE)
            elif is_selected:
                status.configure(text=tr("lang.option.selected"), text_color=_CLR_LINK)
            elif is_current:
                status.configure(text=tr("lang.option.current"), text_color=_CLR_LINK)
            else:
                _native, english = self._labels_for(code)
                status.configure(text=english, text_color="#888899")

    @staticmethod
    def _labels_for(code: str) -> tuple[str, str]:
        for c, native, english in i18n.available_languages():
            if c == code:
                return native, english
        return code, code

    def _on_apply_btn(self) -> None:
        new_code = self._selected_lang.get()
        if self._on_apply is not None:
            self._on_apply(new_code)
        else:
            i18n.set_language(new_code)
        self.destroy()

    def _on_close(self) -> None:
        self.destroy()

    def _retranslate(self) -> None:
        try:
            self.title(tr("lang.title"))
        except Exception:  # noqa: BLE001
            return
        self._heading_label.configure(text=tr("lang.heading"))
        self._description_label.configure(text=tr("lang.description"))
        _fit_button(self._close_btn, tr("lang.btn.close"), min_width=88)
        _fit_button(
            self._apply_btn, tr("lang.btn.apply"), min_width=88, weight="bold"
        )
        self._update_row_visuals()

    def _on_destroy(self, event: Any = None) -> None:
        if event is not None and event.widget is not self:
            return
        if getattr(self, "_unsub_i18n", None):
            self._unsub_i18n()
            self._unsub_i18n = None


# ═══════════════════════════════════════════════════════════════════════════
# Browser Settings Dialog
# ═══════════════════════════════════════════════════════════════════════════
class BrowserSettingsDialog(ctk.CTkToplevel):
    """Modal dialog for configuring how stream pages are opened in the browser."""

    def __init__(self, parent: ctk.CTk, current: dict[str, Any]) -> None:
        super().__init__(parent)
        self.title(tr("browser.title"))
        screen_height = max(self.winfo_screenheight(), 640)
        self.geometry(f"580x{min(780, screen_height - 80)}")
        self.minsize(500, 540)
        self.resizable(True, True)
        self.transient(parent)
        self.configure(fg_color=_CLR_BG_DARK)

        if sys.platform != "win32":
            self.update()
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_window_close)

        self.result: dict[str, Any] | None = None
        settings = {**DEFAULT_BROWSER_SETTINGS, **(current or {})}
        self._new_window_before_app_mode = bool(settings.get("new_window", True))
        profile_stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        self._test_profile_dir = Path(tempfile.gettempdir()) / (
            f"hello_streamer_app_mode_test_profile_{profile_stamp}"
        )
        self._last_test_url: str | None = None

        self._tab_user_label = tr("browser.tab.user")
        self._tab_advanced_label = tr("browser.tab.advanced")
        self.tabview = ctk.CTkTabview(self, fg_color="transparent")
        self.tabview.pack(padx=12, pady=(8, 0), fill="both", expand=True)

        user_tab = self.tabview.add(self._tab_user_label)
        advanced_tab = self.tabview.add(self._tab_advanced_label)
        # CTkTabview stores tab labels in an internal segmented button.
        self.tabview._segmented_button.configure(font=_font(13, "bold"))

        self.content_frame = ctk.CTkScrollableFrame(user_tab, fg_color="transparent")
        self.content_frame.pack(fill="both", expand=True)

        self.advanced_frame = ctk.CTkScrollableFrame(
            advanced_tab, fg_color="transparent"
        )
        self.advanced_frame.pack(fill="both", expand=True)

        self._section_open_label = ctk.CTkLabel(
            self.content_frame,
            text=tr("browser.section.open"),
            font=_font(13, "bold"),
            anchor="w",
        )
        self._section_open_label.pack(padx=16, pady=(12, 4), fill="x")

        # User-tab iso-feature banner. Shown ONLY in the exact state where
        # Migration #2 would silently kick in on save — i.e. master switch
        # on, dedicated profile off, and at least one opt-in isolation
        # flag (app_mode / minimized / hide_from_taskbar / close_on_*).
        # The previous version surfaced this dependency only on the
        # Advanced tab, which left a User-tab user staring at instantly
        # greyed-out checkboxes with no on-screen explanation. The banner
        # both explains and offers a one-click jump to the relevant tab.
        self._user_tab_iso_banner = ctk.CTkLabel(
            self.content_frame,
            text=tr("browser.banner.iso_features_auto_setup"),
            font=_font(11, "bold"),
            text_color="#ffb74d",
            anchor="w",
            wraplength=480,
            justify="left",
            cursor="hand2",
        )
        self._user_tab_iso_banner.bind(
            "<Button-1>",
            lambda _e: self.tabview.set(self._tab_advanced_label),
        )
        # Packed-then-forgotten — visibility cycles via `_refresh_user_tab_iso_banner`.
        self._user_tab_iso_banner.pack(
            padx=16, pady=(0, 6), fill="x", before=self._section_open_label
        )
        self._user_tab_iso_banner.pack_forget()

        self._section_open_hint = ctk.CTkLabel(
            self.content_frame,
            text=tr("browser.section.open.hint"),
            font=_font(12),
            text_color="#aaaabb",
            anchor="w",
            wraplength=480,
        )
        self._section_open_hint.pack(padx=16, pady=(0, 8), fill="x")

        self.enabled_var = ctk.BooleanVar(value=bool(settings.get("enabled", False)))
        self.enabled_switch = ctk.CTkSwitch(
            self.content_frame,
            text=tr("browser.enable"),
            variable=self.enabled_var,
            command=self._refresh_enabled_state,
            font=_font(12),
        )
        self.enabled_switch.pack(padx=16, anchor="w")

        # ── Browser path
        path_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        path_frame.pack(padx=16, pady=(8, 0), fill="x")
        path_frame.grid_columnconfigure(1, weight=1)

        self._path_label = ctk.CTkLabel(
            path_frame,
            text=tr("browser.path.label"),
            font=_font(13),
            anchor="w",
            width=104,
        )
        self._path_label.grid(row=0, column=0, sticky="w")

        self.path_entry = ctk.CTkEntry(
            path_frame,
            placeholder_text=tr("browser.path.placeholder"),
            font=_font(13),
            height=34,
        )
        self.path_entry.insert(0, settings.get("browser_path", "chrome"))
        self.path_entry.grid(row=0, column=1, sticky="ew")
        self.path_entry.bind("<KeyRelease>", self._on_path_change)

        self._path_hint = ctk.CTkLabel(
            self.content_frame,
            text=tr("browser.path.hint"),
            font=_font(11),
            text_color="#888899",
            anchor="w",
            wraplength=480,
        )
        self._path_hint.pack(padx=16, pady=(4, 0), fill="x")

        self.compat_label = ctk.CTkLabel(
            self.content_frame,
            text="",
            font=_font(11, "bold"),
            text_color="#ffb74d",
            anchor="w",
            wraplength=480,
            height=20,
        )
        self.compat_label.pack(padx=16, pady=(2, 0), fill="x")
        self._compat_key: tuple[str, str] | None = None

        self._section_window_label = ctk.CTkLabel(
            self.content_frame,
            text=tr("browser.section.window"),
            font=_font(13, "bold"),
            anchor="w",
        )
        self._section_window_label.pack(padx=16, pady=(10, 4), fill="x")

        self._section_window_hint = ctk.CTkLabel(
            self.content_frame,
            text=tr("browser.section.window.hint"),
            font=_font(12),
            text_color="#aaaabb",
            anchor="w",
            wraplength=480,
        )
        self._section_window_hint.pack(padx=16, pady=(0, 8), fill="x")

        toggle_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        toggle_frame.pack(padx=16, pady=(0, 0), fill="x")

        self.new_window_var = ctk.BooleanVar(
            value=bool(settings.get("new_window", True))
        )
        self.new_window_cb = ctk.CTkCheckBox(
            toggle_frame,
            text=tr("browser.toggle.new_window"),
            variable=self.new_window_var,
            command=self._refresh_win32_management_state,
            font=_font(12),
        )
        self.new_window_cb.pack(anchor="w", pady=(0, 2))
        self._new_window_hint = ctk.CTkLabel(
            toggle_frame,
            text=tr("browser.toggle.new_window.hint"),
            font=_font(11),
            text_color="#aaaabb",
            anchor="w",
            wraplength=480,
            justify="left",
        )
        self._new_window_hint.pack(anchor="w", pady=(0, 4))

        self._no_window_tracking_label = ctk.CTkLabel(
            toggle_frame,
            text=tr("browser.msg.no_window_tracking_warning"),
            font=_font(11),
            text_color="#ffb74d",
            anchor="w",
            wraplength=480,
            justify="left",
        )
        self._no_window_tracking_label.pack(anchor="w", pady=(0, 4))
        self._no_window_tracking_label.pack_forget()

        self.minimized_var = ctk.BooleanVar(
            value=bool(settings.get("minimized", False))
        )
        self.minimized_cb = ctk.CTkCheckBox(
            toggle_frame,
            text=tr("browser.toggle.minimized"),
            variable=self.minimized_var,
            command=self._refresh_user_tab_iso_banner,
            font=_font(12),
        )
        self.minimized_cb.pack(anchor="w")

        self._section_lifecycle_label = ctk.CTkLabel(
            self.content_frame,
            text=tr("browser.section.lifecycle"),
            font=_font(13, "bold"),
            anchor="w",
        )
        self._section_lifecycle_label.pack(padx=16, pady=(12, 4), fill="x")

        self._section_lifecycle_hint = ctk.CTkLabel(
            self.content_frame,
            text=tr("browser.section.lifecycle.hint"),
            font=_font(12),
            text_color="#aaaabb",
            anchor="w",
            wraplength=480,
        )
        self._section_lifecycle_hint.pack(padx=16, pady=(0, 8), fill="x")

        close_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        close_frame.pack(padx=16, pady=(0, 0), fill="x")

        self.close_on_offline_var = ctk.BooleanVar(
            value=bool(settings.get("close_on_offline", False))
        )
        self.close_on_offline_cb = ctk.CTkCheckBox(
            close_frame,
            text=tr("browser.toggle.close_on_offline"),
            variable=self.close_on_offline_var,
            command=self._refresh_user_tab_iso_banner,
            font=_font(12),
        )
        self.close_on_offline_cb.pack(anchor="w", pady=(4, 0))
        self._close_on_offline_hint = ctk.CTkLabel(
            close_frame,
            text=tr("browser.toggle.close_on_offline.hint"),
            font=_font(10),
            text_color="#9aa0b4",
            anchor="w",
            wraplength=480,
            justify="left",
        )
        self._close_on_offline_hint.pack(anchor="w", pady=(0, 2))

        self.close_on_stop_var = ctk.BooleanVar(
            value=bool(settings.get("close_on_stop", False))
        )
        self.close_on_stop_cb = ctk.CTkCheckBox(
            close_frame,
            text=tr("browser.toggle.close_on_stop"),
            variable=self.close_on_stop_var,
            command=self._refresh_user_tab_iso_banner,
            font=_font(12),
        )
        self.close_on_stop_cb.pack(anchor="w", pady=(4, 0))
        self._close_on_stop_hint = ctk.CTkLabel(
            close_frame,
            text=tr("browser.toggle.close_on_stop.hint"),
            font=_font(10),
            text_color="#9aa0b4",
            anchor="w",
            wraplength=480,
            justify="left",
        )
        self._close_on_stop_hint.pack(anchor="w", pady=(0, 2))

        self.close_off_topic_var = ctk.BooleanVar(
            value=bool(settings.get("close_off_topic_pages", False))
        )
        self.close_off_topic_cb = ctk.CTkCheckBox(
            close_frame,
            text=tr("browser.toggle.close_off_topic"),
            variable=self.close_off_topic_var,
            command=self._refresh_user_tab_iso_banner,
            font=_font(12),
        )
        self.close_off_topic_cb.pack(anchor="w", pady=(4, 0))
        self._close_off_topic_hint = ctk.CTkLabel(
            close_frame,
            text=tr("browser.toggle.close_off_topic.hint"),
            font=_font(10),
            text_color="#9aa0b4",
            anchor="w",
            wraplength=480,
            justify="left",
        )
        self._close_off_topic_hint.pack(anchor="w", pady=(0, 2))

        self.hide_from_taskbar_var = ctk.BooleanVar(
            value=bool(settings.get("hide_from_taskbar", False))
        )
        self.hide_from_taskbar_cb = ctk.CTkCheckBox(
            close_frame,
            text=tr("browser.toggle.hide_taskbar"),
            variable=self.hide_from_taskbar_var,
            command=self._refresh_user_tab_iso_banner,
            font=_font(12),
        )
        self.hide_from_taskbar_cb.pack(anchor="w", pady=(4, 0))
        self._hide_taskbar_hint = ctk.CTkLabel(
            close_frame,
            text=tr("browser.toggle.hide_taskbar.hint"),
            font=_font(10),
            text_color="#9aa0b4",
            anchor="w",
            wraplength=480,
            justify="left",
        )
        self._hide_taskbar_hint.pack(anchor="w", pady=(0, 2))

        self._section_advanced_label = ctk.CTkLabel(
            self.advanced_frame,
            text=tr("browser.section.advanced"),
            font=_font(13, "bold"),
            anchor="w",
        )
        self._section_advanced_label.pack(padx=16, pady=(12, 4), fill="x")

        self._section_advanced_hint = ctk.CTkLabel(
            self.advanced_frame,
            text=tr("browser.section.advanced.hint"),
            font=_font(12),
            text_color="#aaaabb",
            anchor="w",
            wraplength=480,
            justify="left",
        )
        self._section_advanced_hint.pack(padx=16, pady=(0, 8), fill="x")

        advanced_toggle_frame = ctk.CTkFrame(
            self.advanced_frame, fg_color="transparent"
        )
        advanced_toggle_frame.pack(padx=16, pady=(0, 0), fill="x")

        self.app_mode_var = ctk.BooleanVar(value=bool(settings.get("app_mode", False)))
        self.app_mode_cb = ctk.CTkCheckBox(
            advanced_toggle_frame,
            text=tr("browser.toggle.app_mode"),
            variable=self.app_mode_var,
            command=self._on_app_mode_toggle,
            font=_font(12),
        )
        self.app_mode_cb.pack(anchor="w", pady=(0, 2))

        self._app_mode_hint = ctk.CTkLabel(
            advanced_toggle_frame,
            text=tr("browser.toggle.app_mode.hint"),
            font=_font(11),
            text_color="#9aa0b4",
            anchor="w",
            wraplength=480,
            justify="left",
        )
        self._app_mode_hint.pack(anchor="w", pady=(0, 8))

        # ── Isolated profile (forces a fresh Chrome master process so
        # --app= / --window-position actually take effect)
        profile_frame = ctk.CTkFrame(
            self.advanced_frame, fg_color=_CLR_CARD, corner_radius=10
        )
        profile_frame.pack(padx=16, pady=(10, 0), fill="x")
        profile_frame.grid_columnconfigure(1, weight=1)

        self._profile_title = ctk.CTkLabel(
            profile_frame,
            text=tr("browser.profile.title"),
            font=_font(12, "bold"),
            anchor="w",
        )
        self._profile_title.grid(row=0, column=0, columnspan=3, padx=12, pady=(10, 2), sticky="w")

        self._profile_desc = ctk.CTkLabel(
            profile_frame,
            text=tr("browser.profile.desc"),
            font=_font(11),
            text_color="#9aa0b4",
            anchor="w",
            wraplength=460,
            justify="left",
        )
        self._profile_desc.grid(row=1, column=0, columnspan=3, padx=12, pady=(0, 6), sticky="w")

        default_profile_dir = default_browser_profile_dir()

        saved_profile_dir = (settings.get("user_data_dir") or "").strip()
        self.user_data_dir_enabled_var = ctk.BooleanVar(value=bool(saved_profile_dir))

        self.user_data_dir_cb = ctk.CTkCheckBox(
            profile_frame,
            text=tr("browser.profile.enable"),
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

        self.per_channel_profile_var = ctk.BooleanVar(
            value=bool(settings.get("per_channel_profile", True))
        )
        self.per_channel_profile_cb = ctk.CTkCheckBox(
            profile_frame,
            text=tr("browser.profile.per_channel"),
            variable=self.per_channel_profile_var,
            command=self._refresh_user_data_dir_state,
            font=_font(12),
        )
        self.per_channel_profile_cb.grid(
            row=3, column=0, columnspan=3, padx=12, pady=(0, 4), sticky="w"
        )
        self._profile_per_channel_hint = ctk.CTkLabel(
            profile_frame,
            text=tr("browser.profile.per_channel.hint"),
            font=_font(11),
            text_color="#9aa0b4",
            anchor="w",
            wraplength=500,
            justify="left",
        )
        self._profile_per_channel_hint.grid(
            row=4, column=0, columnspan=3, padx=12, pady=(0, 10), sticky="w"
        )

        # No-isolation warning. Surfaced when:
        #   browser_settings.enabled = True
        #   AND user_data_dir is effectively empty
        # In that state the runtime intentionally disables Win32 post-launch
        # window management and HWND tracking (notifier.py safety degradation),
        # which means App Mode / geometry / close_on_offline /
        # close_off_topic_pages all become best-effort no-ops to avoid
        # corrupting unrelated browser windows. The label explains that to
        # the user and points them at the fix.
        self._no_isolation_label = ctk.CTkLabel(
            profile_frame,
            text=tr("browser.msg.no_isolation_warning"),
            font=_font(11),
            text_color="#ffb74d",
            anchor="w",
            wraplength=500,
            justify="left",
        )
        # grid() / grid_remove() is the cleanest way to toggle visibility
        # while keeping the row position stable across show/hide cycles.
        self._no_isolation_label.grid(
            row=5, column=0, columnspan=3, padx=12, pady=(0, 10), sticky="w"
        )
        self._no_isolation_label.grid_remove()

        # Sign-in helper. Conceptually a *prerequisite* of using a dedicated
        # profile (you need to log into Twitch/YouTube before per-channel
        # sub-profiles inherit cookies), so it lives in the profile card —
        # not in the "Tools" card next to the launch / close test buttons.
        # Without this colocation users had to scroll past unrelated test
        # actions to find the only profile-bootstrap they actually need.
        self._signin_btn = ctk.CTkButton(
            profile_frame,
            text=tr("browser.btn.signin"),
            width=_button_width(tr("browser.btn.signin"), min_width=120),
            height=30,
            fg_color="transparent",
            border_width=1,
            border_color="#cda043",
            hover_color="#3d3013",
            text_color="#cda043",
            font=_font(12),
            command=self._on_signin,
        )
        self._signin_btn.grid(
            row=6, column=0, columnspan=3, padx=12, pady=(0, 12), sticky="w"
        )
        _tooltip_tr(self._signin_btn, "browser.btn.signin.tooltip")

        tools_frame = ctk.CTkFrame(
            self.advanced_frame, fg_color=_CLR_CARD, corner_radius=10
        )
        tools_frame.pack(padx=16, pady=(10, 0), fill="x")
        tools_frame.grid_columnconfigure(0, weight=1)

        self._tools_title = ctk.CTkLabel(
            tools_frame,
            text=tr("browser.tools.title"),
            font=_font(12, "bold"),
            anchor="w",
        )
        self._tools_title.grid(row=0, column=0, padx=12, pady=(10, 2), sticky="w")

        self._tools_hint = ctk.CTkLabel(
            tools_frame,
            text=tr("browser.tools.hint"),
            font=_font(11),
            text_color="#9aa0b4",
            anchor="w",
            wraplength=480,
            justify="left",
        )
        self._tools_hint.grid(row=1, column=0, padx=12, pady=(0, 8), sticky="w")

        tools_button_frame = ctk.CTkFrame(tools_frame, fg_color="transparent")
        tools_button_frame.grid(row=2, column=0, padx=12, pady=(0, 12), sticky="w")

        self._test_btn = ctk.CTkButton(
            tools_button_frame,
            text=tr("browser.btn.test"),
            width=_button_width(tr("browser.btn.test"), min_width=100),
            height=34,
            fg_color="transparent",
            border_width=1,
            border_color=_CLR_LINK,
            hover_color=_CLR_LINK_HOVER,
            text_color=_CLR_LINK,
            font=_font(12),
            command=self._on_test,
        )
        self._test_btn.pack(side="left", padx=(0, 8))

        self._test_close_btn = ctk.CTkButton(
            tools_button_frame,
            text=tr("browser.btn.test_close"),
            width=_button_width(tr("browser.btn.test_close"), min_width=100),
            height=34,
            fg_color="transparent",
            border_width=1,
            border_color="#555566",
            hover_color="#333344",
            font=_font(12),
            command=self._on_test_close,
        )
        self._test_close_btn.pack(side="left")
        _tooltip_tr(self._test_btn, "browser.btn.test.tooltip")
        _tooltip_tr(self._test_close_btn, "browser.btn.test_close.tooltip")

        # ── Position / size
        pos_frame = ctk.CTkFrame(
            self.content_frame, fg_color=_CLR_CARD, corner_radius=10
        )
        pos_frame.pack(padx=16, pady=(10, 0), fill="x")
        pos_frame.grid_columnconfigure((1, 3), weight=1)

        header_frame = ctk.CTkFrame(pos_frame, fg_color="transparent")
        header_frame.grid(row=0, column=0, columnspan=4, padx=12, pady=(10, 4), sticky="ew")
        header_frame.grid_columnconfigure(1, weight=1)

        self.apply_geometry_var = ctk.BooleanVar(
            value=bool(settings.get("apply_geometry", True))
        )
        self.apply_geometry_cb = ctk.CTkCheckBox(
            header_frame,
            text=tr("browser.geometry.apply"),
            variable=self.apply_geometry_var,
            command=self._refresh_geometry_state,
            font=_font(12, "bold"),
        )
        self.apply_geometry_cb.grid(row=0, column=0, sticky="w")

        self.reset_geometry_btn = ctk.CTkButton(
            header_frame,
            text=tr("browser.geometry.reset"),
            width=_button_width(tr("browser.geometry.reset"), min_width=72),
            height=26,
            corner_radius=6,
            fg_color="transparent",
            border_width=1,
            border_color="#555566",
            hover_color="#333344",
            font=_font(11),
            command=self._on_reset_geometry,
        )
        self.reset_geometry_btn.grid(row=0, column=2, sticky="e")

        _tooltip_tr(self.apply_geometry_cb, "browser.geometry.apply.tooltip")
        _tooltip_tr(self.reset_geometry_btn, "browser.geometry.reset.tooltip")

        def _make_int_entry(parent: ctk.CTkFrame, value: int) -> ctk.CTkEntry:
            entry = ctk.CTkEntry(parent, width=84, height=30, font=_font(13), justify="center")
            entry.insert(0, str(value))
            return entry

        self._x_label = ctk.CTkLabel(pos_frame, text=tr("browser.geometry.x"), font=_font(12))
        self._x_label.grid(row=1, column=0, padx=(14, 4), pady=4, sticky="e")
        self.x_entry = _make_int_entry(pos_frame, int(settings.get("x", 0)))
        self.x_entry.grid(row=1, column=1, padx=(0, 14), pady=4, sticky="w")

        self._y_label = ctk.CTkLabel(pos_frame, text=tr("browser.geometry.y"), font=_font(12))
        self._y_label.grid(row=1, column=2, padx=(14, 4), pady=4, sticky="e")
        self.y_entry = _make_int_entry(pos_frame, int(settings.get("y", 0)))
        self.y_entry.grid(row=1, column=3, padx=(0, 14), pady=4, sticky="w")

        self._w_label = ctk.CTkLabel(pos_frame, text=tr("browser.geometry.width"), font=_font(12))
        self._w_label.grid(row=2, column=0, padx=(14, 4), pady=(4, 10), sticky="e")
        self.w_entry = _make_int_entry(pos_frame, int(settings.get("width", 1280)))
        self.w_entry.grid(row=2, column=1, padx=(0, 14), pady=(4, 10), sticky="w")

        self._h_label = ctk.CTkLabel(pos_frame, text=tr("browser.geometry.height"), font=_font(12))
        self._h_label.grid(row=2, column=2, padx=(14, 4), pady=(4, 10), sticky="e")
        self.h_entry = _make_int_entry(pos_frame, int(settings.get("height", 720)))
        self.h_entry.grid(row=2, column=3, padx=(0, 14), pady=(4, 10), sticky="w")

        self.message_label = ctk.CTkLabel(
            self, text="", font=_font(12), height=24, anchor="w", wraplength=480
        )
        self.message_label.pack(padx=16, pady=(8, 0), fill="x")
        self._message_key: tuple[str, dict[str, Any]] | None = None

        # ── Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent", height=52)
        btn_frame.pack(padx=16, pady=(8, 12), fill="x")
        btn_frame.pack_propagate(False)

        self._save_btn = ctk.CTkButton(
            btn_frame,
            text=tr("browser.btn.save"),
            width=_button_width(
                tr("browser.btn.save"), min_width=96, weight="bold"
            ),
            height=40,
            fg_color=_CLR_ADD,
            hover_color=_CLR_ADD_HOVER,
            font=_font(13, "bold"),
            command=self._on_save,
        )
        self._save_btn.pack(side="right", pady=4)

        self._cancel_btn = ctk.CTkButton(
            btn_frame,
            text=tr("browser.btn.cancel"),
            width=_button_width(tr("browser.btn.cancel"), min_width=96),
            height=40,
            fg_color="transparent",
            border_width=1,
            border_color="#555566",
            hover_color="#333344",
            font=_font(13),
            command=self._on_cancel,
        )
        self._cancel_btn.pack(side="right", padx=(0, 8), pady=4)

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
        self._refresh_app_mode_state()
        self._initial_snapshot = self._snapshot_browser_settings()

        self._unsub_i18n = i18n.subscribe(self._retranslate)
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _retranslate(self) -> None:
        try:
            self.title(tr("browser.title"))
        except Exception:  # noqa: BLE001
            return
        active_tab_label = self.tabview.get()
        new_user_label = tr("browser.tab.user")
        if new_user_label != self._tab_user_label:
            was_active = active_tab_label == self._tab_user_label
            self.tabview.rename(self._tab_user_label, new_user_label)
            if was_active:
                self.tabview.set(new_user_label)
            self._tab_user_label = new_user_label
        new_advanced_label = tr("browser.tab.advanced")
        if new_advanced_label != self._tab_advanced_label:
            was_active = active_tab_label == self._tab_advanced_label
            self.tabview.rename(self._tab_advanced_label, new_advanced_label)
            if was_active:
                self.tabview.set(new_advanced_label)
            self._tab_advanced_label = new_advanced_label
        self._section_open_label.configure(text=tr("browser.section.open"))
        self._section_open_hint.configure(text=tr("browser.section.open.hint"))
        self.enabled_switch.configure(text=tr("browser.enable"))
        self._path_label.configure(text=tr("browser.path.label"))
        self.path_entry.configure(placeholder_text=tr("browser.path.placeholder"))
        self._path_hint.configure(text=tr("browser.path.hint"))
        self._section_window_label.configure(text=tr("browser.section.window"))
        self._section_window_hint.configure(text=tr("browser.section.window.hint"))
        self.new_window_cb.configure(text=tr("browser.toggle.new_window"))
        self._new_window_hint.configure(text=tr("browser.toggle.new_window.hint"))
        self._no_window_tracking_label.configure(
            text=tr("browser.msg.no_window_tracking_warning")
        )
        self.minimized_cb.configure(text=tr("browser.toggle.minimized"))
        self._section_lifecycle_label.configure(text=tr("browser.section.lifecycle"))
        self._section_lifecycle_hint.configure(
            text=tr("browser.section.lifecycle.hint")
        )
        self.close_on_offline_cb.configure(text=tr("browser.toggle.close_on_offline"))
        self._close_on_offline_hint.configure(text=tr("browser.toggle.close_on_offline.hint"))
        self.close_on_stop_cb.configure(text=tr("browser.toggle.close_on_stop"))
        self._close_on_stop_hint.configure(text=tr("browser.toggle.close_on_stop.hint"))
        self.close_off_topic_cb.configure(text=tr("browser.toggle.close_off_topic"))
        self._close_off_topic_hint.configure(text=tr("browser.toggle.close_off_topic.hint"))
        self.hide_from_taskbar_cb.configure(text=tr("browser.toggle.hide_taskbar"))
        self._hide_taskbar_hint.configure(text=tr("browser.toggle.hide_taskbar.hint"))
        self._section_advanced_label.configure(text=tr("browser.section.advanced"))
        self._section_advanced_hint.configure(
            text=tr("browser.section.advanced.hint")
        )
        self.app_mode_cb.configure(text=tr("browser.toggle.app_mode"))
        self._app_mode_hint.configure(text=tr("browser.toggle.app_mode.hint"))
        self._profile_title.configure(text=tr("browser.profile.title"))
        self._profile_desc.configure(text=tr("browser.profile.desc"))
        self.user_data_dir_cb.configure(text=tr("browser.profile.enable"))
        self.per_channel_profile_cb.configure(text=tr("browser.profile.per_channel"))
        self._profile_per_channel_hint.configure(text=tr("browser.profile.per_channel.hint"))
        self._tools_title.configure(text=tr("browser.tools.title"))
        self._tools_hint.configure(text=tr("browser.tools.hint"))
        self.apply_geometry_cb.configure(text=tr("browser.geometry.apply"))
        self.reset_geometry_btn.configure(text=tr("browser.geometry.reset"))
        self._x_label.configure(text=tr("browser.geometry.x"))
        self._y_label.configure(text=tr("browser.geometry.y"))
        self._w_label.configure(text=tr("browser.geometry.width"))
        self._h_label.configure(text=tr("browser.geometry.height"))
        _fit_button(self._cancel_btn, tr("browser.btn.cancel"), min_width=96)
        _fit_button(self._test_btn, tr("browser.btn.test"), min_width=100)
        _fit_button(
            self._test_close_btn, tr("browser.btn.test_close"), min_width=100
        )
        _fit_button(self._signin_btn, tr("browser.btn.signin"), min_width=120)
        _fit_button(
            self._save_btn, tr("browser.btn.save"), min_width=96, weight="bold"
        )
        _fit_button(
            self.reset_geometry_btn, tr("browser.geometry.reset"), min_width=72
        )
        self._no_isolation_label.configure(text=tr("browser.msg.no_isolation_warning"))
        self._user_tab_iso_banner.configure(
            text=tr("browser.banner.iso_features_auto_setup")
        )
        if self._compat_key is not None:
            key, color = self._compat_key
            self.compat_label.configure(text=tr(key), text_color=color)
        if self._message_key is not None:
            key, kwargs = self._message_key
            self.message_label.configure(text=tr(key, **kwargs))

    def _on_destroy(self, event: Any = None) -> None:
        if event is not None and event.widget is not self:
            return
        if getattr(self, "_unsub_i18n", None):
            self._unsub_i18n()
            self._unsub_i18n = None

    def _set_compat(self, key: str, color: str) -> None:
        self._compat_key = (key, color)
        self.compat_label.configure(text=tr(key), text_color=color)

    def _set_message(
        self, key: str | None, *, color: str = "#9aa0b4", **kwargs: Any
    ) -> None:
        if key is None:
            self._message_key = None
            self.message_label.configure(text="", text_color=color)
            return
        self._message_key = (key, dict(kwargs))
        self.message_label.configure(text=tr(key, **kwargs), text_color=color)

    def _refresh_enabled_state(self) -> None:
        state = "normal" if self.enabled_var.get() else "disabled"
        for widget in self._all_inputs:
            widget.configure(state=state)
        for widget in (
            self.new_window_cb,
            self.app_mode_cb,
            self.minimized_cb,
            self.close_on_offline_cb,
            self.close_on_stop_cb,
            self.close_off_topic_cb,
            self.hide_from_taskbar_cb,
            self.user_data_dir_cb,
            self.per_channel_profile_cb,
            self.apply_geometry_cb,
            self.reset_geometry_btn,
        ):
            widget.configure(state=state)
        # _refresh_user_data_dir_state runs the isolation-dependent gating
        # cascade (which also re-applies _refresh_geometry_state for the
        # x/y/w/h entries), so call it before path_change / app_mode_state.
        self._refresh_user_data_dir_state()
        self._on_path_change()
        self._refresh_app_mode_state()

    def _on_app_mode_toggle(self) -> None:
        if self.app_mode_var.get():
            self._new_window_before_app_mode = bool(self.new_window_var.get())
            self.new_window_var.set(True)
        else:
            self.new_window_var.set(self._new_window_before_app_mode)
        self._refresh_app_mode_state()
        self._refresh_user_tab_iso_banner()

    def _iso_features_explicitly_enabled(self) -> bool:
        """True iff the user has opted into any feature whose runtime
        implementation requires the dedicated-profile precondition.

        Mirrors :data:`config_manager._ISOLATION_DEPENDENT_FLAGS` so the
        banner/preview logic and the on-disk migration agree on which set
        of flags counts as "I want this feature to actually work".
        """
        return any(
            var.get()
            for var in (
                self.app_mode_var,
                self.minimized_var,
                self.hide_from_taskbar_var,
                self.close_on_offline_var,
                self.close_on_stop_var,
                self.close_off_topic_var,
            )
        )

    def _refresh_user_tab_iso_banner(self) -> None:
        """Show the User-tab banner exactly when Migration #2 would fire
        on Save — master switch on, dedicated profile off, at least one
        opt-in isolation feature ticked. ``pack(before=…)`` re-applies
        the row ordering each time so the banner stays at the top of the
        tab even after multiple toggle cycles.
        """
        show = (
            self.enabled_var.get()
            and not self.user_data_dir_enabled_var.get()
            and self._iso_features_explicitly_enabled()
        )
        if show:
            self._user_tab_iso_banner.pack(
                padx=16, pady=(12, 0), fill="x", before=self._section_open_label
            )
        else:
            self._user_tab_iso_banner.pack_forget()

    def _refresh_app_mode_state(self) -> None:
        if self.app_mode_var.get():
            self.new_window_var.set(True)

        state = "normal"
        if not self.enabled_var.get() or self.app_mode_var.get():
            state = "disabled"
        self.new_window_cb.configure(state=state)
        self._refresh_win32_management_state()

    def _win32_management_available(self) -> bool:
        """Dedicated profile + a launch mode that spawns a trackable HWND."""
        if not self.enabled_var.get():
            return False
        if not self.user_data_dir_enabled_var.get():
            return False
        return bool(self.app_mode_var.get() or self.new_window_var.get())

    def _refresh_win32_management_state(self) -> None:
        """Grey out HWND-dependent options when opening as a tab only."""
        if not self.enabled_var.get():
            self._no_window_tracking_label.pack_forget()
            return

        profile_enabled = self.user_data_dir_enabled_var.get()
        tracking_possible = self._win32_management_available()
        win32_state = "normal" if tracking_possible else "disabled"

        for widget in self._isolation_dependent_widgets:
            widget.configure(
                state=win32_state if profile_enabled else "disabled"
            )

        executable = self.path_entry.get().strip() or "chrome"
        if detect_browser_family(executable) == "firefox":
            self.app_mode_cb.configure(
                state="normal" if profile_enabled else "disabled"
            )

        if profile_enabled and not tracking_possible:
            self._no_window_tracking_label.pack(
                anchor="w", pady=(0, 4), before=self.minimized_cb
            )
        else:
            self._no_window_tracking_label.pack_forget()

        self._refresh_geometry_state()
        self._refresh_user_tab_iso_banner()

    # Widgets whose feature requires a dedicated profile to actually take
    # effect at runtime. notifier._open_with_browser_settings deliberately
    # skips the Win32 post-launch worker (and HWND tracking) when no
    # isolated user_data_dir is in use, so checking these in shared-profile
    # mode used to be a silent no-op — the exact "勾了卻沒實現" surprise
    # the user reported for hide_from_taskbar and apply_geometry. Greying
    # them out makes the dependency on dedicated profile visible upfront.
    #
    # ``app_mode_cb`` is here too because Chrome's master IPC silently
    # downgrades ``--app=URL`` to a regular tab when no dedicated profile
    # is in use, so the user clicking the checkbox in shared mode would
    # get exactly the same no-op surprise even though the CLI flag is
    # technically still sent.
    @property
    def _isolation_dependent_widgets(self) -> tuple[Any, ...]:
        return (
            self.app_mode_cb,
            self.minimized_cb,
            self.hide_from_taskbar_cb,
            self.apply_geometry_cb,
            self.close_on_offline_cb,
            self.close_on_stop_cb,
            self.close_off_topic_cb,
        )

    def _refresh_user_data_dir_state(self) -> None:
        if not self.enabled_var.get():
            self.user_data_dir_entry.configure(state="disabled")
            self.per_channel_profile_cb.configure(state="disabled")
            self._signin_btn.configure(state="disabled")
            self._no_isolation_label.grid_remove()
            # Master switch off already greys everything via _refresh_enabled_state.
            self._refresh_geometry_state()
            self._refresh_user_tab_iso_banner()
            return
        profile_enabled = self.user_data_dir_enabled_var.get()
        profile_state = "normal" if profile_enabled else "disabled"
        self.user_data_dir_entry.configure(state=profile_state)
        self.per_channel_profile_cb.configure(state=profile_state)
        self._signin_btn.configure(state=profile_state)

        # HWND-dependent gating: dedicated profile AND (app mode or new
        # window). Tab-only launches cannot be tracked or auto-closed.
        self._refresh_win32_management_state()

        # Show the no-isolation warning only when the user actively turned off
        # dedicated profile — that's the only state where Win32 management
        # and HWND tracking get downgraded to silent no-ops at runtime.
        if profile_enabled:
            self._no_isolation_label.grid_remove()
        else:
            self._no_isolation_label.grid()

        # Drive the User-tab banner from the same predicates we used above
        # so the two surfaces never disagree about whether Migration #2
        # would kick in on the next Save.
        self._refresh_user_tab_iso_banner()

    def _refresh_geometry_state(self) -> None:
        """Enable / disable X/Y/W/H entries based on apply_geometry, the
        dedicated-profile state, and the browser family.

        Geometry application happens in two places at runtime:
        ``--window-position`` / ``--window-size`` CLI flags (Chromium only)
        plus a follow-up ``SetWindowPos`` from the Win32 worker (also Chromium
        only, also gated on profile isolation). Without isolation the worker
        is skipped and Chrome's master IPC tends to drop the CLI flags, so we
        also drop the editability of the fields — anything else would be a
        UI lie.
        """
        if not self.enabled_var.get():
            for entry in (self.x_entry, self.y_entry, self.w_entry, self.h_entry):
                entry.configure(state="disabled")
            self.reset_geometry_btn.configure(state="disabled")
            return

        executable = self.path_entry.get().strip() or "chrome"
        is_chromium = detect_browser_family(executable) != "firefox"
        geometry_active = (
            is_chromium
            and self._win32_management_available()
            and self.apply_geometry_var.get()
        )
        entry_state = "normal" if geometry_active else "disabled"
        for entry in (self.x_entry, self.y_entry, self.w_entry, self.h_entry):
            entry.configure(state=entry_state)
        self.reset_geometry_btn.configure(state=entry_state)

    def _on_reset_geometry(self) -> None:
        """Reset X/Y/W/H to the system-default values."""
        defaults = {
            self.x_entry: 0,
            self.y_entry: 0,
            self.w_entry: 1280,
            self.h_entry: 720,
        }
        for entry, value in defaults.items():
            current_state = entry.cget("state")
            entry.configure(state="normal")
            entry.delete(0, "end")
            entry.insert(0, str(value))
            entry.configure(state=current_state)
        self._set_message("browser.msg.reset_done", color="#64b5f6")

    def _on_path_change(self, _event: Any = None) -> None:
        if not self.enabled_var.get():
            self._set_compat("browser.compat.disabled", "#ffb74d")
            return

        executable = self.path_entry.get().strip() or "chrome"
        family = detect_browser_family(executable)

        if family == "firefox":
            self._set_compat("browser.compat.firefox", "#ffb74d")
            for widget in self._family_dependent:
                widget.configure(state="disabled")
        elif family == "chromium":
            self._set_compat("browser.compat.chromium", "#81c784")
            # Chromium can run every advanced flag, but app_mode + the
            # geometry entries still require the dedicated-profile
            # precondition — defer that decision to the isolation gating
            # cascade so we have exactly one source of truth.
            self._refresh_user_data_dir_state()
        else:
            self._set_compat("browser.compat.unknown", "#90caf9")
            self._refresh_user_data_dir_state()
        self._refresh_app_mode_state()

    def _collect(self) -> dict[str, Any] | None:
        apply_geometry = bool(self.apply_geometry_var.get())

        try:
            x = int(self.x_entry.get())
            y = int(self.y_entry.get())
            width = int(self.w_entry.get())
            height = int(self.h_entry.get())
        except ValueError:
            if apply_geometry:
                self._set_message("browser.msg.invalid_int", color="#ef5350")
                return None
            # When apply_geometry is off the fields aren't used, so silently
            # fall back to defaults so the user can save without filling them.
            x, y, width, height = 0, 0, 1280, 720

        if apply_geometry and (width < 100 or height < 100):
            self._set_message("browser.msg.min_size", color="#ef5350")
            return None

        browser_path = self.path_entry.get().strip() or "chrome"

        profile_enabled = bool(self.user_data_dir_enabled_var.get())
        if profile_enabled:
            user_data_dir = self.user_data_dir_entry.get().strip()
            if not user_data_dir:
                self._set_message("browser.msg.empty_profile", color="#ef5350")
                return None
        else:
            user_data_dir = ""
        per_channel_profile = (
            bool(self.per_channel_profile_var.get()) if profile_enabled else False
        )

        app_mode = bool(self.app_mode_var.get())
        new_window = True if app_mode else bool(self.new_window_var.get())

        return {
            "enabled": bool(self.enabled_var.get()),
            "browser_path": browser_path,
            "new_window": new_window,
            "app_mode": app_mode,
            "apply_geometry": apply_geometry,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "minimized": bool(self.minimized_var.get()),
            "user_data_dir": user_data_dir,
            "per_channel_profile": per_channel_profile,
            "close_on_offline": bool(self.close_on_offline_var.get()),
            "close_on_stop": bool(self.close_on_stop_var.get()),
            "close_off_topic_pages": bool(self.close_off_topic_var.get()),
            "hide_from_taskbar": bool(self.hide_from_taskbar_var.get()),
        }

    def _snapshot_browser_settings(self) -> dict[str, Any]:
        app_mode = bool(self.app_mode_var.get())
        profile_enabled = bool(self.user_data_dir_enabled_var.get())
        return {
            "enabled": bool(self.enabled_var.get()),
            "browser_path": self.path_entry.get().strip(),
            "new_window": True if app_mode else bool(self.new_window_var.get()),
            "app_mode": app_mode,
            "apply_geometry": bool(self.apply_geometry_var.get()),
            "x": self.x_entry.get().strip(),
            "y": self.y_entry.get().strip(),
            "width": self.w_entry.get().strip(),
            "height": self.h_entry.get().strip(),
            "minimized": bool(self.minimized_var.get()),
            "user_data_dir_enabled": profile_enabled,
            "user_data_dir": self.user_data_dir_entry.get().strip(),
            "per_channel_profile": (
                bool(self.per_channel_profile_var.get()) if profile_enabled else False
            ),
            "close_on_offline": bool(self.close_on_offline_var.get()),
            "close_on_stop": bool(self.close_on_stop_var.get()),
            "close_off_topic_pages": bool(self.close_off_topic_var.get()),
            "hide_from_taskbar": bool(self.hide_from_taskbar_var.get()),
        }

    def _has_unsaved_changes(self) -> bool:
        return self._snapshot_browser_settings() != self._initial_snapshot

    def _on_cancel(self) -> None:
        # Route through the same unsaved-changes prompt the [X] button uses;
        # without this the Cancel button silently discards edits while the
        # close-button asks for confirmation, which violates user
        # expectations of dialog parity.
        self._on_window_close()

    def _on_window_close(self) -> None:
        if not self._has_unsaved_changes():
            self.destroy()
            return

        from tkinter import messagebox

        choice = messagebox.askyesnocancel(
            tr("browser.close.title"),
            tr("browser.close.body"),
            parent=self,
        )
        if choice is None:
            return
        if choice:
            self._on_save()
            return
        self.destroy()

    def _browser_test_url(self) -> str:
        html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Hello Streamer Browser Test</title>
  <style>
    :root { color-scheme: dark; font-family: Segoe UI, sans-serif; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #111827;
      color: #f8fafc;
    }
    main {
      width: min(560px, calc(100vw - 48px));
      padding: 32px;
      border: 1px solid #334155;
      border-radius: 8px;
      background: #1f2937;
    }
    h1 { margin: 0 0 12px; font-size: 28px; }
    p { margin: 8px 0 0; color: #cbd5e1; line-height: 1.55; }
    code { color: #93c5fd; }
  </style>
</head>
<body>
  <main>
    <h1>Hello Streamer Browser Test</h1>
    <p>This local page is used to verify browser launch settings.</p>
    <p>When App Mode is enabled, this should open without the browser address bar.</p>
    <p><code id="stamp"></code></p>
  </main>
  <script>
    document.getElementById("stamp").textContent =
      new Date().toLocaleString();
  </script>
</body>
</html>
"""
        try:
            path = Path(tempfile.gettempdir()) / "hello_streamer_browser_test.html"
            path.write_text(html, encoding="utf-8")
            return path.as_uri()
        except OSError:
            logger.exception("Failed to write browser test page")
            return "data:text/html;charset=utf-8," + quote(html)

    def _browser_test_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        test_settings = dict(data)
        if test_settings.get("app_mode"):
            test_settings["user_data_dir"] = str(self._test_profile_dir)
            test_settings["per_channel_profile"] = False
        return test_settings

    def _on_test(self) -> None:
        data = self._collect()
        if data is None:
            return
        test_url = self._browser_test_url()
        self._last_test_url = test_url
        test_settings = self._browser_test_settings(data)
        self._set_message("browser.msg.test_opened", color="#64b5f6")
        open_url(test_url, test_settings if test_settings["enabled"] else None)

    def _on_test_close(self) -> None:
        test_url = self._last_test_url or self._browser_test_url()
        closed = close_browser_window_for_url(
            test_url,
            title_keywords=["Hello Streamer Browser Test"],
        )
        self._set_message("browser.msg.test_closed", color="#64b5f6", count=closed)

    def _on_signin(self) -> None:
        """Bootstrap cookies into the dedicated profile via a manual sign-in.

        Reads the *currently typed* profile path (not the saved one) so the
        user can try out a new path before committing to it. Bypasses
        ``_collect()`` validation deliberately — sign-in is read-only with
        respect to settings; we don't want minor geometry errors to block
        the cookie bootstrap.
        """
        if not self.user_data_dir_enabled_var.get():
            self._set_message("browser.msg.signin_no_path", color="#ef5350")
            return
        path = self.user_data_dir_entry.get().strip()
        if not path:
            self._set_message("browser.msg.signin_no_path", color="#ef5350")
            return
        if self.per_channel_profile_var.get():
            self._set_message(
                "browser.msg.signin_per_channel",
                color="#ffb74d",
            )
            return
        browser_path = self.path_entry.get().strip() or "chrome"
        if open_browser_for_signin(path, browser_path=browser_path):
            self._set_message("browser.msg.signin_opened", color="#64b5f6")
        else:
            self._set_message("browser.msg.signin_failed", color="#ef5350")

    def _on_save(self) -> None:
        data = self._collect()
        if data is None:
            return

        # Pre-apply Migration #2 so the dialog round-trip is idempotent and
        # the user sees what's about to be saved before the dialog closes.
        # Without this preview, ticking (e.g.) ``hide_from_taskbar`` while
        # leaving the profile disabled lets ``config_manager.save`` rewrite
        # ``user_data_dir`` and ``per_channel_profile`` behind the user's
        # back — they reopen the dialog and find settings they never set.
        before_dir = (data.get("user_data_dir") or "").strip()
        before_per_channel = bool(data.get("per_channel_profile"))
        config_manager._migrate_browser_settings(data)
        after_dir = (data.get("user_data_dir") or "").strip()
        after_per_channel = bool(data.get("per_channel_profile"))

        migration_auto_filled = before_dir == "" and after_dir != ""
        if migration_auto_filled:
            # Update the dialog vars/widgets so the user can review the
            # auto-filled path before the second Save commits it. This is
            # deliberately a two-click flow — auto-saving on top of an
            # invisible config rewrite is what the audit flagged as the
            # consistency bug we're fixing here.
            self.user_data_dir_enabled_var.set(True)
            self.user_data_dir_entry.configure(state="normal")
            self.user_data_dir_entry.delete(0, "end")
            self.user_data_dir_entry.insert(0, after_dir)
            self.per_channel_profile_var.set(after_per_channel)
            self._refresh_user_data_dir_state()
            self._set_message(
                "browser.msg.migration_applied", color="#ffb74d", path=after_dir
            )
            return

        # No migration kicked in (or the user already saw it last click and
        # is now confirming) — proceed with the normal save flow.
        if before_per_channel != after_per_channel:
            self.per_channel_profile_var.set(after_per_channel)

        self.result = data
        self.destroy()

