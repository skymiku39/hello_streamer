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
                    {"platform": "Twitch", "name": " Some_Channel "},
                    {"platform": "youtube", "name": ""},
                    {"platform": "mixer", "name": "someone"},
                    "bad",
                ],
                "check_interval": 5,
                "action": "bad_action",
                "run_on_startup": "yes",
                "window_geometry": 123,
            }
        ),
        encoding="utf-8",
    )
    _use_config_path(monkeypatch, path)

    config = config_manager.load()

    assert config["channels"] == [{"platform": "twitch", "name": "Some_Channel"}]
    assert config["check_interval"] == config_manager.MIN_CHECK_INTERVAL
    assert config["action"] == "open_and_stop"
    assert config["run_on_startup"] is False
    assert config["window_geometry"] is None


def test_load_non_numeric_interval_uses_default(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"check_interval": "soon"}), encoding="utf-8")
    _use_config_path(monkeypatch, path)

    assert config_manager.load()["check_interval"] == 60


def test_save_is_atomic_and_reloadable(tmp_path, monkeypatch) -> None:
    path = tmp_path / "nested" / "config.json"
    _use_config_path(monkeypatch, path)

    config_manager.save(
        {
            "channels": [{"platform": "youtube", "name": "hello"}],
            "check_interval": 30,
            "action": "notify_only",
            "run_on_startup": True,
            "window_geometry": "720x520+10+10",
        }
    )

    assert path.exists()
    assert not path.with_name(".config.json.tmp").exists()
    assert config_manager.load() == {
        "channels": [{"platform": "youtube", "name": "hello"}],
        "check_interval": 30,
        "action": "notify_only",
        "run_on_startup": True,
        "window_geometry": "720x520+10+10",
    }
