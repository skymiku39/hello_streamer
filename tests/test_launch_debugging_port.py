"""Which launches carry ``--remote-debugging-port``.

2026-11-11 16:31: both managed windows closed at once; the reopen still found
the old browser listening on its fixed port, launched without the flag, and
the old browser then exited. The replacement had no debugging endpoint, so
page assist (and every claim) was dead until the next cold start 14 h later.
"""

from __future__ import annotations

from stream_monitor.cdp_client import CdpAttachResult
from stream_monitor.notifier import _build_browser_args, _launch_debugging_port


def test_reused_fixed_port_browser_still_gets_the_flag() -> None:
    attach = CdpAttachResult(
        ok=True, port=58812, reason="process", pass_debugging_flag=False
    )

    assert _launch_debugging_port(attach) == 58812
    args = _build_browser_args(
        "https://www.twitch.tv/oldpot_o_0",
        {"browser_path": "chrome", "_cdp_debugging_port": 58812},
    )
    assert "--remote-debugging-port=58812" in args


def test_cold_launch_keeps_its_allocated_port() -> None:
    attach = CdpAttachResult(
        ok=True, port=51425, reason="allocated", pass_debugging_flag=True
    )

    assert _launch_debugging_port(attach) == 51425


def test_endpoint_file_reuse_and_failures_omit_the_flag() -> None:
    # A port-zero session publishes its own endpoint; forcing a number here
    # could collide with it.
    reused = CdpAttachResult(
        ok=True, port=40001, reason="devtools_file", pass_debugging_flag=False
    )
    failed = CdpAttachResult(ok=False, reason="profile_nondebug")

    assert _launch_debugging_port(reused) is None
    assert _launch_debugging_port(failed) is None
