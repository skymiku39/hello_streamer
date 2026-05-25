import json
from copy import deepcopy
from pathlib import Path

from stream_monitor import config_manager


def _use_config_path(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(config_manager, "_config_path", lambda: path)


def _migrated_defaults() -> dict:
    """Return ``DEFAULT_CONFIG`` with the same migrations ``load()`` applies.

    Several legacy tests pre-date :func:`_migrate_browser_settings`, which
    fills in ``user_data_dir`` whenever ``per_channel_profile=True`` (the
    out-of-box default) so the on-disk schema cannot drift back into the
    cross-window HWND contamination pitfall. Use this helper instead of
    comparing against ``DEFAULT_CONFIG`` literally.
    """
    expected = deepcopy(config_manager.DEFAULT_CONFIG)
    config_manager._migrate_browser_settings(expected["browser_settings"])
    return expected


def _migrated_default_browser_settings() -> dict:
    expected = deepcopy(config_manager.DEFAULT_BROWSER_SETTINGS)
    config_manager._migrate_browser_settings(expected)
    return expected


def test_load_missing_file_uses_defaults(tmp_path, monkeypatch) -> None:
    _use_config_path(monkeypatch, tmp_path / "config.json")

    assert config_manager.load() == _migrated_defaults()


def test_load_bad_json_uses_defaults(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.json"
    path.write_text("{", encoding="utf-8")
    _use_config_path(monkeypatch, path)

    assert config_manager.load() == _migrated_defaults()


def test_load_validates_config_values(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "channels": [
                    {
                        "platform": "Twitch",
                        "name": " Some_Channel ",
                        "display_name": " Some Channel ",
                    },
                    {"platform": "youtube", "name": ""},
                    {"platform": "mixer", "name": "someone"},
                    "bad",
                ],
                "check_interval": 5,
                "action": "bad_action",
                "run_on_startup": "yes",
                "minimize_to_tray": "no",
                "window_geometry": 123,
            }
        ),
        encoding="utf-8",
    )
    _use_config_path(monkeypatch, path)

    config = config_manager.load()

    assert config["channels"] == [
        {
            "platform": "twitch",
            "name": "Some_Channel",
            "display_name": "Some Channel",
        }
    ]
    assert config["check_interval"] == config_manager.MIN_CHECK_INTERVAL
    assert config["action"] == "open_and_stop"
    assert config["monitor_mode"] == "trigger"
    assert config["run_on_startup"] is False
    assert config["minimize_to_tray"] is True
    assert config["window_geometry"] is None
    assert config["browser_settings"] == _migrated_default_browser_settings()


def test_load_non_numeric_interval_uses_default(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"check_interval": "soon"}), encoding="utf-8")
    _use_config_path(monkeypatch, path)

    assert config_manager.load()["check_interval"] == 60


def test_load_preserves_enabled_field(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "channels": [
                    {"platform": "twitch", "name": "a", "enabled": False},
                    {"platform": "twitch", "name": "b", "enabled": True},
                    {"platform": "twitch", "name": "c"},
                ],
            }
        ),
        encoding="utf-8",
    )
    _use_config_path(monkeypatch, path)

    config = config_manager.load()
    assert config["channels"][0]["enabled"] is False
    assert config["channels"][1]["enabled"] is True
    assert "enabled" not in config["channels"][2]


