"""Opt-in Watch Streak cross-session observation (diagnostic-only).

Enabled only via ``--watch-observation``. Appends versioned NDJSON events to a
dedicated log under ``portable_paths().logs_dir``. Never mutates config,
database, or browser profile; never records titles, full URLs, chat, cookies,
tokens, or profile paths. All I/O failures are non-fatal.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from stream_monitor.events.types import (
    ChannelWentLive,
    ChannelWentOffline,
    MonitorEvent,
)
from stream_monitor.portable_storage import PortablePaths, portable_paths
from stream_monitor.util import channel_key, normalize_channel_name

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
CLI_FLAG = "--watch-observation"
OBSERVATION_LOG_NAME = "watch_streak_observation.ndjson"
RETENTION_DAYS = 30
SNAPSHOT_INTERVAL_S = 30.0

_SENSITIVE_KEYS = frozenset(
    {
        "title",
        "url",
        "full_url",
        "chat",
        "chat_content",
        "cookie",
        "cookies",
        "token",
        "tokens",
        "authorization",
        "password",
        "profile",
        "profile_path",
        "user_data_dir",
        "browser_profile",
        "display_name",
    }
)

_obs_lock = threading.RLock()
_service: WatchObservationService | None = None


def is_watch_observation_requested(argv: Sequence[str] | None = None) -> bool:
    """Return True when ``--watch-observation`` is present (default off)."""
    import sys

    tokens = list(sys.argv[1:] if argv is None else argv)
    return CLI_FLAG in tokens


def observation_log_path(paths: PortablePaths | None = None) -> Path:
    root = paths or portable_paths()
    return root.observation_log


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_ts(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def twitch_channel_key_from_url(url: str) -> str | None:
    """Derive ``twitch:<login>`` from a Twitch URL without retaining the URL."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = (parts.netloc or "").lower()
    if "twitch.tv" not in host:
        return None
    segments = [s for s in (parts.path or "").split("/") if s]
    if not segments:
        return None
    name = normalize_channel_name("twitch", segments[0])
    if not name or name in {"directory", "videos", "settings", "subscriptions"}:
        return None
    return channel_key("twitch", name)


def sanitize_observation_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Drop sensitive keys recursively; never invent replacement secrets."""

    def _clean(value: Any) -> Any:
        if isinstance(value, Mapping):
            out: dict[str, Any] = {}
            for key, item in value.items():
                key_s = str(key)
                if key_s.lower() in _SENSITIVE_KEYS:
                    continue
                out[key_s] = _clean(item)
            return out
        if isinstance(value, list):
            return [_clean(item) for item in value]
        return value

    cleaned = _clean(dict(payload))
    return cleaned if isinstance(cleaned, dict) else {}


def last_observation_activity_at(path: Path) -> datetime | None:
    """Return last activity time for the observation file, or None if absent."""
    if not path.exists() or not path.is_file():
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(mtime, tz=timezone.utc)


def should_expire_observation_file(
    path: Path,
    *,
    now: datetime | None = None,
    retention_days: int = RETENTION_DAYS,
) -> bool:
    """True when the study file has been inactive longer than retention.

    Expire only this generated observation file, and only when starting a new
    observation after inactivity. A currently active study (recent mtime) is
    never pruned.
    """
    activity = last_observation_activity_at(path)
    if activity is None:
        return False
    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current - activity > timedelta(days=retention_days)


def apply_observation_retention(
    path: Path,
    *,
    now: datetime | None = None,
    retention_days: int = RETENTION_DAYS,
) -> str:
    """Apply retention when enabling observation. Returns kept|expired|absent|error."""
    if not path.exists():
        return "absent"
    try:
        if should_expire_observation_file(
            path, now=now, retention_days=retention_days
        ):
            path.unlink()
            return "expired"
        return "kept"
    except OSError:
        logger.exception("Watch observation retention failed for %s", path)
        return "error"


@dataclass(frozen=True, slots=True)
class StreamIdentity:
    channel_key: str
    stream_started_at: str | None
    source: str
    confidence: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel_key": self.channel_key,
            "stream_started_at": self.stream_started_at,
            "source": self.source,
            "confidence": self.confidence,
        }


class ObservationWriter:
    """Append-only NDJSON writer; safe to reopen across app runs."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def append(self, event: Mapping[str, Any]) -> bool:
        try:
            line = json.dumps(dict(event), ensure_ascii=False, separators=(",", ":"))
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
                    fh.write("\n")
            return True
        except OSError:
            logger.exception("Watch observation append failed")
            return False
        except (TypeError, ValueError):
            logger.exception("Watch observation serialize failed")
            return False


