# 哈嘍主播 Hello Streamer

[![CI](https://github.com/skymiku39/hello_streamer/actions/workflows/ci.yml/badge.svg)](https://github.com/skymiku39/hello_streamer/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/skymiku39/hello_streamer?label=release)](https://github.com/skymiku39/hello_streamer/releases)

**Hello Streamer** 是一款 Windows 桌面工具，用於監控 Twitch 與 YouTube 頻道的開播狀態。
當指定頻道開始直播或建立待機室時，程式會即時通知你，並可自動開啟直播頁面。

> 本專案透過 **Vibe Coding** 方式完成 —— 由 **Cursor**、**Claude**、**Codex**、**Gemini** 四個 AI 協作開發，
> 並感謝 Discord 群友 **TIDUS** 提供寶貴的架構建議與 YouTube 偵測策略。

## 功能特色

### 頻道監控

- 同時監控多個 **Twitch** / **YouTube** 頻道
- 無需任何 API Token，以公開網頁資料偵測開播狀態
- Twitch 透過 GQL 端點查詢；YouTube 解析頁面 `ytInitialData` 取得直播與待機室資訊
- 自動偵測 YouTube **待機室（UPCOMING）** 並顯示開播倒數計時
- 正在直播的頻道顯示已開播時長

### 頻道管理

- 支援貼上頻道網址，自動辨識平台與頻道名稱
- 新增頻道時即時驗證帳號是否存在，並自動取得顯示名稱
- 可個別暫停 / 恢復監聽，暫停的頻道會保留在清單中但跳過輪詢
- 頻道列左側提供 ▲▼ 按鈕快速排序

### 狀態顯示

- **LIVE**（綠色）：滑鼠懸停顯示直播標題與已開播時長，點擊開啟直播頁面
- **UPCOMING**（橘色）：滑鼠懸停顯示待機室標題與倒計時，點擊開啟待機室頁面
- **OFFLINE**：頻道目前離線
- 平台標籤（TWITCH / YOUTUBE）可直接點擊開啟頻道首頁

### 觸發行為

偵測到開播時，可選擇四種處理方式：

| 行為 | 說明 |
|------|------|
| 開啟網頁並停止監聽 | 自動開啟直播頁面，並停止所有監聽 |
| 開啟網頁並保持監聽 | 開啟直播頁面，繼續監控其他頻道 |
| 僅跳出系統通知 | 顯示 Windows Toast 通知（含「立即觀看」按鈕） |
| 開啟網頁後關閉程式 | 開啟直播頁面後自動結束程式 |

> 待機室（UPCOMING）事件固定使用「僅跳出系統通知」，不會觸發自動開啟網頁。

### 系統整合

- **系統匣常駐**：可從系統匣快速顯示主畫面、切換監聽狀態或完全退出
- **縮小至系統匣開關**：決定關閉視窗時是縮小到系統匣還是直接結束程式
- **開機自動啟動**：註冊 Windows Registry Run key，開機後以 `--silent` 靜默模式背景執行
- **單一執行個體**：重複啟動時自動喚醒已開啟的視窗，不會產生多個程式

## 下載

最新版本可從 GitHub Releases 下載：

**[Download latest release](https://github.com/skymiku39/hello_streamer/releases/latest)**

下載 `HelloStreamer.exe` 後可直接執行，無需安裝。
首次執行時，Windows 可能會顯示安全提示，請確認來源為本專案的 GitHub Release。

## 使用方式

1. 啟動 `HelloStreamer.exe`
2. 點擊「＋ 新增頻道」
3. 貼上 Twitch 或 YouTube 頻道網址（或手動選擇平台並輸入頻道名稱）
4. 設定檢查間隔（最低 10 秒）與觸發行為
5. 點擊「▶ 開始監聽」

### 網址格式

| 平台 | 支援格式 |
|------|---------|
| Twitch | `https://www.twitch.tv/channel_name` |
| YouTube | `https://www.youtube.com/@handle` 或包含 `@handle` 的任何頁面 |

> YouTube 連結必須包含 `@handle`；不含 `@handle` 的觀看頁、Shorts 或 `/live` 連結無法自動辨識。
> 手動輸入時可填 `@handle` 後的名稱或 `UC` 開頭的頻道 ID。

## 設定

應用程式使用本機 `config.json` 儲存所有設定，包含頻道清單、檢查間隔、觸發行為、視窗位置等。

- 開發模式：`config.json` 位於專案根目錄
- 封裝版：`config.json` 位於 `HelloStreamer.exe` 同一層目錄
- `config.json` 屬於本機 runtime 設定，不會提交到版本控制

### 開機自動啟動

開機自動啟動只支援封裝版 `HelloStreamer.exe`。啟用後，程式會寫入 Windows Registry Run key，開機時以靜默模式自動啟動：

```text
"HelloStreamer.exe" --silent
```

開發模式不會寫入開機自啟 registry；若需要測試背景啟動，請手動執行 `uv run python -m stream_monitor --silent`。

## 開發

本專案使用 **Python 3.11** 與 [uv](https://github.com/astral-sh/uv) 管理環境。

```bash
uv sync --extra dev
uv run python -m stream_monitor
```

也可以透過 project script 啟動：

```bash
uv run stream-monitor
```

### 程式碼品質檢查

```bash
uv run ruff check .
uv run python -m compileall -f stream_monitor build.py
uv run pytest -q
```

GitHub Actions 會在 `main` push 與 pull request 上自動執行以上檢查。

## 打包

使用 PyInstaller 打包 Windows 可執行檔：

```bash
uv sync --extra dev
uv run python build.py
```

輸出檔案位於 `dist/HelloStreamer.exe`。

推送 `v*` tag 時，release workflow 會自動建置 exe 並發佈 GitHub Release。

## 專案結構

```text
stream_monitor/
├── app.py               # CustomTkinter 主視窗與 UI
├── config_manager.py    # config.json 驗證、讀寫與 atomic save
├── db.py                # SQLite 影片紀錄（videoId + style 去重）
├── monitor.py           # 背景輪詢排程器（Twitch 邊緣觸發 / YouTube TIDUS 架構）
├── notifier.py          # 開播通知與觸發行為
├── single_instance.py   # TCP 單一執行個體鎖定
├── startup.py           # Windows Registry 開機自啟動
├── tray.py              # 系統匣圖示與選單
├── url_parser.py        # 頻道網址解析
└── fetcher/
    ├── base.py          # StreamFetcher 抽象介面與資料模型
    ├── twitch.py        # Twitch GQL 狀態偵測
    └── youtube.py       # YouTube 頁面解析（含待機室與開播時間補全）
```

## 已知限制

- Twitch 與 YouTube 的狀態偵測依賴公開網頁資料與非官方端點，平台頁面結構或反爬策略變動時可能需要更新
- 目前主要支援 Windows，其他作業系統未列為正式支援目標
- 本專案目前未提供自動更新機制

## 隱私

Hello Streamer 不會將任何設定同步到外部伺服器。頻道清單與使用者設定僅儲存在本機 `config.json`，影片紀錄存於本機 `seen_videos.db`。程式僅向 Twitch / YouTube 發送請求以檢查頻道狀態。

## 致謝

本專案以 **Vibe Coding** 方式開發，由以下 AI 工具協作完成：

- [Cursor](https://www.cursor.com/) — AI 程式編輯器
- [Claude](https://claude.ai/) — Anthropic 語言模型
- [Codex](https://openai.com/codex/) — OpenAI 程式生成模型
- [Gemini](https://gemini.google.com/) — Google 語言模型

特別感謝 Discord 群友 **TIDUS** 提供 YouTube 待機室偵測策略與架構建議。

## License

本專案採用 MIT License。完整條款請見 [`LICENSE`](LICENSE)。
