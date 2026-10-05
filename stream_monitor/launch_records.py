"""Safe reset of launch / dedupe records (seen DB + status cache).

Clears only monitoring memory that can cause spurious re-triggers:
``seen_videos`` and ``channel_status_cache``. Channel list, browser login
profile, and the rest of ``config.json`` are retained.

The preferred path requires the monitor to be idle with no in-flight
background work, then backups both records into an app-owned timestamp
folder (outside ``browser_profile/``) before mutating anything. Cleared
config state is persisted atomically before DB deletion and before the
caller reports success; failures restore in-memory (and re-persist when
needed) so the caller never claims a half-reset.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping, Protocol

from stream_monitor.db import SeenVideoDB
from stream_monitor.portable_storage import PortablePaths, portable_paths
from stream_monitor.status_cache import CACHE_CONFIG_KEY

logger = logging.getLogger(__name__)

PersistConfig = Callable[[MutableMapping[str, Any]], Any]
AfterGuardHook = Callable[[], None]


class LaunchRecordsBusyError(RuntimeError):
    """Raised when reset is refused because monitoring or workers are active."""


class LaunchRecordsResetError(RuntimeError):
    """Raised when backup or clear fails before a successful completion."""


@dataclass(frozen=True)
class ResetGuardResult:
    ok: bool
    reason_key: str | None = None


@dataclass(frozen=True)
class LaunchRecordsResetResult:
    ok: bool
    backup_dir: Path | None = None
    cleared_rows: int = 0
    error_key: str | None = None
    error_detail: str = ""


class _HasActiveWork(Protocol):
    def has_active_work(self) -> bool: ...


class _SettledIdle(Protocol):
    @property
    def mode(self) -> str: ...

    @property
    def is_running(self) -> bool: ...

    def is_settled_idle(self) -> bool: ...


def evaluate_reset_guard(
    *,
    mode: str,
    is_running: bool,
    is_settled_idle: bool,
    has_active_work: bool,
) -> ResetGuardResult:
    """Return whether a launch-records reset is safe to run."""
    if (
        mode != "idle"
        or is_running
        or not is_settled_idle
        or has_active_work
    ):
        return ResetGuardResult(False, "settings.reset.busy")
    return ResetGuardResult(True)


def evaluate_reset_guard_from(
    controller: _SettledIdle,
    coordinator: _HasActiveWork,
) -> ResetGuardResult:
    return evaluate_reset_guard(
        mode=controller.mode,
        is_running=controller.is_running,
        is_settled_idle=controller.is_settled_idle(),
        has_active_work=coordinator.has_active_work(),
    )


def _timestamp_folder_name(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return f"launch_records_{stamp}"


def create_backup_dir(
    paths: PortablePaths | None = None,
    *,
    now: datetime | None = None,
) -> Path:
    """Create ``<app>/backups/launch_records_<utc>/`` outside browser_profile."""
    root = paths or portable_paths()
    backup_dir = root.backups_dir / _timestamp_folder_name(now)
    backup_dir.mkdir(parents=True, exist_ok=False)
    profile = root.browser_profile_dir.resolve()
    if profile in backup_dir.resolve().parents or backup_dir.resolve() == profile:
        raise LaunchRecordsResetError(
            "backup path must not live inside browser_profile"
        )
    return backup_dir


def backup_status_cache(
    config: Mapping[str, Any],
    backup_dir: Path,
) -> Path | None:
    """Write the current status cache JSON into *backup_dir* if present."""
    cache = config.get(CACHE_CONFIG_KEY)
    if cache is None:
        return None
    dest = backup_dir / "channel_status_cache.json"
    dest.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return dest


def clear_status_cache(config: MutableMapping[str, Any]) -> bool:
    """Remove persisted status cache from *config*. Return True if removed."""
    return config.pop(CACHE_CONFIG_KEY, None) is not None


def _restore_status_cache(
    config: MutableMapping[str, Any],
    *,
    had_cache: bool,
    previous_cache: Any,
) -> None:
    if had_cache:
        config[CACHE_CONFIG_KEY] = previous_cache
    else:
        config.pop(CACHE_CONFIG_KEY, None)


def _mark_backup_failed(backup_dir: Path | None, exc: BaseException) -> None:
    if backup_dir is None or not backup_dir.exists():
        return
    try:
        (backup_dir / "RESET_FAILED.txt").write_text(str(exc), encoding="utf-8")
    except OSError:
        pass


def reset_launch_records(
    *,
    db: SeenVideoDB,
    config: MutableMapping[str, Any],
    paths: PortablePaths | None = None,
    controller: _SettledIdle | None = None,
    coordinator: _HasActiveWork | None = None,
    now: datetime | None = None,
    persist_config: PersistConfig | None = None,
    after_guard: AfterGuardHook | None = None,
) -> LaunchRecordsResetResult:
    """Backup then clear seen DB rows and status cache together.

    When ``persist_config`` is provided, the cleared in-memory status cache is
    written atomically **before** the DB table is cleared and **before** the
    caller may report success. On config-save failure the DB is left untouched
    and the in-memory cache is restored. On DB-clear failure the previous cache
    is restored and re-persisted when possible.

    Callers must not clear in-memory row snapshots unless ``result.ok`` is True.
    """
    if controller is not None and coordinator is not None:
        guard = evaluate_reset_guard_from(controller, coordinator)
        if not guard.ok:
            return LaunchRecordsResetResult(
                ok=False,
                error_key=guard.reason_key or "settings.reset.busy",
            )

    if after_guard is not None:
        try:
            after_guard()
        except Exception as exc:
            logger.exception("Launch-records after_guard failed")
            return LaunchRecordsResetResult(
                ok=False,
                error_key="settings.reset.fail",
                error_detail=str(exc),
            )

    backup_dir: Path | None = None
    had_cache = CACHE_CONFIG_KEY in config
    previous_cache = deepcopy(config.get(CACHE_CONFIG_KEY)) if had_cache else None
    cache_cleared = False
    persisted = False

    try:
        backup_dir = create_backup_dir(paths, now=now)
        backup_status_cache(config, backup_dir)
        db.backup_to(backup_dir / "seen_videos.db")

        clear_status_cache(config)
        cache_cleared = True

        if persist_config is not None:
            try:
                persist_config(config)
            except Exception as exc:
                logger.exception("Launch-records config persist failed")
                _restore_status_cache(
                    config, had_cache=had_cache, previous_cache=previous_cache
                )
                _mark_backup_failed(backup_dir, exc)
                return LaunchRecordsResetResult(
                    ok=False,
                    backup_dir=backup_dir,
                    error_key="settings.reset.fail",
                    error_detail=str(exc),
                )
            persisted = True

        try:
            cleared = db.clear_dedupe_table()
        except Exception as exc:
            logger.exception("Launch-records DB clear failed")
            _restore_status_cache(
                config, had_cache=had_cache, previous_cache=previous_cache
            )
            if persisted and persist_config is not None:
                try:
                    persist_config(config)
                except Exception:
                    logger.exception(
                        "Launch-records rollback persist after DB failure failed"
                    )
            _mark_backup_failed(backup_dir, exc)
            return LaunchRecordsResetResult(
                ok=False,
                backup_dir=backup_dir,
                error_key="settings.reset.fail",
                error_detail=str(exc),
            )
    except FileExistsError as exc:
        logger.exception("Launch-records backup folder already exists")
        if cache_cleared:
            _restore_status_cache(
                config, had_cache=had_cache, previous_cache=previous_cache
            )
        return LaunchRecordsResetResult(
            ok=False,
            backup_dir=backup_dir,
            error_key="settings.reset.fail",
            error_detail=str(exc),
        )
    except Exception as exc:
        logger.exception("Launch-records reset failed")
        if cache_cleared:
            _restore_status_cache(
                config, had_cache=had_cache, previous_cache=previous_cache
            )
            if persisted and persist_config is not None:
                try:
                    persist_config(config)
                except Exception:
                    logger.exception(
                        "Launch-records rollback persist after failure failed"
                    )
        _mark_backup_failed(backup_dir, exc)
        return LaunchRecordsResetResult(
            ok=False,
            backup_dir=backup_dir,
            error_key="settings.reset.fail",
            error_detail=str(exc),
        )

    logger.info(
        "Reset launch records: cleared=%d backup=%s",
        cleared,
        backup_dir,
    )
    return LaunchRecordsResetResult(
        ok=True,
        backup_dir=backup_dir,
        cleared_rows=cleared,
    )
