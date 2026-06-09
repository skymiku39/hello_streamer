from importlib.metadata import version

from stream_monitor import __version__


def test_version_matches_pyproject() -> None:
    assert version("stream-monitor") == __version__
