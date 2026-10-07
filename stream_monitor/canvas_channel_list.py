"""Canvas-rendered channel list.

The original channel list moved a tree of CustomTkinter child windows inside
the scroll viewport.  On Windows that makes a scrollbar drag a compositor
operation: the old child surfaces can remain visible while the new positions
are being painted.  This view keeps the channel state objects off-screen and
draws the visible list as one Tk Canvas surface.  Scrolling therefore moves
Canvas items, not native child windows.
"""

from __future__ import annotations

import sys
import tkinter as tk
from typing import Any, Callable

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

from stream_monitor import i18n
from stream_monitor.app_ui import (
    _CLR_ACCENT,
    _CLR_CARD,
    _CLR_CARD_DISABLED,
    _CLR_DELETE_HOVER,
    _CLR_TEXT_DISABLED,
    _CLR_TWITCH,
    _CLR_YOUTUBE,
    _font,
)
from stream_monitor.channel_policy import (
    CHANNEL_MODE_MONITOR,
    CHANNEL_MODE_NOTIFY,
    CHANNEL_MODE_TRIGGER,
    channel_mode_for,
)
from stream_monitor.channel_reorder import LONG_PRESS_CANCEL_PX, LONG_PRESS_MS
from stream_monitor.channel_row import ChannelRow, is_live_state
from stream_monitor.i18n import tr
from stream_monitor.monitor import ChannelStatus

ROW_BODY_HEIGHT = 50
ROW_SLOT_HEIGHT = 52
ROW_CONTROL_HEIGHT = 30
ROW_CONTROL_HALF = ROW_CONTROL_HEIGHT // 2
ROW_CONTROL_GAP = 8
_SCROLL_UNITS_PER_WHEEL = 3

OnDelete = Callable[[dict[str, str]], None]
OnMove = Callable[[dict[str, str], int], None]
OnReorderBegin = Callable[["CanvasChannelRowAdapter", int], None]


class _CanvasTooltip:
    """One delegated tooltip for Canvas items.

    Canvas items are not Tk widgets, so the old per-button ``_Tooltip``
    binding cannot be attached directly.  A single controller keeps the same
    delayed hover behaviour without creating one binding object per redraw.
    """

    _DELAY_MS = 400

    def __init__(self, canvas: tk.Canvas) -> None:
        self._canvas = canvas
        self._after_id: str | None = None
        self._pending: tuple[str, int, int] | None = None
        self._window: tk.Toplevel | None = None

    def schedule(self, text: str, x_root: int, y_root: int) -> None:
        if not text:
            self.hide()
            return
        pending = (text, x_root, y_root)
        if self._pending is not None and self._pending[0] == text:
            return
        self._cancel_timer()
        self.hide()
        self._pending = pending
        self._after_id = self._canvas.after(self._DELAY_MS, self._show)

    def _show(self) -> None:
        self._after_id = None
        pending = self._pending
        if pending is None or not self._canvas.winfo_exists():
            return
        text, x_root, y_root = pending
        window = tk.Toplevel(self._canvas)
        window.wm_overrideredirect(True)
        window.wm_attributes("-topmost", True)
        label = tk.Label(
            window,
            text=text,
            background="#2a2a3e",
            foreground="#e0e0ee",
            relief="solid",
            borderwidth=1,
            highlightbackground="#555566",
            font=("Microsoft JhengHei UI", 10),
            padx=8,
            pady=4,
        )
        label.pack()
        window.update_idletasks()
        window.wm_geometry(f"+{x_root + 10}+{y_root + 20}")
        self._window = window

    def _cancel_timer(self) -> None:
        if self._after_id is not None:
            try:
                self._canvas.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def hide(self) -> None:
        self._cancel_timer()
        self._pending = None
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None


