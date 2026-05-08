"""PyInstaller build script for HelloStreamer (Windows & Linux)."""

import subprocess
import sys


def main() -> None:
    is_windows = sys.platform == "win32"
    separator = ";" if is_windows else ":"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--windowed",
        "--name", "HelloStreamer",
        "--collect-data", "customtkinter",
        "--add-data", f"stream_monitor{separator}stream_monitor",
        "stream_monitor/app.py",
    ]

    if is_windows:
        cmd += ["--hidden-import", "pystray._win32"]
    else:
        cmd += ["--hidden-import", "pystray._appindicator"]

    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
