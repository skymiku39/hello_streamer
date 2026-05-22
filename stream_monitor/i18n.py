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
    ("zh_CN", "简体中文", "Simplified Chinese"),
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
    "toolbar.minimize_to_tray": "關閉時縮至系統匣",
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
    "browser.tab.user": "使用者設定",
    "browser.tab.advanced": "開發者設定",
    "browser.section.open": "開啟直播頁",
    "browser.section.open.hint": "選擇直播頁要用哪個瀏覽器。關閉自訂模式時，會直接使用系統預設瀏覽器。",
    "browser.enable": "使用自訂瀏覽器設定",
    "browser.path.label": "使用的瀏覽器",
    "browser.path.placeholder": "chrome / msedge / 或瀏覽器執行檔絕對路徑",
    "browser.path.hint": "可直接輸入 chrome 或 msedge；若找不到瀏覽器，再填完整 .exe 路徑。",
    "browser.section.window": "播放器視窗",
    "browser.section.window.hint": "控制 Hello Streamer 自動開啟直播頁時的視窗行為。",
    "browser.toggle.new_window": "用獨立視窗開啟直播頁",
    "browser.toggle.app_mode": "啟用純淨播放器視窗（App Mode）",
    "browser.toggle.app_mode.hint": "使用 Chromium 的 --app=URL 開啟無網址列視窗。這是進階功能，可能需要搭配獨立 Profile。",
    "browser.toggle.minimized": "開啟後先最小化",
    "browser.section.lifecycle": "自動關閉與整理",
    "browser.section.lifecycle.hint": "這些選項只會處理由 Hello Streamer 自動開啟並追蹤到的播放視窗。",
    "browser.toggle.close_on_offline": "直播結束時關閉播放器視窗",
    "browser.toggle.close_on_offline.hint": "只關閉本程式開啟的視窗；找不到追蹤視窗時才用標題關鍵字輔助判斷。",
    "browser.toggle.close_on_stop": "停止監聽時關閉所有播放器視窗",
    "browser.toggle.close_on_stop.hint": "按下主視窗的「停止」時，關閉本程式追蹤中的播放器視窗，不會碰到你原本就開著的分頁。",
    "browser.toggle.close_off_topic": "只保留直播相關視窗",
    "browser.toggle.close_off_topic.hint": "若本程式開啟的視窗被導到首頁、新分頁、廣告頁或其他非直播內容，就自動關閉。",
    "browser.toggle.hide_taskbar": "不顯示在工作列與 Alt+Tab",
    "browser.toggle.hide_taskbar.hint": "視窗仍會顯示，但不佔工作列空間；需要從本程式或視窗本身操作。",
    "browser.section.advanced": "開發者與純淨視窗",
    "browser.section.advanced.hint": "這些設定會改變瀏覽器啟動參數或登入環境。一般使用時可以不調整。",
    "browser.profile.title": "獨立瀏覽器 Profile",
    "browser.profile.desc": "讓純淨視窗、座標與大小更穩定。代價是它和你的主瀏覽器登入狀態分開，可能需要重新登入。",
    "browser.profile.enable": "使用獨立 Profile",
    "browser.profile.per_channel": "每個頻道使用獨立登入環境",
    "browser.profile.per_channel.hint": "可避免多個直播視窗互相干擾；代價是不同頻道可能需要各自登入一次。",
    "browser.tools.title": "測試工具",
    "browser.tools.hint": "用本機測試頁確認開窗、純淨視窗與自動關閉是否如預期運作。",
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
    "browser.btn.test_close": "測試關閉",
    "browser.btn.save": "儲存",
    "browser.compat.disabled": "自訂瀏覽器設定已關閉，會使用系統預設瀏覽器。",
    "browser.compat.firefox": "Firefox 不支援純淨視窗與座標 / 大小控制，相關欄位已停用。",
    "browser.compat.chromium": "Chromium 系列瀏覽器可使用所有視窗設定。",
    "browser.compat.unknown": "無法辨識瀏覽器類型，會先嘗試套用 Chromium 相容設定。",
    "browser.msg.invalid_int": "座標與大小必須為整數。",
    "browser.msg.min_size": "寬度與高度至少需 100 像素。",
    "browser.msg.empty_profile": "獨立 Profile 已啟用但路徑為空，請填入資料夾路徑。",
    "browser.msg.reset_done": "已恢復視窗預設值 (0, 0, 1280×720)。",
    "browser.msg.test_opened": "已開啟本機測試頁；若啟用純淨視窗，會使用臨時測試 Profile。",
    "browser.msg.test_closed": "已送出關閉測試頁指令（{count} 個視窗）。",
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


