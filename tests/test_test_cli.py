"""Tests for stream_monitor.test_cli (selection, forwarding, cwd, errors)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from stream_monitor import test_cli


def test_build_pytest_argv_unit_excludes_named_suites_only() -> None:
    argv = test_cli.build_pytest_argv("unit")
    assert argv[:2] == ["pytest", "tests"]
    for path in test_cli.UNIT_IGNORE_PATHS:
        assert "--ignore" in argv
        assert path in argv
    # Conservative convention: no broad --ignore-glob; boundary tests stay in unit.
    assert "--ignore-glob" not in argv


def test_build_pytest_argv_regression_and_all_are_full_suite() -> None:
    for mode in ("regression", "all"):
        argv = test_cli.build_pytest_argv(mode, ["-q"])
        assert argv == ["pytest", "tests", "-q"]
        for path in test_cli.UNIT_IGNORE_PATHS:
            assert path not in argv


def test_build_pytest_argv_forwards_extra_flags() -> None:
    argv = test_cli.build_pytest_argv("unit", ["-q", "-k", "version"])
    assert argv[-3:] == ["-q", "-k", "version"]


def test_parse_args_default_mode_and_strip_double_dash() -> None:
    ns = test_cli.parse_args([])
    assert ns.mode == "all"
    assert ns.pytest_args == []

    ns = test_cli.parse_args(["unit", "--", "-q", "--tb=short"])
    assert ns.mode == "unit"
    assert ns.pytest_args == ["-q", "--tb=short"]

    ns = test_cli.parse_args(["regression", "-q"])
    assert ns.mode == "regression"
    assert ns.pytest_args == ["-q"]


def test_parse_args_default_mode_forwards_flags_without_named_mode() -> None:
    """Flags alone (with or without --) keep default all and forward to pytest."""
    ns = test_cli.parse_args(["-q"])
    assert ns.mode == "all"
    assert ns.pytest_args == ["-q"]

    ns = test_cli.parse_args(["--", "-q"])
    assert ns.mode == "all"
    assert ns.pytest_args == ["-q"]

    ns = test_cli.parse_args(["--", "-q", "-k", "notifier"])
    assert ns.mode == "all"
    assert ns.pytest_args == ["-q", "-k", "notifier"]


def test_parse_args_rejects_invalid_mode() -> None:
    with pytest.raises(SystemExit) as exc_info:
        test_cli.parse_args(["nope"])
    assert exc_info.value.code == 2


def test_parse_args_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        test_cli.parse_args(["--help"])
    assert exc_info.value.code == 0


def test_project_root_is_checkout_root() -> None:
    root = test_cli.project_root()
    assert (root / "pyproject.toml").is_file()
    assert (root / "stream_monitor" / "test_cli.py").is_file()
    assert (root / "tests").is_dir()


def test_run_pytest_uses_project_root_cwd_and_sys_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_run(cmd, cwd=None, check=False):  # noqa: ANN001
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(test_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(test_cli.importlib.util, "find_spec", lambda name: object())

    code = test_cli.run_pytest("unit", ["-q"], cwd=None)
    assert code == 0
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[0] == sys.executable
    assert cmd[1:3] == ["-m", "pytest"]
    assert "tests" in cmd
    assert captured["cwd"] == str(test_cli.project_root())

    other = tmp_path / "elsewhere"
    other.mkdir()
    code = test_cli.run_pytest("all", [], cwd=other)
    assert code == 0
    assert captured["cwd"] == str(other)


def test_run_pytest_propagates_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        test_cli.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=5),
    )
    monkeypatch.setattr(test_cli.importlib.util, "find_spec", lambda name: object())
    assert test_cli.run_pytest("all") == 5


def test_run_pytest_missing_pytest_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(test_cli.importlib.util, "find_spec", lambda name: None)
    code = test_cli.run_pytest("unit")
    assert code == 1
    err = capsys.readouterr().err
    assert "pytest is not installed" in err
    assert "uv sync --extra dev" in err


def test_run_pytest_missing_tests_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_root = tmp_path / "empty_project"
    fake_root.mkdir()
    monkeypatch.setattr(test_cli, "project_root", lambda: fake_root)
    code = test_cli.run_pytest("all")
    assert code == 1
    err = capsys.readouterr().err
    assert "tests directory not found" in err


def test_main_wires_parse_to_run(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(mode, pytest_args):  # noqa: ANN001
        seen["mode"] = mode
        seen["pytest_args"] = list(pytest_args)
        return 0

    monkeypatch.setattr(test_cli, "run_pytest", fake_run)
    assert test_cli.main(["unit", "-q"]) == 0
    assert seen == {"mode": "unit", "pytest_args": ["-q"]}


def test_module_invocation_help_exits_zero() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "stream_monitor.test_cli", "--help"],
        cwd=str(test_cli.project_root()),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "unit" in proc.stdout
    assert "regression" in proc.stdout
