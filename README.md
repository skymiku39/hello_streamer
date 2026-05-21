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
| Windows x64 | `HelloStreamer-v0.6.2-windows-x64.exe` |
| Linux x64 | `HelloStreamer-v0.6.2-linux-x64.tar.gz` |
| Linux ARM64 / Raspberry Pi 64-bit | `HelloStreamer-v0.6.2-linux-arm64.tar.gz` |

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

Hello Streamer 內建進階瀏覽器設定，可以控制直播頁要如何打開。

| 設定 | 說明 |
| --- | --- |
| 啟用自訂瀏覽器設定 | 使用 `subprocess` 啟動指定瀏覽器，才能套用視窗參數。 |
| 瀏覽器路徑 | 可填 `chrome`、`msedge`、`firefox`，也可填完整 `.exe` 路徑。Windows 會自動解析常見 Chrome / Edge / Firefox 安裝路徑。 |
| 獨立視窗 | 使用瀏覽器的新視窗開啟直播頁。 |
| App Mode | 使用 Chromium 的 `--app=URL`，開出較乾淨的播放器視窗。勾選後會自動啟用並鎖定獨立視窗。 |
| 套用座標 / 大小 | 設定視窗 X、Y、寬、高。 |
| 最小化開啟 | 視窗開啟後最小化。Windows 上會透過 Win32 API 補強。 |
| 獨立 Profile | 使用獨立 `user-data-dir`，避免既有 Chrome / Edge 主程序吃掉 `--app`、視窗座標與大小參數。 |
| 每頻道 Profile | 在 Profile 目錄下依平台與頻道建立子資料夾，讓每個直播視窗更穩定地套用 App Mode。若舊設定啟用此選項但 Profile 路徑是空的，程式會自動補成 `browser_profile/`。 |
| 直播結束自動關閉 | 當頻道從 LIVE 轉回 OFFLINE 時，關閉由 Hello Streamer 開啟的對應瀏覽器視窗。 |
| 停止監聽時關閉所有播放器 | 按下主視窗「停止」時，關閉 Hello Streamer 追蹤到、且由程式開啟的播放器視窗。 |
| 只保留直播相關視窗 | 每次輪詢後檢查追蹤視窗標題，若視窗已跳到新分頁、首頁、廣告頁或其他非直播內容，會自動關閉。 |
| 從工作列與 Alt+Tab 隱藏 | Windows 上將播放視窗設為 tool window，讓它不佔工作列與 Alt+Tab 位置。 |

### Chrome / Edge 注意事項

Chrome 與 Edge 會共用長駐的 master process。當瀏覽器已經開著，再次執行 `chrome.exe --app=... --window-position=...` 時，這些啟動參數可能被既有程序忽略。

如果你需要 App Mode、固定座標、固定大小、離線後自動關閉或每個頻道獨立播放器，建議啟用「獨立 Profile」與「每頻道 Profile」。

### 測試開啟

瀏覽器設定中的「測試開啟」會使用本機 HTML 測試頁，不使用 `about:blank`。App Mode 測試會使用暫時的測試 Profile，避免被主瀏覽器視窗干擾。「測試關閉」可對測試頁送出關閉指令，用來確認視窗追蹤與自動關閉功能是否正常。

## 設定檔與資料

執行時會在程式所在目錄附近使用以下檔案：

| 檔案 / 資料夾 | 說明 |
| --- | --- |
| `config.json` | 使用者設定、頻道清單、瀏覽器設定與語言偏好。 |
| `seen_videos.db` | SQLite 資料庫，記錄已看過的 YouTube 影片與直播。 |
| `logs/stream_monitor.log` | 執行 log。 |
| `browser_profile/` | 預設獨立瀏覽器 Profile 位置。 |

打包後的版本會將設定放在執行檔旁邊。原始碼執行時會使用專案根目錄。

載入舊版 `config.json` 時，Hello Streamer 會自動補齊缺漏欄位並修復舊版瀏覽器設定。常見情境是「每頻道 Profile」已啟用但 `user_data_dir` 為空；程式會改用 `browser_profile/` 作為預設 Profile 根目錄，避免 Chrome / Edge 共用同一個 master process 而導致 App Mode、座標、視窗追蹤或自動關閉行為不穩定。

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
  app.py                 CustomTkinter GUI、系統匣、事件橋接
  config_manager.py      config.json 載入、驗證、atomic save
  db.py                  SQLite seen video database
  i18n.py                多國語言字串表、語言切換與訂閱機制
  monitor.py             背景監控、狀態轉換、離線事件
  notifier.py            通知、瀏覽器啟動、Win32 視窗管理
  single_instance.py     單一實例保護
  startup.py             Windows Registry / Linux XDG Autostart
  tray.py                系統匣圖示與選單
  url_parser.py          Twitch / YouTube URL 解析
  fetcher/
    base.py              Fetcher 抽象類別與資料模型
    twitch.py            Twitch 狀態擷取
    youtube.py           YouTube 頻道頁解析
tests/                   pytest 測試
build.py                 PyInstaller 打包腳本
```

## 疑難排解

### App Mode 沒有變成無網址列視窗

請啟用「獨立 Profile」。如果 Chrome / Edge 已經開著，瀏覽器可能會把 `--app=URL` 交給既有程序處理，導致 App Mode 被忽略。

### 視窗座標或大小沒有生效

請確認：

- 自訂瀏覽器設定已啟用。
- 已勾選「獨立視窗」或「App Mode」。
- 已勾選「套用座標 / 大小」。
- Chrome / Edge 建議啟用「獨立 Profile」。

Windows 上程式會在視窗出現後用 Win32 API 再補一次座標與大小，但如果瀏覽器沒有真的開出新視窗，仍然無法移動既有分頁。

### 直播結束後沒有自動關閉視窗

請確認：

- 瀏覽器設定已啟用。
- 已勾選「直播結束自動關閉」。
- 該視窗是由 Hello Streamer 自動開啟，而不是手動從瀏覽器開啟。
- 監控模式是觸發模式。

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
- 瀏覽器 Profile 資料夾，僅在使用者啟用獨立 Profile 時建立。

所有資料都保存在本機，不會上傳到第三方服務。

## 授權

本專案採用 MIT License，詳見 [LICENSE](LICENSE)。

## 致謝

本專案以 Vibe Coding 方式與 AI 工具共同開發，並感謝社群測試與回饋。
