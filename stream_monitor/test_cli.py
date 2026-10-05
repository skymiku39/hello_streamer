"""CLI to run unit or regression pytest selections without GUI imports.

Usage::

    uv run --extra dev stream-monitor-test [unit|regression|all] [-- pytest args]
    uv run --extra dev python -m stream_monitor.test_cli [unit|regression|all] [-- pytest args]

Default mode is ``all``. ``unit`` excludes only clearly named integration / e2e /
smoke files; boundary-style regressions under other names remain in the unit set.
``regression`` and ``all`` run the full ``tests/`` tree.
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Sequence

MODES = ("unit", "regression", "all")
DEFAULT_MODE = "all"

# Conservative: exclude only filenames that clearly mark heavy / e2e / smoke suites.
# Unit still includes boundary regressions and other fast tests.
UNIT_IGNORE_PATHS: tuple[str, ...] = (
    "tests/test_channel_reorder_integration.py",
    "tests/test_channel_reorder_pack_e2e.py",
    "tests/test_smoke.py",
)


def project_root() -> Path:
    """Return the repository root (parent of the ``stream_monitor`` package)."""
    return Path(__file__).resolve().parent.parent


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse mode and remaining pytest arguments.

    Default mode is ``all``. Flags may be forwarded without naming a mode,
    with or without a leading ``--`` (e.g. ``["-q"]`` or ``["--", "-q"]``).
    """
    parser = argparse.ArgumentParser(
        prog="stream-monitor-test",
        description=(
            "Run Hello Streamer pytest selections. "
            "unit excludes only clearly named integration/e2e/smoke files; "
            "regression and all run the full tests/ tree."
        ),
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default=DEFAULT_MODE,
        choices=MODES,
        help=f"Test selection (default: {DEFAULT_MODE})",
    )
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to pytest (optional leading -- is stripped)",
    )
    raw = list(sys.argv[1:] if argv is None else argv)
    # When the first token is "--" or a flag, keep default mode and forward
    # the rest to pytest (argparse would otherwise treat "-q" as mode).
    if raw and raw[0] == "--":
        raw = [DEFAULT_MODE, *raw]
    elif raw and raw[0].startswith("-") and raw[0] not in ("-h", "--help"):
        raw = [DEFAULT_MODE, "--", *raw]
    args = parser.parse_args(raw)
    forwarded = list(args.pytest_args)
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]
    args.pytest_args = forwarded
    return args


def build_pytest_argv(mode: str, pytest_args: Sequence[str] | None = None) -> list[str]:
    """Build argv for ``python -m pytest`` (excluding the interpreter and ``-m``)."""
    if mode not in MODES:
        raise ValueError(f"invalid mode: {mode!r}; expected one of {MODES}")
    argv = ["pytest", "tests"]
    if mode == "unit":
        for path in UNIT_IGNORE_PATHS:
            argv.extend(["--ignore", path])
    if pytest_args:
        argv.extend(pytest_args)
    return argv


def _missing_pytest_message() -> str:
    return (
        "pytest is not installed. Install dev extras, for example:\n"
        "  uv sync --extra dev"
    )


def _missing_tests_message(tests_dir: Path) -> str:
    return f"tests directory not found: {tests_dir}"


def run_pytest(
    mode: str,
    pytest_args: Sequence[str] | None = None,
    *,
    cwd: Path | None = None,
    executable: str | None = None,
) -> int:
    """Invoke pytest in a subprocess; return its exit code exactly."""
    root = project_root()
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        print(_missing_tests_message(tests_dir), file=sys.stderr)
        return 1

    if importlib.util.find_spec("pytest") is None:
        print(_missing_pytest_message(), file=sys.stderr)
        return 1

    cmd = [executable or sys.executable, "-m", *build_pytest_argv(mode, pytest_args)]
    completed = subprocess.run(cmd, cwd=str(cwd or root), check=False)
    return int(completed.returncode)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry: parse args, run pytest, propagate exit code."""
    args = parse_args(argv)
    return run_pytest(args.mode, args.pytest_args)


if __name__ == "__main__":
    raise SystemExit(main())
