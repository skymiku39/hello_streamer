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
