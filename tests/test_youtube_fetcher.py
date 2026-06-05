import json

from stream_monitor.fetcher.youtube import YouTubeFetcher, _WatchDetails


def _html_with_player(player_json: str, extra: str = "") -> str:
    return f"""
    <html>
      <script>var ytInitialPlayerResponse = {player_json};</script>
      {extra}
    </html>
    """


def _html_with_initial_data(data: dict) -> str:
    return (
        "<html><script>var ytInitialData = "
        + json.dumps(data)
        + ";</script></html>"
    )


def _make_video_renderer(
    video_id: str,
    title: str = "Title",
    style: str = "DEFAULT",
    start_time: str | None = None,
) -> dict:
    overlays = [{"thumbnailOverlayTimeStatusRenderer": {"style": style}}]
    renderer = {
        "videoId": video_id,
        "title": {"runs": [{"text": title}]},
        "thumbnailOverlays": overlays,
    }
    if start_time:
        renderer["upcomingEventData"] = {"startTime": start_time}
    return renderer


def _make_initial_data(
    renderers: list[dict],
    display_name: str = "TestChannel",
) -> dict:
    contents = [
        {"richItemRenderer": {"content": {"videoRenderer": r}}} for r in renderers
    ]
    return {
        "metadata": {"channelMetadataRenderer": {"title": display_name}},
        "contents": {
            "twoColumnBrowseResultsRenderer": {
                "tabs": [
                    {
                        "tabRenderer": {
                            "content": {
                                "richGridRenderer": {"contents": contents}
                            }
                        }
                    }
                ]
            }
        },
    }


def _make_lockup_view_model(
    video_id: str,
    title: str,
    badge_text: str = "",
    metadata_text: str = "",
) -> dict:
    badges = []
    if badge_text:
        badges.append({"thumbnailBadgeViewModel": {"text": badge_text}})

    metadata_parts = []
    if metadata_text:
        metadata_parts.append({"text": {"content": metadata_text}})

    return {
        "contentId": video_id,
        "contentImage": {
            "thumbnailViewModel": {
                "overlays": [
                    {
                        "thumbnailBottomOverlayViewModel": {
                            "badges": badges,
                        }
                    }
                ]
            }
        },
        "metadata": {
            "lockupMetadataViewModel": {
                "title": {"content": title},
                "metadata": {
                    "contentMetadataViewModel": {
                        "metadataRows": [
                            {
                                "metadataParts": metadata_parts,
                            }
                        ],
                    }
                },
            }
        },
    }


def _make_initial_data_with_lockups(
    lockups: list[dict],
    display_name: str = "TestChannel",
) -> dict:
    contents = [
        {"richItemRenderer": {"content": {"lockupViewModel": lockup}}}
        for lockup in lockups
    ]
    return {
        "metadata": {"channelMetadataRenderer": {"title": display_name}},
        "contents": {
            "twoColumnBrowseResultsRenderer": {
                "tabs": [
                    {
                        "tabRenderer": {
                            "content": {
                                "richGridRenderer": {"contents": contents}
                            }
                        }
                    }
                ]
            }
        },
    }


