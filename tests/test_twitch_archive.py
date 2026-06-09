import threading
import time

from stream_monitor.fetcher.twitch import TwitchFetcher


def test_get_latest_archive_url_parses_video_id(monkeypatch) -> None:
    fetcher = TwitchFetcher()

    def fake_gql(_query: str, _variables: dict[str, str]) -> dict:
        return {
            "data": {
                "user": {
                    "videos": {
                        "edges": [
                            {
                                "node": {
                                    "id": "1234567890",
                                    "title": "Past stream",
                                    "createdAt": "2026-06-05T08:00:00Z",
                                    "lengthSeconds": 3600,
                                }
                            }
                        ],
                    }
                }
            }
        }

    monkeypatch.setattr(fetcher, "_gql", fake_gql)
    assert fetcher.get_latest_archive_url("hello") == (
        "https://www.twitch.tv/videos/1234567890"
    )


def test_get_latest_archive_computes_ended_at(monkeypatch) -> None:
    fetcher = TwitchFetcher()

    def fake_gql(_query: str, _variables: dict[str, str]) -> dict:
        return {
            "data": {
                "user": {
                    "videos": {
                        "edges": [
                            {
                                "node": {
                                    "id": "99",
                                    "title": "VOD",
                                    "createdAt": "2026-06-05T08:00:00Z",
                                    "lengthSeconds": 120,
                                }
                            }
                        ],
                    }
                }
            }
        }

    monkeypatch.setattr(fetcher, "_gql", fake_gql)
    archive = fetcher.get_latest_archive("hello")
    assert archive is not None
    assert archive.url == "https://www.twitch.tv/videos/99"
    assert archive.ended_at.startswith("2026-06-05T08:02:00")


def test_get_latest_archive_url_empty_edges(monkeypatch) -> None:
    fetcher = TwitchFetcher()
    monkeypatch.setattr(
        fetcher,
        "_gql",
        lambda _q, _v: {"data": {"user": {"videos": {"edges": []}}}},
    )
    assert fetcher.get_latest_archive_url("hello") is None


def test_get_channel_items_not_supported() -> None:
    """Twitch has no YouTube-style waiting room; default API returns empty."""
    fetcher = TwitchFetcher()
    assert fetcher.get_channel_items("hello") == []


def test_gql_serializes_concurrent_session_access(monkeypatch) -> None:
    """Parallel Monitor workers must not share requests.Session concurrently."""
    fetcher = TwitchFetcher()
    active = 0
    peak = 0
    track_lock = threading.Lock()

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": {}}

    def fake_post(*_args, **_kwargs) -> FakeResponse:
        nonlocal active, peak
        with track_lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.03)
        with track_lock:
            active -= 1
        return FakeResponse()

    monkeypatch.setattr(fetcher._session, "post", fake_post)

    threads = [
        threading.Thread(target=fetcher._gql, args=("query {}", {"login": "a"}))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert peak == 1
