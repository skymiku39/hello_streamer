"""Bounded, thread-safe delivery of page-assist outcomes to the Tk thread."""

from queue import Empty, Full, Queue

_notices: Queue[tuple[str, str]] = Queue(maxsize=32)


def publish_page_assist_status(url: str, reason: str) -> None:
    """An empty reason clears an earlier failure for the same URL."""
    try:
        _notices.put_nowait((url, reason))
    except Full:
        try:
            _notices.get_nowait()
        except Empty:
            pass
        try:
            _notices.put_nowait((url, reason))
        except Full:
            pass


def drain_page_assist_status() -> list[tuple[str, str]]:
    notices = []
    for _ in range(32):
        try:
            notices.append(_notices.get_nowait())
        except Empty:
            break
    return notices
