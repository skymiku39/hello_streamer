"""Unit tests for the PyInstaller build helpers."""

from pathlib import Path

from build import _version_tuple, _write_windows_version_file


def test_version_tuple_pads_to_four_parts() -> None:
    assert _version_tuple("1.2.1") == (1, 2, 1, 0)
    assert _version_tuple("2") == (2, 0, 0, 0)


def test_write_windows_version_file_contains_metadata(tmp_path: Path) -> None:
    path = tmp_path / "version_info.txt"
    _write_windows_version_file(path, "1.2.1")
    text = path.read_text(encoding="utf-8")
    assert "filevers=(1, 2, 1, 0)" in text
    assert "ProductName" in text
    assert "Hello Streamer" in text
    assert "1.2.1" in text
