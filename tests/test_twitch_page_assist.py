"""Tests for CDP Twitch page assist (source-only)."""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest

from stream_monitor import config_manager, notifier
from stream_monitor.cdp_client import CdpClient, allocate_debugging_port
from stream_monitor.twitch_page_assist import (
    fuzzy_uniform,
    should_start_page_assist,
    start_page_assist,
    stop_all_page_assist,
    stop_page_assist,
)
from stream_monitor.viewer_engagement_model import (
    ViewerEngagementSettings,
    page_assist_runtime_allowed,
)


def test_page_assist_defaults_are_opt_in() -> None:
    settings = ViewerEngagementSettings()
    assert settings.page_assist_enabled is False
    assert settings.accept_content_gate is True
    assert settings.claim_channel_points is False
    assert settings.theater_mode is False
    assert settings.auto_refresh is False


def test_page_assist_runtime_blocked_when_frozen(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert page_assist_runtime_allowed() is False
    settings = ViewerEngagementSettings(
        enabled=True, page_assist_enabled=True
    )
    assert settings.page_assist_active() is False


def test_page_assist_runtime_allowed_when_source(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert page_assist_runtime_allowed() is True
    settings = ViewerEngagementSettings(
        enabled=True, page_assist_enabled=True
    )
    assert settings.page_assist_active() is True


def test_normalize_viewer_engagement_page_assist_ints() -> None:
    normalized = config_manager._normalize_viewer_engagement(
        {
            "page_assist_enabled": True,
            "claim_delay_seconds_min": 5,
            "claim_delay_seconds_max": 3,  # should be ordered up
            "refresh_minutes_min": 10,
            "refresh_minutes_max": 8,
        }
    )
    assert normalized["page_assist_enabled"] is True
    assert normalized["claim_delay_seconds_min"] == 5
    assert normalized["claim_delay_seconds_max"] == 5
    assert normalized["refresh_minutes_min"] == 10
    assert normalized["refresh_minutes_max"] == 10


def test_fuzzy_uniform_orders_bounds() -> None:
    for _ in range(20):
        value = fuzzy_uniform(12, 2)
        assert 2 <= value <= 12
    assert fuzzy_uniform(7, 7) == 7


def test_should_start_page_assist_requires_all_gates(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    settings = ViewerEngagementSettings(
        enabled=True, page_assist_enabled=True
    )
    assert (
        should_start_page_assist(
            "https://www.twitch.tv/foo",
            settings,
            managed=True,
            isolated_profile=True,
            chromium_family=True,
            standalone_window=True,
        )
        is True
    )
    assert (
        should_start_page_assist(
            "https://www.youtube.com/@foo",
            settings,
            managed=True,
            isolated_profile=True,
            chromium_family=True,
            standalone_window=True,
        )
        is False
    )
    assert (
        should_start_page_assist(
            "https://www.twitch.tv/foo",
            settings,
            managed=False,
            isolated_profile=True,
            chromium_family=True,
            standalone_window=True,
        )
        is False
    )


def test_page_assist_standalone_window_gate_matches_ui_docs(monkeypatch) -> None:
    """Standalone managed window yes; regular tab / unmanaged no."""
    from stream_monitor.twitch_page_assist import is_standalone_managed_window

    monkeypatch.delattr(sys, "frozen", raising=False)
    settings = ViewerEngagementSettings(
        enabled=True, page_assist_enabled=True
    )

    assert is_standalone_managed_window(
        managed=True, app_mode=True, new_window=False
    )
    assert is_standalone_managed_window(
        managed=True, app_mode=False, new_window=True
    )
    assert not is_standalone_managed_window(
        managed=True, app_mode=False, new_window=False
    )
    assert not is_standalone_managed_window(
        managed=False, app_mode=True, new_window=True
    )

    assert (
        should_start_page_assist(
            "https://www.twitch.tv/foo",
            settings,
            managed=True,
            isolated_profile=True,
            chromium_family=True,
            standalone_window=True,
        )
        is True
    )
    assert (
        should_start_page_assist(
            "https://www.twitch.tv/foo",
            settings,
            managed=True,
            isolated_profile=True,
            chromium_family=True,
            standalone_window=False,
        )
        is False
    )
    assert (
        should_start_page_assist(
            "https://www.twitch.tv/foo",
            settings,
            managed=False,
            isolated_profile=True,
            chromium_family=True,
            standalone_window=True,
        )
        is False
    )


def test_open_page_assist_skipped_for_regular_tab(monkeypatch, tmp_path) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    started: list[tuple[str, int]] = []
    monkeypatch.setattr(notifier.subprocess, "Popen", lambda *a, **k: object())
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(
        notifier,
        "start_page_assist",
        lambda url, port, settings: started.append((url, port)) or True,
    )
    notifier.configure_viewer_engagement(
        ViewerEngagementSettings(
            enabled=True,
            page_assist_enabled=True,
            keep_system_awake=False,
        )
    )
    try:
        ok = notifier._open_with_browser_settings(
            "https://www.twitch.tv/foo",
            {
                "enabled": True,
                "browser_path": "chrome",
                "app_mode": False,
                "new_window": False,
                "user_data_dir": str(tmp_path / "profile"),
                "per_channel_profile": False,
            },
            manage=True,
        )
        assert ok is True
        assert started == []
    finally:
        notifier.configure_viewer_engagement(None)
        stop_all_page_assist()


def test_allocate_debugging_port_is_bindable() -> None:
    port = allocate_debugging_port()
    assert isinstance(port, int)
    assert 0 < port < 65536


def test_build_browser_args_includes_ephemeral_cdp_port() -> None:
    args = notifier._build_browser_args(
        "https://www.twitch.tv/foo",
        {
            "browser_path": "chrome",
            "user_data_dir": "C:/tmp/profile",
            "app_mode": True,
            "apply_geometry": False,
            "_cdp_debugging_port": 0,
        },
    )
    assert "--remote-debugging-port=0" in args
    assert "--remote-allow-origins=*" in args


def test_build_browser_args_includes_explicit_cdp_port() -> None:
    args = notifier._build_browser_args(
        "https://www.twitch.tv/foo",
        {
            "browser_path": "chrome",
            "user_data_dir": "C:/tmp/profile",
            "app_mode": True,
            "apply_geometry": False,
            "_cdp_debugging_port": 19222,
        },
    )
    assert "--remote-debugging-port=19222" in args
    assert "--remote-allow-origins=*" in args


def _patch_open_browser_common(monkeypatch) -> None:
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(
        notifier,
        "_apply_new_browser_window_settings_async",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda *_a, **_k: set())


def test_open_fresh_profile_ephemeral_port_resolves_devtools(
    monkeypatch, tmp_path
) -> None:
    """Cold start leases a bindable CDP port and passes it to Chrome + assist."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    import stream_monitor.cdp_client as cdp_client

    profile = tmp_path / "profile"
    started: list[tuple[str, int]] = []
    launched: list[list[str]] = []
    cdp_client.reset_cdp_port_registry()

    def fake_popen(args, **_k):
        launched.append(list(args))
        return object()

    _patch_open_browser_common(monkeypatch)
    monkeypatch.setattr(notifier.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cdp_client, "profile_looks_busy", lambda _p: False)
    monkeypatch.setattr(
        cdp_client, "probe_cdp_endpoint", lambda *_a, **_k: False
    )
    monkeypatch.setattr(
        notifier,
        "start_page_assist",
        lambda url, port, settings: started.append((url, port)) or True,
    )

    notifier.configure_viewer_engagement(
        ViewerEngagementSettings(
            enabled=True,
            page_assist_enabled=True,
            keep_system_awake=False,
        )
    )
    try:
        ok = notifier._open_with_browser_settings(
            "https://www.twitch.tv/foo",
            {
                "enabled": True,
                "browser_path": "chrome",
                "app_mode": True,
                "new_window": False,
                "user_data_dir": str(profile),
                "per_channel_profile": False,
            },
            manage=True,
        )
        assert ok is True
        assert launched, "browser should still launch"
        port_flags = [
            a for a in launched[0] if a.startswith("--remote-debugging-port=")
        ]
        assert len(port_flags) == 1
        leased = int(port_flags[0].split("=", 1)[1])
        assert leased > 0
        assert started == [("https://www.twitch.tv/foo", leased)]
    finally:
        notifier.configure_viewer_engagement(None)
        stop_all_page_assist()
        cdp_client.reset_cdp_port_registry()


def test_open_busy_profile_attaches_existing_usable_endpoint(
    monkeypatch, tmp_path
) -> None:
    """Busy profile with a live CDP endpoint still starts page assist."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    import stream_monitor.cdp_client as cdp_client

    profile = tmp_path / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "SingletonLock").write_text("busy", encoding="utf-8")
    (profile / "DevToolsActivePort").write_text(
        "35555\n/devtools/browser/existing\n", encoding="utf-8"
    )

    started: list[tuple[str, int]] = []
    launched: list[list[str]] = []

    def fake_popen(args, **_k):
        launched.append(list(args))
        return object()

    _patch_open_browser_common(monkeypatch)
    monkeypatch.setattr(notifier.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        cdp_client,
        "probe_cdp_endpoint",
        lambda port, timeout=1.5: port == 35555,
    )
    monkeypatch.setattr(
        notifier,
        "start_page_assist",
        lambda url, port, settings: started.append((url, port)) or True,
    )

    notifier.configure_viewer_engagement(
        ViewerEngagementSettings(
            enabled=True,
            page_assist_enabled=True,
            keep_system_awake=False,
        )
    )
    try:
        ok = notifier._open_with_browser_settings(
            "https://www.twitch.tv/foo",
            {
                "enabled": True,
                "browser_path": "chrome",
                "app_mode": True,
                "new_window": False,
                "user_data_dir": str(profile),
                "per_channel_profile": False,
            },
            manage=True,
        )
        assert ok is True
        assert launched, "ordinary browser launch must still happen"
        assert "--remote-debugging-port=0" not in launched[0]
        assert started == [("https://www.twitch.tv/foo", 35555)]
    finally:
        notifier.configure_viewer_engagement(None)
        stop_all_page_assist()


def test_open_busy_profile_without_cdp_skips_page_assist_promptly(
    monkeypatch, tmp_path, caplog
) -> None:
    """Busy profile without a usable CDP endpoint fails fast and skips worker."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    import time as time_mod

    import stream_monitor.cdp_client as cdp_client
    from stream_monitor.cdp_client import CdpAttachResult

    profile = tmp_path / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "SingletonLock").write_text("busy", encoding="utf-8")

    started: list[tuple[str, int]] = []
    _patch_open_browser_common(monkeypatch)
    monkeypatch.setattr(notifier.subprocess, "Popen", lambda *a, **k: object())
    monkeypatch.setattr(cdp_client, "DEFAULT_CDP_DISCOVERY_TIMEOUT_S", 0.4)
    monkeypatch.setattr(time_mod, "sleep", lambda _s: None)
    monkeypatch.setattr(
        cdp_client,
        "acquire_cdp_debugging_port",
        lambda *_a, **_k: CdpAttachResult(
            ok=False,
            reason="profile_nondebug",
            message=(
                "Existing browser/profile is running without a proven "
                "remote-debugging endpoint for this user_data_dir"
            ),
        ),
    )
    monkeypatch.setattr(
        notifier,
        "start_page_assist",
        lambda url, port, settings: started.append((url, port)) or True,
    )

    notifier.configure_viewer_engagement(
        ViewerEngagementSettings(
            enabled=True,
            page_assist_enabled=True,
            keep_system_awake=False,
        )
    )
    try:
        with caplog.at_level("WARNING"):
            t0 = time_mod.perf_counter()
            ok = notifier._open_with_browser_settings(
                "https://www.twitch.tv/foo",
                {
                    "enabled": True,
                    "browser_path": "chrome",
                    "app_mode": True,
                    "new_window": False,
                    "user_data_dir": str(profile),
                    "per_channel_profile": False,
                },
                manage=True,
            )
            elapsed = time_mod.perf_counter() - t0
        assert ok is True
        assert started == []
        assert elapsed < 2.0
        assert any(
            "profile_nondebug" in rec.message
            and "CDP unavailable" in rec.message
            for rec in caplog.records
        )
    finally:
        notifier.configure_viewer_engagement(None)
        stop_all_page_assist()


def test_open_fresh_profile_devtools_timeout_skips_page_assist(
    monkeypatch, tmp_path, caplog
) -> None:
    """When pre-launch CDP acquisition fails, page assist is skipped promptly."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    import time as time_mod

    import stream_monitor.cdp_client as cdp_client
    from stream_monitor.cdp_client import CdpAttachResult

    profile = tmp_path / "profile"
    started: list[tuple[str, int]] = []
    _patch_open_browser_common(monkeypatch)
    monkeypatch.setattr(notifier.subprocess, "Popen", lambda *a, **k: object())
    monkeypatch.setattr(time_mod, "sleep", lambda _s: None)
    monkeypatch.setattr(
        cdp_client,
        "acquire_cdp_debugging_port",
        lambda *_a, **_k: CdpAttachResult(
            ok=False,
            reason="timeout",
            message="CDP discovery timed out",
        ),
    )
    monkeypatch.setattr(
        notifier,
        "start_page_assist",
        lambda url, port, settings: started.append((url, port)) or True,
    )

    notifier.configure_viewer_engagement(
        ViewerEngagementSettings(
            enabled=True,
            page_assist_enabled=True,
            keep_system_awake=False,
        )
    )
    try:
        with caplog.at_level("WARNING"):
            t0 = time_mod.perf_counter()
            ok = notifier._open_with_browser_settings(
                "https://www.twitch.tv/foo",
                {
                    "enabled": True,
                    "browser_path": "chrome",
                    "app_mode": True,
                    "new_window": False,
                    "user_data_dir": str(profile),
                    "per_channel_profile": False,
                },
                manage=True,
            )
            elapsed = time_mod.perf_counter() - t0
        assert ok is True
        assert started == []
        assert elapsed < 2.5
        assert any(
            "reason=timeout" in rec.message and "CDP unavailable" in rec.message
            for rec in caplog.records
        )
    finally:
        notifier.configure_viewer_engagement(None)
        stop_all_page_assist()


def test_close_browser_window_stops_page_assist(monkeypatch) -> None:
    stopped: list[str] = []
    monkeypatch.setattr(
        notifier, "stop_page_assist", lambda url: stopped.append(url)
    )
    monkeypatch.setattr(
        notifier, "_close_browser_window_for_url_impl", lambda *a, **k: 0
    )
    monkeypatch.setattr(notifier, "_release_engagement_keep_awake", lambda *_a: None)
    assert notifier.close_browser_window_for_url("https://www.twitch.tv/foo") == 0
    assert stopped == ["https://www.twitch.tv/foo"]


def test_cdp_evaluate_and_click(monkeypatch) -> None:
    client = CdpClient()
    calls: list[tuple[str, dict[str, Any] | None]] = []

    def fake_call(method: str, params: dict[str, Any] | None = None, **_k):
        calls.append((method, params))
        if method == "Runtime.evaluate":
            return {
                "result": {
                    "type": "object",
                    "value": {"found": True, "x": 10, "y": 20},
                }
            }
        return {}

    client._ws = object()  # mark as connected
    monkeypatch.setattr(client, "call", fake_call)
    value = client.evaluate("1+1")
    assert value == {"found": True, "x": 10, "y": 20}
    client.click_at(11, 22)
    assert calls[0][0] == "Runtime.evaluate"
    assert calls[1][0] == "Input.dispatchMouseEvent"
    assert calls[1][1]["type"] == "mousePressed"
    assert calls[2][1]["type"] == "mouseReleased"


def test_gate_js_finds_start_watching_in_fake_dom() -> None:
    """Sanity-check the gate finder expression against a tiny fake document."""
    from stream_monitor.twitch_page_assist import _FIND_GATE_JS

    harness = f"""
class HTMLElement {{}}
globalThis.HTMLElement = HTMLElement;
const button = {{
  innerText: "Start Watching",
  textContent: "Start Watching",
  getAttribute: () => "",
  getBoundingClientRect: () => ({{ left: 100, top: 200, width: 80, height: 40 }}),
}};
Object.setPrototypeOf(button, HTMLElement.prototype);
globalThis.document = {{
  querySelector: () => null,
  querySelectorAll: () => [button],
}};
const result = {_FIND_GATE_JS};
console.log(JSON.stringify(result));
"""
    import shutil
    import subprocess

    if not shutil.which("node"):
        pytest.skip("node not available")
    proc = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout.strip())
    assert data["found"] is True
    assert data["x"] == 140
    assert data["y"] == 220


def test_start_page_assist_rejected_when_frozen(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert (
        start_page_assist(
            "https://www.twitch.tv/foo",
            9222,
            ViewerEngagementSettings(enabled=True, page_assist_enabled=True),
        )
        is False
    )
    stop_page_assist("https://www.twitch.tv/foo")
