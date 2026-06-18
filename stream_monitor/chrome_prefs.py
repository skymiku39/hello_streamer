"""Merge Chrome Memory Saver exceptions into a dedicated profile before launch.

Chrome's Memory Saver "always keep these sites active" exclusion list is the
``performance_tuning.tab_discarding.exceptions`` LIST pref inside a profile's
``Preferences`` JSON file. When a tab is discarded its Twitch heartbeat stops,
so the viewer-engagement assist adds ``twitch.tv`` to that list before we launch
the dedicated profile.

This only ever touches a user-data-dir the app controls, preserves every other
pref, writes atomically, and must run *before* the browser process for that
profile starts (a running Chrome would otherwise overwrite the file on exit).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PREF_GROUP = "performance_tuning"
_PREF_SUB = "tab_discarding"
_PREF_KEY = "exceptions"

# Pattern format is ``[scheme://][.]host[:port][/path][@query]``; a leading dot
# matches subdomains, so these two cover twitch.tv and www/m subdomains.
TWITCH_EXCEPTION_PATTERNS: tuple[str, ...] = ("twitch.tv", ".twitch.tv")


def _preferences_path(user_data_dir: str, profile: str) -> Path:
    base = Path(os.path.expandvars(os.path.expanduser(user_data_dir)))
    return base / profile / "Preferences"


def _load_preferences(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        logger.debug("chrome_prefs: could not read %s", path, exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write(path: Path, data: dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except OSError:
        logger.exception("chrome_prefs: failed to write %s", path)
        return False


def merge_tab_discarding_exceptions(
    user_data_dir: str,
    patterns: tuple[str, ...] = TWITCH_EXCEPTION_PATTERNS,
    *,
    profile: str = "Default",
) -> bool:
    """Add *patterns* to the profile's tab-discarding exception list.

    Returns True when the list already covers the patterns or was written
    successfully, False on error or when there is nothing to act on. Existing
    preferences are preserved; only the exception list is extended.
    """
    if not user_data_dir or not patterns:
        return False
    path = _preferences_path(user_data_dir, profile)
    prefs = _load_preferences(path)

    group = prefs.get(_PREF_GROUP)
    if not isinstance(group, dict):
        group = {}
    sub = group.get(_PREF_SUB)
    if not isinstance(sub, dict):
        sub = {}
    existing = sub.get(_PREF_KEY)
    current = list(existing) if isinstance(existing, list) else []

    merged = list(current)
    for pattern in patterns:
        if pattern not in merged:
            merged.append(pattern)

    if merged == current and path.exists():
        return True

    sub[_PREF_KEY] = merged
    group[_PREF_SUB] = sub
    prefs[_PREF_GROUP] = group
    return _atomic_write(path, prefs)