class WatchObservationService:
    """Process-wide observation sink used by thin diagnostic hooks."""

    def __init__(
        self,
        *,
        path: Path,
        app_run_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
        writer: ObservationWriter | None = None,
    ) -> None:
        self.path = path
        self.app_run_id = app_run_id or uuid.uuid4().hex
        self._clock = clock or utc_now
        self._writer = writer or ObservationWriter(path)
        self._lock = threading.Lock()
        self._first_seen: dict[str, str] = {}
        self._last_identity: dict[str, StreamIdentity] = {}
        self._last_media_time: dict[str, float] = {}
        self._bus_attached = False

    def resolve_identity(
        self,
        channel_key_value: str,
        started_at: str | None,
        *,
        reuse_last: bool = False,
    ) -> StreamIdentity:
        started = (started_at or "").strip()
        with self._lock:
            if reuse_last and not started and channel_key_value in self._last_identity:
                return self._last_identity[channel_key_value]
            if started:
                prior = self._last_identity.get(channel_key_value)
                if prior is not None and prior.stream_started_at == started:
                    return prior
                identity = StreamIdentity(
                    channel_key=channel_key_value,
                    stream_started_at=started,
                    source="twitch_started_at",
                    confidence="high",
                )
                self._last_identity[channel_key_value] = identity
                return identity

            local = self._first_seen.get(channel_key_value)
            if local is None:
                local = format_ts(self._clock())
                self._first_seen[channel_key_value] = local
            identity = StreamIdentity(
                channel_key=channel_key_value,
                stream_started_at=local,
                source="local_first_seen",
                confidence="unknown",
            )
            self._last_identity[channel_key_value] = identity
            return identity

    def emit(self, event_type: str, payload: Mapping[str, Any] | None = None) -> bool:
        body = sanitize_observation_payload(payload or {})
        event = {
            "schema_version": SCHEMA_VERSION,
            "type": event_type,
            "ts_utc": format_ts(self._clock()),
            "app_run_id": self.app_run_id,
            **body,
        }
        # Second pass in case callers nested sensitive keys under aliases.
        event = sanitize_observation_payload(event)
        event["schema_version"] = SCHEMA_VERSION
        event["type"] = event_type
        event["app_run_id"] = self.app_run_id
        if "ts_utc" not in event:
            event["ts_utc"] = format_ts(self._clock())
        return self._writer.append(event)

    def record_app_lifecycle(self, action: str) -> bool:
        return self.emit("app.lifecycle", {"action": action})

    def record_stream_edge(
        self,
        *,
        edge: str,
        channel_key_value: str,
        started_at: str | None = None,
        platform: str = "twitch",
    ) -> bool:
        if platform != "twitch":
            return False
        identity = self.resolve_identity(channel_key_value, started_at)
        return self.emit(
            "stream.edge",
            {
                "edge": edge,
                "platform": platform,
                "identity": identity.as_dict(),
            },
        )

    def record_window_action(
        self,
        *,
        action: str,
        origin: str,
        channel_key_value: str | None,
        started_at: str | None = None,
    ) -> bool:
        payload: dict[str, Any] = {
            "action": action,
            "origin": origin if origin in {"app_requested", "observed", "unknown"} else "unknown",
        }
        if channel_key_value:
            # Reuse the latest known live identity when started_at is absent.
            identity = self.resolve_identity(
                channel_key_value, started_at, reuse_last=True
            )
            payload["identity"] = identity.as_dict()
        return self.emit("window.action", payload)

    def record_page_snapshot(
        self,
        *,
        channel_key_value: str,
        started_at: str | None = None,
        cdp: str,
        has_video: bool | None = None,
        ready_state: int | None = None,
        paused: bool | None = None,
        media_current_time: float | None = None,
        content_gate_present: bool | None = None,
        content_gate_attempt: str = "none",
        content_gate_result: str = "none",
        document_visibility: str = "unknown",
    ) -> bool:
        cdp_state = cdp if cdp in {"connected", "unavailable", "unknown"} else "unknown"
        # CDP failure must never imply paused/offline playback.
        if cdp_state != "connected":
            has_video = None
            ready_state = None
            paused = None
            media_current_time = None
            content_gate_present = None
            document_visibility = "unknown"
            if content_gate_attempt == "none":
                content_gate_attempt = "unknown"
            if content_gate_result == "none":
                content_gate_result = "unknown"

        media_delta: float | None = None
        if media_current_time is not None and cdp_state == "connected":
            with self._lock:
                prev = self._last_media_time.get(channel_key_value)
                if prev is not None:
                    media_delta = float(media_current_time) - float(prev)
                self._last_media_time[channel_key_value] = float(media_current_time)

        # Snapshots rarely carry started_at; reuse live-edge identity when known.
        identity = self.resolve_identity(
            channel_key_value, started_at, reuse_last=True
        )
        return self.emit(
            "page.snapshot",
            {
                "identity": identity.as_dict(),
                "cdp": cdp_state,
                "has_video": has_video,
                "ready_state": ready_state,
                "paused": paused,
                "media_current_time": media_current_time,
                "media_time_delta": media_delta,
                "content_gate_present": content_gate_present,
                "content_gate_attempt": content_gate_attempt,
                "content_gate_result": content_gate_result,
                "document_visibility": document_visibility,
            },
        )

    def handle_monitor_event(self, event: MonitorEvent) -> None:
        try:
            if isinstance(event, ChannelWentLive):
                if event.entry.platform != "twitch":
                    return
                self.record_stream_edge(
                    edge="live",
                    channel_key_value=event.entry.key,
                    started_at=event.info.started_at,
                    platform="twitch",
                )
            elif isinstance(event, ChannelWentOffline):
                if event.entry.platform != "twitch":
                    return
                identity = self.resolve_identity(
                    event.entry.key, None, reuse_last=True
                )
                self.emit(
                    "stream.edge",
                    {
                        "edge": "offline",
                        "platform": "twitch",
                        "identity": identity.as_dict(),
                    },
                )
        except Exception:
            logger.exception("Watch observation monitor subscriber failed")