# ─────────────────────────────────────────────
# TIDUS: get_channel_items via ytInitialData
# ─────────────────────────────────────────────
class TestParseChannelItems:
    def test_extracts_live_item(self) -> None:
        fetcher = YouTubeFetcher()
        vr = _make_video_renderer("vid1", "Live Stream", "LIVE")
        data = _make_initial_data([vr])
        items, display_name = fetcher._parse_channel_items(data, "chan")

        assert len(items) == 1
        assert items[0].video_id == "vid1"
        assert items[0].title == "Live Stream"
        assert items[0].style == "LIVE"
        assert items[0].url == "https://www.youtube.com/watch?v=vid1"
        assert display_name == "TestChannel"

    def test_extracts_upcoming_with_start_time(self) -> None:
        fetcher = YouTubeFetcher()
        vr = _make_video_renderer("vid2", "Waiting Room", "UPCOMING", "1714924800")
        data = _make_initial_data([vr])
        items, _ = fetcher._parse_channel_items(data, "chan")

        assert len(items) == 1
        assert items[0].style == "UPCOMING"
        assert items[0].scheduled_start != ""

    def test_extracts_upcoming_from_event_data_without_overlay_style(self) -> None:
        fetcher = YouTubeFetcher()
        vr = _make_video_renderer("vid2", "Waiting Room", "DEFAULT", "1714924800")
        vr.pop("thumbnailOverlays")
        data = _make_initial_data([vr])
        items, _ = fetcher._parse_channel_items(data, "chan")

        assert len(items) == 1
        assert items[0].style == "UPCOMING"
        assert items[0].scheduled_start != ""

    def test_extracts_upcoming_from_upcoming_event_overlay_style(self) -> None:
        fetcher = YouTubeFetcher()
        vr = _make_video_renderer(
            "vid2", "Waiting Room", "UPCOMING_EVENT", "1714924800"
        )
        data = _make_initial_data([vr])
        items, _ = fetcher._parse_channel_items(data, "chan")

        assert len(items) == 1
        assert items[0].style == "UPCOMING"
        assert items[0].scheduled_start != ""

    def test_extracts_upcoming_from_lockup_view_model_badge(self) -> None:
        fetcher = YouTubeFetcher()
        lockup = _make_lockup_view_model(
            "vid_lockup",
            "Waiting Room",
            badge_text="即將直播",
            metadata_text="預定發布時間：2026/5/8 下午15:30",
        )
        data = _make_initial_data_with_lockups([lockup])
        items, display_name = fetcher._parse_channel_items(data, "chan")

        assert len(items) == 1
        assert items[0].video_id == "vid_lockup"
        assert items[0].title == "Waiting Room"
        assert items[0].style == "UPCOMING"
        assert items[0].url == "https://www.youtube.com/watch?v=vid_lockup"
        assert display_name == "TestChannel"

    def test_extracts_default_from_lockup_view_model_without_upcoming_badge(self) -> None:
        fetcher = YouTubeFetcher()
        lockup = _make_lockup_view_model(
            "vid_lockup",
            "Regular Upload",
            metadata_text="1 天前",
        )
        data = _make_initial_data_with_lockups([lockup])
        items, _ = fetcher._parse_channel_items(data, "chan")

        assert len(items) == 1
        assert items[0].style == "DEFAULT"

    def test_extracts_default_video(self) -> None:
        fetcher = YouTubeFetcher()
        vr = _make_video_renderer("vid3", "Regular Video", "DEFAULT")
        data = _make_initial_data([vr])
        items, _ = fetcher._parse_channel_items(data, "chan")

        assert len(items) == 1
        assert items[0].style == "DEFAULT"

    def test_multiple_items_with_mixed_styles(self) -> None:
        fetcher = YouTubeFetcher()
        vr_live = _make_video_renderer("v1", "Live", "LIVE")
        vr_upcoming = _make_video_renderer("v2", "Soon", "UPCOMING", "1714924800")
        vr_default = _make_video_renderer("v3", "Old", "DEFAULT")
        data = _make_initial_data([vr_live, vr_upcoming, vr_default])
        items, _ = fetcher._parse_channel_items(data, "chan")

        assert len(items) == 3
        assert [i.style for i in items] == ["LIVE", "UPCOMING", "DEFAULT"]

    def test_empty_data_returns_no_items(self) -> None:
        fetcher = YouTubeFetcher()
        items, display_name = fetcher._parse_channel_items({}, "chan")
        assert items == []
        assert display_name == ""

    def test_skips_renderer_without_video_id(self) -> None:
        fetcher = YouTubeFetcher()
        bad_renderer = {"title": {"runs": [{"text": "No ID"}]}}
        data = _make_initial_data([bad_renderer])
        items, _ = fetcher._parse_channel_items(data, "chan")
        assert items == []


class TestGetChannelItemsIntegration:
    def test_parses_html_with_initial_data(self, monkeypatch) -> None:
        fetcher = YouTubeFetcher()
        vr = _make_video_renderer("xyz", "Test", "LIVE")
        data = _make_initial_data([vr], "Creator")
        html = _html_with_initial_data(data)

        monkeypatch.setattr(fetcher, "_fetch_page", lambda url: html)
        items = fetcher.get_channel_items("creator")

        assert len(items) == 1
        assert items[0].video_id == "xyz"
        assert items[0].display_name == "Creator"

    def test_returns_empty_on_fetch_failure(self, monkeypatch) -> None:
        fetcher = YouTubeFetcher()
        monkeypatch.setattr(fetcher, "_fetch_page", lambda url: None)
        assert fetcher.get_channel_items("nobody") == []

    def test_fill_timing_false_skips_live_watch_page(self, monkeypatch) -> None:
        fetcher = YouTubeFetcher()
        vr = _make_video_renderer("xyz", "Test", "LIVE")
        data = _make_initial_data([vr], "Creator")
        html = _html_with_initial_data(data)
        watch_calls: list[str] = []

        monkeypatch.setattr(fetcher, "_fetch_page", lambda url: html)
        monkeypatch.setattr(
            fetcher,
            "_get_watch_details",
            lambda video_id: watch_calls.append(video_id) or _WatchDetails(),
        )

        items = fetcher.get_channel_items("creator", fill_timing=False)

        assert len(items) == 1
        assert watch_calls == []

    def test_enrich_items_fetches_upcoming_schedule(self, monkeypatch) -> None:
        fetcher = YouTubeFetcher()
        vr = _make_video_renderer("up1", "Premiere Soon", "UPCOMING")
        data = _make_initial_data([vr], "Creator")
        html = _html_with_initial_data(data)
        watch_calls: list[str] = []

        monkeypatch.setattr(fetcher, "_fetch_page", lambda url: html)
        monkeypatch.setattr(
            fetcher,
            "_get_watch_details",
            lambda video_id: (
                watch_calls.append(video_id)
                or _WatchDetails(scheduled_start="2026-12-01T18:00:00+00:00")
            ),
        )

        items = fetcher.get_channel_items("creator", fill_timing=False)
        assert len(items) == 1
        assert items[0].style == "UPCOMING"
        assert not items[0].scheduled_start
        assert watch_calls == []

        fetcher.enrich_items_for_details(items)

        assert items[0].scheduled_start == "2026-12-01T18:00:00+00:00"
        assert watch_calls == ["up1"]


