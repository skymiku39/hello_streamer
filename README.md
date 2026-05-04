# 開播監聽器 Stream Monitor

監控 Twitch / YouTube 實況主開播狀態的 Windows 桌面應用程式。

## 功能

- 多頻道監聽：支援 Twitch 與 YouTube。
- 四種觸發行為：開啟網頁並停止、開啟並保持、僅通知、開啟後關閉程式。
- Windows Toast 通知與系統匣操作。
- 設定自動儲存到 `config.json`。
- 封裝版可寫入 Registry Run key，在登入 Windows 後以靜默模式啟動。
- CustomTkinter 深色介面。

## 快速開始

```bash
uv sync
uv run python -m stream_monitor.app
```

也可以使用套件入口：

```bash
uv run stream-monitor
```

## 開發與測試

```bash
uv sync --extra dev
uv run ruff check .
uv run python -m compileall -f stream_monitor build.py
uv run pytest -q
```

CI 會在 `main` push 與 pull request 上執行相同的檢查。Release workflow 則會在推送 `v*` tag 時建置並上傳 Windows exe。

## 打包為 .exe

```bash
uv sync --extra dev
uv run python build.py
```

產出的執行檔位於 `dist/HelloStreamer.exe`。

## 設定與開機啟動

- 開發模式會讀寫專案根目錄的 `config.json`。
- 封裝成 exe 後會讀寫 exe 同層的 `config.json`。
- `config.json` 是本機 runtime 設定，已由 `.gitignore` 排除。
- 開機啟動只會在封裝版寫入完整 command，例如 `"HelloStreamer.exe" --silent`；開發模式不會寫入不完整的 `python.exe`。

## 常見問題

- 如果 YouTube 或 Twitch 狀態偶爾抓不到，通常是平台頁面或反爬策略變動造成；目前版本維持無 token 的網頁偵測方式。
- 如果通知沒有出現，請先確認 Windows 通知設定允許應用程式顯示 Toast。
- 如果開機啟動無效，請確認使用的是 `dist/HelloStreamer.exe`，不是開發模式的 Python 程式。

## 專案結構

```text
stream_monitor/
├── app.py               # CustomTkinter 主視窗與 UI
├── config_manager.py    # config.json 驗證、讀寫與 atomic save
├── monitor.py           # 背景輪詢排程器
├── notifier.py          # 開播後的觸發行為
├── startup.py           # Windows Registry 開機自啟動
├── tray.py              # 系統匣圖示與選單
└── fetcher/
    ├── base.py          # StreamFetcher 抽象介面
    ├── twitch.py        # Twitch 狀態偵測
    └── youtube.py       # YouTube 狀態偵測
```

## 架構設計

- `Monitor` 在背景執行緒輪詢，透過 callback 與 Queue 把事件送回 UI 執行緒。
- UI 只透過 `Monitor.snapshot_statuses()` 取得狀態快照，避免直接讀取背景執行緒內部狀態。
- `config_manager` 會驗證設定值並使用 atomic save，降低設定檔損壞風險。