class CanvasChannelRowAdapter:
    """Canvas-facing protocol wrapper around the existing stateful ChannelRow.

    ``ChannelRow`` remains the canonical holder for status/cache/open-url
    behaviour, but it is never placed in the scrolling viewport.  The adapter
    exposes the small row protocol consumed by the monitor/event bridge and
    supplies geometry for ``ChannelReorderMode``.
    """

    def __init__(self, owner: "CanvasChannelList", raw: ChannelRow) -> None:
        self.owner = owner
        self.raw = raw
        self._can_move_up = False
        self._can_move_down = False
        self._reorder_highlight = False
        self._truncated_display_name = ""

    @property
    def channel(self) -> dict[str, str]:
        return self.raw.channel

    @property
    def key(self) -> str:
        return self.raw.key

    @property
    def _status_state(self) -> str | None:
        return self.raw._status_state

    @property
    def _ended_at_source(self) -> str:
        return self.raw._ended_at_source

    def set_status(
        self,
        status: bool | str | ChannelStatus | None,
        *,
        pending: bool = False,
    ) -> None:
        self.raw.set_status(status, pending=pending)
        self.owner.request_redraw()

    def status_snapshot(self) -> ChannelStatus | None:
        return self.raw.status_snapshot()

    def set_display_name(self, display_name: str | None) -> bool:
        changed = self.raw.set_display_name(display_name)
        if changed:
            self.owner.request_redraw()
        return changed

    def refresh_elapsed_display(self) -> None:
        self.raw.refresh_elapsed_display()
        self.owner.request_redraw()

    def set_move_state(self, can_move_up: bool, can_move_down: bool) -> None:
        self._can_move_up = can_move_up
        self._can_move_down = can_move_down
        # Keep the old row's controls coherent for code that still inspects
        # the state holder, while the visible controls are Canvas drawings.
        self.raw.set_move_state(can_move_up, can_move_down)
        self.owner.request_redraw()

    def set_reorder_highlight(self, active: bool) -> None:
        self._reorder_highlight = active
        self.owner.request_redraw()

    def cancel_reorder_drag(self) -> None:
        self.raw.cancel_reorder_drag()

    def toggle(self) -> None:
        self.raw._on_toggle_click()
        self.owner.request_redraw()

    def toggle_monitor_only(self) -> None:
        self.raw._on_monitor_only_click()
        self.owner.request_redraw()

    def open_channel_page(self) -> None:
        self.raw._open_channel_page()

    def open_current_page(self) -> None:
        self.raw._open_current_page()

    def open_active_page(self) -> None:
        self.raw._open_active_page()

    def destroy(self) -> None:
        self.raw.destroy()

    # ChannelReorderMode uses these two methods through its row geometry
    # helpers.  Return the position of the Canvas item in screen coordinates.
    def winfo_rooty(self) -> int:
        return self.owner.row_root_y(self)

    def winfo_height(self) -> int:
        return ROW_BODY_HEIGHT


