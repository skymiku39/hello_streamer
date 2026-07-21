"""Unit tests for the consolidated browser-window tracking registry."""

from __future__ import annotations

import stream_monitor.browser_win32 as bw
from stream_monitor.browser_win32 import _WindowRegistry


def test_register_dedupes_same_hwnd_and_merges_keywords() -> None:
    reg = _WindowRegistry()
    reg.register("https://a", 100, keywords=("chan",))
    reg.register("https://a", 100, keywords=("chan", "Display Name"))
    snap = reg.snapshot("https://a")
    assert len(snap) == 1  # same HWND is not duplicated
    # keywords normalise to lower-case and merge across registrations
    assert set(snap[0].keywords) == {"chan", "display name"}


def test_register_ignores_empty_url_or_hwnd() -> None:
    reg = _WindowRegistry()
    reg.register("", 100)
    reg.register("https://a", 0)
    assert reg.all_urls() == []


def test_snapshot_is_a_defensive_copy() -> None:
    reg = _WindowRegistry()
    reg.register("https://a", 100)
    snap = reg.snapshot("https://a")
    snap[0].opened_at = -999.0
    # Mutating the copy must not change the registry's stored entry.
    assert reg.snapshot("https://a")[0].opened_at != -999.0


def test_remove_hwnd_drops_entry_and_empty_bucket() -> None:
    reg = _WindowRegistry()
    reg.register("https://a", 100)
    reg.register("https://a", 101)
    reg.remove_hwnd("https://a", 100)
    assert {t.hwnd for t in reg.snapshot("https://a")} == {101}
    reg.remove_hwnd("https://a", 101)
    assert "https://a" not in reg.all_urls()


def test_clear_removes_all_hwnds_for_url() -> None:
    reg = _WindowRegistry()
    reg.register("https://a", 100)
    reg.clear("https://a")
    assert reg.snapshot("https://a") == []


def test_closing_flag_lifecycle() -> None:
    reg = _WindowRegistry()
    assert reg.is_closing("https://a") is False
    reg.mark_closing("https://a")
    assert reg.is_closing("https://a") is True
    reg.unmark_closing("https://a")
    assert reg.is_closing("https://a") is False
    # empty url is never "closing"
    reg.mark_closing("")
    assert reg.is_closing("") is False


def test_title_fallback_block_pops_once() -> None:
    reg = _WindowRegistry()
    reg.block_title_fallback("https://a")
    assert reg.pop_title_fallback_block("https://a") is True
    # Second pop returns False — the block fires exactly once.
    assert reg.pop_title_fallback_block("https://a") is False


def test_reset_clears_every_structure() -> None:
    reg = _WindowRegistry()
    reg.register("https://a", 100)
    reg.mark_closing("https://a")
    reg.block_title_fallback("https://a")
    reg.reset()
    assert reg.all_urls() == []
    assert reg.is_closing("https://a") is False
    assert reg.pop_title_fallback_block("https://a") is False


def test_module_aliases_are_views_onto_singleton() -> None:
    """Back-compat globals must stay bound to the singleton's containers."""
    assert bw._TRACKED_WINDOWS_BY_URL is bw._REGISTRY.by_url
    assert bw._TITLE_FALLBACK_BLOCKED_URLS is bw._REGISTRY.title_fallback_blocked
    assert bw._CLOSING_URLS is bw._REGISTRY.closing
    assert bw._TRACKED_HWNDS_LOCK is bw._REGISTRY.lock

    bw._REGISTRY.reset()
    try:
        bw._register_tracked_hwnd("https://view", 7)
        # Free-function write is visible through the alias …
        assert "https://view" in bw._TRACKED_WINDOWS_BY_URL
        # … and a direct alias mutation is visible to the registry.
        bw._TRACKED_WINDOWS_BY_URL.clear()
        assert bw._REGISTRY.all_urls() == []
    finally:
        bw._REGISTRY.reset()
