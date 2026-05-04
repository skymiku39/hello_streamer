from stream_monitor.url_parser import ParsedChannel, parse_url


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


def test_parse_rejects_invalid_urls_and_twitch_reserved_paths() -> None:
    assert parse_url("not a stream URL") is None
    assert parse_url("https://www.twitch.tv/directory") is None
    assert parse_url("https://www.twitch.tv/settings") is None


def test_parse_rejects_youtube_live_and_video_pages() -> None:
    assert parse_url("https://www.youtube.com/watch?v=abc123") is None
    assert parse_url("https://www.youtube.com/shorts/abc123") is None
    assert parse_url("https://www.youtube.com/live/abc123") is None