# ─────────────────────────────────────────────
# get_stream_info (used for channel validation)
# ─────────────────────────────────────────────
class TestGetStreamInfo:
    def test_reports_live_from_channel_items(self, monkeypatch) -> None:
        fetcher = YouTubeFetcher()
        vr = _make_video_renderer("v1", "Going Live", "LIVE")
        data = _make_initial_data([vr], "Streamer")
        html = _html_with_initial_data(data)

        monkeypatch.setattr(fetcher, "_fetch_page", lambda url: html)
        info = fetcher.get_stream_info("streamer")

        assert info is not None
        assert info.is_live is True
        assert info.display_name == "Streamer"
        assert info.url == "https://www.youtube.com/watch?v=v1"
        assert info.stream_status == "live"
        assert info.video_id == "v1"

    def test_reports_upcoming_from_channel_items(self, monkeypatch) -> None:
        fetcher = YouTubeFetcher()
        vr = _make_video_renderer(
            "v_up", "Waiting Room", "UPCOMING", "4102444800"
        )
        data = _make_initial_data([vr], "Streamer")
        html = _html_with_initial_data(data)

        monkeypatch.setattr(fetcher, "_fetch_page", lambda url: html)
        info = fetcher.get_stream_info("streamer")

        assert info is not None
        assert info.is_live is False
        assert info.stream_status == "upcoming"
        assert info.url == "https://www.youtube.com/watch?v=v_up"
        assert info.scheduled_start != ""

    def test_reports_offline_from_default_items(self, monkeypatch) -> None:
        fetcher = YouTubeFetcher()
        vr = _make_video_renderer("v1", "Old Video", "DEFAULT")
        data = _make_initial_data([vr], "Streamer")
        html = _html_with_initial_data(data)

        monkeypatch.setattr(fetcher, "_fetch_page", lambda url: html)
        info = fetcher.get_stream_info("streamer")

        assert info is not None
        assert info.is_live is False

    def test_falls_back_on_fetch_failure(self, monkeypatch) -> None:
        fetcher = YouTubeFetcher()
        monkeypatch.setattr(fetcher, "_fetch_page", lambda url: None)
        info = fetcher.get_stream_info("nobody")
        assert info is None

    def test_empty_streams_page_uses_live_fallback(self, monkeypatch) -> None:
        fetcher = YouTubeFetcher()
        data = _make_initial_data([], "Streamer")
        streams_html = _html_with_initial_data(data)
        live_html = _html_with_player(
            """
            {
              "playabilityStatus": {"status": "OK"},
              "videoDetails": {
                "author": "Streamer",
                "videoId": "live123",
                "isLive": true,
                "title": "Now Live"
              },
              "microformat": {
                "playerMicroformatRenderer": {
                  "liveBroadcastDetails": {
                    "isLiveNow": true,
                    "startTimestamp": "2026-06-05T10:00:00Z"
                  }
                }
              }
            }
            """
        )

        def fake_fetch(url: str) -> str:
            if url.endswith("/streams"):
                return streams_html
            return live_html

        monkeypatch.setattr(fetcher, "_fetch_page", fake_fetch)
        info = fetcher.get_stream_info("streamer")

        assert info is not None
        assert info.is_live is True
        assert info.title == "Now Live"
        assert info.url == "https://www.youtube.com/watch?v=live123"
        assert info.video_id == "live123"

    def test_empty_streams_page_detects_upcoming_fallback(self, monkeypatch) -> None:
        fetcher = YouTubeFetcher()
        data = _make_initial_data([], "Streamer")
        streams_html = _html_with_initial_data(data)
        live_html = _html_with_player(
            """
            {
              "playabilityStatus": {"status": "LIVE_STREAM_OFFLINE"},
              "videoDetails": {
                "author": "Streamer",
                "videoId": "wait123",
                "isUpcoming": true,
                "title": "Waiting Room"
              },
              "microformat": {
                "playerMicroformatRenderer": {
                  "liveBroadcastDetails": {
                    "isLiveNow": false,
                    "startTimestamp": "2099-01-01T00:00:00Z"
                  }
                }
              }
            }
            """
        )

        def fake_fetch(url: str) -> str:
            if url.endswith("/streams"):
                return streams_html
            return live_html

        monkeypatch.setattr(fetcher, "_fetch_page", fake_fetch)
        info = fetcher.get_stream_info("streamer")

        assert info is not None
        assert info.is_live is False
        assert info.stream_status == "upcoming"
        assert info.url == "https://www.youtube.com/watch?v=wait123"
        assert info.scheduled_start != ""


