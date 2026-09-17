"""Tests for the explicit portable filesystem contract."""

from __future__ import annotations

from pathlib import Path

from stream_monitor.portable_storage import portable_paths
from stream_monitor.single_instance import port_for_root


def test_portable_paths_keep_all_runtime_state_under_one_root(tmp_path: Path) -> None:
    paths = portable_paths(tmp_path)

    assert paths.root == tmp_path.resolve()
    assert paths.config_file == paths.root / "config.json"
    assert paths.database_file == paths.root / "seen_videos.db"
    assert paths.application_log == paths.root / "logs" / "stream_monitor.log"
    assert paths.diagnostics_log == paths.root / "debug-reorder.log"
    assert paths.browser_profile_dir == paths.root / "browser_profile"


def test_single_instance_port_is_stable_per_portable_root(tmp_path: Path) -> None:
    first = port_for_root(tmp_path / "one")
    second = port_for_root(tmp_path / "two")

    assert 41000 <= first < 59000
    assert first == port_for_root((tmp_path / "one").resolve())
    assert first != second
