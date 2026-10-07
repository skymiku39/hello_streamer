"""Run native checks in isolated Tcl interpreters with bounded timeouts."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("linux") and not os.environ.get("DISPLAY"),
    reason="Native Tk checks need a display (run under xvfb-run in CI)",
)


def _run(*args):
    result = subprocess.run(
        [sys.executable, "-m", "tests.native_app_probe", *args],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["visible", "silent"])
@pytest.mark.parametrize("scale", ["1.0", "1.25", "1.5"])
def test_cold_start_interval_fits_without_manual_resize(mode, scale):
    _run("layout", mode, scale)


def test_live_event_reaches_browser_through_real_app():
    _run("action")


def test_watch_to_trigger_opens_current_live_once():
    _run("watch_to_trigger")


def test_dialog_buttons_use_application_style():
    _run("dialogs")
