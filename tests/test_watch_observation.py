"""Focused tests for opt-in Watch Streak observation (diagnostic-only)."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from stream_monitor import watch_observation as wo
from stream_monitor.domain import ChannelEntry, OfflineInfo
from stream_monitor.events import ChannelWentLive, ChannelWentOffline, MonitorEventBus
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.portable_storage import portable_paths


@pytest.fixture(autouse=True)
def _reset_observation() -> None:
    wo.disable_watch_observation(emit_close=False)
    try:
        from stream_monitor import notifier

        notifier._OBS_MANAGED_URLS.clear()
        notifier._OBS_MANAGED_SINCE.clear()
    except Exception:
        pass
    yield
    wo.disable_watch_observation(emit_close=False)
    try:
        from stream_monitor import notifier

        notifier._OBS_MANAGED_URLS.clear()
        notifier._OBS_MANAGED_SINCE.clear()
    except Exception:
        pass


def _read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def test_cli_flag_default_off_and_enable() -> None:
    assert wo.is_watch_observation_requested([]) is False
    assert wo.is_watch_observation_requested(["--silent"]) is False
    assert wo.is_watch_observation_requested(["--watch-observation"]) is True
    assert wo.is_watch_observation_requested(["--silent", "--watch-observation"]) is True
    assert wo.CLI_FLAG == "--watch-observation"


def test_event_schema_and_append_across_writer_reopen_and_app_runs(
    tmp_path: Path,
) -> None:
    paths = portable_paths(tmp_path)
    clock = {"t": datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)}

    def now() -> datetime:
        return clock["t"]

    first = wo.enable_watch_observation(
        paths=paths, app_run_id="run-a", clock=now, now=clock["t"]
    )
    assert first.emit(
        "stream.edge",
        {
            "edge": "live",
            "identity": {
                "channel_key": "twitch:demo",
                "stream_started_at": "2026-10-05T11:00:00Z",
                "source": "twitch_started_at",
                "confidence": "high",
            },
        },
    )
    wo.disable_watch_observation(emit_close=True)

    clock["t"] = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)
    second = wo.enable_watch_observation(
        paths=paths, app_run_id="run-b", clock=now, now=clock["t"]
    )
    assert second.app_run_id == "run-b"
    assert second.emit(
        "stream.edge",
        {
            "edge": "offline",
            "identity": {
                "channel_key": "twitch:demo",
                "stream_started_at": "2026-10-05T11:00:00Z",
                "source": "twitch_started_at",
                "confidence": "high",
            },
        },
    )

    events = _read_events(paths.observation_log)
    types = [e["type"] for e in events]
    assert types[0] == "app.lifecycle"
    assert events[0]["action"] == "open"
    assert events[0]["app_run_id"] == "run-a"
    assert events[0]["schema_version"] == wo.SCHEMA_VERSION
    assert "ts_utc" in events[0]
    assert "stream.edge" in types
    assert any(e.get("app_run_id") == "run-b" for e in events)
    # Cross-run study preserved in one file.
    assert paths.observation_log.exists()
    assert sum(1 for e in events if e["type"] == "stream.edge") == 2


def test_stream_identity_fallback_marks_unknown(tmp_path: Path) -> None:
    paths = portable_paths(tmp_path)
    fixed = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)
    service = wo.enable_watch_observation(
        paths=paths, app_run_id="id1", clock=lambda: fixed, now=fixed
    )
    high = service.resolve_identity("twitch:a", "2026-10-05T07:00:00Z")
    assert high.source == "twitch_started_at"
    assert high.confidence == "high"
    assert high.stream_started_at == "2026-10-05T07:00:00Z"

    unknown = service.resolve_identity("twitch:b", None)
    assert unknown.source == "local_first_seen"
    assert unknown.confidence == "unknown"
    assert unknown.stream_started_at == "2026-10-05T08:00:00Z"
    again = service.resolve_identity("twitch:b", None)
    assert again.stream_started_at == unknown.stream_started_at


def test_live_edge_created_at_reused_by_later_page_snapshots(tmp_path: Path) -> None:
    """Twitch live-edge createdAt must be shared by later snapshots/window actions."""
    paths = portable_paths(tmp_path)
    bus = MonitorEventBus()
    service = wo.enable_watch_observation(paths=paths, app_run_id="reuse")
    assert wo.attach_monitor_bus(bus) is True

    created_at = "2026-10-05T07:15:00Z"
    bus.publish(
        ChannelWentLive(
            entry=ChannelEntry(platform="twitch", name="Demo"),
            info=StreamInfo(
                channel="demo",
                platform="twitch",
                is_live=True,
                started_at=created_at,
                title="ignored",
                url="https://www.twitch.tv/demo",
            ),
        )
    )
    assert service.record_page_snapshot(
        channel_key_value="twitch:demo",
        cdp="connected",
        has_video=True,
        ready_state=4,
        paused=False,
        media_current_time=12.0,
        document_visibility="visible",
    )
    assert service.record_window_action(
        action="close",
        origin="app_requested",
        channel_key_value="twitch:demo",
    )

    events = _read_events(paths.observation_log)
    snaps = [e for e in events if e["type"] == "page.snapshot"]
    actions = [e for e in events if e["type"] == "window.action"]
    edges = [e for e in events if e["type"] == "stream.edge"]
    assert edges[0]["identity"]["stream_started_at"] == created_at
    assert edges[0]["identity"]["source"] == "twitch_started_at"
    assert edges[0]["identity"]["confidence"] == "high"
    assert snaps[0]["identity"]["stream_started_at"] == created_at
    assert snaps[0]["identity"]["source"] == "twitch_started_at"
    assert snaps[0]["identity"]["confidence"] == "high"
    assert actions[0]["identity"]["stream_started_at"] == created_at
    assert actions[0]["identity"]["confidence"] == "high"
    # Without a prior live identity, local_first_seen remains unknown.
    other = service.resolve_identity("twitch:other", None, reuse_last=True)
    assert other.source == "local_first_seen"
    assert other.confidence == "unknown"


def test_page_snapshot_and_cdp_unknown_never_implies_paused(
    tmp_path: Path,
) -> None:
    paths = portable_paths(tmp_path)
    service = wo.enable_watch_observation(paths=paths, app_run_id="snap")
    assert service.record_page_snapshot(
        channel_key_value="twitch:demo",
        started_at="2026-10-05T07:00:00Z",
        cdp="connected",
        has_video=True,
        ready_state=4,
        paused=False,
        media_current_time=10.0,
        content_gate_present=False,
        content_gate_attempt="none",
        content_gate_result="none",
        document_visibility="visible",
    )
    assert service.record_page_snapshot(
        channel_key_value="twitch:demo",
        started_at="2026-10-05T07:00:00Z",
        cdp="connected",
        has_video=True,
        ready_state=4,
        paused=False,
        media_current_time=40.0,
        document_visibility="visible",
    )
    assert service.record_page_snapshot(
        channel_key_value="twitch:demo",
        cdp="unavailable",
        has_video=False,
        ready_state=0,
        paused=True,
        media_current_time=0.0,
        document_visibility="hidden",
    )
    snaps = [e for e in _read_events(paths.observation_log) if e["type"] == "page.snapshot"]
    assert snaps[0]["paused"] is False
    assert snaps[0]["cdp"] == "connected"
    assert snaps[1]["media_time_delta"] == pytest.approx(30.0)
    bad = snaps[2]
    assert bad["cdp"] == "unavailable"
    assert bad["paused"] is None
    assert bad["has_video"] is None
    assert bad["ready_state"] is None
    assert bad["media_current_time"] is None


def test_sensitive_fields_excluded_from_log(tmp_path: Path) -> None:
    paths = portable_paths(tmp_path)
    service = wo.enable_watch_observation(paths=paths, app_run_id="priv")
    service.emit(
        "stream.edge",
        {
            "edge": "live",
            "title": "SECRET TITLE",
            "url": "https://www.twitch.tv/secretchannel",
            "cookies": "session=abc",
            "token": "tok",
            "user_data_dir": "C:/secret/profile",
            "chat_content": "hello chat",
            "identity": {
                "channel_key": "twitch:demo",
                "stream_started_at": "2026-10-05T07:00:00Z",
                "source": "twitch_started_at",
                "confidence": "high",
                "display_name": "Nope",
            },
        },
    )
    raw = paths.observation_log.read_text(encoding="utf-8")
    assert "SECRET TITLE" not in raw
    assert "twitch.tv/secretchannel" not in raw
    assert "session=abc" not in raw
    assert "tok" not in raw
    assert "secret/profile" not in raw
    assert "hello chat" not in raw
    assert "Nope" not in raw
    assert "twitch:demo" in raw


def test_retention_expires_only_inactive_observation_file(tmp_path: Path) -> None:
    paths = portable_paths(tmp_path)
    path = paths.observation_log
    path.parent.mkdir(parents=True)
    path.write_text('{"type":"old"}\n', encoding="utf-8")
    old = datetime(2026, 8, 1, tzinfo=timezone.utc)
    os.utime(path, (old.timestamp(), old.timestamp()))

    now = datetime(2026, 10, 5, tzinfo=timezone.utc)
    assert wo.should_expire_observation_file(path, now=now, retention_days=30)
    assert wo.apply_observation_retention(path, now=now, retention_days=30) == "expired"
    assert not path.exists()

    path.write_text('{"type":"fresh"}\n', encoding="utf-8")
    recent = now - timedelta(days=2)
    os.utime(path, (recent.timestamp(), recent.timestamp()))
    assert not wo.should_expire_observation_file(path, now=now, retention_days=30)
    assert wo.apply_observation_retention(path, now=now, retention_days=30) == "kept"
    assert path.read_text(encoding="utf-8").startswith('{"type":"fresh"}')

    # Active study: enabling observation must not prune recent file.
    wo.enable_watch_observation(paths=paths, app_run_id="keep", now=now)
    text = path.read_text(encoding="utf-8")
    assert '{"type":"fresh"}' in text
    assert "app.lifecycle" in text


def test_disabled_observation_is_noop_and_does_not_attach_side_effects(
    tmp_path: Path,
) -> None:
    assert wo.is_watch_observation_enabled() is False
    wo.observe_window_action(
        action="open", origin="app_requested", url="https://www.twitch.tv/demo"
    )
    wo.observe_page_snapshot(channel_key_value="twitch:demo", cdp="connected")
    assert not (tmp_path / "logs" / "watch_streak_observation.ndjson").exists()

    bus = MonitorEventBus()
    assert wo.attach_monitor_bus(bus) is False

    # Enabling then handling Twitch edges only (YouTube ignored).
    paths = portable_paths(tmp_path)
    wo.enable_watch_observation(paths=paths, app_run_id="bus")
    assert wo.attach_monitor_bus(bus) is True
    bus.publish(
        ChannelWentLive(
            entry=ChannelEntry(platform="twitch", name="Demo"),
            info=StreamInfo(
                channel="demo",
                platform="twitch",
                is_live=True,
                started_at="2026-10-05T07:00:00Z",
                title="should-not-log",
                url="https://www.twitch.tv/demo",
            ),
        )
    )
    bus.publish(
        ChannelWentOffline(
            entry=ChannelEntry(platform="youtube", name="yt"),
            offline_info=OfflineInfo(
                url="https://youtube.com",
                title="x",
                platform="youtube",
                name="yt",
            ),
        )
    )
    edges = [
        e for e in _read_events(paths.observation_log) if e["type"] == "stream.edge"
    ]
    assert len(edges) == 1
    assert edges[0]["edge"] == "live"
    assert edges[0]["identity"]["confidence"] == "high"
    raw = paths.observation_log.read_text(encoding="utf-8")
    assert "should-not-log" not in raw
    assert "twitch.tv/demo" not in raw


def test_twitch_channel_key_from_url_does_not_require_full_url_storage() -> None:
    assert wo.twitch_channel_key_from_url("https://www.twitch.tv/Hello_World") == (
        "twitch:hello_world"
    )
    assert wo.twitch_channel_key_from_url("https://youtube.com/watch?v=1") is None


def test_csv_template_exists_with_required_columns() -> None:
    csv_path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "watch-streak-external-checklist.csv"
    )
    text = csv_path.read_text(encoding="utf-8")
    header = text.splitlines()[0]
    for col in (
        "channel",
        "broadcast_start_utc",
        "broadcast_end_utc",
        "baseline_streak_result",
        "baseline_streak_notification",
        "after_streak_result",
        "after_streak_notification",
        "login_status",
        "channel_points_eligibility",
        "stream_ge_10_min",
        "gap_ge_30_min",
        "consecutive_viewing_eligibility",
        "recovery_notification_utc",
        "recovery_deadline_utc",
        "recovery_content_type",
        "recovery_before_result",
        "recovery_after_result",
        "recovery_result",
        "notes",
    ):
        assert col in header
    # Blank template: no private channel names seeded.
    assert "twitch.tv" not in text.lower()


def test_main_does_not_enable_observation_when_single_instance_lock_fails(
    monkeypatch, tmp_path: Path
) -> None:
    """Second-instance exit must not leave an orphan app.lifecycle open."""
    from stream_monitor import app as app_module

    enable_calls: list[str] = []

    class _Lock:
        _on_show = None

        def try_lock(self) -> bool:
            return False

        def release(self) -> None:
            return None

    class _NoFileHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            return None

    def _tracking_enable(*_a, **_k):
        enable_calls.append("enable")
        raise AssertionError("enable_watch_observation must not run without lock")

    monkeypatch.setattr(app_module, "SingleInstance", _Lock)
    monkeypatch.setattr(app_module, "_check_writable", lambda _path: None)
    monkeypatch.setattr(
        app_module,
        "portable_paths",
        lambda: SimpleNamespace(
            root=tmp_path,
            logs_dir=tmp_path / "logs",
            application_log=tmp_path / "logs" / "app.log",
            observation_log=tmp_path / "logs" / "watch_streak_observation.ndjson",
        ),
    )
    monkeypatch.setattr(
        app_module.config_manager,
        "load",
        lambda: {"language": "en-US"},
    )
    monkeypatch.setattr(app_module.i18n, "set_language", lambda *_a, **_k: None)
    monkeypatch.setattr(app_module.i18n, "normalize", lambda value: value)
    monkeypatch.setattr(
        app_module.sys, "argv", ["stream-monitor", "--watch-observation"]
    )
    monkeypatch.setattr(
        app_module.logging.handlers,
        "RotatingFileHandler",
        lambda *_a, **_k: _NoFileHandler(),
    )
    monkeypatch.setattr(wo, "enable_watch_observation", _tracking_enable)

    previous_hook = threading.excepthook
    try:
        with pytest.raises(SystemExit) as exited:
            app_module.main()
    finally:
        threading.excepthook = previous_hook

    assert exited.value.code == 0
    assert enable_calls == []
    assert wo.is_watch_observation_enabled() is False
    obs = tmp_path / "logs" / "watch_streak_observation.ndjson"
    assert not obs.exists()


def test_app_managed_close_observation_does_not_require_keep_awake(
    monkeypatch, tmp_path: Path
) -> None:
    """App-requested close is observed even when keep-awake tracking is empty."""
    from stream_monitor import notifier

    paths = portable_paths(tmp_path)
    wo.enable_watch_observation(paths=paths, app_run_id="close-no-awake")
    url = "https://www.twitch.tv/demo"

    monkeypatch.setattr(notifier, "stop_page_assist", lambda *_a, **_k: None)
    monkeypatch.setattr(
        notifier, "_close_browser_window_for_url_impl", lambda *_a, **_k: 1
    )
    monkeypatch.setattr(notifier, "_release_engagement_keep_awake", lambda *_a: None)

    assert not notifier._ENGAGEMENT_AWAKE_URLS
    closed = notifier.close_browser_window_for_url(url)
    assert closed == 1

    actions = [
        e
        for e in _read_events(paths.observation_log)
        if e["type"] == "window.action"
    ]
    assert len(actions) == 1
    assert actions[0]["action"] == "close"
    assert actions[0]["origin"] == "app_requested"
    assert actions[0]["identity"]["channel_key"] == "twitch:demo"


def test_observed_external_close_uses_hwnd_tracking_not_keep_awake(
    monkeypatch, tmp_path: Path
) -> None:
    """External close sync must not depend on keep-awake membership."""
    from stream_monitor import notifier

    paths = portable_paths(tmp_path)
    wo.enable_watch_observation(paths=paths, app_run_id="obs-hwnd")
    url = "https://www.twitch.tv/demo"

    notifier._register_obs_managed_url(url)
    notifier._OBS_MANAGED_SINCE[url] = 0.0
    monkeypatch.setattr(notifier, "tracked_hwnds_for_url", lambda _url: set())
    assert not notifier._ENGAGEMENT_AWAKE_URLS

    noted = notifier.sync_observed_closes_for_tracked_windows(min_age_s=0.0)
    assert noted == 1
    assert url not in notifier._OBS_MANAGED_URLS

    actions = [
        e
        for e in _read_events(paths.observation_log)
        if e["type"] == "window.action"
    ]
    assert len(actions) == 1
    assert actions[0]["action"] == "close"
    assert actions[0]["origin"] == "observed"
    assert "manual" not in actions[0]["origin"]


def test_close_all_tracked_windows_records_app_requested_channel_closes(
    monkeypatch, tmp_path: Path
) -> None:
    """Stop-close keeps per-channel evidence before clearing managed URLs."""
    from stream_monitor import notifier

    paths = portable_paths(tmp_path)
    wo.enable_watch_observation(paths=paths, app_run_id="close-all")
    url = "https://www.twitch.tv/demo"
    notifier._register_obs_managed_url(url)

    monkeypatch.setattr(notifier, "stop_all_page_assist", lambda: None)
    monkeypatch.setattr(
        notifier, "_close_all_tracked_windows_impl", lambda: 1
    )
    monkeypatch.setattr(notifier, "set_system_keep_awake", lambda _active: None)

    assert notifier.close_all_tracked_windows() == 1
    assert url not in notifier._OBS_MANAGED_URLS

    actions = [
        e
        for e in _read_events(paths.observation_log)
        if e["type"] == "window.action"
    ]
    assert len(actions) == 1
    assert actions[0]["action"] == "close"
    assert actions[0]["origin"] == "app_requested"
    assert actions[0]["identity"]["channel_key"] == "twitch:demo"


def test_managed_open_without_page_assist_records_unknown_player_sample(
    monkeypatch, tmp_path: Path
) -> None:
    """A diagnostic open without CDP is explicit unknown, not absent evidence."""
    from stream_monitor import notifier
    from stream_monitor.browser_settings_model import BrowserSettings

    paths = portable_paths(tmp_path)
    wo.enable_watch_observation(paths=paths, app_run_id="open-no-cdp")
    monkeypatch.setattr(notifier, "_VIEWER_ENGAGEMENT", None)
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    monkeypatch.setattr(notifier, "_resolve_browser_executable", lambda _path: "chrome")
    monkeypatch.setattr(notifier.subprocess, "Popen", lambda *_a, **_k: object())

    opened = notifier._open_with_browser_settings(
        "https://www.twitch.tv/demo",
        BrowserSettings(
            enabled=True,
            new_window=True,
            user_data_dir=str(tmp_path / "profile"),
            per_channel_profile=False,
        ),
    )

    assert opened is True
    events = _read_events(paths.observation_log)
    action = next(e for e in events if e["type"] == "window.action")
    sample = next(e for e in events if e["type"] == "page.snapshot")
    assert action["action"] == "open"
    assert action["identity"]["channel_key"] == "twitch:demo"
    assert sample["cdp"] == "unavailable"
    assert sample["has_video"] is None
    assert sample["paused"] is None
    assert sample["identity"]["confidence"] == "unknown"