_ZH_CN: dict[str, str] = {
    # App
    "app.title": "哈喽主播  Hello Streamer",
    "app.title.cn": "哈喽主播",
    "app.title.en": "Hello Streamer",

    # Main toolbar
    "toolbar.add_channel": "＋  新增频道",
    "toolbar.browser_settings": "⚙  浏览器设置",
    "toolbar.startup": "开机启动",
    "toolbar.minimize_to_tray": "关闭时缩到系统托盘",
    "toolbar.start": "▶  监听+触发",
    "toolbar.watch": "👁  仅监测",
    "toolbar.stop": "■  停止",
    "toolbar.check_interval": "检查间隔",
    "toolbar.seconds": "秒",
    "toolbar.action_label": "触发动作",

    # Tooltips (main window)
    "tooltip.language": "切换界面语言",
    "tooltip.add_channel": "新增 Twitch 或 YouTube 频道",
    "tooltip.browser_settings": "设置触发时的浏览器：独立窗口 / 坐标 / 大小 / 最小化 / App Mode",
    "tooltip.startup": "系统开机时自动启动程序",
    "tooltip.minimize_to_tray": "开启：关闭时缩到系统托盘\n关闭：关闭时直接退出程序",
    "tooltip.start": "开始监听并在检测到开播时执行触发动作",
    "tooltip.watch": "仅刷新状态与显示，不执行触发动作（不自动打开网页/不通知）",
    "tooltip.stop": "停止所有监听",
    "tooltip.interval_entry": "每次检查的间隔秒数（最低 10 秒）",
    "tooltip.action_menu": "检测到开播时要执行的动作",

    # Channel row tooltips
    "tooltip.row.up": "上移",
    "tooltip.row.down": "下移",
    "tooltip.row.delete": "删除频道",
    "tooltip.row.link.default": "打开频道首页",
    "tooltip.row.link.paused": "频道已暂停；打开频道首页",
    "tooltip.row.link.idle": "尚未更新；打开频道首页",
    "tooltip.row.link.offline": "当前离线；打开频道首页",
    "tooltip.row.link.upcoming": "打开等待室",
    "tooltip.row.link.live": "打开直播间",
    "tooltip.row.link.with_title": "{link_text}：{title}",
    "tooltip.row.toggle.pause": "暂停监听此频道",
    "tooltip.row.toggle.resume": "恢复监听此频道（触发通知/打开浏览器）",
    "tooltip.row.monitor_only.enable": "切换为仅监测（仅显示状态，不触发通知/浏览器）",
    "tooltip.row.monitor_only.disable": "关闭仅监测（恢复触发通知/浏览器）",
    "tooltip.row.status.upcoming": "等待中",
    "tooltip.row.status.live": "直播中",
    "tooltip.row.status.title": "📺 {title}",
    "tooltip.row.status.starts_in": "⏱ {countdown} 后开始",
    "tooltip.row.status.live_elapsed": "⏱ 已开播 {elapsed}",

    # Bottom status bar
    "status.idle": "尚未启动",
    "status.trigger_running": "监听+触发中…",
    "status.watching": "仅监测中…",
    "status.stopped": "已停止",
    "status.empty_hint": "尚无频道，请点击「＋ 新增频道」开始",
    "status.row.placeholder": "  --  ",
    "status.row.paused": " 已暂停 ",
    "status.row.upcoming": " UPCOMING ",
    "status.row.live": " ● LIVE ",
    "status.row.offline": " OFFLINE ",

    # Tray
    "tray.show": "显示主界面",
    "tray.trigger_active": "✓ 监听+触发中",
    "tray.start_trigger": "开始监听+触发",
    "tray.watch_active": "✓ 仅监测中",
    "tray.start_watch": "切换为仅监测",
    "tray.stopped": "已停止",
    "tray.stop": "停止监听",
    "tray.quit": "完全退出",
    "tray.tooltip.default": "哈喽主播  Hello Streamer",
    "tray.tooltip.trigger": "哈喽主播 — 监听+触发中",
    "tray.tooltip.watch": "哈喽主播 — 仅监测中",
    "tray.tooltip.stopped": "哈喽主播 — 已停止",

    # Actions
    "action.open_and_stop": "打开网页并停止监听",
    "action.open_and_keep": "打开网页并保持监听",
    "action.notify_only": "仅弹出系统通知",
    "action.open_and_exit": "打开网页后关闭程序",

    # Add channel dialog
    "add.title": "新增频道",
    "add.heading": "粘贴频道链接（自动检测平台）",
    "add.url.placeholder": "粘贴频道链接",
    "add.url.hint": "Twitch: twitch.tv/channel_name    YouTube: youtube.com/@channel_name",
    "add.url.warning": "YouTube 链接只要包含 @handle 即可识别；不含 @handle 的观看页、Shorts 或 /live 不支持。",
    "add.manual.heading": "或手动输入频道名称",
    "add.manual.platform": "平台",
    "add.manual.name": "频道名称",
    "add.manual.name.placeholder": "例如 kaicenat、channel_name 或 UC 开头频道 ID",
    "add.manual.hint": "手动输入 YouTube 时，请填 @handle 后面的名称，或 UC 开头的频道 ID。",
    "add.btn.cancel": "取消",
    "add.btn.add": "新增",
    "add.msg.parsed": "{platform_upper} : {name}",
    "add.msg.unparseable": "无法识别。YouTube 链接需包含 @handle，或手动输入 @ 后面的名称。",
    "add.msg.invalid_url": "网址格式不支持。YouTube 请粘贴含 @handle 的链接，例如 https://www.youtube.com/@channel_name/live。",
    "add.msg.empty": "请粘贴频道链接，或手动输入频道名称。",
    "add.msg.validating": "验证中…",
    "add.msg.not_found": "找不到此账号：{platform_upper} / {name}。请确认名称是否正确。",

    # Language dialog
    "lang.title": "语言偏好",
    "lang.heading": "界面语言",
    "lang.description": "选择要应用到主窗口、对话框、悬停提示与系统托盘的语言。",
    "lang.btn.apply": "应用",
    "lang.btn.close": "关闭",
    "lang.option.current": "当前语言",
    "lang.option.selected": "已选择",

    # Browser settings dialog
    "browser.title": "浏览器设置",
    "browser.tab.user": "用户设置",
    "browser.tab.advanced": "开发者设置",
    "browser.section.open": "打开直播页",
    "browser.section.open.hint": "选择直播页要用哪个浏览器。关闭自定义模式时，会直接使用系统默认浏览器。",
    "browser.enable": "使用自定义浏览器设置",
    "browser.path.label": "使用的浏览器",
    "browser.path.placeholder": "chrome / msedge / 或浏览器可执行文件绝对路径",
    "browser.path.hint": "可直接输入 chrome 或 msedge；若找不到浏览器，再填完整 .exe 路径。",
    "browser.section.window": "播放器窗口",
    "browser.section.window.hint": "控制 Hello Streamer 自动打开直播页时的窗口行为。",
    "browser.toggle.new_window": "用独立窗口打开直播页",
    "browser.toggle.app_mode": "启用纯净播放器窗口（App Mode）",
    "browser.toggle.app_mode.hint": "使用 Chromium 的 --app=URL 打开无地址栏窗口。这是进阶功能，可能需要搭配独立 Profile。",
    "browser.toggle.minimized": "打开后先最小化",
    "browser.section.lifecycle": "自动关闭与整理",
    "browser.section.lifecycle.hint": "这些选项只会处理由 Hello Streamer 自动打开并追踪到的播放窗口。",
    "browser.toggle.close_on_offline": "直播结束时关闭播放器窗口",
    "browser.toggle.close_on_offline.hint": "只关闭本程序打开的窗口；找不到追踪窗口时才用标题关键字辅助判断。",
    "browser.toggle.close_on_stop": "停止监听时关闭所有播放器窗口",
    "browser.toggle.close_on_stop.hint": "按下主窗口的「停止」时，关闭本程序追踪中的播放器窗口，不会碰到你原本就打开的标签页。",
    "browser.toggle.close_off_topic": "只保留直播相关窗口",
    "browser.toggle.close_off_topic.hint": "若本程序打开的窗口被导到首页、新标签页、广告页或其他非直播内容，就自动关闭。",
    "browser.toggle.hide_taskbar": "不显示在任务栏与 Alt+Tab",
    "browser.toggle.hide_taskbar.hint": "窗口仍会显示，但不占任务栏空间；需要从本程序或窗口本身操作。",
    "browser.section.advanced": "开发者与纯净窗口",
    "browser.section.advanced.hint": "这些设置会改变浏览器启动参数或登录环境。一般使用时可以不调整。",
    "browser.profile.title": "独立浏览器 Profile",
    "browser.profile.desc": "让纯净窗口、坐标与大小更稳定。代价是它和你的主浏览器登录状态分开，可能需要重新登录。",
    "browser.profile.enable": "使用独立 Profile",
    "browser.profile.per_channel": "每个频道使用独立登录环境",
    "browser.profile.per_channel.hint": "可避免多个直播窗口互相干扰；代价是不同频道可能需要各自登录一次。",
    "browser.tools.title": "测试工具",
    "browser.tools.hint": "用本机测试页确认开窗、纯净窗口与自动关闭是否如预期运作。",
    "browser.geometry.apply": "应用自定义窗口位置 / 大小",
    "browser.geometry.reset": "恢复默认",
    "browser.geometry.reset.tooltip": "重设为 X=0, Y=0, 1280×720",
    "browser.geometry.apply.tooltip": "关闭时：使用系统默认窗口位置与大小",
    "browser.geometry.x": "X",
    "browser.geometry.y": "Y",
    "browser.geometry.width": "宽度",
    "browser.geometry.height": "高度",
    "browser.btn.cancel": "取消",
    "browser.btn.test": "测试打开",
    "browser.btn.test_close": "测试关闭",
    "browser.btn.save": "保存",
    "browser.compat.disabled": "自定义浏览器设置已关闭，会使用系统默认浏览器。",
    "browser.compat.firefox": "Firefox 不支持纯净窗口与坐标 / 大小控制，相关字段已停用。",
    "browser.compat.chromium": "Chromium 系列浏览器可使用所有窗口设置。",
    "browser.compat.unknown": "无法识别浏览器类型，会先尝试套用 Chromium 兼容设置。",
    "browser.msg.invalid_int": "坐标与大小必须为整数。",
    "browser.msg.min_size": "宽度与高度至少需 100 像素。",
    "browser.msg.empty_profile": "独立 Profile 已启用但路径为空，请填入文件夹路径。",
    "browser.msg.reset_done": "已恢复窗口默认值 (0, 0, 1280×720)。",
    "browser.msg.test_opened": "已打开本机测试页；若启用纯净窗口，会使用临时测试 Profile。",
    "browser.msg.test_closed": "已发送关闭测试页指令（{count} 个窗口）。",
    "browser.close.title": "保存浏览器设置？",
    "browser.close.body": "在关闭前要保存你修改的浏览器设置吗？",

    # Channel labels
    "channel.id.prefix": "ID: {id}",

    # Boot / fatal
    "boot.write_fail.title": "启动失败",
    "boot.write_fail.body": "程序所在目录无法写入：\n{directory}\n\n请将程序移至有写入权限的文件夹后重试。",

    # Notifier
    "notify.upcoming.title": "📅 {channel_name} 已创建等待室 [{platform_display}]",
    "notify.upcoming.body.scheduled": "预计开播：{time_str}",
    "notify.upcoming.body.default": "即将开播",
    "notify.video.title": "🎬 {channel_name} 上传了新视频 [{platform_display}]",
    "notify.video.body.default": "新视频",
    "notify.live.title": "🔴 {channel_name} 开播了！ [{platform_display}]",
    "notify.live.body.default": "{channel_name} 正在 {platform} 上直播中",
    "notify.watch_now": "立即观看",
}


