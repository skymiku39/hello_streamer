# Hello Streamer

[![CI](https://github.com/skymiku39/hello_streamer/actions/workflows/ci.yml/badge.svg)](https://github.com/skymiku39/hello_streamer/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/skymiku39/hello_streamer?label=release)](https://github.com/skymiku39/hello_streamer/releases)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Hello Streamer 是一個桌面實況監控工具，用來追蹤 Twitch 與 YouTube 頻道的開播狀態。當追蹤的頻道開播時，它可以通知你、打開直播頁、保持監控其他頻道，或在直播結束後自動關閉由程式開啟的播放視窗。

支援 Windows、Linux 與 Raspberry Pi 64-bit。主要介面使用 CustomTkinter，通知支援 Windows Toast 與 Linux `notify-send`。

## 功能特色

- 監控 Twitch 與 YouTube 頻道，不需要使用者提供 API Token。
- 支援 YouTube 直播、預定直播與一般影片狀態判斷。
- 支援 Twitch 開播狀態、直播標題、頻道顯示名稱與直播網址。
- 可透過 URL 貼上新增頻道，支援 Twitch channel、YouTube handle 與 YouTube channel ID。
- 可停用單一頻道、調整頻道順序、移除頻道。
- 支援繁體中文、簡體中文、英文、日文、韓文介面，語言可在執行中切換。
- 內建開播 / 離線防抖機制，降低平台短暫查詢異常造成的重複通知或誤關閉視窗。
- 支援全域「只監測」與單一頻道「只監測」，可只更新狀態而不觸發通知或瀏覽器。
- 瀏覽器設定採單頁流程：開啟方式、帳號、視窗大小與自動整理依選項自動顯示或隱藏，減少術語與無效勾選。
- 程式專用帳號提供登入輔助工具，可先登入 Twitch / YouTube，讓後續自動開啟的播放器沿用 cookies。
- 自動整理（下播關窗、停止時關閉等）僅在程式專用帳號搭配獨立視窗時可用，避免誤關使用者原本的瀏覽器。
- 支援兩種監控模式：
  - 觸發模式：偵測到開播時執行設定的動作。
  - 觀察模式：只更新畫面狀態，不自動通知或開啟瀏覽器。
- 支援系統匣最小化、單一實例、防止重複啟動。
- 支援開機自動啟動。
- 使用 SQLite 記錄已看過的 YouTube video ID，避免重複通知。

## 下載

最新版本請到 [GitHub Releases](https://github.com/skymiku39/hello_streamer/releases/latest) 下載。

| 平台 | 檔案 |
| --- | --- |
| Windows x64 | `HelloStreamer-v1.1.2-windows-x64.exe` |
| Linux x64 | `HelloStreamer-v1.1.2-linux-x64.tar.gz` |
| Linux ARM64 / Raspberry Pi 64-bit | `HelloStreamer-v1.1.2-linux-arm64.tar.gz` |

Windows 第一次執行時可能會顯示安全提示，請確認來源是本專案的 GitHub Release。

## 快速開始

1. 下載並啟動 `HelloStreamer`。
2. 按「新增頻道」。
3. 貼上 Twitch 或 YouTube 頻道網址。
4. 選擇偵測到開播時要執行的動作。
5. 按「開始監聽」。

支援的網址範例：

| 平台 | 範例 |
| --- | --- |
| Twitch | `https://www.twitch.tv/channel_name` |
| YouTube handle | `https://www.youtube.com/@handle` |
| YouTube channel ID | `https://www.youtube.com/channel/UCxxxxxxxx` |
| YouTube handle 簡寫 | `@handle` |

## 開播動作

偵測到直播開播時，可以選擇以下動作：

| 動作 | 說明 |
| --- | --- |
| 開啟並停止監聽 | 打開直播頁後停止監控。 |
| 開啟並繼續監聽 | 打開直播頁後繼續監控其他頻道。 |
| 只通知 | 只顯示系統通知，不自動開啟瀏覽器。 |
| 開啟並結束程式 | 打開直播頁後關閉 Hello Streamer。 |

YouTube 預定直播會強制走「只通知」，避免尚未開播時就自動打開播放器。

## 監控模式

主視窗下方提供兩種啟動方式：

| 模式 | 說明 |
| --- | --- |
| 監聽+觸發 | 依照「觸發行為」設定通知、開啟直播頁、停止監聽或結束程式。 |
| 只監測 | 只輪詢 Twitch / YouTube 狀態並更新畫面，不送通知、不自動開啟瀏覽器，也不執行離線關閉。 |

每個頻道列也有眼睛按鈕，可把單一頻道切到「只監測」。這適合想觀察某些頻道狀態，但不想讓它們觸發通知、開瀏覽器或關閉播放器的情境。

## 監控穩定性

直播平台偶爾會出現短暫查詢異常，例如 Twitch 暫時回傳未開播，或 YouTube 的直播清單在單次輪詢中漏掉仍在直播的影片。Hello Streamer 會要求連續兩次確認離線，才會真的觸發離線事件。

這個保護可以避免：

- 單次 API 抖動造成重複「已開播」通知。
- 已開啟的播放器因短暫誤判離線而被自動關閉。
- YouTube LIVE 影片短暫消失時，畫面閃回 UPCOMING 或 OFFLINE。
- YouTube fallback 監控與 TIDUS 清單恢復時產生錯誤離線事件。

v0.9.13 起，程式會在啟動、監控執行緒重啟與每 24 小時自動清理 `seen_videos.db` 舊紀錄，並修剪 YouTube watch 頁快取，適合長期常駐監聽。

### 檢查間隔與單輪耗時

主視窗的「檢查間隔」（預設 60 秒、最低 10 秒）決定**多久輪詢一次**；從主播開播到你看到 LIVE，通常約 **1 個檢查間隔 + 單輪 HTTP 耗時**。離線事件需連續兩次確認，最慢約 **2 × 檢查間隔**。

每輪採兩梯次：先並行探測開播（Tier 1），再並行更新離線／待機室細節（Tier 2）。多頻道預設最多 4 路並行，N 個頻道的牆鐘時間約為 `ceil(N/4) × 單頻道耗時`（兩個 tier 相加）。

| 平台 | 單頻道典型 HTTP（每輪） | 主要請求 |
| --- | --- | --- |
| Twitch | 約 0.5–2 秒（通常 1 次 GQL） | `StreamStatus`；冷啟動離線時可能再查 ARCHIVE 錄播 |
| YouTube | 約 1–4 秒（1 次 streams 頁 + 0–1 watch 頁） | `/@channel/streams`；Tier 2 可能補抓 watch 頁取得開播／待機時間 |

Twitch 建議檢查間隔 ≥30 秒，以降低 rate limit 風險。若單輪耗時超過檢查間隔，log 會出現 `Poll slower than interval` 警告。

## 介面語言

Hello Streamer 內建多國語言介面，可透過主視窗左上方的語言按鈕切換。

| 語言 | 代碼 |
| --- | --- |
| 繁體中文 | `zh_TW` |
| 简体中文 | `zh_CN` |
| English | `en` |
| 日本語 | `ja` |
| 한국어 | `ko` |

語言設定會寫入 `config.json`，下次啟動時會自動套用。切換語言後，主視窗、設定對話框、工具提示、通知文字與系統匣選單會同步更新。

## 瀏覽器設定

主視窗的「瀏覽器設定」為單頁流程。頂部「**使用自訂設定開啟直播頁**」勾選後，由 Hello Streamer 啟動 Chrome / Edge 並套用下方選項；取消勾選則改用 Windows 預設瀏覽器。

### 如何開啟直播頁

| 選項 | 說明 |
| --- | --- |
| 開在現有視窗（預設） | 在目前瀏覽器視窗新增分頁；最輕量，但無法設定視窗大小，也無法自動整理。 |
| 單獨新視窗 | 另開一扇瀏覽器視窗，通常只有這一個分頁；可設定視窗位置與大小。 |
| 獨占視窗（無分頁列） | 類似命名視窗，無法再開其他分頁；搭配程式專用帳號時較穩定。 |

### 視窗位置與大小

僅在「單獨新視窗」或「獨占視窗」時顯示。可設定 X、Y、寬、高；Windows 上會透過 Win32 API 補強。使用平常 Chrome 帳號時，若瀏覽器已在執行，座標參數可能被既有程序忽略。

### 使用哪個帳號

| 選項 | 說明 |
| --- | --- |
| 平常使用的 Chrome 帳號 | 沿用目前已登入的 Twitch / YouTube；無法使用自動整理。 |
| 程式專用帳號 | 登入資料存在專案資料夾（預設 `browser_profile/`），需另行登入一次；可啟用自動整理。可用「登入此帳號」先完成 cookies 初始化。 |

### 自動整理

僅在「程式專用帳號」且選擇「單獨新視窗」或「獨占視窗」時顯示。若仍選「開在現有視窗」，介面會提示需改為獨立視窗才能啟用。

| 選項 | 說明 |
| --- | --- |
| 直播結束時關閉播放器視窗 | 頻道從 LIVE 轉回 OFFLINE 時，關閉由程式開啟的對應視窗。 |
| 停止監聽時關閉所有播放器 | 按下主視窗「停止」時，關閉程式追蹤到的播放器視窗。 |
| 只保留直播相關視窗 | 輪詢後檢查追蹤視窗標題，若已跳到非直播內容則自動關閉。 |
| 開啟後先最小化 | 視窗開啟後最小化。 |
| 從工作列與 Alt+Tab 隱藏 | Windows 上將播放視窗設為 tool window。 |

### 更多選項

| 設定 | 說明 |
| --- | --- |
| 使用的瀏覽器 | 可填 `chrome`、`msedge`、`firefox`，或完整 `.exe` 路徑。 |
| 每頻道隔離 | 在 Profile 目錄下依平台與頻道建立子資料夾；需程式專用帳號。 |

### Chrome / Edge 注意事項

Chrome 與 Edge 會共用長駐的 master process。若瀏覽器已經開著，再次執行帶 `--app`、座標或大小參數的啟動指令時，這些參數可能被既有程序忽略。

精準的視窗追蹤與自動關閉，建議使用「程式專用帳號」搭配「單獨新視窗」或「獨占視窗」。使用平常帳號時，程式仍會開啟直播頁，但會保守停用不可靠的視窗追蹤，避免誤關使用者原本的瀏覽器視窗。

### 測試工具

對話框底部的「測試開啟」使用本機 HTML 測試頁。「測試關閉」可確認視窗追蹤與自動關閉是否正常。

## 設定檔與資料

執行時會在程式所在目錄附近使用以下檔案：

| 檔案 / 資料夾 | 說明 |
| --- | --- |
| `config.json` | 使用者設定、頻道清單、瀏覽器設定與語言偏好。 |
| `seen_videos.db` | SQLite 資料庫，記錄已看過的 YouTube 影片與直播。 |
| `logs/stream_monitor.log` | 執行 log；單檔上限 2 MB、保留 5 份備份，總計約 12 MB 後自動輪替，適合長期常駐。 |
| `browser_profile/` | 預設獨立瀏覽器 Profile 位置。 |

### 疑難排解（漏檢開播）

若開播時沒有通知或沒自動開啟播放器，請先開啟 `logs/stream_monitor.log` 並搜尋下列關鍵字：

| log 關鍵字 | 意義 |
| --- | --- |
| `went_live_suppressed` | 程式仍認為該頻道已在 LIVE，因此不會再觸發開播動作。常見於下播後狀態未正確落地；v0.9.3 已將 `fetch returned None` 計入離線防抖以緩解。 |
| `fetch returned None` | Twitch GQL 或 YouTube fallback 查詢失敗；若連續兩次發生且先前為 LIVE，會確認下播。 |
| `ignoring transient offline reading` | 單次 API 回報 offline，防抖中尚未確認下播（`(1/2)` 表示還需再 1 次）。 |
| `Poll complete` | 每輪輪詢摘要；含 `tier1=`（觸發梯次耗時）與 `total=`（整輪耗時）。 |

另請確認：已按「監聽+觸發」（非「只監測」）、檢查間隔不要過長、觸發「開啟並停止監聽」後 monitor 會停止直到再次啟動。

### 休眠／喚醒後狀態異常

電腦睡眠或休眠後喚醒，網路可能尚未就緒；若頻道列凍結、誤判下播或仍顯示 LIVE，請先查看 log：

| log 關鍵字 | 意義 |
| --- | --- |
| `wake_verify_scheduled` | 偵測到長時間暫停（如睡眠）；將先執行喚醒驗證輪詢（無視檢查間隔）。 |
| `wake_verify_confirmed` | 驗證結果與目前快取狀態一致，已刷新 UI，**不觸發**開播／下播動作。 |
| `wake_verify_deferred` | 驗證結果不同或無回應；保留現狀，等下一輪正式輪詢再判斷。 |
| `Monitor thread died unexpectedly` | 背景監控執行緒異常結束；程式會保留狀態並自動重啟，底部狀態短暫顯示「監控已自動恢復…」。 |
| `Poll cycle failed unexpectedly` | 單輪輪詢發生未預期錯誤，但執行緒會繼續下一輪（不會整體停擺）。 |
| `fetch exception` | API 查詢拋出例外；若先前為 LIVE 會計入離線防抖（與 `fetch returned None` 相同），不再永久凍結在 LIVE。 |

喚醒後流程：先驗證輪詢 → 等待檢查間隔 → 正式輪詢（含 2-strike 防抖）。驗證輪期間不會觸發 `close_on_offline`。

**限制：** 若直播在睡眠期間開始又結束，醒來時已下播，程式無法事後補發開播通知（平台 API 限制）。

### 離線列與錄播連結（v0.9.4+）

v0.9.10 起：輪詢採**兩梯次**——先確認直播並觸發動作，再更新離線／待機室／錄播等細節。YouTube / Twitch 離線狀態與連結各自獨立處理。下播時間採**混合模式**（有錄播結束時間則優先採用，不設 48 小時上限）；左側平台徽章開**頻道首頁**。

**YouTube 待機室**（7 日內、未來排程）一律顯示為 `OFFLINE` 列：左側顯示開播倒數，右側 🔗 連待機直播間。僅在 fallback 偵測到 active premiere（`/live` 待機畫面）時才顯示橘色 `UPCOMING`。無排程、已過期或週表排程不會誤判為待機室。若 `/streams` 頁沒有排程時間，會在梯次 2 補抓 watch 頁。

離線時右側 🔗 優先順序：

- **YouTube**：待機直播間（合法 `UPCOMING` 排程）> 上一場錄播 > 頻道首頁
- **Twitch**：上一場錄播 > 頻道首頁

頻道下播後，列表會顯示 `OFFLINE` 與「已下播多久」；有待機室時左側改顯示開播倒數。

打包後的版本會將設定放在執行檔旁邊。原始碼執行時會使用專案根目錄。

載入舊版 `config.json` 時，Hello Streamer 會自動補齊缺漏欄位，並將舊版瀏覽器設定對應到新介面（開啟方式、帳號、視窗大小、自動整理）。若「每頻道隔離」已啟用但 `user_data_dir` 為空，程式會改用 `browser_profile/` 作為預設登入資料目錄。若啟用自訂瀏覽器且勾選獨占視窗、自動整理等需要隔離的功能，但登入資料路徑仍為空，也會自動補上 `browser_profile/`，避免 Chrome / Edge 共用主程序而導致視窗追蹤或自動關閉不穩定。

## 從原始碼執行

需求：

- Python 3.11 或更新版本
- [uv](https://github.com/astral-sh/uv)

安裝依賴並啟動：

```bash
uv sync --extra dev
uv run python -m stream_monitor
```

也可以使用 project script：

```bash
uv run stream-monitor
```

靜默啟動：

```bash
uv run python -m stream_monitor --silent
```

`--silent` 會依照上次保存的監控模式自動啟動，適合搭配開機自動啟動。

## 開發與測試

常用檢查指令：

```bash
uv run ruff check .
uv run python -m compileall -f stream_monitor build.py
uv run pytest -q
```

目前 CI 也會在 GitHub Actions 中執行檢查。

## 打包

本專案使用 PyInstaller 打包：

```bash
uv sync --extra dev
uv run python build.py
```

輸出位置：

| 平台 | 輸出 |
| --- | --- |
| Windows | `dist/HelloStreamer.exe` |
| Linux | `dist/HelloStreamer` |

推送 `v*` tag 後，release workflow 會建立 GitHub Release 並上傳對應平台的產物。

## Linux / Raspberry Pi

Linux 桌面環境建議安裝以下套件：

```bash
sudo apt update
sudo apt install -y \
    python3-tk \
    python3-gi \
    python3-gi-cairo \
    libgirepository1.0-dev \
    gir1.2-gtk-3.0 \
    gir1.2-ayatanaappindicator3-0.1 \
    libnotify-bin \
    fonts-dejavu-core
```

說明：

- `libnotify-bin` 提供 `notify-send`。
- `python3-gi` 與 `gir1.2-ayatanaappindicator3-0.1` 可改善系統匣支援。
- Raspberry Pi 請使用 64-bit Raspberry Pi OS，並下載 `linux-arm64` 版本。

檢查 Raspberry Pi 架構：

```bash
uname -m
getconf LONG_BIT
```

結果是 `aarch64` 且 `64` 時，可以使用 ARM64 release。若是 32-bit OS，請改用 64-bit Raspberry Pi OS 或從原始碼自行調整環境。

## 專案結構

```text
stream_monitor/
  app.py                 CustomTkinter 主視窗、監控生命週期
  channel_row.py         頻道列表單列 UI
  events/                監控 Pub/Sub 事件型別與 EventBus
  event_sink.py          EventBridge 窄介面 Protocol（ISP）
  event_bridge.py        EventBus 訂閱者 → UI 執行緒副作用
  app_dialogs.py         頻道 / 語言 / 瀏覽器設定對話框
  app_ui.py              UI 共用工具（字型、按鈕、tooltip、時間格式化）
  browser_settings_model.py  瀏覽器設定 UI 維度與能力判斷
  browser_win32.py       Win32 視窗追蹤、幾何、關閉、工作列隱藏
  config_manager.py      config.json 載入、驗證、atomic save
  db.py                  SQLite seen video database
  i18n.py                多國語言字串表、語言切換與訂閱機制
  monitor/               背景輪詢（types、core、probes/ 平台策略）
  notifier.py            通知、瀏覽器啟動（轉發 browser_win32）
  single_instance.py     單一實例保護
  startup.py             Windows Registry / Linux XDG Autostart
  tray.py                系統匣圖示與選單
  url_parser.py          Twitch / YouTube URL 解析
  util.py                頻道 key、頻道頁 URL、ISO 時間解析
  fetcher/
    base.py              Fetcher 抽象類別與資料模型
    twitch.py            Twitch 狀態擷取
    youtube.py           YouTube 頻道頁解析
tests/                   pytest 測試
build.py                 PyInstaller 打包腳本
```

## 疑難排解

### 獨占視窗沒有變成無網址列視窗

請改用「程式專用帳號」並選擇「獨占視窗」。如果 Chrome / Edge 已經開著，瀏覽器可能會把 `--app=URL` 交給既有程序處理，導致獨占視窗被降級成一般分頁。

### 視窗座標或大小沒有生效

請確認：

- 已勾選「使用自訂設定開啟直播頁」。
- 已選擇「單獨新視窗」或「獨占視窗」（「開在現有視窗」不會顯示視窗大小區塊）。
- 已在「視窗位置與大小」區塊啟用並填入座標。
- 使用平常 Chrome 帳號時，若瀏覽器已在執行，座標可能被既有程序忽略；建議改用程式專用帳號。

Windows 上程式會在視窗出現後用 Win32 API 再補一次座標與大小，但如果瀏覽器沒有真的開出新視窗，仍然無法移動既有分頁。

### 直播結束後沒有自動關閉視窗

請確認：

- 已勾選「使用自訂設定開啟直播頁」。
- 已選擇「程式專用帳號」與「單獨新視窗」或「獨占視窗」（自動整理區塊才會出現）。
- 已勾選「直播結束時關閉播放器視窗」。
- 該視窗是由 Hello Streamer 自動開啟，而不是手動從瀏覽器開啟。
- 監控模式是「監聽+觸發」（非「只監測」）。

### Linux 沒有通知

請確認已安裝 `notify-send`：

```bash
which notify-send
```

若沒有，請安裝 `libnotify-bin`。

### `Exec format error`

通常代表下載到錯誤架構的 binary。Raspberry Pi 64-bit 請使用 `linux-arm64`，一般 x64 Linux 請使用 `linux-x64`。

### `GLIBC_x.xx not found`

代表系統版本太舊或 binary 建置環境較新。建議使用較新的 Debian / Ubuntu / Raspberry Pi OS Bookworm，或從原始碼執行。

## 隱私與安全

Hello Streamer 不需要 Twitch 或 YouTube API Token。它會在本機保存：

- 追蹤頻道清單與設定。
- 已看過的 YouTube video ID。
- 瀏覽器登入資料資料夾，僅在使用者啟用程式專用帳號時建立。

所有資料都保存在本機，不會上傳到第三方服務。

## 授權

本專案採用 MIT License，詳見 [LICENSE](LICENSE)。

## 致謝

本專案以 Vibe Coding 方式與 AI 工具共同開發，並感謝社群測試與回饋。
