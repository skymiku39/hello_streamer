from __future__ import annotations

import json
from types import SimpleNamespace

from stream_monitor.browser_win32 import _WindowRegistry
from stream_monitor.window_recovery import WindowIdentity, WindowRecovery


class _Identity:
    def __init__(self):
        self.markers = {}
        self.sequence = 100

    def capture(self, hwnd):
        self.sequence += 1
        row = dict(hwnd=hwnd, pid=22, token=self.sequence, class_name="Chrome_WidgetWin_1")
        self.markers[hwnd] = dict(row)
        return row

    def matches(self, record):
        expected = self.markers.get(record.get("hwnd"))
        return bool(expected and all(record.get(k) == v for k, v in expected.items()))

    def release(self, record):
        if self.matches(record):
            self.markers.pop(record["hwnd"])


def _new_registry(path, identity):
    registry = _WindowRegistry()
    registry.enable_recovery(WindowRecovery(path, identity))
    return registry


def test_restart_recovers_original_window_even_after_raid_title_changes(tmp_path):
    path = tmp_path / "windows.json"
    identity = _Identity()
    before = _new_registry(path, identity)
    before.register("https://www.twitch.tv/original", 42, ("Original",))

    # A new process has no in-memory registry. Recovery uses no title or URL
    # read from the page, so a raid cannot switch ownership to another channel.
    after = _new_registry(path, identity)
    assert [w.hwnd for w in after.snapshot("https://www.twitch.tv/original")] == [42]
    assert after.snapshot("https://www.twitch.tv/raid-destination") == []
    after.clear("https://www.twitch.tv/original")
    assert json.loads(path.read_text())["windows"] == []
    assert identity.markers == {}


def test_reused_hwnd_is_not_recovered_or_adopted(tmp_path):
    path = tmp_path / "windows.json"
    identity = _Identity()
    _new_registry(path, identity).register("https://original", 42)
    replacement = identity.capture(42)
    after = _new_registry(path, identity)
    assert after.all_urls() == []
    assert after.pop_title_fallback_block("https://original") is True
    assert after.pop_title_fallback_block("https://original") is True
    # Do not remove a new window's marker merely because its HWND was reused.
    assert identity.markers[42] == replacement


def test_hwnd_reuse_after_restore_drops_tracking_before_close(tmp_path):
    identity = _Identity()
    registry = _new_registry(tmp_path / "windows.json", identity)
    registry.register("https://original", 42)
    replacement = identity.capture(42)
    assert registry.snapshot("https://original") == []
    assert identity.markers[42] == replacement


def test_unmarked_window_is_never_adopted_from_disk(tmp_path):
    path = tmp_path / "windows.json"
    path.write_text(json.dumps({"version": 1, "windows": [
        dict(url="https://original", hwnd=42, pid=22, token=123, keywords=[]),
    ]}))
    identity = _Identity()
    registry = _new_registry(path, identity)
    assert registry.all_urls() == []
    assert identity.markers == {}


def test_invalid_journal_does_not_guess_ownership(tmp_path):
    path = tmp_path / "windows.json"
    path.write_text('{"windows":')
    assert _new_registry(path, _Identity()).all_urls() == []


def test_persistence_failure_keeps_current_window_tracked(tmp_path):
    path = tmp_path / "blocked"
    path.write_text("not a folder")
    registry = _new_registry(path / "windows.json", _Identity())
    registry.register("https://original", 42)
    assert [w.hwnd for w in registry.snapshot("https://original")] == [42]


def test_native_identity_requires_marker_pid_and_browser_class():
    identity = object.__new__(WindowIdentity)
    identity.property_name = "test"
    identity.api = SimpleNamespace(GetPropW=lambda hwnd, name: 123)
    identity._owner = lambda hwnd: (22, "Chrome_WidgetWin_1")
    row = dict(hwnd=42, pid=22, token=123, class_name="Chrome_WidgetWin_1")
    assert identity.matches(row)
    for change in ({"token": 124}, {"pid": 23}, {"hwnd": "42"}, {"class_name": "Other"}):
        assert not identity.matches(row | change)


def test_recovered_window_resumes_assist_without_opening_duplicate(monkeypatch, tmp_path):
    from stream_monitor import cdp_client, notifier
    from stream_monitor.viewer_engagement_model import ViewerEngagementSettings

    url = "https://www.twitch.tv/original"
    calls = []
    monkeypatch.setattr(notifier, "managed_window_is_open", lambda candidate: candidate == url)
    monkeypatch.setattr(notifier, "_active_viewer_engagement", lambda: ViewerEngagementSettings(
        enabled=True, page_assist_enabled=True, keep_system_awake=False,
    ))
    monkeypatch.setattr(notifier, "active_page_assist_urls", lambda: set())
    monkeypatch.setattr(notifier, "_register_obs_managed_url", lambda candidate: None)
    monkeypatch.setattr(cdp_client, "acquire_cdp_debugging_port", lambda profile: SimpleNamespace(
        ok=True, port=43210,
    ))
    monkeypatch.setattr(notifier, "start_page_assist", lambda *args: calls.append(args))
    monkeypatch.setattr(notifier, "_open_with_browser_settings", lambda *a, **kw: calls.append("opened"))
    assert notifier.open_url(url, {
        "enabled": True, "app_mode": True, "user_data_dir": str(tmp_path),
        "per_channel_profile": False,
    })
    assert len(calls) == 1
    assert calls[0][:2] == (url, 43210)



