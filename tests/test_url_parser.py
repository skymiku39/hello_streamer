from stream_monitor.url_parser import ParsedChannel, parse_url


def test_parse_youtube_bare_handle_shorthand() -> None:
    assert parse_url("@handle") == ParsedChannel(platform="youtube", name="handle")
    assert parse_url("@hello.streamer") == ParsedChannel(
        platform="youtube",
        name="hello.streamer",
    )
    assert parse_url("  @hello.streamer  ") == ParsedChannel(
        platform="youtube",
        name="hello.streamer",
    )
    assert parse_url("@\u4e2d\u6587\u540d\u5b57") == ParsedChannel(
        platform="youtube",
        name="\u4e2d\u6587\u540d\u5b57",
    )


def test_parse_rejects_invalid_bare_handles() -> None:
    assert parse_url("@") is None
    assert parse_url("@@hello") is None
    assert parse_url("@hello world") is None
    assert parse_url("@hello/streams") is None


def test_parse_twitch_url_lowercases_name() -> None:
    assert parse_url("https://www.twitch.tv/Some_Channel") == ParsedChannel(
        platform="twitch",
        name="some_channel",
    )


def test_parse_youtube_url_variants() -> None:
    assert parse_url("https://www.youtube.com/@hello.streamer") == ParsedChannel(
        platform="youtube",
        name="hello.streamer",
    )
    assert parse_url("https://www.youtube.com/@hello.streamer/live") == ParsedChannel(
        platform="youtube",
        name="hello.streamer",
    )
    assert parse_url("https://www.youtube.com/@hello.streamer/streams") == ParsedChannel(
        platform="youtube",
        name="hello.streamer",
    )
    assert parse_url("https://www.youtube.com/@hello.streamer/shorts/abc") == ParsedChannel(
        platform="youtube",
        name="hello.streamer",
    )
    assert parse_url("www.youtube.com/@hello.streamer") == ParsedChannel(
        platform="youtube",
        name="hello.streamer",
    )
    assert parse_url("https://www.youtube.com/channel/UCabc_123-def") == ParsedChannel(
        platform="youtube",
        name="UCabc_123-def",
    )
    assert parse_url("https://www.youtube.com/c/Hello-Streamer") == ParsedChannel(
        platform="youtube",
        name="Hello-Streamer",
    )
    assert parse_url("https://www.youtube.com/user/HelloStreamer") == ParsedChannel(
        platform="youtube",
        name="HelloStreamer",
    )


def test_parse_twitch_url_with_subpaths() -> None:
    assert parse_url("https://www.twitch.tv/kaicenat/videos") == ParsedChannel(
        platform="twitch",
        name="kaicenat",
    )
    assert parse_url("https://www.twitch.tv/kaicenat/clips") == ParsedChannel(
        platform="twitch",
        name="kaicenat",
    )
    assert parse_url("https://www.twitch.tv/kaicenat/about") == ParsedChannel(
        platform="twitch",
        name="kaicenat",
    )


def test_parse_rejects_invalid_urls_and_twitch_reserved_paths() -> None:
    assert parse_url("not a stream URL") is None
    assert parse_url("https://www.twitch.tv/directory") is None
    assert parse_url("https://www.twitch.tv/settings") is None
    assert parse_url("https://www.twitch.tv/directory/game/Just%20Chatting") is None


def test_parse_youtube_url_with_encoded_unicode_handle() -> None:
    result = parse_url(
        "https://www.youtube.com/@%E9%B3%B6%E5%86%A5%C2%B7%E7%BF%A0%E9%9A%B1/videos"
    )
    assert result == ParsedChannel(
        platform="youtube", name="\u9cf6\u51a5\u00b7\u7fe0\u96b1"
    )

    result = parse_url("https://www.youtube.com/@%E4%B8%AD%E6%96%87%E5%90%8D%E5%AD%97")
    assert result == ParsedChannel(platform="youtube", name="\u4e2d\u6587\u540d\u5b57")


def test_parse_youtube_url_with_raw_unicode_handle() -> None:
    assert parse_url(
        "https://www.youtube.com/@\u9cf6\u51a5\u00b7\u7fe0\u96b1/live"
    ) == ParsedChannel(platform="youtube", name="\u9cf6\u51a5\u00b7\u7fe0\u96b1")


def test_parse_youtube_watch_url_resolves_channel(monkeypatch) -> None:
    class _FakeFetcher:
        def resolve_channel_from_video(self, video_id: str):
            if video_id == "abc123":
                return ("LofiGirl", "Lofi Girl")
            return None

    monkeypatch.setattr(
        "stream_monitor.fetcher.youtube.YouTubeFetcher",
        _FakeFetcher,
    )
    assert parse_url("https://www.youtube.com/watch?v=abc123") == ParsedChannel(
        platform="youtube",
        name="LofiGirl",
    )


def test_parse_rejects_youtube_shorts_and_live_pages() -> None:
    assert parse_url("https://www.youtube.com/shorts/abc123") is None
    assert parse_url("https://www.youtube.com/live/abc123") is None