_EN: dict[str, str] = {
    "app.title": "Hello Streamer",
    "app.title.cn": "Hello Streamer",
    "app.title.en": "",

    "toolbar.add_channel": "＋  Add Channel",
    "toolbar.browser_settings": "⚙  Browser Settings",
    "toolbar.startup": "Run at Startup",
    "toolbar.minimize_to_tray": "Close to Tray",
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
    "status.trigger_running": "Monitor + Trigger…",
    "status.watching": "Watch Only…",
    "status.stopped": "Stopped",
    "status.empty_hint": "No channels yet — click “＋ Add Channel” to begin",
    "status.row.placeholder": "  --  ",
    "status.row.paused": " PAUSED ",
    "status.row.upcoming": " UPCOMING ",
    "status.row.live": " ● LIVE ",
    "status.row.offline": " OFFLINE ",

    "tray.show": "Show main window",
    "tray.trigger_active": "✓ Monitor + Trigger",
    "tray.start_trigger": "Start Monitor + Trigger",
    "tray.watch_active": "✓ Watch Only",
    "tray.start_watch": "Switch to Watch Only",
    "tray.stopped": "Stopped",
    "tray.stop": "Stop monitoring",
    "tray.quit": "Quit",
    "tray.tooltip.default": "Hello Streamer",
    "tray.tooltip.trigger": "Hello Streamer — Monitor + Trigger",
    "tray.tooltip.watch": "Hello Streamer — Watch Only",
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
    "browser.tab.user": "User Settings",
    "browser.tab.advanced": "Developer Settings",
    "browser.section.open": "Open Stream Pages",
    "browser.section.open.hint": "Choose which browser opens stream pages. When custom mode is off, Hello Streamer uses the system default browser.",
    "browser.enable": "Use custom browser settings",
    "browser.path.label": "Browser",
    "browser.path.placeholder": "chrome / msedge / or absolute path to browser executable",
    "browser.path.hint": "You can enter chrome or msedge directly; if the browser is not found, use the full .exe path.",
    "browser.section.window": "Player Window",
    "browser.section.window.hint": "Control the window behavior when Hello Streamer opens stream pages automatically.",
    "browser.toggle.new_window": "Open stream pages in a separate window",
    "browser.toggle.app_mode": "Enable clean player window (App Mode)",
    "browser.toggle.app_mode.hint": "Uses Chromium --app=URL to open a window without the address bar. This is an advanced option and may need a dedicated profile.",
    "browser.toggle.minimized": "Start minimized",
    "browser.section.lifecycle": "Auto-Close and Cleanup",
    "browser.section.lifecycle.hint": "These options only affect player windows opened and tracked by Hello Streamer.",
    "browser.toggle.close_on_offline": "Close the player window when the stream ends",
    "browser.toggle.close_on_offline.hint": "Only closes windows opened by this app; title keywords are used as a fallback when the tracked window is not found.",
    "browser.toggle.close_on_stop": "Close all player windows when monitoring stops",
    "browser.toggle.close_on_stop.hint": "When you click Stop in the main window, tracked player windows are closed. Browser tabs you opened yourself are not touched.",
    "browser.toggle.close_off_topic": "Keep only stream-related windows",
    "browser.toggle.close_off_topic.hint": "If a tracked window moves to a homepage, new tab, ad page, or other non-stream content, it is closed automatically.",
    "browser.toggle.hide_taskbar": "Hide from taskbar and Alt+Tab",
    "browser.toggle.hide_taskbar.hint": "The window stays visible but no longer occupies taskbar space; control it from this app or the window itself.",
    "browser.section.advanced": "Developer and Clean Window",
    "browser.section.advanced.hint": "These settings change browser launch parameters or the login environment. You can leave them alone for normal use.",
    "browser.profile.title": "Dedicated Browser Profile",
    "browser.profile.desc": "Keeps clean windows, position, and size more reliable. Trade-off: this profile is separate from your main browser login state, so you may need to log in again.",
    "browser.profile.enable": "Use a dedicated profile",
    "browser.profile.per_channel": "Use a separate login environment per channel",
    "browser.profile.per_channel.hint": "Helps prevent multiple stream windows from interfering with each other. Trade-off: each channel may need its own login.",
    "browser.tools.title": "Test Tools",
    "browser.tools.hint": "Use a local test page to verify launch behavior, clean windows, and auto-close tracking.",
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
    "browser.btn.test_close": "Test close",
    "browser.btn.save": "Save",
    "browser.compat.disabled": "Custom browser settings are off; the system default browser will be used.",
    "browser.compat.firefox": "Firefox does not support clean windows or position / size control. Related fields are disabled.",
    "browser.compat.chromium": "Chromium-based browsers can use all window settings.",
    "browser.compat.unknown": "Browser type was not recognized, so Chromium-compatible settings will be tried.",
    "browser.msg.invalid_int": "Position and size must be integers.",
    "browser.msg.min_size": "Width and height must be at least 100 pixels.",
    "browser.msg.empty_profile": "Dedicated profile is enabled but the path is empty. Please fill in a folder path.",
    "browser.msg.reset_done": "Reset to default window (0, 0, 1280×720).",
    "browser.msg.test_opened": "Test page opened; clean window mode uses a temporary test profile when enabled.",
    "browser.msg.test_closed": "Sent close command for the test page ({count} window(s)).",
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
    "app.title.cn": "ハローストリーマー",
    "app.title.en": "Hello Streamer",

    "toolbar.add_channel": "＋  チャンネル追加",
    "toolbar.browser_settings": "⚙  ブラウザ設定",
    "toolbar.startup": "起動時に実行",
    "toolbar.minimize_to_tray": "閉じてトレイへ",
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
    "tooltip.row.status.starts_in": "⏱ {countdown} 後に開始",
    "tooltip.row.status.live_elapsed": "⏱ 配信開始から {elapsed} 経過",

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
    "tray.quit": "完全終了",
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
    "browser.tab.user": "ユーザー設定",
    "browser.tab.advanced": "開発者設定",
    "browser.section.open": "配信ページを開く",
    "browser.section.open.hint": "配信ページを開くブラウザを選びます。カスタム設定をオフにすると、システム既定のブラウザを使います。",
    "browser.enable": "カスタムブラウザ設定を使用",
    "browser.path.label": "使用するブラウザ",
    "browser.path.placeholder": "chrome / msedge / またはブラウザ実行ファイルの絶対パス",
    "browser.path.hint": "chrome または msedge はそのまま入力できます。見つからない場合は .exe のフルパスを指定してください。",
    "browser.section.window": "プレイヤーウィンドウ",
    "browser.section.window.hint": "Hello Streamer が配信ページを自動で開くときのウィンドウ動作を設定します。",
    "browser.toggle.new_window": "配信ページを独立ウィンドウで開く",
    "browser.toggle.app_mode": "クリーンなプレイヤーウィンドウを有効化（App Mode）",
    "browser.toggle.app_mode.hint": "Chromium の --app=URL を使い、アドレスバーのないウィンドウを開きます。上級者向けで、独立プロファイルが必要になることがあります。",
    "browser.toggle.minimized": "最小化して開く",
    "browser.section.lifecycle": "自動終了と整理",
    "browser.section.lifecycle.hint": "これらは Hello Streamer が自動で開き、追跡しているプレイヤーウィンドウだけに適用されます。",
    "browser.toggle.close_on_offline": "配信終了時にプレイヤーウィンドウを閉じる",
    "browser.toggle.close_on_offline.hint": "このアプリが開いたウィンドウのみ閉じます。追跡ウィンドウが見つからない場合だけ、タイトルのキーワードで補助判定します。",
    "browser.toggle.close_on_stop": "監視停止時にすべてのプレイヤーウィンドウを閉じる",
    "browser.toggle.close_on_stop.hint": "メイン画面の「停止」を押すと、追跡中のプレイヤーウィンドウを閉じます。自分で開いたタブには触れません。",
    "browser.toggle.close_off_topic": "配信関連ウィンドウだけを残す",
    "browser.toggle.close_off_topic.hint": "追跡中のウィンドウがホーム、新規タブ、広告ページなど配信以外の内容に移動した場合、自動で閉じます。",
    "browser.toggle.hide_taskbar": "タスクバーと Alt+Tab に表示しない",
    "browser.toggle.hide_taskbar.hint": "ウィンドウは表示されたままですが、タスクバーには出ません。このアプリまたはウィンドウ自体から操作してください。",
    "browser.section.advanced": "開発者とクリーンウィンドウ",
    "browser.section.advanced.hint": "これらはブラウザ起動パラメータやログイン環境を変更します。通常利用では変更しなくてもかまいません。",
    "browser.profile.title": "独立ブラウザプロファイル",
    "browser.profile.desc": "クリーンウィンドウ、位置、サイズを安定させます。メインブラウザのログイン状態とは分かれるため、再ログインが必要になることがあります。",
    "browser.profile.enable": "独立プロファイルを使用",
    "browser.profile.per_channel": "チャンネルごとに別のログイン環境を使う",
    "browser.profile.per_channel.hint": "複数の配信ウィンドウが干渉するのを防ぎます。チャンネルごとにログインが必要になる場合があります。",
    "browser.tools.title": "テストツール",
    "browser.tools.hint": "ローカルのテストページで、起動、クリーンウィンドウ、自動終了の動作を確認します。",
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
    "browser.btn.test_close": "テスト終了",
    "browser.btn.save": "保存",
    "browser.compat.disabled": "カスタムブラウザ設定はオフです。システム既定のブラウザを使用します。",
    "browser.compat.firefox": "Firefox はクリーンウィンドウや位置／サイズ制御に対応していません。関連項目は無効です。",
    "browser.compat.chromium": "Chromium 系ブラウザではすべてのウィンドウ設定を使用できます。",
    "browser.compat.unknown": "ブラウザ種別を判別できないため、Chromium 互換設定を試します。",
    "browser.msg.invalid_int": "座標とサイズは整数で入力してください。",
    "browser.msg.min_size": "幅と高さは 100 ピクセル以上が必要です。",
    "browser.msg.empty_profile": "独立プロファイルが有効ですがパスが空です。フォルダパスを入力してください。",
    "browser.msg.reset_done": "ウィンドウを既定値にリセットしました (0, 0, 1280×720)。",
    "browser.msg.test_opened": "テストページを開きました。クリーンウィンドウ有効時は一時テストプロファイルを使用します。",
    "browser.msg.test_closed": "テストページに閉じる指示を送信しました（{count} 個のウィンドウ）。",
    "browser.close.title": "ブラウザ設定を保存しますか？",
    "browser.close.body": "閉じる前に、ブラウザ設定の変更を保存しますか？",

    "channel.id.prefix": "ID: {id}",

    "boot.write_fail.title": "起動失敗",
    "boot.write_fail.body": "プログラムフォルダに書き込めません：\n{directory}\n\n書き込み可能なフォルダへ移動してから再試行してください。",

    "notify.upcoming.title": "📅 {channel_name} が待機室を開設 [{platform_display}]",
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
    "app.title.cn": "헬로 스트리머",
    "app.title.en": "Hello Streamer",

    "toolbar.add_channel": "＋  채널 추가",
    "toolbar.browser_settings": "⚙  브라우저 설정",
    "toolbar.startup": "시작 시 실행",
    "toolbar.minimize_to_tray": "닫을 때 트레이로",
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
    "tray.tooltip.watch": "Hello Streamer — 감시 전용",
    "tray.tooltip.stopped": "Hello Streamer — 정지됨",

    "action.open_and_stop": "페이지 열고 감시 정지",
    "action.open_and_keep": "페이지 열고 감시 유지",
    "action.notify_only": "시스템 알림만",
    "action.open_and_exit": "페이지 열고 앱 종료",

    "add.title": "채널 추가",
    "add.heading": "채널 URL 붙여넣기 (플랫폼 자동 감지)",
    "add.url.placeholder": "채널 URL 붙여넣기",
    "add.url.hint": "Twitch: twitch.tv/channel_name    YouTube: youtube.com/@channel_name",
    "add.url.warning": "YouTube URL은 @handle을 포함해야 합니다. @handle이 없는 시청 페이지, Shorts, /live는 지원하지 않습니다.",
    "add.manual.heading": "또는 채널 이름을 직접 입력",
    "add.manual.platform": "플랫폼",
    "add.manual.name": "채널 이름",
    "add.manual.name.placeholder": "예: kaicenat, channel_name, 또는 UC로 시작하는 채널 ID",
    "add.manual.hint": "YouTube의 경우 @handle 뒤의 이름이나 UC로 시작하는 채널 ID를 입력하세요.",
    "add.btn.cancel": "취소",
    "add.btn.add": "추가",
    "add.msg.parsed": "{platform_upper} : {name}",
    "add.msg.unparseable": "URL을 파싱할 수 없습니다. YouTube URL은 @handle을 포함하거나, @ 뒤의 이름을 직접 입력하세요.",
    "add.msg.invalid_url": "지원하지 않는 URL 형식입니다. YouTube는 @handle이 포함된 URL(예: https://www.youtube.com/@channel_name/live)을 붙여넣으세요.",
    "add.msg.empty": "채널 URL을 붙여넣거나 채널 이름을 직접 입력하세요.",
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
    "browser.tab.user": "사용자 설정",
    "browser.tab.advanced": "개발자 설정",
    "browser.section.open": "방송 페이지 열기",
    "browser.section.open.hint": "방송 페이지를 열 브라우저를 선택합니다. 사용자 정의 모드를 끄면 시스템 기본 브라우저를 사용합니다.",
    "browser.enable": "사용자 정의 브라우저 설정 사용",
    "browser.path.label": "사용할 브라우저",
    "browser.path.placeholder": "chrome / msedge / 또는 브라우저 실행 파일의 절대 경로",
    "browser.path.hint": "chrome 또는 msedge를 그대로 입력할 수 있습니다. 찾을 수 없으면 .exe 전체 경로를 입력하세요.",
    "browser.section.window": "플레이어 창",
    "browser.section.window.hint": "Hello Streamer가 방송 페이지를 자동으로 열 때의 창 동작을 설정합니다.",
    "browser.toggle.new_window": "방송 페이지를 독립 창으로 열기",
    "browser.toggle.app_mode": "깔끔한 플레이어 창 사용 (App Mode)",
    "browser.toggle.app_mode.hint": "Chromium --app=URL을 사용해 주소 표시줄 없는 창을 엽니다. 고급 옵션이며 독립 프로필이 필요할 수 있습니다.",
    "browser.toggle.minimized": "최소화해서 열기",
    "browser.section.lifecycle": "자동 닫기와 정리",
    "browser.section.lifecycle.hint": "이 옵션들은 Hello Streamer가 자동으로 열고 추적하는 플레이어 창에만 적용됩니다.",
    "browser.toggle.close_on_offline": "방송 종료 시 플레이어 창 닫기",
    "browser.toggle.close_on_offline.hint": "이 앱이 연 창만 닫습니다. 추적 창을 찾지 못한 경우에만 제목 키워드로 보조 판단합니다.",
    "browser.toggle.close_on_stop": "감시 중지 시 모든 플레이어 창 닫기",
    "browser.toggle.close_on_stop.hint": "메인 창에서 중지를 누르면 추적 중인 플레이어 창을 닫습니다. 사용자가 직접 연 탭은 건드리지 않습니다.",
    "browser.toggle.close_off_topic": "방송 관련 창만 유지",
    "browser.toggle.close_off_topic.hint": "추적 중인 창이 홈, 새 탭, 광고 페이지 등 방송 외 콘텐츠로 이동하면 자동으로 닫습니다.",
    "browser.toggle.hide_taskbar": "작업 표시줄과 Alt+Tab에 표시하지 않기",
    "browser.toggle.hide_taskbar.hint": "창은 계속 보이지만 작업 표시줄 공간을 차지하지 않습니다. 이 앱이나 창 자체에서 조작하세요.",
    "browser.section.advanced": "개발자와 깔끔한 창",
    "browser.section.advanced.hint": "이 설정은 브라우저 실행 매개변수나 로그인 환경을 바꿉니다. 일반 사용에서는 건드리지 않아도 됩니다.",
    "browser.profile.title": "독립 브라우저 프로필",
    "browser.profile.desc": "깔끔한 창, 위치, 크기를 더 안정적으로 적용합니다. 메인 브라우저 로그인 상태와 분리되므로 다시 로그인해야 할 수 있습니다.",
    "browser.profile.enable": "독립 프로필 사용",
    "browser.profile.per_channel": "채널마다 별도 로그인 환경 사용",
    "browser.profile.per_channel.hint": "여러 방송 창이 서로 간섭하는 것을 줄입니다. 채널마다 별도로 로그인해야 할 수 있습니다.",
    "browser.tools.title": "테스트 도구",
    "browser.tools.hint": "로컬 테스트 페이지로 실행, 깔끔한 창, 자동 닫기 추적이 예상대로 동작하는지 확인합니다.",
    "browser.geometry.apply": "사용자 정의 창 위치/크기 적용",
    "browser.geometry.reset": "기본값으로",
    "browser.geometry.reset.tooltip": "X=0, Y=0, 1280×720으로 재설정",
    "browser.geometry.apply.tooltip": "꺼짐: 시스템 기본 창 위치와 크기 사용",
    "browser.geometry.x": "X",
    "browser.geometry.y": "Y",
    "browser.geometry.width": "너비",
    "browser.geometry.height": "높이",
    "browser.btn.cancel": "취소",
    "browser.btn.test": "테스트 실행",
    "browser.btn.test_close": "테스트 닫기",
    "browser.btn.save": "저장",
    "browser.compat.disabled": "사용자 정의 브라우저 설정이 꺼져 있어 시스템 기본 브라우저를 사용합니다.",
    "browser.compat.firefox": "Firefox는 깔끔한 창이나 위치 / 크기 제어를 지원하지 않습니다. 관련 항목은 비활성화됩니다.",
    "browser.compat.chromium": "Chromium 계열 브라우저는 모든 창 설정을 사용할 수 있습니다.",
    "browser.compat.unknown": "브라우저 종류를 알 수 없어 Chromium 호환 설정을 먼저 시도합니다.",
    "browser.msg.invalid_int": "좌표와 크기는 정수여야 합니다.",
    "browser.msg.min_size": "너비와 높이는 최소 100 픽셀이어야 합니다.",
    "browser.msg.empty_profile": "독립 프로필이 활성화되었지만 경로가 비어 있습니다. 폴더 경로를 입력하세요.",
    "browser.msg.reset_done": "기본 창으로 재설정됨 (0, 0, 1280×720).",
    "browser.msg.test_opened": "테스트 페이지가 열렸습니다. 깔끔한 창을 켜면 임시 테스트 프로필을 사용합니다.",
    "browser.msg.test_closed": "테스트 페이지 닫기 명령을 보냈습니다 ({count}개 창).",
    "browser.close.title": "브라우저 설정을 저장하시겠습니까?",
    "browser.close.body": "닫기 전에 브라우저 설정 변경 사항을 저장하시겠습니까?",

    "channel.id.prefix": "ID: {id}",

    "boot.write_fail.title": "시작 실패",
    "boot.write_fail.body": "프로그램 폴더에 쓸 수 없습니다:\n{directory}\n\n쓰기 가능한 폴더로 옮긴 후 다시 시도하세요.",

    "notify.upcoming.title": "📅 {channel_name} 대기실 생성됨 [{platform_display}]",
    "notify.upcoming.body.scheduled": "방송 예정: {time_str}",
    "notify.upcoming.body.default": "곧 방송 시작",
    "notify.video.title": "🎬 {channel_name} 새 영상 업로드 [{platform_display}]",
    "notify.video.body.default": "새 영상",
    "notify.live.title": "🔴 {channel_name} 라이브 시작! [{platform_display}]",
    "notify.live.body.default": "{channel_name} — {platform}에서 방송 중",
    "notify.watch_now": "지금 시청",
}


_TRANSLATIONS: dict[str, dict[str, str]] = {
    "zh_TW": _ZH_TW,
    "zh_CN": _ZH_CN,
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