def get_watch_observation() -> WatchObservationService | None:
    with _obs_lock:
        return _service


def is_watch_observation_enabled() -> bool:
    with _obs_lock:
        return _service is not None


def enable_watch_observation(
    *,
    paths: PortablePaths | None = None,
    app_run_id: str | None = None,
    clock: Callable[[], datetime] | None = None,
    now: datetime | None = None,
    retention_days: int = RETENTION_DAYS,
) -> WatchObservationService:
    """Enable observation, applying retention before the first append."""
    global _service
    path = observation_log_path(paths)
    apply_observation_retention(path, now=now, retention_days=retention_days)
    service = WatchObservationService(
        path=path,
        app_run_id=app_run_id,
        clock=clock,
    )
    with _obs_lock:
        _service = service
    try:
        service.record_app_lifecycle("open")
    except Exception:
        logger.exception("Watch observation open event failed")
    return service


def disable_watch_observation(*, emit_close: bool = True) -> None:
    global _service
    with _obs_lock:
        service = _service
        _service = None
    if service is None:
        return
    if emit_close:
        try:
            service.record_app_lifecycle("close")
        except Exception:
            logger.exception("Watch observation close event failed")


def attach_monitor_bus(bus: Any) -> bool:
    """Subscribe to monitor live/offline edges when observation is enabled."""
    service = get_watch_observation()
    if service is None:
        return False
    with service._lock:
        if service._bus_attached:
            return True
        service._bus_attached = True
    try:
        bus.subscribe(service.handle_monitor_event)
        return True
    except Exception:
        logger.exception("Watch observation bus attach failed")
        return False


def observe_window_action(
    *,
    action: str,
    origin: str,
    url: str = "",
    channel_key_value: str | None = None,
    started_at: str | None = None,
) -> None:
    service = get_watch_observation()
    if service is None:
        return
    key = channel_key_value or twitch_channel_key_from_url(url)
    try:
        service.record_window_action(
            action=action,
            origin=origin,
            channel_key_value=key,
            started_at=started_at,
        )
    except Exception:
        logger.exception("Watch observation window action failed")


def observe_page_snapshot(**kwargs: Any) -> None:
    service = get_watch_observation()
    if service is None:
        return
    try:
        service.record_page_snapshot(**kwargs)
    except Exception:
        logger.exception("Watch observation page snapshot failed")
