"""One-off probe for a YouTube watch URL."""
import sys

sys.stdout.reconfigure(encoding="utf-8")

from stream_monitor.fetcher.youtube import YouTubeFetcher  # noqa: E402
from stream_monitor.url_parser import parse_url  # noqa: E402

url = "https://www.youtube.com/watch?v=X4VbdwhkE10"
parsed = parse_url(url)
print("parse_url:", parsed)

vid = "X4VbdwhkE10"
f = YouTubeFetcher()
details = f._get_watch_details(vid)
print("watch_details:", details)

html = f._fetch_page(url)
if html:
    player = f._extract_json_var(html, "ytInitialPlayerResponse")
    if isinstance(player, dict):
        vd = player.get("videoDetails") or {}
        mf = (player.get("microformat") or {}).get("playerMicroformatRenderer") or {}
        print("channelId:", vd.get("channelId"))
        print("author:", vd.get("author"))
        print("isLiveContent:", vd.get("isLiveContent"))
        print("ownerChannelName:", mf.get("ownerChannelName"))
        live = mf.get("liveBroadcastDetails") or {}
        print("liveBroadcastDetails:", live)

for name in ("LofiGirl", "DaemonCasoul"):
    items = f.get_channel_items(name, fill_timing=False)
    live = [i for i in (items or []) if i.style == "LIVE"]
    print(f"channel {name}: items={len(items or [])} live={len(live)}")
    if vid in {i.video_id for i in (items or [])}:
        print(f"  FOUND {vid} in feed")
