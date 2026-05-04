# 開播監聽器 Stream Monitor

監控 Twitch / YouTube 實況主開播狀態的 Windows 桌面應用程式。

## 功能

- 多頻道監聽（Twitch / YouTube）
- 四種觸發行為：開啟網頁並停止、開啟並保持、僅通知、開啟後關閉程式
- Windows Toast 通知
- 設定自動儲存 (`config.json`)
- 開機自動啟動（Registry）
- CustomTkinter 現代化深色介面

## 快速開始

```bash
# 安裝依賴（使用 uv）
uv sync

# 啟動應用程式
uv run python -m stream_monitor.app
```

## 打包為 .exe

```bash
uv sync --extra dev
uv run python build.py
```

產出的執行檔位於 `dist/StreamMonitor.exe`。

## 專案結構

```
stream_monitor/
├── __init__.py          # 套件初始化
├── app.py               # CustomTkinter 主視窗 & UI
├── config_manager.py    # config.json 讀寫
├── monitor.py           # 背景輪詢排程器
├── notifier.py          # 觸發行為
├── startup.py           # 開機自啟動 (winreg)
└── fetcher/
    ├── __init__.py      # 工廠函式
    ├── base.py          # StreamFetcher 抽象介面
    ├── twitch.py        # Twitch 爬蟲
    └── youtube.py       # YouTube 爬蟲（預留）
```

## 架構設計

- **策略模式**：`StreamFetcher` 抽象介面，可擴充不同平台的實作
- **解耦設計**：核心邏輯與 UI 分離，未來可轉為背景服務
- **執行緒安全**：Monitor 在背景執行緒輪詢，透過 Queue 與 UI 執行緒通訊
