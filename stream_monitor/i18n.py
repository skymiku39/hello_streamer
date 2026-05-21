"""多國語言支援 — 集中管理 UI 字串並支援執行階段切換語言。

設計重點：
    - 翻譯資料皆內嵌於本模組，避免額外的 IO 依賴與打包路徑問題。
    - ``tr(key, **kwargs)`` 缺鍵時退回繁體中文，再退回 key 本身，避免畫面破版。
    - ``subscribe`` 機制讓 widget / tray / notifier 自行訂閱語言變更事件，
      使「懸停視窗 (tooltip)」、系統匣選單、通知文字都能即時跟隨切換。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# (code, native_label, english_label)
LANGUAGES: list[tuple[str, str, str]] = [
    ("zh_TW", "繁體中文", "Traditional Chinese"),
    ("en", "English", "English"),
    ("ja", "日本語", "Japanese"),
    ("ko", "한국어", "Korean"),
]

LANGUAGE_CODES: set[str] = {code for code, _, _ in LANGUAGES}
DEFAULT_LANGUAGE = "zh_TW"


# ---------------------------------------------------------------------------
# Translation tables
# ---------------------------------------------------------------------------
_ZH_TW: dict[str, str] = {
    # App
    "app.title": "哈嘍主播  Hello Streamer",
    "app.title.cn": "哈嘍主播",
    "app.title.en": "Hello Streamer",

    # Main toolbar
    "toolbar.add_channel": "＋  新增頻道",
    "toolbar.browser_settings": "⚙  瀏覽器設定",
    "toolbar.startup": "開機啟動",
    "toolbar.minimize_to_tray": "縮小至系統匣",
    "toolbar.start": "▶  監聽+觸發",
    "toolbar.watch": "👁  只監測",
    "toolbar.stop": "■  停止",
    "toolbar.check_interval": "檢查間隔",
    "toolbar.seconds": "秒",
    "toolbar.action_label": "觸發行為",

    # Tooltips (main window)
    "tooltip.language": "切換介面語言",
    "tooltip.add_channel": "新增 Twitch 或 YouTube 頻道",
    "tooltip.browser_settings": "設定觸發時的瀏覽器：獨立視窗 / 座標 / 大小 / 最小化 / App Mode",
    "tooltip.startup": "系統開機時自動啟動程式",
    "tooltip.minimize_to_tray": "開啟：關閉時縮小到系統匣\n關閉：關閉時直接結束程式",
    "tooltip.start": "開始監聽並在偵測到開播時執行觸發行為",
    "tooltip.watch": "只更新狀態與顯示，不執行觸發行為（不自動開網頁/不通知）",
    "tooltip.stop": "停止所有監聽",
    "tooltip.interval_entry": "每次檢查的間隔秒數(最低 10 秒)",
    "tooltip.action_menu": "偵測到開播時要執行的動作",

    # Channel row tooltips
    "tooltip.row.up": "上移",
    "tooltip.row.down": "下移",
    "tooltip.row.delete": "刪除頻道",
    "tooltip.row.link.default": "開啟頻道首頁",
    "tooltip.row.link.paused": "頻道已暫停；開啟頻道首頁",
    "tooltip.row.link.idle": "尚未更新；開啟頻道首頁",
    "tooltip.row.link.offline": "目前離線；開啟頻道首頁",
    "tooltip.row.link.upcoming": "開啟待機間",
    "tooltip.row.link.live": "開啟直播間",
    "tooltip.row.link.with_title": "{link_text}：{title}",
    "tooltip.row.toggle.pause": "暫停監聽此頻道",
    "tooltip.row.toggle.resume": "恢復監聽此頻道（觸發通知/開瀏覽器）",
    "tooltip.row.monitor_only.enable": "切換為只監測（只顯示狀態，不觸發通知/瀏覽器）",
    "tooltip.row.monitor_only.disable": "關閉只監測（改回觸發通知/瀏覽器）",
    "tooltip.row.status.upcoming": "待機中",
    "tooltip.row.status.live": "直播中",
    "tooltip.row.status.title": "📺 {title}",
    "tooltip.row.status.starts_in": "⏱ {countdown} 後開始",
    "tooltip.row.status.live_elapsed": "⏱ 已開播 {elapsed}",

    # Bottom status bar
    "status.idle": "尚未啟動",
    "status.trigger_running": "監聽+觸發中…",
    "status.watching": "只監測中…",
    "status.stopped": "已停止",
    "status.empty_hint": "尚無頻道，請點擊「＋ 新增頻道」開始",
    "status.row.placeholder": "  --  ",
    "status.row.paused": " 已暫停 ",
    "status.row.upcoming": " UPCOMING ",
    "status.row.live": " ● LIVE ",
    "status.row.offline": " OFFLINE ",

    # Tray
    "tray.show": "顯示主畫面",
    "tray.trigger_active": "✓ 監聽+觸發中",
    "tray.start_trigger": "開始監聽+觸發",
    "tray.watch_active": "✓ 只監測中",
    "tray.start_watch": "切換為只監測",
    "tray.stopped": "已停止",
    "tray.stop": "停止監聽",
    "tray.quit": "完全退出",
    "tray.tooltip.default": "哈嘍主播  Hello Streamer",
    "tray.tooltip.trigger": "哈嘍主播 — 監聽+觸發中",
    "tray.tooltip.watch": "哈嘍主播 — 只監測中",
    "tray.tooltip.stopped": "哈嘍主播 — 已停止",

    # Actions
    "action.open_and_stop": "開啟網頁並停止監聽",
    "action.open_and_keep": "開啟網頁並保持監聽",
    "action.notify_only": "僅跳出系統通知",
    "action.open_and_exit": "開啟網頁後關閉程式",

    # Add channel dialog
    "add.title": "新增頻道",
    "add.heading": "貼上頻道連結（自動偵測平台）",
    "add.url.placeholder": "貼上頻道連結",
    "add.url.hint": "Twitch: twitch.tv/channel_name    YouTube: youtube.com/@channel_name",
    "add.url.warning": "YouTube 連結只要包含 @handle 就能辨識；不含 @handle 的觀看頁、Shorts 或 /live 不支援。",
    "add.manual.heading": "或手動輸入頻道名稱",
    "add.manual.platform": "平台",
    "add.manual.name": "頻道名稱",
    "add.manual.name.placeholder": "例如 kaicenat、channel_name 或 UC 開頭頻道 ID",
    "add.manual.hint": "手動輸入 YouTube 時，請填 @handle 後面的名稱，或 UC 開頭的頻道 ID。",
    "add.btn.cancel": "取消",
    "add.btn.add": "新增",
    "add.msg.parsed": "{platform_upper} : {name}",
    "add.msg.unparseable": "無法辨識。YouTube 連結需包含 @handle，或手動輸入 @ 後面的名稱。",
    "add.msg.invalid_url": "網址格式不支援。YouTube 請貼含 @handle 的連結，例如 https://www.youtube.com/@channel_name/live。",
    "add.msg.empty": "請貼上頻道連結，或手動輸入頻道名稱。",
    "add.msg.validating": "驗證中…",
    "add.msg.not_found": "找不到此帳號：{platform_upper} / {name}。請確認名稱是否正確。",

    # Language dialog
    "lang.title": "語言偏好",
    "lang.heading": "介面語言",
    "lang.description": "選擇要套用到主視窗、對話框、懸停提示與系統匣的語言。",
    "lang.btn.apply": "套用",
    "lang.btn.close": "關閉",
    "lang.option.current": "目前語言",
    "lang.option.selected": "已選取",

    # Browser settings dialog
    "browser.title": "瀏覽器設定",
    "browser.section.open": "瀏覽器開啟方式",
    "browser.section.open.hint": "關閉自訂模式時，將使用系統預設瀏覽器（無法控制座標／視窗）。",
    "browser.enable": "啟用自訂瀏覽器開啟（subprocess 模式）",
    "browser.path.label": "瀏覽器指令",
    "browser.path.placeholder": "chrome / msedge / 或瀏覽器執行檔絕對路徑",
    "browser.path.hint": "提示：chrome、msedge 可直接輸入；若未在 PATH 內請填完整 .exe 路徑。",
    "browser.toggle.new_window": "強制獨立新視窗 (--new-window)",
    "browser.toggle.app_mode": "App Mode：純淨播放器 (--app=URL；已隱含獨立視窗)",
    "browser.toggle.minimized": "最小化啟動（不搶焦點；由本程式於視窗出現後自動 Win32 縮小）",
    "browser.toggle.close_on_offline": "🗙 關台時自動關閉播放器視窗 (PostMessage WM_CLOSE)",
    "browser.toggle.close_on_offline.hint": "只關閉本程式開啟的視窗（HWND 已登記）；找不到時才用視窗標題關鍵字後備。",
    "browser.toggle.hide_taskbar": "👻 從工作列與 Alt+Tab 隱藏 (WS_EX_TOOLWINDOW)",
    "browser.toggle.hide_taskbar.hint": "視窗依然可見，但不會佔工作列空間，也不會在 Alt+Tab 中干擾。\n代價：無法從工作列把它叫到最上層（需從本程式控制）。",
    "browser.profile.title": "獨立瀏覽器 Profile（建議啟用）",
    "browser.profile.desc": "Chrome / Edge 已開啟時，--app= 與座標/大小會被忽略。設定獨立 Profile 可強迫瀏覽器以新 master process 開啟，所有設定才會生效。\n代價：此 profile 沒有你主瀏覽器的書籤/登入/外掛（彈窗純粹播片用）。",
    "browser.profile.enable": "啟用獨立 Profile",
    "browser.profile.per_channel": "每個頻道使用獨立子資料夾（強烈建議）",
    "browser.profile.per_channel.hint": "開啟時：browser_profile/twitch_<channel>、youtube_<handle> 各一份。\n確保 App Mode 純淨視窗對每個頻道都有效（不會被 Chrome master process 吃掉），代價是每頻道需重新登入一次。",
    "browser.geometry.apply": "套用自訂視窗位置 / 大小",
    "browser.geometry.reset": "恢復預設",
    "browser.geometry.reset.tooltip": "重設為 X=0, Y=0, 1280×720",
    "browser.geometry.apply.tooltip": "關閉時：使用系統預設視窗位置與大小",
    "browser.geometry.x": "X",
    "browser.geometry.y": "Y",
    "browser.geometry.width": "寬度",
    "browser.geometry.height": "高度",
    "browser.btn.cancel": "取消",
    "browser.btn.test": "測試開啟",
    "browser.btn.save": "儲存",
    "browser.compat.disabled": "（自訂模式已停用 — 將使用系統預設瀏覽器）",
    "browser.compat.firefox": "⚠ 偵測到 Firefox：座標 / 大小 / App Mode 都無法控制（CLI 不支援），相關欄位已停用。",
    "browser.compat.chromium": "✓ Chromium 系列瀏覽器：所有參數都可使用。",
    "browser.compat.unknown": "ℹ 未知瀏覽器類型 — 仍會嘗試送出 Chromium 參數，若無效請改用 chrome / msedge。",
    "browser.msg.invalid_int": "座標與大小必須為整數。",
    "browser.msg.min_size": "寬度與高度至少需 100 像素。",
    "browser.msg.empty_profile": "獨立 Profile 已啟用但路徑為空，請填入資料夾路徑。",
    "browser.msg.reset_done": "已恢復視窗預設值 (0, 0, 1280×720)。",
    "browser.msg.test_opened": "已開啟本機測試頁；App Mode 會使用獨立測試 Profile。",
    "browser.close.title": "儲存瀏覽器設定？",
    "browser.close.body": "在關閉前要儲存你修改的瀏覽器設定嗎？",

    # Channel labels
    "channel.id.prefix": "ID: {id}",

    # Boot / fatal
    "boot.write_fail.title": "啟動失敗",
    "boot.write_fail.body": "程式所在目錄無法寫入：\n{directory}\n\n請將程式移至有寫入權限的資料夾後再試。",

    # Notifier
    "notify.upcoming.title": "📅 {channel_name} 已建立待機室 [{platform_display}]",
    "notify.upcoming.body.scheduled": "預計開播：{time_str}",
    "notify.upcoming.body.default": "即將開播",
    "notify.video.title": "🎬 {channel_name} 上傳了新影片 [{platform_display}]",
    "notify.video.body.default": "新影片",
    "notify.live.title": "🔴 {channel_name} 開播了！ [{platform_display}]",
    "notify.live.body.default": "{channel_name} 正在 {platform} 上直播中",
    "notify.watch_now": "立即觀看",
}


_EN: dict[str, str] = {
    "app.title": "Hello Streamer",
    "app.title.cn": "Hello",
    "app.title.en": "Streamer",

    "toolbar.add_channel": "＋  Add Channel",
    "toolbar.browser_settings": "⚙  Browser Settings",
    "toolbar.startup": "Run at Startup",
    "toolbar.minimize_to_tray": "Minimize to Tray",
    "toolbar.start": "▶  Monitor + Trigger",
    "toolbar.watch": "👁  Watch Only",
    "toolbar.stop": "■  Stop",
    "toolbar.check_interval": "Check Interval",
    "toolbar.seconds": "sec",
    "toolbar.action_label": "Trigger Action",

    "tooltip.language": "Switch interface language",
    "tooltip.add_channel": "Add a Twitch or YouTube channel",
    "tooltip.browser_settings": "Configure browser launch: window / position / size / minimized / App Mode",
    "tooltip.startup": "Launch app automatically at system startup",
    "tooltip.minimize_to_tray": "On: hide to tray when closed\nOff: quit immediately when closed",
    "tooltip.start": "Start monitoring and run trigger actions when a stream goes live",
    "tooltip.watch": "Refresh status only — never auto-open browser or send notifications",
    "tooltip.stop": "Stop all monitoring",
    "tooltip.interval_entry": "Seconds between checks (minimum 10)",
    "tooltip.action_menu": "Action to run when a stream goes live",

    "tooltip.row.up": "Move up",
    "tooltip.row.down": "Move down",
    "tooltip.row.delete": "Delete channel",
    "tooltip.row.link.default": "Open channel home page",
    "tooltip.row.link.paused": "Channel paused — open channel home page",
    "tooltip.row.link.idle": "Not updated yet — open channel home page",
    "tooltip.row.link.offline": "Currently offline — open channel home page",
    "tooltip.row.link.upcoming": "Open waiting room",
    "tooltip.row.link.live": "Open live stream",
    "tooltip.row.link.with_title": "{link_text}: {title}",
    "tooltip.row.toggle.pause": "Pause monitoring this channel",
    "tooltip.row.toggle.resume": "Resume monitoring this channel (notify / open browser)",
    "tooltip.row.monitor_only.enable": "Switch to monitor-only (status only — no notifications / browser)",
    "tooltip.row.monitor_only.disable": "Disable monitor-only (resume notifications / browser)",
    "tooltip.row.status.upcoming": "Waiting",
    "tooltip.row.status.live": "Live",
    "tooltip.row.status.title": "📺 {title}",
    "tooltip.row.status.starts_in": "⏱ Starts in {countdown}",
    "tooltip.row.status.live_elapsed": "⏱ Live for {elapsed}",

    "status.idle": "Not started",
    "status.trigger_running": "Monitoring + triggering…",
    "status.watching": "Watching only…",
    "status.stopped": "Stopped",
    "status.empty_hint": "No channels yet — click “＋ Add Channel” to begin",
    "status.row.placeholder": "  --  ",
    "status.row.paused": " PAUSED ",
    "status.row.upcoming": " UPCOMING ",
    "status.row.live": " ● LIVE ",
    "status.row.offline": " OFFLINE ",

    "tray.show": "Show main window",
    "tray.trigger_active": "✓ Monitoring + triggering",
    "tray.start_trigger": "Start monitor + trigger",
    "tray.watch_active": "✓ Watching only",
    "tray.start_watch": "Switch to watch-only",
    "tray.stopped": "Stopped",
    "tray.stop": "Stop monitoring",
    "tray.quit": "Quit",
    "tray.tooltip.default": "Hello Streamer",
    "tray.tooltip.trigger": "Hello Streamer — Monitoring + triggering",
    "tray.tooltip.watch": "Hello Streamer — Watching only",
    "tray.tooltip.stopped": "Hello Streamer — Stopped",

    "action.open_and_stop": "Open page and stop monitoring",
    "action.open_and_keep": "Open page and keep monitoring",
    "action.notify_only": "Show system notification only",
    "action.open_and_exit": "Open page and exit app",

    "add.title": "Add Channel",
    "add.heading": "Paste channel URL (platform auto-detected)",
    "add.url.placeholder": "Paste channel URL",
    "add.url.hint": "Twitch: twitch.tv/channel_name    YouTube: youtube.com/@channel_name",
    "add.url.warning": "YouTube URLs must contain @handle to be recognized. Watch pages, Shorts, and /live without @handle are not supported.",
    "add.manual.heading": "Or enter the channel name manually",
    "add.manual.platform": "Platform",
    "add.manual.name": "Channel name",
    "add.manual.name.placeholder": "e.g. kaicenat, channel_name, or UC-prefixed channel ID",
    "add.manual.hint": "For YouTube, enter the name after @handle, or a channel ID starting with UC.",
    "add.btn.cancel": "Cancel",
    "add.btn.add": "Add",
    "add.msg.parsed": "{platform_upper} : {name}",
    "add.msg.unparseable": "Could not parse URL. YouTube URLs must include @handle, or enter the name after @ manually.",
    "add.msg.invalid_url": "URL format not supported. For YouTube paste an @handle URL, e.g. https://www.youtube.com/@channel_name/live.",
    "add.msg.empty": "Paste a channel URL or enter the channel name manually.",
    "add.msg.validating": "Validating…",
    "add.msg.not_found": "Account not found: {platform_upper} / {name}. Please check the name.",

    "lang.title": "Language",
    "lang.heading": "Interface language",
    "lang.description": "Choose the language for the main window, dialogs, tooltips, and system tray.",
    "lang.btn.apply": "Apply",
    "lang.btn.close": "Close",
    "lang.option.current": "Current",
    "lang.option.selected": "Selected",

    "browser.title": "Browser Settings",
    "browser.section.open": "Browser launch mode",
    "browser.section.open.hint": "When custom mode is off, the system default browser is used (no control over position / window).",
    "browser.enable": "Enable custom browser launch (subprocess mode)",
    "browser.path.label": "Browser command",
    "browser.path.placeholder": "chrome / msedge / or absolute path to browser executable",
    "browser.path.hint": "Tip: chrome and msedge can be entered as-is; if not on PATH, fill the full .exe path.",
    "browser.toggle.new_window": "Force a separate new window (--new-window)",
    "browser.toggle.app_mode": "App Mode: clean player (--app=URL; implies a separate window)",
    "browser.toggle.minimized": "Start minimized (no focus steal; this app minimizes via Win32 once the window appears)",
    "browser.toggle.close_on_offline": "🗙 Auto-close the player window when the stream goes offline (PostMessage WM_CLOSE)",
    "browser.toggle.close_on_offline.hint": "Only closes windows opened by this app (HWND tracked); falls back to title keywords if not found.",
    "browser.toggle.hide_taskbar": "👻 Hide from taskbar and Alt+Tab (WS_EX_TOOLWINDOW)",
    "browser.toggle.hide_taskbar.hint": "The window stays visible but no longer occupies a taskbar slot or appears in Alt+Tab.\nTrade-off: you can no longer bring it to the front from the taskbar (control from this app).",
    "browser.profile.title": "Dedicated browser profile (recommended)",
    "browser.profile.desc": "When Chrome / Edge is already running, --app= and position/size are ignored. A dedicated profile forces a new master process so all settings actually apply.\nTrade-off: this profile has none of your main browser's bookmarks/logins/extensions (player-only).",
    "browser.profile.enable": "Enable dedicated profile",
    "browser.profile.per_channel": "Use a separate sub-folder per channel (strongly recommended)",
    "browser.profile.per_channel.hint": "When on: browser_profile/twitch_<channel> and youtube_<handle> each get their own folder.\nKeeps App Mode clean for every channel (avoids Chrome master-process collisions). Trade-off: log in again per channel.",
    "browser.geometry.apply": "Apply custom window position / size",
    "browser.geometry.reset": "Reset",
    "browser.geometry.reset.tooltip": "Reset to X=0, Y=0, 1280×720",
    "browser.geometry.apply.tooltip": "Off: use system default position and size",
    "browser.geometry.x": "X",
    "browser.geometry.y": "Y",
    "browser.geometry.width": "Width",
    "browser.geometry.height": "Height",
    "browser.btn.cancel": "Cancel",
    "browser.btn.test": "Test launch",
    "browser.btn.save": "Save",
    "browser.compat.disabled": "(Custom mode is off — system default browser will be used)",
    "browser.compat.firefox": "⚠ Firefox detected: position / size / App Mode cannot be controlled (CLI not supported). Related fields disabled.",
    "browser.compat.chromium": "✓ Chromium-based browser: all parameters are available.",
    "browser.compat.unknown": "ℹ Unknown browser — Chromium-style flags will be tried; if they fail, switch to chrome / msedge.",
    "browser.msg.invalid_int": "Position and size must be integers.",
    "browser.msg.min_size": "Width and height must be at least 100 pixels.",
    "browser.msg.empty_profile": "Dedicated profile is enabled but the path is empty. Please fill in a folder path.",
    "browser.msg.reset_done": "Reset to default window (0, 0, 1280×720).",
    "browser.msg.test_opened": "Test page opened; App Mode uses a dedicated test profile.",
    "browser.close.title": "Save browser settings?",
    "browser.close.body": "Do you want to save your browser settings changes before closing?",

    "channel.id.prefix": "ID: {id}",

    "boot.write_fail.title": "Startup failed",
    "boot.write_fail.body": "The program directory is not writable:\n{directory}\n\nPlease move the program to a writable folder and try again.",

    "notify.upcoming.title": "📅 {channel_name} created a waiting room [{platform_display}]",
    "notify.upcoming.body.scheduled": "Scheduled start: {time_str}",
    "notify.upcoming.body.default": "Starting soon",
    "notify.video.title": "🎬 {channel_name} uploaded a new video [{platform_display}]",
    "notify.video.body.default": "New video",
    "notify.live.title": "🔴 {channel_name} is live now! [{platform_display}]",
    "notify.live.body.default": "{channel_name} is now live on {platform}",
    "notify.watch_now": "Watch now",
}


_JA: dict[str, str] = {
    "app.title": "ハローストリーマー",
    "app.title.cn": "ハロー",
    "app.title.en": "Streamer",

    "toolbar.add_channel": "＋  チャンネル追加",
    "toolbar.browser_settings": "⚙  ブラウザ設定",
    "toolbar.startup": "起動時に実行",
    "toolbar.minimize_to_tray": "トレイに最小化",
    "toolbar.start": "▶  監視＋実行",
    "toolbar.watch": "👁  監視のみ",
    "toolbar.stop": "■  停止",
    "toolbar.check_interval": "確認間隔",
    "toolbar.seconds": "秒",
    "toolbar.action_label": "配信開始時の動作",

    "tooltip.language": "インターフェース言語を切り替え",
    "tooltip.add_channel": "Twitch または YouTube のチャンネルを追加",
    "tooltip.browser_settings": "ブラウザの起動方法を設定：独立ウィンドウ / 座標 / サイズ / 最小化 / App Mode",
    "tooltip.startup": "システム起動時に自動で実行",
    "tooltip.minimize_to_tray": "ON：閉じるとトレイに格納\nOFF：閉じると終了",
    "tooltip.start": "監視を開始し、配信検出時に動作を実行",
    "tooltip.watch": "状態と表示のみ更新（ブラウザは開かず、通知も送らない）",
    "tooltip.stop": "すべての監視を停止",
    "tooltip.interval_entry": "確認間隔（秒、最小 10 秒）",
    "tooltip.action_menu": "配信検出時に実行する動作",

    "tooltip.row.up": "上へ",
    "tooltip.row.down": "下へ",
    "tooltip.row.delete": "チャンネルを削除",
    "tooltip.row.link.default": "チャンネルホームを開く",
    "tooltip.row.link.paused": "監視停止中：チャンネルホームを開く",
    "tooltip.row.link.idle": "未更新：チャンネルホームを開く",
    "tooltip.row.link.offline": "現在オフライン：チャンネルホームを開く",
    "tooltip.row.link.upcoming": "待機室を開く",
    "tooltip.row.link.live": "配信を開く",
    "tooltip.row.link.with_title": "{link_text}：{title}",
    "tooltip.row.toggle.pause": "このチャンネルの監視を一時停止",
    "tooltip.row.toggle.resume": "このチャンネルの監視を再開（通知・ブラウザ起動あり）",
    "tooltip.row.monitor_only.enable": "監視のみに切替（状態表示のみ・通知/ブラウザなし）",
    "tooltip.row.monitor_only.disable": "監視のみを解除（通知・ブラウザ起動を再開）",
    "tooltip.row.status.upcoming": "待機中",
    "tooltip.row.status.live": "配信中",
    "tooltip.row.status.title": "📺 {title}",
    "tooltip.row.status.starts_in": "⏱ {countdown}後に開始",
    "tooltip.row.status.live_elapsed": "⏱ 配信開始から {elapsed}",

    "status.idle": "未開始",
    "status.trigger_running": "監視＋実行中…",
    "status.watching": "監視のみ中…",
    "status.stopped": "停止しました",
    "status.empty_hint": "チャンネルがありません。「＋ チャンネル追加」をクリック",
    "status.row.placeholder": "  --  ",
    "status.row.paused": " 一時停止 ",
    "status.row.upcoming": " 待機中 ",
    "status.row.live": " ● LIVE ",
    "status.row.offline": " OFFLINE ",

    "tray.show": "メイン画面を表示",
    "tray.trigger_active": "✓ 監視＋実行中",
    "tray.start_trigger": "監視＋実行を開始",
    "tray.watch_active": "✓ 監視のみ実行中",
    "tray.start_watch": "監視のみに切替",
    "tray.stopped": "停止しました",
    "tray.stop": "監視を停止",
    "tray.quit": "完全に終了",
    "tray.tooltip.default": "Hello Streamer",
    "tray.tooltip.trigger": "Hello Streamer — 監視＋実行中",
    "tray.tooltip.watch": "Hello Streamer — 監視のみ",
    "tray.tooltip.stopped": "Hello Streamer — 停止中",

    "action.open_and_stop": "ページを開いて監視を停止",
    "action.open_and_keep": "ページを開いて監視を継続",
    "action.notify_only": "システム通知のみ",
    "action.open_and_exit": "ページを開いてアプリを終了",

    "add.title": "チャンネル追加",
    "add.heading": "チャンネル URL を貼り付け（プラットフォーム自動判定）",
    "add.url.placeholder": "チャンネル URL を貼り付け",
    "add.url.hint": "Twitch: twitch.tv/channel_name    YouTube: youtube.com/@channel_name",
    "add.url.warning": "YouTube URL は @handle が含まれている必要があります。@handle を含まない視聴ページ、Shorts、/live は対応外です。",
    "add.manual.heading": "またはチャンネル名を手動で入力",
    "add.manual.platform": "プラットフォーム",
    "add.manual.name": "チャンネル名",
    "add.manual.name.placeholder": "例：kaicenat、channel_name、または UC で始まるチャンネル ID",
    "add.manual.hint": "YouTube の場合は @handle の後の名前、または UC で始まるチャンネル ID を入力。",
    "add.btn.cancel": "キャンセル",
    "add.btn.add": "追加",
    "add.msg.parsed": "{platform_upper} : {name}",
    "add.msg.unparseable": "URL を判定できません。YouTube は @handle 付き URL、または @ の後の名前を手動で入力してください。",
    "add.msg.invalid_url": "URL 形式が未対応です。YouTube は @handle 付き URL（例：https://www.youtube.com/@channel_name/live）を貼り付けてください。",
    "add.msg.empty": "チャンネル URL を貼り付けるか、チャンネル名を手動で入力してください。",
    "add.msg.validating": "検証中…",
    "add.msg.not_found": "アカウントが見つかりません：{platform_upper} / {name}。名前を確認してください。",

    "lang.title": "言語設定",
    "lang.heading": "インターフェース言語",
    "lang.description": "メイン画面、ダイアログ、ツールチップ、システムトレイに適用する言語を選択します。",
    "lang.btn.apply": "適用",
    "lang.btn.close": "閉じる",
    "lang.option.current": "現在",
    "lang.option.selected": "選択中",

    "browser.title": "ブラウザ設定",
    "browser.section.open": "ブラウザの起動方法",
    "browser.section.open.hint": "カスタムモードをオフにすると、システム既定のブラウザを使用します（座標／ウィンドウは制御不可）。",
    "browser.enable": "カスタムブラウザ起動を有効にする（subprocess モード）",
    "browser.path.label": "ブラウザコマンド",
    "browser.path.placeholder": "chrome / msedge / またはブラウザ実行ファイルの絶対パス",
    "browser.path.hint": "ヒント：chrome、msedge はそのまま入力可。PATH にない場合は .exe のフルパスを指定。",
    "browser.toggle.new_window": "独立した新規ウィンドウを強制 (--new-window)",
    "browser.toggle.app_mode": "App Mode：シンプルプレイヤー (--app=URL；独立ウィンドウを含む)",
    "browser.toggle.minimized": "最小化で起動（フォーカスを奪わず、ウィンドウ出現後に Win32 で最小化）",
    "browser.toggle.close_on_offline": "🗙 配信終了時にプレイヤーウィンドウを自動で閉じる (PostMessage WM_CLOSE)",
    "browser.toggle.close_on_offline.hint": "このアプリが開いたウィンドウ（HWND 登録済）のみを閉じます。見つからない場合はウィンドウタイトルのキーワードで後方互換。",
    "browser.toggle.hide_taskbar": "👻 タスクバーと Alt+Tab から非表示 (WS_EX_TOOLWINDOW)",
    "browser.toggle.hide_taskbar.hint": "ウィンドウは表示されますが、タスクバーや Alt+Tab には現れません。\nトレードオフ：タスクバーから前面へ呼び出せなくなります（このアプリから操作）。",
    "browser.profile.title": "独立ブラウザプロファイル（推奨）",
    "browser.profile.desc": "Chrome / Edge が既に起動中の場合、--app= や座標/サイズは無視されます。独立プロファイルを設定すると新しい master process が強制起動され、すべての設定が有効になります。\nトレードオフ：このプロファイルにはメインブラウザのブックマーク/ログイン/拡張機能がありません（再生専用）。",
    "browser.profile.enable": "独立プロファイルを有効化",
    "browser.profile.per_channel": "チャンネルごとに別サブフォルダを使用（強く推奨）",
    "browser.profile.per_channel.hint": "オン：browser_profile/twitch_<channel>、youtube_<handle> がそれぞれ作成されます。\nApp Mode のシンプルウィンドウを全チャンネルで保証（Chrome master process に飲まれない）。トレードオフ：チャンネルごとに再ログインが必要。",
    "browser.geometry.apply": "カスタムウィンドウ位置／サイズを適用",
    "browser.geometry.reset": "既定に戻す",
    "browser.geometry.reset.tooltip": "X=0, Y=0, 1280×720 にリセット",
    "browser.geometry.apply.tooltip": "オフ：システム既定のウィンドウ位置とサイズを使用",
    "browser.geometry.x": "X",
    "browser.geometry.y": "Y",
    "browser.geometry.width": "幅",
    "browser.geometry.height": "高さ",
    "browser.btn.cancel": "キャンセル",
    "browser.btn.test": "テスト起動",
    "browser.btn.save": "保存",
    "browser.compat.disabled": "（カスタムモード無効 — システム既定のブラウザを使用）",
    "browser.compat.firefox": "⚠ Firefox を検出：座標／サイズ／App Mode は制御不可（CLI 未対応）。関連項目は無効化。",
    "browser.compat.chromium": "✓ Chromium 系ブラウザ：すべての設定が使用可能。",
    "browser.compat.unknown": "ℹ 未知のブラウザ — Chromium 用パラメータを試行。動作しない場合は chrome / msedge を使用。",
    "browser.msg.invalid_int": "座標とサイズは整数で入力してください。",
    "browser.msg.min_size": "幅と高さは 100 ピクセル以上が必要です。",
    "browser.msg.empty_profile": "独立プロファイルが有効ですがパスが空です。フォルダパスを入力してください。",
    "browser.msg.reset_done": "ウィンドウを既定値にリセットしました (0, 0, 1280×720)。",
    "browser.msg.test_opened": "テストページを開きました。App Mode は独立テストプロファイルを使用します。",
    "browser.close.title": "ブラウザ設定を保存しますか？",
    "browser.close.body": "閉じる前に、ブラウザ設定の変更を保存しますか？",

    "channel.id.prefix": "ID: {id}",

    "boot.write_fail.title": "起動失敗",
    "boot.write_fail.body": "プログラムフォルダに書き込めません：\n{directory}\n\n書き込み可能なフォルダへ移動してから再試行してください。",

    "notify.upcoming.title": "📅 {channel_name} が待機室を作成 [{platform_display}]",
    "notify.upcoming.body.scheduled": "開始予定：{time_str}",
    "notify.upcoming.body.default": "まもなく開始",
    "notify.video.title": "🎬 {channel_name} が新しい動画を投稿 [{platform_display}]",
    "notify.video.body.default": "新着動画",
    "notify.live.title": "🔴 {channel_name} が配信開始！ [{platform_display}]",
    "notify.live.body.default": "{channel_name} が {platform} で配信中",
    "notify.watch_now": "今すぐ視聴",
}


_KO: dict[str, str] = {
    "app.title": "헬로 스트리머",
    "app.title.cn": "헬로",
    "app.title.en": "Streamer",

    "toolbar.add_channel": "＋  채널 추가",
    "toolbar.browser_settings": "⚙  브라우저 설정",
    "toolbar.startup": "시작 시 실행",
    "toolbar.minimize_to_tray": "트레이로 최소화",
    "toolbar.start": "▶  감시 + 트리거",
    "toolbar.watch": "👁  감시만",
    "toolbar.stop": "■  정지",
    "toolbar.check_interval": "확인 간격",
    "toolbar.seconds": "초",
    "toolbar.action_label": "트리거 동작",

    "tooltip.language": "인터페이스 언어 전환",
    "tooltip.add_channel": "Twitch 또는 YouTube 채널 추가",
    "tooltip.browser_settings": "브라우저 실행 방식 설정: 독립 창 / 좌표 / 크기 / 최소화 / App Mode",
    "tooltip.startup": "시스템 시작 시 자동 실행",
    "tooltip.minimize_to_tray": "켜짐: 닫을 때 트레이로\n꺼짐: 닫을 때 즉시 종료",
    "tooltip.start": "감시 시작, 방송 시작이 감지되면 트리거 동작 실행",
    "tooltip.watch": "상태와 표시만 갱신 (브라우저 자동 열기/알림 없음)",
    "tooltip.stop": "모든 감시 정지",
    "tooltip.interval_entry": "확인 간격(초, 최소 10초)",
    "tooltip.action_menu": "방송 감지 시 실행할 동작",

    "tooltip.row.up": "위로",
    "tooltip.row.down": "아래로",
    "tooltip.row.delete": "채널 삭제",
    "tooltip.row.link.default": "채널 홈 열기",
    "tooltip.row.link.paused": "채널 일시 정지 — 채널 홈 열기",
    "tooltip.row.link.idle": "아직 갱신되지 않음 — 채널 홈 열기",
    "tooltip.row.link.offline": "현재 오프라인 — 채널 홈 열기",
    "tooltip.row.link.upcoming": "대기실 열기",
    "tooltip.row.link.live": "라이브 열기",
    "tooltip.row.link.with_title": "{link_text}: {title}",
    "tooltip.row.toggle.pause": "이 채널 감시 일시 정지",
    "tooltip.row.toggle.resume": "이 채널 감시 재개 (알림/브라우저 실행)",
    "tooltip.row.monitor_only.enable": "감시만 모드로 전환 (상태만 표시, 알림/브라우저 없음)",
    "tooltip.row.monitor_only.disable": "감시만 모드 해제 (알림/브라우저 실행 재개)",
    "tooltip.row.status.upcoming": "대기 중",
    "tooltip.row.status.live": "라이브",
    "tooltip.row.status.title": "📺 {title}",
    "tooltip.row.status.starts_in": "⏱ {countdown} 후 시작",
    "tooltip.row.status.live_elapsed": "⏱ {elapsed} 동안 방송 중",

    "status.idle": "시작 전",
    "status.trigger_running": "감시 + 트리거 중…",
    "status.watching": "감시만 진행 중…",
    "status.stopped": "정지됨",
    "status.empty_hint": "채널이 없습니다. “＋ 채널 추가”를 클릭하세요",
    "status.row.placeholder": "  --  ",
    "status.row.paused": " 일시정지 ",
    "status.row.upcoming": " 예정 ",
    "status.row.live": " ● LIVE ",
    "status.row.offline": " OFFLINE ",

    "tray.show": "메인 화면 표시",
    "tray.trigger_active": "✓ 감시 + 트리거 중",
    "tray.start_trigger": "감시 + 트리거 시작",
    "tray.watch_active": "✓ 감시만 중",
    "tray.start_watch": "감시만으로 전환",
    "tray.stopped": "정지됨",
    "tray.stop": "감시 정지",
    "tray.quit": "완전 종료",
    "tray.tooltip.default": "Hello Streamer",
    "tray.tooltip.trigger": "Hello Streamer — 감시 + 트리거 중",
    "tray.tooltip.watch": "Hello Streamer — 감시만",
    "tray.tooltip.stopped": "Hello Streamer — 정지됨",

    "action.open_and_stop": "페이지 열고 감시 정지",
    "action.open_and_keep": "페이지 열고 감시 유지",
    "action.notify_only": "시스템 알림만",
    "action.open_and_exit": "페이지 열고 앱 종료",

    "add.title": "채널 추가",
    "add.heading": "채널 URL 붙여넣기 (플랫폼 자동 감지)",
    "add.url.placeholder": "채널 URL 붙여넣기",
    "add.url.hint": "Twitch: twitch.tv/channel_name    YouTube: youtube.com/@channel_name",
    "add.url.warning": "YouTube URL 은 @handle 을 포함해야 합니다. @handle 이 없는 시청 페이지, Shorts, /live 는 지원하지 않습니다.",
    "add.manual.heading": "또는 채널 이름을 직접 입력",
    "add.manual.platform": "플랫폼",
    "add.manual.name": "채널 이름",
    "add.manual.name.placeholder": "예: kaicenat, channel_name, 또는 UC 로 시작하는 채널 ID",
    "add.manual.hint": "YouTube 의 경우 @handle 뒤의 이름이나 UC 로 시작하는 채널 ID 를 입력하세요.",
    "add.btn.cancel": "취소",
    "add.btn.add": "추가",
    "add.msg.parsed": "{platform_upper} : {name}",
    "add.msg.unparseable": "URL 을 파싱할 수 없습니다. YouTube URL 은 @handle 을 포함하거나, @ 뒤의 이름을 직접 입력하세요.",
    "add.msg.invalid_url": "지원하지 않는 URL 형식입니다. YouTube 는 @handle URL (예: https://www.youtube.com/@channel_name/live) 을 붙여넣으세요.",
    "add.msg.empty": "채널 URL 을 붙여넣거나 채널 이름을 직접 입력하세요.",
    "add.msg.validating": "확인 중…",
    "add.msg.not_found": "계정을 찾을 수 없습니다: {platform_upper} / {name}. 이름을 확인하세요.",

    "lang.title": "언어 설정",
    "lang.heading": "인터페이스 언어",
    "lang.description": "메인 창, 대화상자, 툴팁, 시스템 트레이에 적용할 언어를 선택하세요.",
    "lang.btn.apply": "적용",
    "lang.btn.close": "닫기",
    "lang.option.current": "현재",
    "lang.option.selected": "선택됨",

    "browser.title": "브라우저 설정",
    "browser.section.open": "브라우저 실행 방식",
    "browser.section.open.hint": "사용자 정의 모드를 끄면 시스템 기본 브라우저가 사용됩니다 (좌표/창 제어 불가).",
    "browser.enable": "사용자 정의 브라우저 실행 활성화 (subprocess 모드)",
    "browser.path.label": "브라우저 명령",
    "browser.path.placeholder": "chrome / msedge / 또는 브라우저 실행 파일의 절대 경로",
    "browser.path.hint": "팁: chrome, msedge 는 그대로 입력 가능. PATH 에 없으면 .exe 전체 경로를 입력하세요.",
    "browser.toggle.new_window": "독립 새 창 강제 (--new-window)",
    "browser.toggle.app_mode": "App Mode: 깔끔한 플레이어 (--app=URL; 독립 창 포함)",
    "browser.toggle.minimized": "최소화로 시작 (포커스 빼앗지 않음; 창이 뜨면 Win32 로 최소화)",
    "browser.toggle.close_on_offline": "🗙 방송 종료 시 플레이어 창 자동 닫기 (PostMessage WM_CLOSE)",
    "browser.toggle.close_on_offline.hint": "이 앱이 연 창 (HWND 등록)만 닫습니다. 찾지 못하면 창 제목 키워드로 대체.",
    "browser.toggle.hide_taskbar": "👻 작업 표시줄과 Alt+Tab 에서 숨기기 (WS_EX_TOOLWINDOW)",
    "browser.toggle.hide_taskbar.hint": "창은 그대로 보이지만 작업 표시줄/Alt+Tab 에 나타나지 않습니다.\n트레이드오프: 작업 표시줄에서 앞으로 가져올 수 없습니다 (이 앱에서 제어).",
    "browser.profile.title": "독립 브라우저 프로필 (권장)",
    "browser.profile.desc": "Chrome / Edge 가 이미 실행 중이면 --app= 와 좌표/크기가 무시됩니다. 독립 프로필을 설정하면 새 master process 가 강제 실행되어 모든 설정이 적용됩니다.\n트레이드오프: 이 프로필에는 메인 브라우저의 북마크/로그인/확장이 없습니다 (재생 전용).",
    "browser.profile.enable": "독립 프로필 활성화",
    "browser.profile.per_channel": "채널별로 별도 하위 폴더 사용 (강력 권장)",
    "browser.profile.per_channel.hint": "켜짐: browser_profile/twitch_<channel>, youtube_<handle> 가 각각 생성됩니다.\nApp Mode 의 깔끔한 창을 모든 채널에 보장 (Chrome master process 충돌 방지). 트레이드오프: 채널별 재로그인 필요.",
    "browser.geometry.apply": "사용자 정의 창 위치/크기 적용",
    "browser.geometry.reset": "기본값으로",
    "browser.geometry.reset.tooltip": "X=0, Y=0, 1280×720 으로 재설정",
    "browser.geometry.apply.tooltip": "꺼짐: 시스템 기본 창 위치와 크기 사용",
    "browser.geometry.x": "X",
    "browser.geometry.y": "Y",
    "browser.geometry.width": "너비",
    "browser.geometry.height": "높이",
    "browser.btn.cancel": "취소",
    "browser.btn.test": "테스트 실행",
    "browser.btn.save": "저장",
    "browser.compat.disabled": "(사용자 정의 모드 꺼짐 — 시스템 기본 브라우저 사용)",
    "browser.compat.firefox": "⚠ Firefox 감지: 좌표/크기/App Mode 제어 불가 (CLI 미지원). 관련 항목 비활성화.",
    "browser.compat.chromium": "✓ Chromium 계열 브라우저: 모든 매개변수 사용 가능.",
    "browser.compat.unknown": "ℹ 알 수 없는 브라우저 — Chromium 스타일 매개변수를 시도합니다. 실패 시 chrome / msedge 사용.",
    "browser.msg.invalid_int": "좌표와 크기는 정수여야 합니다.",
    "browser.msg.min_size": "너비와 높이는 최소 100 픽셀이어야 합니다.",
    "browser.msg.empty_profile": "독립 프로필이 활성화되었지만 경로가 비어 있습니다. 폴더 경로를 입력하세요.",
    "browser.msg.reset_done": "기본 창으로 재설정됨 (0, 0, 1280×720).",
    "browser.msg.test_opened": "테스트 페이지가 열렸습니다. App Mode 는 독립 테스트 프로필을 사용합니다.",
    "browser.close.title": "브라우저 설정을 저장하시겠습니까?",
    "browser.close.body": "닫기 전에 브라우저 설정 변경 사항을 저장하시겠습니까?",

    "channel.id.prefix": "ID: {id}",

    "boot.write_fail.title": "시작 실패",
    "boot.write_fail.body": "프로그램 폴더에 쓸 수 없습니다:\n{directory}\n\n쓰기 가능한 폴더로 옮긴 후 다시 시도하세요.",

    "notify.upcoming.title": "📅 {channel_name} 대기실 생성됨 [{platform_display}]",
    "notify.upcoming.body.scheduled": "예정 시작 시간: {time_str}",
    "notify.upcoming.body.default": "곧 시작",
    "notify.video.title": "🎬 {channel_name} 새 영상 업로드 [{platform_display}]",
    "notify.video.body.default": "새 영상",
    "notify.live.title": "🔴 {channel_name} 라이브 시작! [{platform_display}]",
    "notify.live.body.default": "{channel_name} 가 {platform} 에서 방송 중",
    "notify.watch_now": "지금 시청",
}


_TRANSLATIONS: dict[str, dict[str, str]] = {
    "zh_TW": _ZH_TW,
    "en": _EN,
    "ja": _JA,
    "ko": _KO,
}


# ---------------------------------------------------------------------------
# Mutable state + observer machinery
# ---------------------------------------------------------------------------
_current_lang: str = DEFAULT_LANGUAGE
_listeners: list[Callable[[], None]] = []


def available_languages() -> list[tuple[str, str, str]]:
    """Return the list of (code, native_label, english_label) tuples."""
    return list(LANGUAGES)


def current_language() -> str:
    return _current_lang


def label_for(code: str) -> str:
    """Return the native label of a language code (falls back to the code)."""
    for c, native, _ in LANGUAGES:
        if c == code:
            return native
    return code


def normalize(code: Any) -> str:
    """Coerce an arbitrary value into a known language code."""
    if isinstance(code, str) and code in LANGUAGE_CODES:
        return code
    return DEFAULT_LANGUAGE


def set_language(code: str, *, notify: bool = True) -> bool:
    """Switch the active language. Returns True if the value actually changed."""
    global _current_lang
    if code not in LANGUAGE_CODES:
        logger.warning("Unknown language code: %s — ignored", code)
        return False
    if code == _current_lang:
        return False
    _current_lang = code
    if notify:
        _broadcast()
    return True


def _broadcast() -> None:
    for cb in list(_listeners):
        try:
            cb()
        except Exception:  # noqa: BLE001
            logger.exception("i18n listener raised")


def subscribe(callback: Callable[[], None]) -> Callable[[], None]:
    """Register *callback* for language-change notifications.

    Returns an ``unsubscribe`` function that removes the listener idempotently.
    """
    _listeners.append(callback)

    def _unsubscribe() -> None:
        try:
            _listeners.remove(callback)
        except ValueError:
            pass

    return _unsubscribe


def tr(key: str, **kwargs: Any) -> str:
    """Translate *key* under the active language.

    - Missing keys fall back to the default-language table, then to the key.
    - Format placeholders use ``str.format``; failures return the unformatted
      template so a translation typo never crashes the UI.
    """
    table = _TRANSLATIONS.get(_current_lang) or _TRANSLATIONS[DEFAULT_LANGUAGE]
    template = table.get(key)
    if template is None:
        template = _TRANSLATIONS[DEFAULT_LANGUAGE].get(key, key)
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        logger.warning("i18n format failed for key=%s lang=%s", key, _current_lang)
        return template
