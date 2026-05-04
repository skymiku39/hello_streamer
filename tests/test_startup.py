import sys

from stream_monitor import startup


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
        return self.values[name], self.REG_SZ

    def DeleteValue(self, _key, name) -> None:
        del self.values[name]


def test_enable_startup_writes_packaged_exe_command(monkeypatch) -> None:
    fake_winreg = FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
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
    monkeypatch.delattr(startup.sys, "frozen", raising=False)

    assert startup.enable_startup() is False
    assert fake_winreg.values == {}


def test_enable_startup_returns_false_on_registry_error(monkeypatch) -> None:
    fake_winreg = FakeWinreg(fail_open=True)
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(startup.sys, "frozen", True, raising=False)

    assert startup.enable_startup(exe_path=r"C:\HelloStreamer.exe") is False
