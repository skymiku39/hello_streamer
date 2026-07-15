"""Tests for scroll-time row repaint deferral."""

from __future__ import annotations

from stream_monitor.scroll_guard import ScrollRepaintGuard


class _FakeScrollbar:
    def __init__(self) -> None:
        self.set_calls: list[tuple[str, str]] = []

    def bind(self, *_args, **_kwargs) -> None:
        pass

    def set(self, first: str, last: str) -> None:
        self.set_calls.append((first, last))


class _FakeCanvas:
    def __init__(self) -> None:
        self._yscrollcommand = None
        self.update_calls = 0

    def bind(self, *_args, **_kwargs) -> None:
        pass

    def cget(self, key: str):
        if key == "yscrollcommand":
            return self._yscrollcommand
        return ""

    def configure(self, **kwargs) -> None:
        if "yscrollcommand" in kwargs:
            self._yscrollcommand = kwargs["yscrollcommand"]

    def update_idletasks(self) -> None:
        self.update_calls += 1


class _FakeScrollFrame:
    def __init__(self) -> None:
        self._parent_canvas = _FakeCanvas()
        self._scrollbar = _FakeScrollbar()


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
        for _ms, callback in self._callbacks:
            callback()
        self._callbacks.clear()


def test_scroll_guard_defers_until_idle() -> None:
    root = _FakeRoot()
    idle_calls: list[str] = []
    guard = ScrollRepaintGuard(
        _FakeScrollFrame(),
        root,
        idle_ms=150,
        on_idle=lambda: idle_calls.append("idle"),
    )

    assert guard.repaints_deferred is False
    guard._on_scroll_activity()
    assert guard.repaints_deferred is True

    root.run_idle()
    assert guard.repaints_deferred is False
    assert idle_calls == ["idle"]


def test_yscroll_wrapper_marks_scroll_active() -> None:
    root = _FakeRoot()
    frame = _FakeScrollFrame()
    guard = ScrollRepaintGuard(frame, root)
    wrapped = frame._parent_canvas._yscrollcommand
    assert callable(wrapped)

    wrapped("0.0", "0.5")
    assert guard.repaints_deferred is True


def test_yscroll_wrapper_forwards_to_scrollbar_set() -> None:
    root = _FakeRoot()
    frame = _FakeScrollFrame()
    ScrollRepaintGuard(frame, root)
    wrapped = frame._parent_canvas._yscrollcommand

    wrapped("0.1", "0.6")
    assert frame._scrollbar.set_calls == [("0.1", "0.6")]

    wrapped("0.3", "0.8")
    assert frame._scrollbar.set_calls == [("0.1", "0.6"), ("0.3", "0.8")]
