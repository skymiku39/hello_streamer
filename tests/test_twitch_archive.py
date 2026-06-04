from stream_monitor.fetcher.twitch import TwitchFetcher


def test_get_latest_archive_url_parses_video_id(monkeypatch) -> None:
    fetcher = TwitchFetcher()

    def fake_gql(_query: str, _variables: dict[str, str]) -> dict:
        return {
            "data": {
                "user": {
                    "videos": {
                        "edges": [{"node": {"id": "1234567890"}}],
                    }
                }
            }
        }

    monkeypatch.setattr(fetcher, "_gql", fake_gql)
    assert fetcher.get_latest_archive_url("hello") == (
        "https://www.twitch.tv/videos/1234567890"
    )


def test_get_latest_archive_url_empty_edges(monkeypatch) -> None:
    fetcher = TwitchFetcher()
    monkeypatch.setattr(
        fetcher,
        "_gql",
        lambda _q, _v: {"data": {"user": {"videos": {"edges": []}}}},
    )
    assert fetcher.get_latest_archive_url("hello") is None
