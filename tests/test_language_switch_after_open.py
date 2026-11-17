"""Regression: switching UI language must keep the after-open action."""

from __future__ import annotations

import pytest

from stream_monitor import app as app_module
from stream_monitor import i18n
from stream_monitor.action_plan import LifecycleEffect
from stream_monitor.app import _after_open_key_for_display, _after_open_labels


@pytest.fixture(autouse=True)
def _restore_language():
    original = i18n.current_language()
    yield
    i18n.set_language(original, notify=False)


class _Var:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class _Menu:
    def __init__(self) -> None:
        self.values: list[str] = []

    def configure(self, **kwargs) -> None:
        self.values = kwargs.get("values", self.values)


class _Label:
    def configure(self, **kwargs) -> None:
        pass


@pytest.mark.parametrize(
    "effect",
    [LifecycleEffect.NONE, LifecycleEffect.STOP_MONITOR, LifecycleEffect.EXIT_APP],
)
@pytest.mark.parametrize("old", ["zh_TW", "zh_CN", "en", "ja", "ko"])
@pytest.mark.parametrize("new", ["zh_TW", "zh_CN", "en", "ja", "ko"])
def test_after_open_label_from_previous_language_maps_back(effect, old, new) -> None:
    i18n.set_language(old, notify=False)
    shown = _after_open_labels()[effect]
    i18n.set_language(new, notify=False)  # language already switched
    assert _after_open_key_for_display(shown) == effect


def test_on_language_changed_keeps_selected_after_open() -> None:
    i18n.set_language("zh_TW", notify=False)
    var = _Var(_after_open_labels()[LifecycleEffect.EXIT_APP])
    menu = _Menu()
    probe = type("Probe", (), {})()
    probe.after_open_var = var
    probe.after_open_menu = menu
    for name in (
        "_title_cn_label _title_en_label startup_switch tray_switch empty_label "
        "_interval_caption _interval_unit _mode_caption _duration_caption "
        "_notify_group_label _open_group_label notify_on_live_switch "
        "open_on_live_switch notify_on_upcoming_switch open_on_upcoming_switch "
        "notify_on_open_failure_switch _after_open_caption"
    ).split():
        setattr(probe, name, _Label())
    probe.title = lambda text: None
    probe._fit_main_toolbar_i18n = lambda: None

    i18n.set_language("en", notify=False)  # what set_language does before broadcast
    app_module.App._on_language_changed(probe)

    assert var.get() == _after_open_labels()[LifecycleEffect.EXIT_APP]
    assert _after_open_key_for_display(var.get()) == LifecycleEffect.EXIT_APP
