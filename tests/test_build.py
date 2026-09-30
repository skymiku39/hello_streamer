"""Unit tests for the PyInstaller build helpers (offline / static)."""

from __future__ import annotations

from pathlib import Path

import pytest

from build import (
    WINDOWS_HIDDEN_IMPORTS,
    _version_tuple,
    _write_windows_version_file,
    build_pyinstaller_command,
    normalize_release_tag,
    require_tag_matches_package_version,
    validate_windows_onedir_bundle,
)

ROOT = Path(__file__).resolve().parents[1]
RELEASE_YML = ROOT / ".github" / "workflows" / "release.yml"


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


def test_normalize_release_tag_strips_leading_v() -> None:
    assert normalize_release_tag("v1.2.1") == "1.2.1"
    assert normalize_release_tag("V1.2.1") == "1.2.1"
    assert normalize_release_tag("1.2.1") == "1.2.1"
    assert normalize_release_tag("vnext") == "vnext"


def test_require_tag_matches_package_version_ok() -> None:
    require_tag_matches_package_version(
        "v1.2.1",
        module_version="1.2.1",
        distribution_version="1.2.1",
    )


def test_require_tag_matches_package_version_rejects_mismatch() -> None:
    with pytest.raises(SystemExit, match="Release tag"):
        require_tag_matches_package_version(
            "v9.9.9",
            module_version="1.2.1",
            distribution_version="1.2.1",
        )


def test_require_tag_matches_package_version_rejects_module_dist_mismatch() -> None:
    with pytest.raises(SystemExit, match="stream_monitor.__version__"):
        require_tag_matches_package_version(
            "v1.2.1",
            module_version="1.2.1",
            distribution_version="0.0.0",
        )


def test_validate_windows_onedir_bundle_requires_exe_and_internal(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "HelloStreamer"
    bundle.mkdir()
    with pytest.raises(FileNotFoundError, match="HelloStreamer.exe"):
        validate_windows_onedir_bundle(bundle)

    (bundle / "HelloStreamer.exe").write_bytes(b"MZ")
    with pytest.raises(FileNotFoundError, match="_internal"):
        validate_windows_onedir_bundle(bundle)

    (bundle / "_internal").mkdir()
    validate_windows_onedir_bundle(bundle)


def test_windows_pyinstaller_command_hides_winotify(tmp_path: Path) -> None:
    assert "winotify" in WINDOWS_HIDDEN_IMPORTS
    cmd = build_pyinstaller_command(
        is_windows=True,
        root=tmp_path,
        version="1.2.1",
        python_executable="python",
    )
    # Pair-wise: every hidden import appears right after --hidden-import.
    pairs = list(zip(cmd, cmd[1:], strict=False))
    for hidden in WINDOWS_HIDDEN_IMPORTS:
        assert ("--hidden-import", hidden) in pairs


def test_linux_pyinstaller_command_skips_winotify(tmp_path: Path) -> None:
    cmd = build_pyinstaller_command(
        is_windows=False,
        root=tmp_path,
        version="1.2.1",
        python_executable="python",
    )
    assert "winotify" not in cmd
    assert ("--hidden-import", "pystray._appindicator") in list(
        zip(cmd, cmd[1:], strict=False)
    )


def test_release_workflow_statically_wires_gates() -> None:
    """Offline static check: release.yml must gate tag + Windows onedir layout."""
    text = RELEASE_YML.read_text(encoding="utf-8")
    assert "--check-release-tag" in text
    assert "--validate-windows-onedir" in text
    assert "dist/HelloStreamer" in text
    assert "HelloStreamer.exe --self-check" in text


def test_app_exposes_frozen_self_check_path() -> None:
    text = (ROOT / "stream_monitor" / "app.py").read_text(encoding="utf-8")
    assert "def _run_frozen_self_check" in text
    assert '"--self-check"' in text
