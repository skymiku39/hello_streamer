"""PyInstaller build script for Stream Monitor."""

import subprocess
import sys


def main() -> None:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--windowed",
        "--name",
        "StreamMonitor",
        "--collect-data",
        "customtkinter",
        "--add-data",
        "stream_monitor;stream_monitor",
        "stream_monitor/app.py",
    ]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
