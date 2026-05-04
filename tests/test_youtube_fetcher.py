from stream_monitor.fetcher.youtube import YouTubeFetcher


def _html_with_player(player_json: str, extra: str = "") -> str:
    return f"""
    <html>
      <script>var ytInitialPlayerResponse = {player_json};</script>
      {extra}
    </html>
    """


def test_parse_live_status_accepts_actual_live_player_response() -> None:
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


def test_parse_live_status_rejects_waiting_room_with_live_markers() -> None:
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
        """,
        extra='''<script>var ytInitialData = {"status":"LIVE","badges":[{"metadataBadgeRenderer":{"label":"LIVE"}}]};</script>''',
    )

    assert fetcher._parse_live_status(html) == (False, "Waiting Room", "Schedule Channel")


def test_parse_live_status_ignores_weekly_schedule_live_text() -> None:
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
