import json
from pathlib import Path

from stream_monitor import config_manager


def _use_config_path(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(config_manager, "_config_path", lambda: path)


def test_load_missing_file_uses_defaults(tmp_path, monkeypatch) -> None:
    _use_config_path(monkeypatch, tmp_path / "config.json")

    assert config_manager.load() == config_manager.DEFAULT_CONFIG


def test_load_bad_json_uses_defaults(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.json"
    path.write_text("{", encoding="utf-8")
    _use_config_path(monkeypatch, path)

    assert config_manager.load() == config_manager.DEFAULT_CONFIG


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
    assert config["browser_settings"] == config_manager.DEFAULT_BROWSER_SETTINGS


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
        "browser_settings": config_manager.DEFAULT_BROWSER_SETTINGS,
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
