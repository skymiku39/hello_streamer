"""Filesystem layout for the portable distribution.

The application intentionally has one distribution mode: portable.  Runtime
state therefore lives beside the executable (or beside the repository root
when running from source).  Keeping the layout in one value object prevents
individual modules from inventing their own paths and makes the portable
contract easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from stream_monitor import base_dir


@dataclass(frozen=True)
class PortablePaths:
    """All writable paths owned by one portable application copy."""

    root: Path

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def database_file(self) -> Path:
        return self.root / "seen_videos.db"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def application_log(self) -> Path:
        return self.logs_dir / "stream_monitor.log"

    @property
    def observation_log(self) -> Path:
        """Opt-in Watch Streak diagnostic NDJSON (separate from application_log)."""
        return self.logs_dir / "watch_streak_observation.ndjson"

    @property
    def diagnostics_log(self) -> Path:
        return self.root / "debug-reorder.log"

    @property
    def diagnostics_backup(self) -> Path:
        return self.root / "debug-reorder.log.1"

    @property
    def browser_profile_dir(self) -> Path:
        return self.root / "browser_profile"

    @property
    def backups_dir(self) -> Path:
        """App-owned backups root (never inside ``browser_profile/``)."""
        return self.root / "backups"


def portable_paths(root: Path | None = None) -> PortablePaths:
    """Return the canonical writable layout for this application copy."""
    return PortablePaths((root or base_dir()).resolve())
