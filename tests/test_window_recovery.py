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
