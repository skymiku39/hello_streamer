"""The poll subline must not grow past the reserved status column.

Regression for 2026-11-10: a long display name ("遊戲研究室 × 蛇靈Celine ×
Serpens Spirit Studio") widened the run group and wrapped the compact control
panel onto a third row while that channel was being polled.
"""

from __future__ import annotations

from stream_monitor import app_ui
from stream_monitor.i18n import tr


def _fake_measure(text: str, *, size: int = 13, weight: str = "normal") -> int:
    return len(text) * 10


def test_long_name_is_shortened_to_fit_column(monkeypatch) -> None:
    monkeypatch.setattr(app_ui, "_measure_text", _fake_measure)
    name = "遊戲研究室 × 蛇靈Celine × Serpens Spirit Studio"

    text = app_ui._fit_status_subline("status.poll_checking", name, 200)

    assert _fake_measure(text) <= 200 - 16
    assert text.endswith("…") or "…" in text
    assert text.startswith(tr("status.poll_checking", name="遊")[:2])


def test_short_name_is_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(app_ui, "_measure_text", _fake_measure)

    text = app_ui._fit_status_subline("status.poll_checking", "早日", 400)

    assert text == tr("status.poll_checking", name="早日")