class CanvasChannelList(ctk.CTkFrame):
    """A single-surface, vertically scrollable channel list."""

    def __init__(
        self,
        master: Any,
        *,
        fg_color: Any = _CLR_ACCENT,
        corner_radius: int = 0,
        scrollbar_button_color: Any = None,
        scrollbar_button_hover_color: Any = None,
        on_delete: OnDelete | None = None,
        on_move: OnMove | None = None,
        on_reorder_begin: OnReorderBegin | None = None,
    ) -> None:
        super().__init__(master, fg_color=fg_color, corner_radius=corner_radius)
        self._rows: list[CanvasChannelRowAdapter] = []
        self._visual_order: list[int] = []
        self._redraw_after: str | None = None
        self._pending_drag_row: CanvasChannelRowAdapter | None = None
        self._pending_drag_y_root = 0
        self._pending_drag_after: str | None = None
        self._on_delete = on_delete
        self._on_move = on_move
        self._on_reorder_begin = on_reorder_begin
        self._fonts: dict[tuple[int, str], Any] = {}
        self._shape_images: dict[tuple[int, int, int, str, str, int], Any] = {}
        self._hover_group: str | None = None
        self._hover_items: dict[str, list[tuple[int, Any, Any]]] = {}
        self._cursor_mode = ""

        surface_color = self._apply_appearance_mode(self.cget("fg_color"))
        if surface_color == "transparent":
            surface_color = self._apply_appearance_mode(self.cget("bg_color"))

        self.canvas = tk.Canvas(
            self,
            background=surface_color,
            bd=0,
            highlightthickness=0,
            relief="flat",
            takefocus=True,
        )
        self._tooltip = _CanvasTooltip(self.canvas)
        self._parent_canvas = self.canvas
        self._scrollbar = ctk.CTkScrollbar(
            self,
            orientation="vertical",
            width=10,
            border_spacing=4,
            corner_radius=8,
            command=self.canvas.yview,
            button_color=scrollbar_button_color,
            button_hover_color=scrollbar_button_hover_color,
        )

        # This host is deliberately never geometry-managed.  It owns the
        # legacy ChannelRow state widgets, keeping their business logic alive
        # without putting any native child window into the scrolling surface.
        self.state_host = tk.Frame(self, width=1, height=1)
        self.state_host.pack_propagate(False)

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=(0, 2))
        # Interior padding between list content and the thin scrollbar track.
        self._scrollbar.grid(row=0, column=1, sticky="ns", padx=(2, 4), pady=4)

        self.canvas.configure(yscrollcommand=self._on_canvas_scroll)
        self.canvas.bind("<Configure>", self._on_canvas_configure, add="+")
        self.canvas.bind("<Button-1>", self._on_button_press, add="+")
        self.canvas.bind("<ButtonRelease-1>", self._on_button_release, add="+")
        self.canvas.bind("<B1-Motion>", self._on_pointer_motion, add="+")
        self.canvas.bind("<Motion>", self._on_pointer_motion, add="+")
        self.canvas.bind("<Leave>", self._on_canvas_leave, add="+")
        self.canvas.bind("<MouseWheel>", self._on_mousewheel, add="+")
        self.canvas.bind("<Button-4>", self._on_mousewheel, add="+")
        self.canvas.bind("<Button-5>", self._on_mousewheel, add="+")
        self._unsub_i18n = i18n.subscribe(self._on_language_changed)
        tk.Misc.bind(self, "<Destroy>", self._on_destroy, add="+")

    @property
    def rows(self) -> list[CanvasChannelRowAdapter]:
        return self._rows

    def add_state_row(self, raw: ChannelRow) -> CanvasChannelRowAdapter:
        """Create an adapter without mutating the caller's row collection.

        App owns the canonical ``_channel_rows`` list and calls ``set_rows``
        after appending.  Mutating ``self._rows`` here would alias that same
        list after the first set and duplicate every subsequent channel.
        """
        adapter = CanvasChannelRowAdapter(self, raw)
        return adapter

    def set_rows(self, rows: list[CanvasChannelRowAdapter]) -> None:
        self._rows = rows
        self._visual_order = list(range(len(rows)))
        self.redraw_all()

    def set_visual_order(self, order: list[int]) -> None:
        if sorted(order) != list(range(len(self._rows))):
            order = list(range(len(self._rows)))
        if order == self._visual_order:
            return
        self._visual_order = list(order)
        self.redraw_all()

    def row_root_y(self, row: CanvasChannelRowAdapter) -> int:
        try:
            visual_index = self._visual_order.index(self._rows.index(row))
            offset = int(self.canvas.canvasy(0))
            return self.canvas.winfo_rooty() + visual_index * ROW_SLOT_HEIGHT + 2 - offset
        except (ValueError, tk.TclError):
            return self.canvas.winfo_rooty()

    def request_redraw(self) -> None:
        if self._redraw_after is not None:
            return
        try:
            self._redraw_after = self.after_idle(self._flush_redraw)
        except tk.TclError:
            self._redraw_after = None

    def _flush_redraw(self) -> None:
        self._redraw_after = None
        if self.winfo_exists():
            self.redraw_all()

    def redraw_all(self) -> None:
        if not self.winfo_exists():
            return
        self._tooltip.hide()
        self._hover_group = None
        self._hover_items.clear()
        self._cursor_mode = ""
        self.canvas.delete("all")
        width = max(1, self.canvas.winfo_width())
        content_height = max(1, len(self._visual_order) * ROW_SLOT_HEIGHT + 4)
        self.canvas.configure(scrollregion=(0, 0, width, content_height))
        for visual_index, row_index in enumerate(self._visual_order):
            self._draw_row(self._rows[row_index], visual_index, width)

    def _draw_row(
        self, row: CanvasChannelRowAdapter, visual_index: int, width: int
    ) -> None:
        channel = row.channel
        enabled = bool(channel.get("enabled", True))
        channel_mode = channel_mode_for(channel)
        active_mode = channel_mode if enabled else CHANNEL_MODE_TRIGGER
        top = visual_index * ROW_SLOT_HEIGHT + 2
        left = 6
        right = max(left + 320, width - 6)
        card_color = _CLR_CARD if enabled else _CLR_CARD_DISABLED
        tag = f"row:{id(row)}"
        mid_y = top + ROW_BODY_HEIGHT // 2

        self._rounded_rectangle(
            left,
            top,
            right,
            top + ROW_BODY_HEIGHT,
            radius=8,
            fill=card_color,
            outline="#2196F3" if row._reorder_highlight else card_color,
            width=2 if row._reorder_highlight else 1,
            tags=(tag,),
        )

        # Left drag handle only (▲/▼ arrow controls removed).
        handle_y = mid_y
        if enabled:
            self._draw_hover_slot(22, handle_y, tag, "drag", card_color, "#243052")
        self._draw_text(
            22,
            handle_y,
            "⠿",
            fill="#d8d8e5" if enabled else _CLR_TEXT_DISABLED,
            anchor="center",
            size=14,
            tags=(tag, "action:drag"),
        )

        platform = str(channel.get("platform", "")).upper()
        platform_key = platform.strip().lower()
        platform_color = _CLR_TWITCH if platform_key == "twitch" else _CLR_YOUTUBE
        platform_hover = f"hover:{tag}:platform"
        control_top = top + (ROW_BODY_HEIGHT - ROW_CONTROL_HEIGHT) // 2
        control_center_y = control_top + ROW_CONTROL_HALF
        platform_item, platform_image = self._rounded_rectangle(
            48,
            control_top,
            126,
            control_top + ROW_CONTROL_HEIGHT,
            radius=6,
            fill=platform_color if enabled else "#2a2a3a",
            outline=platform_color if enabled else "#2a2a3a",
            tags=(tag, "action:platform", platform_hover),
        )
        platform_hover_color = (
            "#7b35d1" if platform_key == "twitch" else "#cc0000"
        )
        platform_hover_image = self._rounded_image(
            78,
            ROW_CONTROL_HEIGHT,
            radius=6,
            fill=platform_hover_color,
            outline=platform_hover_color,
            width=1,
        )
        self._register_hover(
            platform_hover, platform_item, platform_image, platform_hover_image
        )
        self._draw_text(
            87,
            control_center_y,
            platform,
            fill="white" if enabled else _CLR_TEXT_DISABLED,
            anchor="center",
            size=11,
            weight="bold",
            tags=(tag, "action:platform"),
        )

        channel_id = str(channel.get("name", ""))
        display_name = (channel.get("display_name") or "").strip() or channel_id
        action_delete_x = right - 27
        action_toggle_x = right - 65
        action_monitor_x = right - 103
        action_link_x = right - 141
        # Measure the status text with the exact Canvas font used below.  The
        # old shared CTk label width was just tight enough that the bullet in
        # "● LIVE" could touch/clip at the right edge on some DPI settings.
        status_font = self._font_for(12, "bold")
        status_width = max(
            84,
            max(
                int(status_font.measure(tr(key))) + 28
                for key in (
                    "status.row.placeholder",
                    "status.row.paused",
                    "status.row.upcoming",
                    "status.row.live",
                    "status.row.offline",
                )
            ),
        )
        # The link button is an opaque Canvas image spanning +/-16px around
        # its center.  Keep a real gap so it can never paint over the status
        # badge (the old -13px boundary overlapped it by 3px).
        status_right = action_link_x - ROW_CONTROL_HALF - ROW_CONTROL_GAP
        status_left = status_right - status_width
        time_right = status_left - 10
        # Reserve a bounded time/status column before measuring the channel
        # name.  Canvas text is not clipped by a widget boundary, so both
        # strings must be explicitly fitted before they are painted.
        time_slot_width = min(130, max(0, time_right - 140 - 12))
        name_right = max(140, time_right - time_slot_width - 12)
        name_width = max(1, name_right - 140)
        name = self._truncate_to_width(
            display_name,
            self._font_for(15, "bold"),
            name_width,
        )
        name_truncated = name != display_name
        id_text = (
            self._truncate_to_width(
                tr("channel.id.prefix", id=channel_id),
                self._font_for(11),
                name_width,
            )
            if display_name != channel_id
            else ""
        )
        name_color = ("#d8d8e5" if enabled else _CLR_TEXT_DISABLED)
        name_tags: tuple[str, ...] = (tag,)
        if name_truncated:
            name_tags = (tag, "action:name")
        name_y = mid_y - 8 if id_text else mid_y
        self._draw_text(
            140,
            name_y,
            name,
            fill=name_color,
            anchor="w",
            size=15,
            weight="bold",
            tags=name_tags,
        )
        if id_text:
            self._draw_text(
                140,
                mid_y + 10,
                id_text,
                fill="#c0c6d8" if enabled else _CLR_TEXT_DISABLED,
                anchor="w",
                size=11,
                tags=(tag,),
            )
        row._truncated_display_name = display_name if name_truncated else ""

        time_text, status_text, status_fill, status_text_color, status_action = (
            self._status_paint(row, enabled)
        )
        time_text = self._truncate_to_width(
            time_text,
            self._font_for(11, "bold"),
            time_slot_width,
        )
        self._draw_text(
            time_right,
            mid_y,
            time_text,
            fill="#aab3d5" if enabled else _CLR_TEXT_DISABLED,
            anchor="e",
            size=11,
            weight="bold",
            tags=(tag,),
        )
        self._rounded_rectangle(
            status_left,
            control_top,
            status_right,
            control_top + ROW_CONTROL_HEIGHT,
            radius=6,
            fill=status_fill,
            outline=status_fill,
            tags=(tag, f"action:{status_action}"),
        )
        self._draw_text(
            (status_left + status_right) // 2,
            control_center_y,
            status_text,
            fill=status_text_color,
            anchor="center",
            size=12,
            weight="bold",
            tags=(tag, f"action:{status_action}"),
        )

        self._draw_button(
            action_delete_x,
            mid_y,
            "✕",
            tag,
            "delete",
            enabled=enabled,
            hover_fill=_CLR_DELETE_HOVER,
            border=None,
            size=14,
        )
        toggle_text = "⏸" if enabled else "▶"
        mode_active = active_mode in {CHANNEL_MODE_MONITOR, CHANNEL_MODE_NOTIFY}
        toggle_border = "#1565c0" if mode_active else "#3c4566"
        toggle_hover = "#1976d2" if mode_active else "#2f4c73"
        self._draw_button(
            action_toggle_x,
            mid_y,
            toggle_text,
            tag,
            "toggle",
            enabled=True,
            hover_fill=toggle_hover,
            border=toggle_border,
            size=13,
        )
        if active_mode == CHANNEL_MODE_MONITOR:
            monitor_fill = "#1565c0"
            monitor_border = "#1565c0"
            monitor_text_color = "white"
            monitor_hover = "#1976d2"
        elif active_mode == CHANNEL_MODE_NOTIFY:
            monitor_fill = "#7c3aed"
            monitor_border = "#a78bfa"
            monitor_text_color = "white"
            monitor_hover = "#8b5cf6"
        else:
            monitor_fill = card_color
            monitor_border = "#526a99" if enabled else "#4b5c7d"
            monitor_text_color = "#d8d8e5" if enabled else _CLR_TEXT_DISABLED
            monitor_hover = "#2f4c73" if enabled else "#2b3c5a"
        self._draw_button(
            action_monitor_x,
            mid_y,
            "👁",
            tag,
            "monitor",
            enabled=True,
            hover_fill=monitor_hover,
            border=monitor_border,
            fill=monitor_fill,
            text_color=monitor_text_color,
            size=13,
        )
        self._draw_button(
            action_link_x,
            mid_y,
            "🔗",
            tag,
            "link",
            enabled=True,
            hover_fill="#1769aa",
            border=None,
            size=12,
        )

    def _status_paint(
        self, row: CanvasChannelRowAdapter, enabled: bool
    ) -> tuple[str, str, str, str, str]:
        if not enabled:
            return "", tr("status.row.paused"), "", _CLR_TEXT_DISABLED, "status"
        state = row.raw._status_state
        if state is None:
            return "", tr("status.row.placeholder"), "", "#666677", "status"
        time_text = row.raw._compose_time_label_text()
        if state == "upcoming":
            return time_text, tr("status.row.upcoming"), "#e65100", "white", "status"
        if state == "live" or is_live_state(state):
            return time_text, tr("status.row.live"), "#1b5e20", "white", "status"
        return time_text, tr("status.row.offline"), "", "#999999", "status"

    def _draw_text(
        self,
        x: int,
        y: int,
        text: str,
        *,
        fill: str,
        anchor: str,
        size: int,
        weight: str = "normal",
        tags: tuple[str, ...],
    ) -> int:
        font = self._font_for(size, weight)
        return self.canvas.create_text(
            x,
            y,
            text=text,
            fill=fill,
            anchor=anchor,
            font=font,
            tags=tags,
        )

    def _font_for(self, size: int, weight: str = "normal") -> Any:
        font_key = (size, weight)
        font = self._fonts.get(font_key)
        if font is None:
            font = _font(size, weight)
            self._fonts[font_key] = font
        return font

    @staticmethod
    def _truncate_to_width(text: str, font: Any, max_width: int) -> str:
        """Fit text to the measured pixel width of its actual Canvas column."""
        if max_width <= 0:
            return ""
        if int(font.measure(text)) <= max_width:
            return text
        ellipsis = "…"
        if int(font.measure(ellipsis)) > max_width:
            return ""
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if int(font.measure(text[:middle] + ellipsis)) <= max_width:
                low = middle
            else:
                high = middle - 1
        return text[:low] + ellipsis

    def _rounded_rectangle(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        radius: int,
        fill: str,
        outline: str,
        width: int = 1,
        tags: tuple[str, ...],
    ) -> tuple[int, Any]:
        """Draw an anti-aliased, genuinely rounded Canvas image.

        Tk Canvas has no native rounded rectangle.  A low-sample polygon is
        visibly chamfered (the previous implementation looked octagonal), so
        render the primitive at 4x with Pillow and downsample it.  The result
        stays one Canvas surface and has smooth corners like the CTk buttons.
        """
        image = self._rounded_image(
            max(1, x2 - x1),
            max(1, y2 - y1),
            radius=radius,
            fill=fill,
            outline=outline,
            width=width,
        )
        item_id = self.canvas.create_image(
            x1,
            y1,
            anchor="nw",
            image=image,
            tags=tags,
        )
        return item_id, image

    def _rounded_image(
        self,
        image_width: int,
        image_height: int,
        *,
        radius: int,
        fill: str,
        outline: str,
        width: int = 1,
    ) -> Any:
        """Return a cached, anti-aliased rounded shape for hover/state swaps."""
        image_width = max(1, int(image_width))
        image_height = max(1, int(image_height))
        radius = max(0, min(int(radius), image_width // 2, image_height // 2))
        width = max(0, int(width))
        key = (image_width, image_height, radius, fill or "", outline or "", width)
        cached = self._shape_images.get(key)
        if cached is not None:
            return cached

        scale = 4
        image = Image.new(
            "RGBA", (image_width * scale, image_height * scale), (0, 0, 0, 0)
        )
        drawer = ImageDraw.Draw(image)
        box = (0, 0, image.width - 1, image.height - 1)
        fill_color = fill if fill and fill != "transparent" else None
        outline_color = outline if outline and outline != "transparent" else None
        drawer.rounded_rectangle(
            box,
            radius=radius * scale,
            fill=fill_color,
            outline=outline_color,
            width=width * scale if outline_color and width else 1,
        )
        image = image.resize((image_width, image_height), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(image, master=self.canvas)
        self._shape_images[key] = photo
        return photo

    def _draw_button(
        self,
        x: int,
        y: int,
        text: str,
        row_tag: str,
        action: str,
        *,
        enabled: bool,
        hover_fill: str,
        border: str | None,
        size: int,
        fill: str | None = None,
        text_color: str | None = None,
    ) -> None:
        button_fill = fill if fill is not None else _CLR_CARD
        outline = border if border is not None else button_fill
        hover_group = f"hover:{row_tag}:{action}"
        x1, y1 = x - 16, y - ROW_CONTROL_HALF
        x2, y2 = x + 16, y + ROW_CONTROL_HALF
        item_id, base_image = self._rounded_rectangle(
            x1,
            y1,
            x2,
            y2,
            radius=6,
            fill=button_fill,
            outline=outline,
            width=1 if border else 0,
            tags=(row_tag, f"action:{action}", hover_group),
        )
        hover_image = self._rounded_image(
            x2 - x1,
            y2 - y1,
            radius=6,
            fill=hover_fill,
            outline=outline,
            width=1 if border else 0,
        )
        self._register_hover(hover_group, item_id, base_image, hover_image)
        self._draw_text(
            x,
            y,
            text,
            fill=text_color or ("#d8d8e5" if enabled else _CLR_TEXT_DISABLED),
            anchor="center",
            size=size,
            tags=(row_tag, f"action:{action}"),
        )

    def _draw_hover_slot(
        self,
        x: int,
        y: int,
        row_tag: str,
        action: str,
        base_fill: str,
        hover_fill: str,
    ) -> None:
        hover_group = f"hover:{row_tag}:{action}"
        x1, y1 = x - 15, y - 9
        x2, y2 = x + 15, y + 9
        item_id, base_image = self._rounded_rectangle(
            x1,
            y1,
            x2,
            y2,
            radius=4,
            fill=base_fill,
            outline=base_fill,
            width=0,
            tags=(row_tag, f"action:{action}", hover_group),
        )
        hover_image = self._rounded_image(
            x2 - x1,
            y2 - y1,
            radius=4,
            fill=hover_fill,
            outline=hover_fill,
            width=0,
        )
        self._register_hover(hover_group, item_id, base_image, hover_image)

    def _register_hover(
        self, group: str, item_id: int, base_image: Any, hover_image: Any
    ) -> None:
        self._hover_items.setdefault(group, []).append(
            (item_id, base_image, hover_image)
        )

    def _set_hover_group(self, group: str | None) -> None:
        if group == self._hover_group:
            return
        if self._hover_group is not None:
            for item_id, base_image, _hover_image in self._hover_items.get(
                self._hover_group, []
            ):
                self.canvas.itemconfigure(item_id, image=base_image)
        self._hover_group = group
        if group is not None:
            for item_id, _base_image, hover_image in self._hover_items.get(group, []):
                self.canvas.itemconfigure(item_id, image=hover_image)

    def _row_and_action(self) -> tuple[CanvasChannelRowAdapter | None, str | None]:
        tags = self.canvas.gettags("current")
        row = None
        action = None
        for tag in tags:
            if tag.startswith("row:"):
                try:
                    row_id = int(tag[4:])
                except ValueError:
                    continue
                row = next((item for item in self._rows if id(item) == row_id), None)
            elif tag.startswith("action:"):
                action = tag[7:]
        return row, action

    def _on_button_press(self, event: Any) -> str | None:
        self._tooltip.hide()
        row, action = self._row_and_action()
        if row is None or action is None:
            return None
        if action == "drag":
            self._arm_drag_pending(row, int(event.y_root))
        elif action == "delete" and self._on_delete is not None:
            self._on_delete(row.channel)
        elif action == "toggle":
            row.toggle()
        elif action == "monitor":
            row.toggle_monitor_only()
        elif action == "link":
            row.open_current_page()
        elif action == "platform":
            row.open_channel_page()
        elif action == "status":
            row.open_active_page()
        elif action == "name":
            return None
        else:
            return None
        return "break"

    def _arm_drag_pending(self, row: CanvasChannelRowAdapter, y_root: int) -> None:
        self._cancel_drag_pending()
        self._pending_drag_row = row
        self._pending_drag_y_root = y_root
        self._pending_drag_after = self.after(LONG_PRESS_MS, self._engage_drag)

    def _engage_drag(self) -> None:
        self._pending_drag_after = None
        row = self._pending_drag_row
        if row is None:
            return
        y_root = self._pending_drag_y_root
        self._pending_drag_row = None
        if self._on_reorder_begin is not None:
            self._on_reorder_begin(row, y_root)

    def _cancel_drag_pending(self) -> None:
        if self._pending_drag_after is not None:
            try:
                self.after_cancel(self._pending_drag_after)
            except tk.TclError:
                pass
            self._pending_drag_after = None
        self._pending_drag_row = None

    def _on_button_release(self, _event: Any) -> None:
        self._cancel_drag_pending()

    def _on_pointer_motion(self, event: Any) -> None:
        row = self._pending_drag_row
        if row is not None and abs(int(event.y_root) - self._pending_drag_y_root) > LONG_PRESS_CANCEL_PX:
            self._cancel_drag_pending()
        row, action = self._row_and_action()
        cursor_mode = "hand2" if action else ""
        if cursor_mode != self._cursor_mode:
            self.canvas.configure(cursor=cursor_mode)
            self._cursor_mode = cursor_mode
        if row is None or action is None:
            self._set_hover_group(None)
            self._tooltip.hide()
            return
        hover_group = f"hover:row:{id(row)}:{action}"
        self._set_hover_group(
            hover_group if hover_group in self._hover_items else None
        )
        self._tooltip.schedule(
            self._tooltip_text(row, action), int(event.x_root), int(event.y_root)
        )

    def _tooltip_text(self, row: CanvasChannelRowAdapter, action: str) -> str:
        channel = row.channel
        enabled = bool(channel.get("enabled", True))
        if action == "name":
            return row._truncated_display_name or (
                (channel.get("display_name") or "").strip()
                or str(channel.get("name", ""))
            )
        if action == "drag":
            return tr("tooltip.row.drag")
        if action == "delete":
            return tr("tooltip.row.delete")
        if action == "toggle":
            return tr("tooltip.row.toggle.pause" if enabled else "tooltip.row.toggle.resume")
        if action == "monitor":
            if not enabled:
                key = "tooltip.row.monitor_mode.enable"
            else:
                key = {
                    CHANNEL_MODE_TRIGGER: "tooltip.row.monitor_mode.trigger",
                    CHANNEL_MODE_MONITOR: "tooltip.row.monitor_mode.monitor",
                    CHANNEL_MODE_NOTIFY: "tooltip.row.monitor_mode.notify",
                }[channel_mode_for(channel)]
            return tr(key)
        if action == "platform":
            return tr("tooltip.row.link.default")
        if action == "link":
            if not enabled:
                key = "tooltip.row.link.paused"
            else:
                state = row.raw._status_state
                if state == "live":
                    key = "tooltip.row.link.live"
                elif state == "upcoming":
                    key = "tooltip.row.link.upcoming"
                elif state == "offline" and row.raw._upcoming_url:
                    key = "tooltip.row.link.upcoming"
                elif state == "offline" and row.raw._vod_url:
                    key = "tooltip.row.link.vod"
                elif state == "offline":
                    key = "tooltip.row.link.offline"
                else:
                    key = "tooltip.row.link.idle"
            text = tr(key)
            if row.raw._status_title:
                return tr("tooltip.row.link.with_title", link_text=text, title=row.raw._status_title)
            return text
        if action == "status" and enabled and row.raw._status_state:
            return row.raw._compose_status_tip(row.raw._status_state)
        return ""

    def _on_canvas_leave(self, _event: Any) -> None:
        if self._cursor_mode:
            self.canvas.configure(cursor="")
            self._cursor_mode = ""
        self._set_hover_group(None)
        self._tooltip.hide()

    def _on_mousewheel(self, event: Any) -> str:
        if sys.platform.startswith("win") or sys.platform == "darwin":
            delta = int(getattr(event, "delta", 0))
            steps = -(_SCROLL_UNITS_PER_WHEEL if delta > 0 else _SCROLL_UNITS_PER_WHEEL)
            if delta < 0:
                steps = _SCROLL_UNITS_PER_WHEEL
            if delta == 0:
                return "break"
        else:
            steps = -_SCROLL_UNITS_PER_WHEEL if getattr(event, "num", None) == 4 else _SCROLL_UNITS_PER_WHEEL
        self.canvas.yview_scroll(steps, "units")
        return "break"

    def _on_canvas_scroll(self, first: str, last: str) -> None:
        self._scrollbar.set(first, last)

    def _on_canvas_configure(self, _event: Any = None) -> None:
        self.request_redraw()

    def _on_language_changed(self) -> None:
        self.redraw_all()

    def _on_destroy(self, event: Any = None) -> None:
        if event is not None and getattr(event, "widget", None) is not self:
            return
        self._cancel_drag_pending()
        self._tooltip.hide()
        if self._redraw_after is not None:
            try:
                self.after_cancel(self._redraw_after)
            except tk.TclError:
                pass
            self._redraw_after = None
        if self._unsub_i18n is not None:
            self._unsub_i18n()
            self._unsub_i18n = None