def test_recovered_window_in_browser_without_endpoint_requests_restart(
    monkeypatch, tmp_path,
):
    # A surviving browser without a debugging endpoint cannot be reattached;
    # the allocator must close and reopen every slot window instead.
    from stream_monitor import cdp_client, claim_registry, notifier
    from stream_monitor.viewer_engagement_model import ViewerEngagementSettings

    claim_registry.reset_for_tests()
    url = "https://www.twitch.tv/original"
    monkeypatch.setattr(notifier, "managed_window_is_open", lambda candidate: candidate == url)
    monkeypatch.setattr(notifier, "_active_viewer_engagement", lambda: ViewerEngagementSettings(
        enabled=True, page_assist_enabled=True, keep_system_awake=False,
    ))
    monkeypatch.setattr(notifier, "active_page_assist_urls", lambda: set())
    monkeypatch.setattr(cdp_client, "acquire_cdp_debugging_port", lambda profile: SimpleNamespace(
        ok=False, port=None, reason="profile_nondebug",
    ))
    monkeypatch.setattr(notifier, "start_page_assist", lambda *args: None)
    monkeypatch.setattr(notifier, "_open_with_browser_settings", lambda *a, **kw: None)
    try:
        notifier.open_url(url, {
            "enabled": True, "app_mode": True, "user_data_dir": str(tmp_path),
            "per_channel_profile": False,
        })
        assert claim_registry.take_endpoint_restart(now=1000.0)
    finally:
        claim_registry.reset_for_tests()

def test_resume_surviving_players_does_not_open_or_use_closed_windows(monkeypatch):
    from stream_monitor import notifier

    resumed = []
    monkeypatch.setattr(notifier, "managed_window_is_open", lambda url: url == "owned")
    monkeypatch.setattr(notifier, "_resume_managed_window_assist",
                        lambda *args: resumed.append(args[0]))
    worker = notifier.resume_managed_window_assists(["owned", "closed", "owned"], {})
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert resumed == ["owned"]


def test_resume_rechecks_window_after_cdp_discovery(monkeypatch, tmp_path):
    from stream_monitor import cdp_client, notifier
    from stream_monitor.viewer_engagement_model import ViewerEngagementSettings

    calls = []
    monkeypatch.setattr(notifier, "_active_viewer_engagement", lambda: ViewerEngagementSettings(
        enabled=True, page_assist_enabled=True, keep_system_awake=False,
    ))
    monkeypatch.setattr(notifier, "active_page_assist_urls", lambda: set())
    monkeypatch.setattr(notifier, "managed_window_is_open", lambda url: False)
    monkeypatch.setattr(cdp_client, "acquire_cdp_debugging_port", lambda profile: SimpleNamespace(
        ok=True, port=43210,
    ))
    monkeypatch.setattr(notifier, "start_page_assist", lambda *args: calls.append(args))
    notifier._resume_managed_window_assist("https://www.twitch.tv/original", {
        "enabled": True, "app_mode": True, "user_data_dir": str(tmp_path),
        "per_channel_profile": False,
    }, None)
    assert calls == []


def test_resumed_watch_only_window_does_not_claim_points(monkeypatch, tmp_path):
    # The launch and retry paths pass for_channel(url); the recovery path did
    # not, so a recovered watch-only window clicked chests and ran the
    # 30-minute no-points refresh.
    from stream_monitor import cdp_client, notifier
    from stream_monitor import viewer_engagement_model as vem
    from stream_monitor.viewer_engagement_model import ViewerEngagementSettings

    engagement = ViewerEngagementSettings(
        enabled=True,
        page_assist_enabled=True,
        keep_system_awake=False,
        points_allocation_enabled=True,
        points_priority_channels=["holder"],
        claim_channel_points=True,
        points_stall_refresh_enabled=True,
    )
    calls = []
    monkeypatch.setattr(notifier, "_active_viewer_engagement", lambda: engagement)
    monkeypatch.setattr(notifier, "active_page_assist_urls", lambda: set())
    monkeypatch.setattr(notifier, "managed_window_is_open", lambda url: True)
    monkeypatch.setattr(notifier, "_register_obs_managed_url", lambda url: None)
    monkeypatch.setattr(cdp_client, "acquire_cdp_debugging_port", lambda profile: SimpleNamespace(
        ok=True, port=43210,
    ))
    monkeypatch.setattr(notifier, "start_page_assist", lambda *args: calls.append(args))
    monkeypatch.setattr(vem, "page_assist_runtime_allowed", lambda: True)
    # Cold start: no allocation yet, so the static priority list decides and
    # a non-listed channel is watch-only.
    vem.set_active_points_channels(None)
    notifier._resume_managed_window_assist("https://www.twitch.tv/other", {
        "enabled": True, "app_mode": True, "user_data_dir": str(tmp_path),
        "per_channel_profile": False,
    }, None)

    assert len(calls) == 1
    settings = calls[0][2]
    assert settings.claim_channel_points is False
    assert settings.points_stall_refresh_enabled is False