# ─────────────────────────────────────────────
# Fallback: old ytInitialPlayerResponse parsing
# ─────────────────────────────────────────────
class TestFallbackParseLiveStatus:
    def test_accepts_actual_live_player_response(self) -> None:
        fetcher = YouTubeFetcher()
        html = _html_with_player(
            """
            {
              "playabilityStatus": {"status": "OK"},
              "videoDetails": {
                "author": "Streamer Name",
                "isLive": true,
                "title": "Now Live"
              },
              "microformat": {
                "playerMicroformatRenderer": {
                  "liveBroadcastDetails": {"isLiveNow": true}
                }
              }
            }
            """
        )
        assert fetcher._parse_live_status(html) == (True, "Now Live", "Streamer Name")

    def test_rejects_standby_screen_with_video_details_is_live(self) -> None:
        """Standby screen: videoDetails.isLive=true but no isLiveNow confirmation."""
        fetcher = YouTubeFetcher()
        html = _html_with_player(
            """
            {
              "playabilityStatus": {"status": "OK"},
              "videoDetails": {
                "author": "VTuber Channel",
                "isLive": true,
                "isLiveContent": true,
                "title": "待機畫面"
              },
              "microformat": {
                "playerMicroformatRenderer": {
                  "liveBroadcastDetails": {}
                }
              }
            }
            """
        )

        assert fetcher._parse_live_status(html) == (
            False,
            "待機畫面",
            "VTuber Channel",
        )

    def test_rejects_past_stream_shown_on_live_page(self) -> None:
        """Past broadcast shown on /live page: isLive=true but no liveBroadcastDetails."""
        fetcher = YouTubeFetcher()
        html = _html_with_player(
            """
            {
              "playabilityStatus": {"status": "OK"},
              "videoDetails": {
                "author": "Streamer",
                "isLive": true,
                "title": "Past Stream Title"
              },
              "microformat": {
                "playerMicroformatRenderer": {}
              }
            }
            """
        )

        assert fetcher._parse_live_status(html) == (
            False,
            "Past Stream Title",
            "Streamer",
        )

    def test_rejects_waiting_room(self) -> None:
        fetcher = YouTubeFetcher()
        html = _html_with_player(
            """
            {
              "playabilityStatus": {"status": "LIVE_STREAM_OFFLINE"},
              "videoDetails": {
                "author": "Schedule Channel",
                "isLiveContent": true,
                "title": "Waiting Room"
              },
              "microformat": {
                "playerMicroformatRenderer": {
                  "liveBroadcastDetails": {
                    "isLiveNow": false,
                    "startTimestamp": "2026-05-04T12:00:00Z"
                  }
                }
              }
            }
            """
        )
        assert fetcher._parse_live_status(html) == (
            False,
            "Waiting Room",
            "Schedule Channel",
        )

    def test_ignores_weekly_schedule_live_text(self) -> None:
        fetcher = YouTubeFetcher()
        html = """
        <html>
          <script>
            var ytInitialData = {
              "contents": [
                {"title": {"simpleText": "Weekly Schedule"}, "status": "LIVE"}
              ]
            };
          </script>
        </html>
        """
        assert fetcher._parse_live_status(html) == (False, "", "")


def test_get_latest_finished_vod_prefers_broadcast_over_upload(monkeypatch) -> None:
    fetcher = YouTubeFetcher()
    upload = _make_video_renderer("upload1", "Regular Upload", "DEFAULT")
    replay = _make_video_renderer("replay1", "Past Stream", "DEFAULT")
    data = _make_initial_data([upload, replay], "Streamer")
    html = _html_with_initial_data(data)

    def fake_watch_details(video_id: str):
        if video_id == "upload1":
            return _WatchDetails()
        if video_id == "replay1":
            return _WatchDetails(ended_at="2026-06-05T12:00:00+00:00")
        return _WatchDetails()

    monkeypatch.setattr(fetcher, "_fetch_page", lambda url: html)
    monkeypatch.setattr(fetcher, "_get_watch_details", fake_watch_details)

    vod = fetcher.get_latest_finished_vod("streamer")

    assert vod is not None
    assert vod.url == "https://www.youtube.com/watch?v=replay1"
    assert vod.title == "Past Stream"
