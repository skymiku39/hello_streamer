"""PyInstaller build script for HelloStreamer."""

import subprocess
import sys


def main() -> None:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--windowed",
        "--name", "HelloStreamer",
        "--collect-data", "customtkinter",
        "--hidden-import", "pystray._win32",
        "--add-data", "stream_monitor;stream_monitor",
        "stream_monitor/app.py",
    ]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
