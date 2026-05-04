# Hello Streamer

[![CI](https://github.com/skymiku39/hello_streamer/actions/workflows/ci.yml/badge.svg)](https://github.com/skymiku39/hello_streamer/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/skymiku39/hello_streamer?label=release)](https://github.com/skymiku39/hello_streamer/releases)

Hello Streamer 是一款 Windows 桌面工具，用於監控 Twitch 與 YouTube 頻道的開播狀態。當指定頻道開始直播時，應用程式可以顯示 Windows 通知、開啟直播頁面、持續監控其他頻道，或在觸發後自動結束。

> 本專案目前以 Windows 使用情境為主，並以無 API token 的網頁狀態偵測方式運作。

## Features

- 監控多個 Twitch / YouTube 頻道。
- 支援貼上頻道網址，自動判斷平台與頻道名稱。
- 新增頻道時會驗證帳號是否存在，並在可取得時自動儲存顯示名稱。
- 可個別暫停 / 恢復頻道監聽，暫停狀態會儲存在本機設定中。
- 頻道列提供快速開啟頻道頁、刪除與排序操作。
- 提供四種開播觸發行為：
  - 開啟直播頁面並停止監控。
  - 開啟直播頁面並保持監控。
  - 僅顯示 Windows Toast 通知。
  - 開啟直播頁面後關閉程式。
- 支援 Windows 系統匣操作。
- 支援封裝版開機自動啟動，並以靜默模式在背景執行。
- 設定檔自動儲存，並具備基本驗證與安全寫入機制。

## Download

最新版本可從 GitHub Releases 下載：

[Download latest release](https://github.com/skymiku39/hello_streamer/releases/latest)

目前 release 會提供 Windows 可執行檔：

```text
HelloStreamer.exe
```

下載後可直接執行。首次執行時，Windows 可能會顯示安全提示；請確認來源為本專案的 GitHub Release。

## Usage

1. 啟動 `HelloStreamer.exe`。
2. 點選新增頻道。
3. 貼上 Twitch 或 YouTube 頻道網址，或手動選擇平台並輸入頻道名稱。
4. 選擇檢查間隔與觸發行為。
5. 開始監控。

頻道清單中每列都可以個別暫停或恢復監聽；被暫停的頻道會留在清單中，但背景輪詢會略過它。恢復後會先顯示待更新狀態，並在下一輪檢查後更新為 LIVE / OFFLINE。

關閉主視窗時，應用程式會隱藏到系統匣，而不是直接結束。若要完全結束，請從系統匣選單退出。

## Configuration

應用程式會使用本機 `config.json` 儲存設定。

- 開發模式：`config.json` 位於專案根目錄。
- 封裝版：`config.json` 位於 `HelloStreamer.exe` 同一層目錄。
- `config.json` 屬於本機 runtime 設定，不會提交到版本控制。

開機自動啟動只支援封裝後的 exe。啟用後，程式會寫入 Windows Registry Run key，並使用類似下列的啟動命令：

```text
"HelloStreamer.exe" --silent
```

## Development

本專案使用 Python 3.11 與 [uv](https://github.com/astral-sh/uv) 管理環境。

```bash
uv sync --extra dev
uv run python -m stream_monitor.app
```

也可以透過 project script 啟動：

```bash
uv run stream-monitor
```

### Quality checks

```bash
uv run ruff check .
uv run python -m compileall -f stream_monitor build.py
uv run pytest -q
```

GitHub Actions 會在 `main` push 與 pull request 上執行相同檢查。

## Build

使用 PyInstaller 打包 Windows exe：

```bash
uv sync --extra dev
uv run python build.py
```

輸出檔案：

```text
dist/HelloStreamer.exe
```

推送 `v*` tag 時，release workflow 會自動建置 exe、上傳 artifact，並建立 GitHub Release。

## Project Structure

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

## Limitations

- Twitch 與 YouTube 的狀態偵測目前依賴公開網頁資料與非官方端點，平台頁面或反爬策略改動時可能失效。
- 目前主要支援 Windows；其他作業系統未列為正式支援目標。
- 本專案目前未提供自動更新機制。

## Privacy

Hello Streamer 不會將設定同步到外部伺服器。頻道清單與使用者設定儲存在本機 `config.json`。應用程式會向 Twitch / YouTube 發送請求，以檢查指定頻道的開播狀態。

## License

本專案採用 MIT License。完整條款請見 [`LICENSE`](LICENSE)。
