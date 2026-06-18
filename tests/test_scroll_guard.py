"""Tests for scroll-time row repaint deferral and anti-ghost hiding."""

from __future__ import annotations

from stream_monitor.scroll_guard import ChannelListScrollController, ScrollRepaintGuard


class _FakeScrollbar:
    def bind(self, *_args, **_kwargs) -> None:
        pass


class _FakeCanvas:
    def __init__(self) -> None:
        self._yscrollcommand = None
        self._items: dict[int, dict[str, str]] = {1: {"state": "normal"}}
        self.update_calls = 0
        self.yview_calls: list[tuple[str, int, str]] = []

    def bind(self, *_args, **_kwargs) -> None:
        pass

    def cget(self, key: str):
        if key == "yscrollcommand":
            return self._yscrollcommand
        return ""

    def configure(self, **kwargs) -> None:
        if "yscrollcommand" in kwargs:
            self._yscrollcommand = kwargs["yscrollcommand"]

    def itemconfigure(self, item_id: int, **kwargs) -> None:
        self._items.setdefault(item_id, {}).update(kwargs)

    def yview(self, *args):
        if not args:
            return (0.0, 0.5)
        self.yview_calls.append(args)
        return None

    def update_idletasks(self) -> None:
        self.update_calls += 1


class _FakeScrollFrame:
    def __init__(self) -> None:
        self._parent_canvas = _FakeCanvas()
        self._scrollbar = _FakeScrollbar()
        self._create_window_id = 1
        self._shift_pressed = False
        self._mouse_wheel_all = lambda _event: None

    def check_if_master_is_canvas(self, widget) -> bool:
        return widget is self._parent_canvas


class _FakeRoot:
    def __init__(self) -> None:
        self._callbacks: list[tuple[int, object]] = []
        self._cancelled: set[str] = set()

    def after(self, ms: int, callback) -> str:
        token = str(len(self._callbacks))
        self._callbacks.append((ms, callback))
        return token

    def after_cancel(self, token: str) -> None:
        self._cancelled.add(token)

    def run_idle(self) -> None:
        for _ms, callback in list(self._callbacks):
            callback()
        self._callbacks.clear()


def test_scroll_guard_defers_until_idle() -> None:
    root = _FakeRoot()
    idle_calls: list[str] = []
    guard = ChannelListScrollController(
        _FakeScrollFrame(),
        root,
        rows_provider=list,
        idle_ms=150,
        on_idle=lambda: idle_calls.append("idle"),
    )

    assert guard.repaints_deferred is False
    guard._begin_scroll_motion()
    assert guard.repaints_deferred is True
    assert guard._content_hidden is True

    root.run_idle()
    assert guard.repaints_deferred is False
    assert guard._content_hidden is False
    assert idle_calls == ["idle"]


def test_yscroll_wrapper_hides_content() -> None:
    root = _FakeRoot()
    frame = _FakeScrollFrame()
    guard = ChannelListScrollController(frame, root, rows_provider=list)
    wrapped = frame._parent_canvas._yscrollcommand
    assert callable(wrapped)

    wrapped("0.0", "0.5")
    assert guard.repaints_deferred is True
    assert frame._parent_canvas._items[1]["state"] == "hidden"


def test_mouse_wheel_hides_scrolls_and_breaks() -> None:
    root = _FakeRoot()
    frame = _FakeScrollFrame()

    class _WheelEvent:
        delta = 120
        widget = frame._parent_canvas

    guard = ChannelListScrollController(frame, root, rows_provider=list)
    result = guard._mouse_wheel_all(_WheelEvent())

    assert result == "break"
    assert frame._parent_canvas._items[1]["state"] == "hidden"
    assert frame._parent_canvas.yview_calls == [("scroll", -20, "units")]


def test_legacy_alias_is_controller() -> None:
    assert ScrollRepaintGuard is ChannelListScrollController
