"""PyInstaller build script for HelloStreamer (Windows & Linux).

Uses ``--onedir`` (not ``--onefile``) and ``--noupx`` to reduce Windows
Defender / SmartScreen false positives: onefile self-extracts to ``%TEMP%``,
which looks like a malware dropper to heuristic scanners.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from stream_monitor import __version__


def _version_tuple(version: str) -> tuple[int, int, int, int]:
    """Parse ``major.minor.patch`` into a 4-part PE version tuple."""
    parts: list[int] = []
    for piece in version.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    while len(parts) < 4:
        parts.append(0)
    return (parts[0], parts[1], parts[2], parts[3])


def _write_windows_version_file(path: Path, version: str) -> None:
    """Write a PyInstaller ``--version-file`` resource for Authenticode metadata."""
    major, minor, patch, build = _version_tuple(version)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, {build}),
    prodvers=({major}, {minor}, {patch}, {build}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '040904B0',
          [
            StringStruct('CompanyName', 'skymiku39'),
            StringStruct('FileDescription', 'Hello Streamer'),
            StringStruct('FileVersion', '{version}'),
            StringStruct('InternalName', 'HelloStreamer'),
            StringStruct('LegalCopyright', 'MIT License'),
            StringStruct('OriginalFilename', 'HelloStreamer.exe'),
            StringStruct('ProductName', 'Hello Streamer'),
            StringStruct('ProductVersion', '{version}'),
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )


def main() -> None:
    is_windows = sys.platform == "win32"
    separator = ";" if is_windows else ":"
    root = Path(__file__).resolve().parent

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--noupx",
        "--name",
        "HelloStreamer",
        "--collect-data",
        "customtkinter",
        "--add-data",
        f"stream_monitor{separator}stream_monitor",
    ]

    if is_windows:
        version_file = root / "build" / "HelloStreamer_version_info.txt"
        _write_windows_version_file(version_file, __version__)
        cmd += [
            "--version-file",
            str(version_file),
            "--hidden-import",
            "pystray._win32",
        ]
    else:
        cmd += ["--hidden-import", "pystray._appindicator"]

    cmd.append("stream_monitor/app.py")

    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"Built onedir bundle: {root / 'dist' / 'HelloStreamer'}")


if __name__ == "__main__":
    main()