def test_load_preserves_monitor_only_field(tmp_path, monkeypatch) -> None:
    """monitor_only must survive round-trip through normalize without coercion."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "channels": [
                    {"platform": "twitch", "name": "a", "monitor_only": True},
                    {"platform": "twitch", "name": "b", "monitor_only": False},
                    {"platform": "twitch", "name": "c"},
                    # Non-bool values must be discarded silently.
                    {"platform": "twitch", "name": "d", "monitor_only": "yes"},
                ],
            }
        ),
        encoding="utf-8",
    )
    _use_config_path(monkeypatch, path)

    config = config_manager.load()
    assert config["channels"][0]["monitor_only"] is True
    assert config["channels"][1]["monitor_only"] is False
    assert "monitor_only" not in config["channels"][2]
    assert "monitor_only" not in config["channels"][3]


def test_save_is_atomic_and_reloadable(tmp_path, monkeypatch) -> None:
    path = tmp_path / "nested" / "config.json"
    _use_config_path(monkeypatch, path)

    config_manager.save(
        {
            "channels": [
                {
                    "platform": "youtube",
                    "name": "hello",
                    "display_name": "Hello Channel",
                }
            ],
            "check_interval": 30,
            "action": "notify_only",
            "run_on_startup": True,
            "minimize_to_tray": False,
            "window_geometry": "720x520+10+10",
        }
    )

    assert path.exists()
    assert not path.with_name(".config.json.tmp").exists()
    assert config_manager.load() == {
        "channels": [
            {
                "platform": "youtube",
                "name": "hello",
                "display_name": "Hello Channel",
            }
        ],
        "check_interval": 30,
        "action": "notify_only",
        "monitor_mode": "trigger",
        "run_on_startup": True,
        "minimize_to_tray": False,
        "window_geometry": "720x520+10+10",
        "language": "zh_TW",
        "browser_settings": _migrated_default_browser_settings(),
    }


def test_load_normalizes_browser_settings(tmp_path, monkeypatch) -> None:
    import json

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "browser_settings": {
                    "enabled": True,
                    "browser_path": "  msedge  ",
                    "new_window": False,
                    "app_mode": True,
                    "x": "100",
                    "y": "200",
                    "width": "50",
                    "height": 800,
                    "minimized": True,
                    "ignored": "value",
                }
            }
        ),
        encoding="utf-8",
    )
    _use_config_path(monkeypatch, path)

    config = config_manager.load()
    settings = config["browser_settings"]

    assert settings["enabled"] is True
    assert settings["browser_path"] == "msedge"
    assert settings["new_window"] is False
    assert settings["app_mode"] is True
    assert settings["x"] == 100
    assert settings["y"] == 200
    assert settings["width"] == 100
    assert settings["height"] == 800
    assert settings["minimized"] is True
    assert "ignored" not in settings


def test_load_invalid_monitor_mode_falls_back(tmp_path, monkeypatch) -> None:
    import json

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"monitor_mode": "garbage"}), encoding="utf-8")
    _use_config_path(monkeypatch, path)

    assert config_manager.load()["monitor_mode"] == "trigger"


def test_load_watch_monitor_mode(tmp_path, monkeypatch) -> None:
    import json

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"monitor_mode": "watch"}), encoding="utf-8")
    _use_config_path(monkeypatch, path)

    assert config_manager.load()["monitor_mode"] == "watch"


def test_load_normalizes_close_on_offline(tmp_path, monkeypatch) -> None:
    import json

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"browser_settings": {"close_on_offline": True}}),
        encoding="utf-8",
    )
    _use_config_path(monkeypatch, path)

    assert config_manager.load()["browser_settings"]["close_on_offline"] is True


def test_load_close_on_offline_defaults_to_false(tmp_path, monkeypatch) -> None:
    _use_config_path(monkeypatch, tmp_path / "config.json")
    assert (
        config_manager.load()["browser_settings"]["close_on_offline"] is False
    )


def test_load_normalizes_close_on_stop(tmp_path, monkeypatch) -> None:
    import json

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"browser_settings": {"close_on_stop": True}}),
        encoding="utf-8",
    )
    _use_config_path(monkeypatch, path)

    assert config_manager.load()["browser_settings"]["close_on_stop"] is True


def test_load_close_on_stop_defaults_to_false(tmp_path, monkeypatch) -> None:
    _use_config_path(monkeypatch, tmp_path / "config.json")
    assert (
        config_manager.load()["browser_settings"]["close_on_stop"] is False
    )


def test_load_normalizes_close_off_topic_pages(tmp_path, monkeypatch) -> None:
    import json

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"browser_settings": {"close_off_topic_pages": True}}),
        encoding="utf-8",
    )
    _use_config_path(monkeypatch, path)

    assert (
        config_manager.load()["browser_settings"]["close_off_topic_pages"] is True
    )


def test_load_close_off_topic_pages_defaults_to_false(tmp_path, monkeypatch) -> None:
    _use_config_path(monkeypatch, tmp_path / "config.json")
    assert (
        config_manager.load()["browser_settings"]["close_off_topic_pages"] is False
    )


def test_load_normalizes_hide_from_taskbar(tmp_path, monkeypatch) -> None:
    import json

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"browser_settings": {"hide_from_taskbar": True}}),
        encoding="utf-8",
    )
    _use_config_path(monkeypatch, path)

    assert config_manager.load()["browser_settings"]["hide_from_taskbar"] is True


def test_load_hide_from_taskbar_defaults_to_false(tmp_path, monkeypatch) -> None:
    _use_config_path(monkeypatch, tmp_path / "config.json")
    assert (
        config_manager.load()["browser_settings"]["hide_from_taskbar"] is False
    )


# ---------------------------------------------------------------------------
# Migration: per_channel_profile=True + user_data_dir=""
# ---------------------------------------------------------------------------


def test_migrate_orphan_per_channel_fills_default_path() -> None:
    """``per_channel_profile=True`` + empty ``user_data_dir`` is a known
    pre-migration pitfall — it leaves every channel sharing one Chrome
    master process. Migration must fill in the default profile root.
    """
    settings = {
        "per_channel_profile": True,
        "user_data_dir": "",
    }
    config_manager._migrate_browser_settings(settings)

    assert settings["user_data_dir"]
    assert settings["user_data_dir"].endswith("browser_profile")


def test_migrate_orphan_per_channel_idempotent() -> None:
    """Running migration twice should be a no-op once the user_data_dir is set."""
    settings = {"per_channel_profile": True, "user_data_dir": ""}
    config_manager._migrate_browser_settings(settings)
    first_pass = settings["user_data_dir"]

    config_manager._migrate_browser_settings(settings)
    assert settings["user_data_dir"] == first_pass


def test_migrate_skips_when_per_channel_disabled() -> None:
    """If the user explicitly turned ``per_channel_profile`` off, leave the
    empty ``user_data_dir`` alone — they've opted into the shared-profile
    behaviour and we shouldn't second-guess that choice.
    """
    settings = {"per_channel_profile": False, "user_data_dir": ""}
    config_manager._migrate_browser_settings(settings)

    assert settings["user_data_dir"] == ""


def test_save_preserves_explicit_profile_opt_out(tmp_path, monkeypatch) -> None:
    """The settings dialog represents "Use a dedicated profile" off as
    ``user_data_dir=""`` plus ``per_channel_profile=False``.

    Saving that explicit opt-out must not be migrated back into the default
    ``browser_profile`` folder, otherwise users cannot actually disable
    profile isolation from the UI.
    """
    path = tmp_path / "config.json"
    _use_config_path(monkeypatch, path)

    config_manager.save(
        {
            "browser_settings": {
                "enabled": True,
                "user_data_dir": "",
                "per_channel_profile": False,
            }
        }
    )

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    settings = on_disk["browser_settings"]
    assert settings["user_data_dir"] == ""
    assert settings["per_channel_profile"] is False


def test_migrate_preserves_user_supplied_path() -> None:
    """Custom ``user_data_dir`` paths must survive migration unchanged."""
    settings = {
        "per_channel_profile": True,
        "user_data_dir": "C:/custom/profile",
    }
    config_manager._migrate_browser_settings(settings)

    assert settings["user_data_dir"] == "C:/custom/profile"


def test_load_migrates_orphan_per_channel_on_disk(tmp_path, monkeypatch) -> None:
    """Loading a legacy config (per_channel=True + empty user_data_dir) must
    transparently inject the default profile path so the runtime sees a
    consistent state.
    """
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "browser_settings": {
                    "enabled": True,
                    "per_channel_profile": True,
                    "user_data_dir": "",
                }
            }
        ),
        encoding="utf-8",
    )
    _use_config_path(monkeypatch, path)

    config = config_manager.load()
    user_data_dir = config["browser_settings"]["user_data_dir"]

    assert user_data_dir
    assert user_data_dir.endswith("browser_profile")


def test_load_self_heals_disk_after_migration(tmp_path, monkeypatch) -> None:
    """When normalization changes anything (missing keys, applied migration),
    ``load()`` should rewrite the file so the next launch sees a clean schema.
    """
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "browser_settings": {
                    "enabled": True,
                    "per_channel_profile": True,
                    "user_data_dir": "",
                }
            }
        ),
        encoding="utf-8",
    )
    _use_config_path(monkeypatch, path)

    config_manager.load()

    # Disk should now contain the post-migration data.
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["browser_settings"]["user_data_dir"].endswith("browser_profile")
    # Plus all default keys merged in.
    for key in config_manager.DEFAULT_BROWSER_SETTINGS:
        assert key in on_disk["browser_settings"]
    for key in config_manager.DEFAULT_CONFIG:
        assert key in on_disk


def test_load_skips_save_when_disk_is_already_normalized(
    tmp_path, monkeypatch
) -> None:
    """If the on-disk file is already in canonical form, ``load()`` must
    not rewrite it (avoid unnecessary disk churn on every launch).
    """
    path = tmp_path / "config.json"
    _use_config_path(monkeypatch, path)

    # Seed disk with the canonical form by saving an empty config first.
    config_manager.save({})
    original_mtime = path.stat().st_mtime_ns

    # Touch a sentinel to force any subsequent write to bump the mtime.
    import time

    time.sleep(0.01)

    config_manager.load()
    assert path.stat().st_mtime_ns == original_mtime, (
        "load() must not rewrite an already-canonical file"
    )


def test_load_does_not_create_file_when_missing(tmp_path, monkeypatch) -> None:
    """If config.json doesn't exist, ``load()`` must return in-memory defaults
    without creating the file (preserves the historical first-launch contract).
    """
    path = tmp_path / "config.json"
    _use_config_path(monkeypatch, path)

    config_manager.load()
    assert not path.exists()


# ---------------------------------------------------------------------------
# Combination sanity: every browser_settings flag normalises cleanly.
# ---------------------------------------------------------------------------


def test_normalize_clamps_undersized_geometry() -> None:
    """``width`` / ``height`` below 100 must be clamped to 100 — anything
    smaller produces an unusable browser window and Chrome may even refuse
    to render it.
    """
    result = config_manager._normalize_browser_settings(
        {"width": 5, "height": -100}
    )
    assert result["width"] == 100
    assert result["height"] == 100


def test_normalize_coerces_string_numeric_geometry() -> None:
    """Geometry values written by older versions could be JSON strings.
    Normalisation must coerce them back to ints without raising.
    """
    result = config_manager._normalize_browser_settings(
        {"x": "50", "y": "60", "width": "800", "height": "600"}
    )
    assert (result["x"], result["y"]) == (50, 60)
    assert (result["width"], result["height"]) == (800, 600)


def test_normalize_rejects_non_string_browser_path() -> None:
    """Non-string ``browser_path`` (e.g. someone hand-edited JSON to null)
    must fall back to the default rather than raise downstream.
    """
    result = config_manager._normalize_browser_settings(
        {"browser_path": None}
    )
    assert result["browser_path"] == "chrome"


def test_normalize_rejects_whitespace_only_browser_path() -> None:
    """A whitespace-only ``browser_path`` is indistinguishable from empty —
    fall back to the default ``"chrome"`` so launches still succeed.
    """
    result = config_manager._normalize_browser_settings(
        {"browser_path": "   "}
    )
    assert result["browser_path"] == "chrome"


def test_normalize_rejects_non_bool_flag_values() -> None:
    """Boolean flags fed garbage (strings/ints/None) must keep their
    default value, not silently flip to truthy/falsy interpretations.
    """
    raw_garbage = {
        "enabled": "yes",
        "new_window": 1,
        "app_mode": "true",
        "apply_geometry": None,
        "minimized": "",
        "per_channel_profile": 0,
        "close_on_offline": "false",
        "close_on_stop": [],
        "close_off_topic_pages": {},
        "hide_from_taskbar": "1",
    }
    result = config_manager._normalize_browser_settings(raw_garbage)

    for key in raw_garbage:
        assert result[key] == config_manager.DEFAULT_BROWSER_SETTINGS[key], (
            f"flag {key!r} must fall back to default when fed non-bool input"
        )


def test_normalize_strips_user_data_dir_whitespace() -> None:
    result = config_manager._normalize_browser_settings(
        {
            "user_data_dir": "  C:/profiles/main  ",
            "per_channel_profile": True,
        }
    )
    assert result["user_data_dir"] == "C:/profiles/main"


def test_normalize_drops_unknown_keys() -> None:
    """Keys that aren't part of the schema (e.g. dropped settings from older
    versions) must not be carried through into the normalized output.
    """
    result = config_manager._normalize_browser_settings(
        {"unknown_legacy_flag": True, "ignored_dict": {"nested": 1}}
    )
    assert "unknown_legacy_flag" not in result
    assert "ignored_dict" not in result


def test_normalize_browser_settings_idempotent_on_clean_input() -> None:
    """Normalising twice must produce the same dict — no oscillating
    transformations or accidental field churn.
    """
    once = config_manager._normalize_browser_settings(
        {
            "enabled": True,
            "browser_path": "msedge",
            "per_channel_profile": True,
            "user_data_dir": "C:/profiles/main",
            "close_off_topic_pages": True,
        }
    )
    twice = config_manager._normalize_browser_settings(once)
    assert once == twice


def test_load_inserts_missing_legacy_keys(tmp_path, monkeypatch) -> None:
    """A config written by an older version (missing ``language`` /
    ``monitor_mode`` / new browser flags) must come back populated with
    sane defaults rather than ``KeyError``-ing on first access.
    """
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "channels": [],
                "check_interval": 30,
                "action": "open_and_stop",
                # Deliberately omit monitor_mode, language, browser_settings.
            }
        ),
        encoding="utf-8",
    )
    _use_config_path(monkeypatch, path)

    config = config_manager.load()
    assert config["monitor_mode"] == "trigger"
    assert config["language"] == config_manager.DEFAULT_CONFIG["language"]
    assert "close_off_topic_pages" in config["browser_settings"]
    assert "hide_from_taskbar" in config["browser_settings"]
