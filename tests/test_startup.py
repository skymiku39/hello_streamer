import sys

from stream_monitor import startup

# ---------------------------------------------------------------------------
# Windows tests (use FakeWinreg mock — run on any platform)
# ---------------------------------------------------------------------------


class FakeKey:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeWinreg:
    HKEY_CURRENT_USER = object()
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self, fail_open: bool = False) -> None:
        self.fail_open = fail_open
        self.values: dict[str, str] = {}

    def OpenKey(self, *_args):
        if self.fail_open:
            raise OSError("registry unavailable")
        return FakeKey()

    def SetValueEx(self, _key, name, _reserved, _kind, value) -> None:
        self.values[name] = value

    def QueryValueEx(self, _key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def DeleteValue(self, _key, name) -> None:
        del self.values[name]


def test_enable_startup_writes_packaged_exe_command(monkeypatch) -> None:
    fake_winreg = FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(startup, "_IS_WINDOWS", True)
    monkeypatch.setattr(startup.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        startup.sys,
        "executable",
        r"C:\Program Files\HelloStreamer\HelloStreamer.exe",
    )

    assert startup.enable_startup() is True

    assert fake_winreg.values["StreamMonitor"] == (
        r'"C:\Program Files\HelloStreamer\HelloStreamer.exe" --silent'
    )


def test_enable_startup_returns_false_in_non_frozen_mode(monkeypatch) -> None:
    fake_winreg = FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(startup, "_IS_WINDOWS", True)
    monkeypatch.delattr(startup.sys, "frozen", raising=False)

    assert startup.enable_startup() is False
    assert fake_winreg.values == {}


def test_enable_startup_returns_false_on_registry_error(monkeypatch) -> None:
    fake_winreg = FakeWinreg(fail_open=True)
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(startup, "_IS_WINDOWS", True)
    monkeypatch.setattr(startup.sys, "frozen", True, raising=False)

    assert startup.enable_startup(exe_path=r"C:\HelloStreamer.exe") is False


def test_heal_startup_rewrites_stale_exe_path(monkeypatch) -> None:
    fake_winreg = FakeWinreg()
    fake_winreg.values["StreamMonitor"] = (
        r'"D:\Tools\HelloStreamer-v1.1.7-windows-x64.exe" --silent'
    )
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(startup, "_IS_WINDOWS", True)
    monkeypatch.setattr(startup.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        startup.sys,
        "executable",
        r"D:\Tools\HelloStreamer-v1.2.0-windows-x64.exe",
    )

    assert startup.heal_startup_command_if_enabled() is True
    assert fake_winreg.values["StreamMonitor"] == (
        r"D:\Tools\HelloStreamer-v1.2.0-windows-x64.exe --silent"
    )


def test_heal_startup_noop_when_disabled(monkeypatch) -> None:
    fake_winreg = FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(startup, "_IS_WINDOWS", True)

    assert startup.heal_startup_command_if_enabled() is False
    assert fake_winreg.values == {}


# ---------------------------------------------------------------------------
# Linux tests (XDG Autostart)
# ---------------------------------------------------------------------------


def test_linux_is_startup_enabled_returns_true_when_file_exists(monkeypatch, tmp_path) -> None:
    desktop_file = tmp_path / ".config" / "autostart" / "stream-monitor.desktop"
    desktop_file.parent.mkdir(parents=True)
    desktop_file.write_text("[Desktop Entry]\nExec=hello\n")

    monkeypatch.setattr(startup, "_IS_WINDOWS", False)
    monkeypatch.setattr(startup, "_autostart_path", lambda: desktop_file)

    assert startup.is_startup_enabled() is True


def test_linux_is_startup_enabled_returns_false_when_missing(monkeypatch, tmp_path) -> None:
    desktop_file = tmp_path / ".config" / "autostart" / "stream-monitor.desktop"

    monkeypatch.setattr(startup, "_IS_WINDOWS", False)
    monkeypatch.setattr(startup, "_autostart_path", lambda: desktop_file)

    assert startup.is_startup_enabled() is False


def test_linux_enable_startup_creates_desktop_file(monkeypatch, tmp_path) -> None:
    desktop_file = tmp_path / ".config" / "autostart" / "stream-monitor.desktop"

    monkeypatch.setattr(startup, "_IS_WINDOWS", False)
    monkeypatch.setattr(startup, "_autostart_path", lambda: desktop_file)
    monkeypatch.setattr(startup.sys, "frozen", True, raising=False)
    monkeypatch.setattr(startup.sys, "executable", "/opt/hello-streamer/HelloStreamer")

    assert startup.enable_startup() is True
    assert desktop_file.exists()

    content = desktop_file.read_text()
    assert "[Desktop Entry]" in content
    assert "--silent" in content
    assert "/opt/hello-streamer/HelloStreamer" in content


def test_linux_enable_startup_returns_false_without_frozen(monkeypatch, tmp_path) -> None:
    desktop_file = tmp_path / ".config" / "autostart" / "stream-monitor.desktop"

    monkeypatch.setattr(startup, "_IS_WINDOWS", False)
    monkeypatch.setattr(startup, "_autostart_path", lambda: desktop_file)
    monkeypatch.delattr(startup.sys, "frozen", raising=False)

    assert startup.enable_startup() is False
    assert not desktop_file.exists()


def test_linux_disable_startup_removes_file(monkeypatch, tmp_path) -> None:
    desktop_file = tmp_path / ".config" / "autostart" / "stream-monitor.desktop"
    desktop_file.parent.mkdir(parents=True)
    desktop_file.write_text("[Desktop Entry]\nExec=hello\n")

    monkeypatch.setattr(startup, "_IS_WINDOWS", False)
    monkeypatch.setattr(startup, "_autostart_path", lambda: desktop_file)

    assert startup.disable_startup() is True
    assert not desktop_file.exists()


def test_linux_disable_startup_ok_when_already_missing(monkeypatch, tmp_path) -> None:
    desktop_file = tmp_path / ".config" / "autostart" / "stream-monitor.desktop"

    monkeypatch.setattr(startup, "_IS_WINDOWS", False)
    monkeypatch.setattr(startup, "_autostart_path", lambda: desktop_file)

    assert startup.disable_startup() is True


# ---------------------------------------------------------------------------
# Startup sync must not hijack an entry that points at another live exe
# ---------------------------------------------------------------------------


def _setup_win(monkeypatch, registered: str, current: str) -> FakeWinreg:
    fake_winreg = FakeWinreg()
    fake_winreg.values["StreamMonitor"] = registered
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(startup, "_IS_WINDOWS", True)
    monkeypatch.setattr(startup.sys, "frozen", True, raising=False)
    monkeypatch.setattr(startup.sys, "executable", current)
    return fake_winreg


def test_heal_keeps_entry_pointing_at_other_existing_exe(monkeypatch, tmp_path) -> None:
    other = tmp_path / "installed" / "HelloStreamer.exe"
    other.parent.mkdir()
    other.write_text("x")
    registered = f'"{other}" --silent'
    fake = _setup_win(monkeypatch, registered, str(tmp_path / "dist" / "HelloStreamer.exe"))

    assert startup.heal_startup_command_if_enabled(keep_existing_other_exe=True) is False
    assert fake.values["StreamMonitor"] == registered


def test_heal_rewrites_entry_pointing_at_missing_exe(monkeypatch, tmp_path) -> None:
    missing = tmp_path / "moved" / "HelloStreamer.exe"
    current = str(tmp_path / "new" / "HelloStreamer.exe")
    fake = _setup_win(monkeypatch, f'"{missing}" --silent', current)

    assert startup.heal_startup_command_if_enabled(keep_existing_other_exe=True) is True
    assert fake.values["StreamMonitor"] == f"{current} --silent"


def test_heal_same_exe_case_insensitive_still_updates(monkeypatch, tmp_path) -> None:
    exe = tmp_path / "HelloStreamer.exe"
    exe.write_text("x")
    fake = _setup_win(monkeypatch, f'"{str(exe).upper()}"', str(exe))

    assert startup.heal_startup_command_if_enabled(keep_existing_other_exe=True) is True
    assert fake.values["StreamMonitor"] == f"{exe} --silent"


def test_user_enable_always_writes_current_path(monkeypatch, tmp_path) -> None:
    other = tmp_path / "installed" / "HelloStreamer.exe"
    other.parent.mkdir()
    other.write_text("x")
    current = str(tmp_path / "dist" / "HelloStreamer.exe")
    fake = _setup_win(monkeypatch, f'"{other}" --silent', current)

    assert startup.enable_startup() is True
    assert fake.values["StreamMonitor"] == f"{current} --silent"


def test_parse_command_exe_handles_quotes_and_args() -> None:
    parse = startup._parse_command_exe
    assert parse(r'"D:\Tools\HelloStreamer\HelloStreamer.exe" --silent') == (
        r"D:\Tools\HelloStreamer\HelloStreamer.exe"
    )
    assert parse(r"D:\Tools\Hello Streamer\a.exe --silent") == (
        r"D:\Tools\Hello Streamer\a.exe"
    )
    assert parse(r"C:\a\b.exe") == r"C:\a\b.exe"
    assert parse("") is None


def test_linux_heal_keeps_other_existing_exe(monkeypatch, tmp_path) -> None:
    other = tmp_path / "HelloStreamer"
    other.write_text("x")
    desktop_file = tmp_path / "stream-monitor.desktop"
    desktop_file.write_text(f'[Desktop Entry]\nExec="{other}" --silent\n')
    monkeypatch.setattr(startup, "_IS_WINDOWS", False)
    monkeypatch.setattr(startup, "_autostart_path", lambda: desktop_file)
    monkeypatch.setattr(startup.sys, "frozen", True, raising=False)
    monkeypatch.setattr(startup.sys, "executable", "/opt/dev/HelloStreamer")

    assert startup.heal_startup_command_if_enabled(keep_existing_other_exe=True) is False
    assert "/opt/dev" not in desktop_file.read_text()
