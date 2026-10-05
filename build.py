"""PyInstaller build script for HelloStreamer (Windows & Linux).

Uses ``--onedir`` (not ``--onefile``) and ``--noupx`` to reduce Windows
Defender / SmartScreen false positives: onefile self-extracts to ``%TEMP%``,
which looks like a malware dropper to heuristic scanners.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from stream_monitor import __version__

# winotify is imported lazily inside notifier toast code; PyInstaller will not
# see it unless we force a hidden import for frozen Windows builds.
WINDOWS_HIDDEN_IMPORTS: tuple[str, ...] = ("pystray._win32", "winotify")
LINUX_HIDDEN_IMPORTS: tuple[str, ...] = ("pystray._appindicator",)


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


def normalize_release_tag(tag: str) -> str:
    """Strip a leading ``v`` / ``V`` from a git release tag."""
    if len(tag) > 1 and tag[0] in "vV" and tag[1].isdigit():
        return tag[1:]
    return tag


def require_tag_matches_package_version(
    tag: str,
    *,
    module_version: str | None = None,
    distribution_version: str | None = None,
    distribution_name: str = "stream-monitor",
) -> None:
    """Fail if tag (without leading v) != module / installed package version."""
    sm_version = module_version if module_version is not None else __version__
    if distribution_version is None:
        from importlib.metadata import version as pkg_version

        dist_version = pkg_version(distribution_name)
    else:
        dist_version = distribution_version
    if sm_version != dist_version:
        raise SystemExit(
            f"stream_monitor.__version__ ({sm_version!r}) != "
            f"{distribution_name} package version ({dist_version!r})"
        )
    normalized = normalize_release_tag(tag)
    if normalized != sm_version:
        raise SystemExit(
            f"Release tag {tag!r} (normalized {normalized!r}) != "
            f"package version {sm_version!r}"
        )


def validate_windows_onedir_bundle(bundle_dir: Path) -> None:
    """Require Windows onedir layout: HelloStreamer.exe + _internal/."""
    exe = bundle_dir / "HelloStreamer.exe"
    internal = bundle_dir / "_internal"
    missing: list[str] = []
    if not exe.is_file():
        missing.append(str(exe))
    if not internal.is_dir():
        missing.append(str(internal))
    if missing:
        raise FileNotFoundError(
            "Windows onedir bundle incomplete; missing: " + ", ".join(missing)
        )


def validate_linux_onedir_bundle(bundle_dir: Path) -> None:
    """Require Linux onedir layout: nonempty HelloStreamer + nonempty ``_internal/``.

    Does not assert the Unix execute bit; retain a shell ``test -x`` for that.
    """
    exe = bundle_dir / "HelloStreamer"
    internal = bundle_dir / "_internal"
    problems: list[str] = []
    if not exe.is_file():
        problems.append(f"missing executable: {exe}")
    elif exe.stat().st_size == 0:
        problems.append(f"empty executable: {exe}")
    if not internal.is_dir():
        problems.append(f"missing _internal: {internal}")
    elif not any(internal.iterdir()):
        problems.append(f"empty _internal: {internal}")
    if problems:
        raise FileNotFoundError(
            "Linux onedir bundle incomplete; " + "; ".join(problems)
        )


def build_pyinstaller_command(
    *,
    is_windows: bool,
    root: Path,
    version: str,
    python_executable: str | None = None,
) -> list[str]:
    """Build the PyInstaller argv (does not run it)."""
    separator = ";" if is_windows else ":"
    exe = python_executable if python_executable is not None else sys.executable
    cmd = [
        exe,
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
        _write_windows_version_file(version_file, version)
        cmd += ["--version-file", str(version_file)]
        for hidden in WINDOWS_HIDDEN_IMPORTS:
            cmd += ["--hidden-import", hidden]
    else:
        for hidden in LINUX_HIDDEN_IMPORTS:
            cmd += ["--hidden-import", hidden]

    cmd.append("stream_monitor/app.py")
    return cmd


def main() -> None:
    root = Path(__file__).resolve().parent
    cmd = build_pyinstaller_command(
        is_windows=sys.platform == "win32",
        root=root,
        version=__version__,
    )
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"Built onedir bundle: {root / 'dist' / 'HelloStreamer'}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HelloStreamer PyInstaller build helpers")
    parser.add_argument(
        "--check-release-tag",
        metavar="TAG",
        help="Verify TAG (without leading v) matches module/package version, then exit",
    )
    parser.add_argument(
        "--validate-windows-onedir",
        metavar="DIR",
        help="Validate Windows onedir bundle DIR (exe + _internal), then exit",
    )
    parser.add_argument(
        "--validate-linux-onedir",
        metavar="DIR",
        help=(
            "Validate Linux onedir bundle DIR (nonempty HelloStreamer + "
            "nonempty _internal), then exit"
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    if args.check_release_tag is not None:
        require_tag_matches_package_version(args.check_release_tag)
        print(f"OK: tag {args.check_release_tag!r} matches version {__version__!r}")
    elif args.validate_windows_onedir is not None:
        validate_windows_onedir_bundle(Path(args.validate_windows_onedir))
        print(f"OK: Windows onedir bundle at {args.validate_windows_onedir}")
    elif args.validate_linux_onedir is not None:
        validate_linux_onedir_bundle(Path(args.validate_linux_onedir))
        print(f"OK: Linux onedir bundle at {args.validate_linux_onedir}")
    else:
        main()
