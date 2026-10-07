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
    validate_linux_onedir_bundle,
    validate_windows_onedir_bundle,
)
from stream_monitor import __version__

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


def test_validate_linux_onedir_bundle_requires_nonempty_exe_and_internal(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "HelloStreamer"
    bundle.mkdir()
    with pytest.raises(FileNotFoundError, match="missing executable: .*HelloStreamer"):
        validate_linux_onedir_bundle(bundle)

    exe = bundle / "HelloStreamer"
    exe.write_bytes(b"")
    with pytest.raises(FileNotFoundError, match="empty executable: .*HelloStreamer"):
        validate_linux_onedir_bundle(bundle)

    exe.write_bytes(b"\x7fELF")
    with pytest.raises(FileNotFoundError, match="missing _internal:"):
        validate_linux_onedir_bundle(bundle)

    internal = bundle / "_internal"
    internal.mkdir()
    with pytest.raises(FileNotFoundError, match="empty _internal:"):
        validate_linux_onedir_bundle(bundle)

    (internal / "base_library.zip").write_bytes(b"pk")
    validate_linux_onedir_bundle(bundle)


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
    assert "--validate-linux-onedir" in text
    assert "dist/HelloStreamer" in text
    assert "HelloStreamer.exe --self-check" in text
    assert "dist/HelloStreamer/HelloStreamer --self-check" in text
    # Issue #9: Windows + Linux share the same pinned uv and locked sync.
    assert 'UV_VERSION: "0.12.20"' in text
    assert "version: ${{ env.UV_VERSION }}" in text
    assert "astral.sh/uv/${UV_VERSION}/install.sh" in text
    assert text.count("uv sync --locked --extra dev") >= 2
    assert text.count("uv tree --locked --extra dev --depth 2") == 2
    assert "pip install -e" not in text
    assert "--system-site-packages" in text


def test_readme_release_assets_match_workflow_naming() -> None:
    """README download names/policy must track release.yml tag-driven artifacts."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    workflow = RELEASE_YML.read_text(encoding="utf-8")

    windows_pattern = "HelloStreamer-${{ github.ref_name }}-windows-x64.zip"
    linux_pattern = (
        "HelloStreamer-${{ github.ref_name }}-${{ matrix.artifact }}.tar.gz"
    )
    assert windows_pattern in workflow
    assert linux_pattern in workflow
    assert "artifact: linux-x64" in workflow
    assert "artifact: linux-arm64" in workflow

    assert f"HelloStreamer-v{__version__}-windows-x64.zip" in readme
    assert f"HelloStreamer-v{__version__}-linux-x64.tar.gz" in readme
    assert f"HelloStreamer-v{__version__}-linux-arm64.tar.gz" in readme
    assert "HelloStreamer-${tag}-windows-x64.zip" in readme
    assert "HelloStreamer-${tag}-linux-x64.tar.gz" in readme
    assert "HelloStreamer-${tag}-linux-arm64.tar.gz" in readme
    assert (
        "https://github.com/skymiku39/hello_streamer/releases/latest" in readme
    )
    assert "_internal" in readme
    assert "整包解壓" in readme
    assert "可執行檔已自 GitHub 移除" in readme
    assert "僅保留版本說明與 tag 供查閱" in readme


def test_release_workflow_linux_matrix_validates_before_package_and_upload() -> None:
    """Both linux/amd64 and linux/arm64 must smoke-check before packaging/upload."""
    text = RELEASE_YML.read_text(encoding="utf-8")
    assert "artifact: linux-x64" in text
    assert "platform: linux/amd64" in text
    assert "artifact: linux-arm64" in text
    assert "platform: linux/arm64" in text

    build_idx = text.index("Build Linux executable in Debian Bookworm")
    validate_idx = text.index("--validate-linux-onedir dist/HelloStreamer", build_idx)
    shell_exec_idx = text.index("test -x dist/HelloStreamer/HelloStreamer", validate_idx)
    self_check_idx = text.index(
        "dist/HelloStreamer/HelloStreamer --self-check",
        shell_exec_idx,
    )
    package_idx = text.index("Package Linux executable", self_check_idx)
    upload_idx = text.index("name: release-${{ matrix.artifact }}", package_idx)
    assert validate_idx < shell_exec_idx < self_check_idx < package_idx < upload_idx
    # Host-side layout gate remains immediately before tar packaging.
    package_block = text[package_idx:upload_idx]
    assert "missing or empty executable" in package_block
    assert "empty _internal" in package_block
    assert "tar -czf" in package_block


def test_app_exposes_frozen_self_check_path() -> None:
    text = (ROOT / "stream_monitor" / "app.py").read_text(encoding="utf-8")
    assert "def _run_frozen_self_check" in text
    assert '"--self-check"' in text
