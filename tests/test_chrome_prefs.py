"""Profile preference updates must preserve data when reading or writing fails."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stream_monitor import chrome_prefs


@pytest.mark.parametrize(
    "original",
    [b'{"profile":', b"\xff\xfeinvalid", b"null", b"[]", b'"profile"'],
)
def test_unreadable_preferences_are_not_replaced(tmp_path, original):
    prefs = tmp_path / "Default" / "Preferences"
    prefs.parent.mkdir()
    prefs.write_bytes(original)

    assert chrome_prefs.merge_tab_discarding_exceptions(str(tmp_path)) is False
    assert prefs.read_bytes() == original


def test_read_permission_error_does_not_replace_preferences(tmp_path, monkeypatch):
    prefs = tmp_path / "Default" / "Preferences"
    prefs.parent.mkdir()
    original = b'{"profile":{"name":"Existing profile"}}'
    prefs.write_bytes(original)
    real_open = Path.open

    def protected_open(path, *args, **kwargs):
        if path == prefs:
            raise PermissionError("temporarily locked")
        return real_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", protected_open)
        assert chrome_prefs.merge_tab_discarding_exceptions(str(tmp_path)) is False
    assert prefs.read_bytes() == original


@pytest.mark.parametrize(
    "data",
    [
        {"performance_tuning": []},
        {"performance_tuning": None},
        {"performance_tuning": {"tab_discarding": "unknown"}},
        {"performance_tuning": {"tab_discarding": {"exceptions": {"future": True}}}},
    ],
)
def test_unrecognized_preference_structure_is_preserved(tmp_path, data):
    prefs = tmp_path / "Default" / "Preferences"
    prefs.parent.mkdir()
    original = json.dumps(data).encode()
    prefs.write_bytes(original)

    assert chrome_prefs.merge_tab_discarding_exceptions(str(tmp_path)) is False
    assert prefs.read_bytes() == original


def test_bom_preferences_keep_existing_values(tmp_path):
    prefs = tmp_path / "Default" / "Preferences"
    prefs.parent.mkdir()
    prefs.write_text('{"profile":{"name":"Existing profile"}}', encoding="utf-8-sig")

    assert chrome_prefs.merge_tab_discarding_exceptions(str(tmp_path)) is True
    data = json.loads(prefs.read_text(encoding="utf-8"))
    assert data["profile"]["name"] == "Existing profile"
    assert "twitch.tv" in data["performance_tuning"]["tab_discarding"]["exceptions"]


def test_does_not_overwrite_another_preferences_temporary_file(tmp_path):
    profile = tmp_path / "Default"
    profile.mkdir()
    other_temp = profile / "Preferences.tmp"
    other_temp.write_bytes(b"another pending write")

    assert chrome_prefs.merge_tab_discarding_exceptions(str(tmp_path)) is True
    assert other_temp.read_bytes() == b"another pending write"
    assert {path.name for path in profile.iterdir()} == {"Preferences", "Preferences.tmp"}


def test_failed_replace_preserves_original_and_cleans_own_temporary_file(tmp_path, monkeypatch):
    prefs = tmp_path / "Default" / "Preferences"
    prefs.parent.mkdir()
    original = b'{"profile":{"name":"Existing profile"}}'
    prefs.write_bytes(original)

    def fail_replace(*args):
        raise PermissionError("temporarily locked")

    monkeypatch.setattr(chrome_prefs.os, "replace", fail_replace)
    assert chrome_prefs.merge_tab_discarding_exceptions(str(tmp_path)) is False
    assert prefs.read_bytes() == original
    assert list(prefs.parent.iterdir()) == [prefs]
