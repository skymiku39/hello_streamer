"""Tests for Trello-style channel reorder mode."""

from __future__ import annotations

from stream_monitor.channel_reorder import preview_row_indices


def test_preview_row_indices_pushes_cards_aside() -> None:
    assert preview_row_indices(1, 3, 4) == [0, 2, 1, 3]
    assert preview_row_indices(0, 2, 4) == [1, 0, 2, 3]


def test_preview_row_indices_noop_returns_identity() -> None:
    assert preview_row_indices(1, 1, 4) == [0, 1, 2, 3]
    assert preview_row_indices(1, 2, 4) == [0, 1, 2, 3]
