# Watch Streak 跨場次診斷觀察

診斷紀錄不改動開窗／關窗／點擊／recovery／refresh／輪詢決策，也不寫入 `config.json`、`seen_videos.db`、`browser_profile/`。當儲存的觀看輔助與頁內輔助皆啟用時，程式啟動會自動啟用診斷；其他情況仍預設關閉。

## 啟用方式

來源碼執行（`run_hello_streamer.bat` 已轉發 `%*`）：

```bat
run_hello_streamer.bat --watch-observation
```

或：

```bat
python -m stream_monitor --watch-observation
```

診斷在單一執行個體鎖取得之後才開始，第二個程式不會留下孤立的啟動事件。

## 產物

| 路徑 | 說明 |
| --- | --- |
| `logs/watch_streak_observation.ndjson` | 專用 NDJSON（與 `logs/stream_monitor.log` 分離） |
| `logs/stream_monitor.log` | 既有應用 log，不受影響 |

每筆事件含 `schema_version`、`type`、`ts_utc`（UTC）、`app_run_id`（單次行程隨機 ID）。跨重啟以 `(channel_key, stream_started_at)` 關聯；page snapshot／window open／close 若未帶 `started_at`，會重用該頻道最近一次已知的 live `StreamIdentity`；僅在尚無 live identity 時才用本機 first-seen 並標 `identity.confidence=unknown`。

### 事件類型（最小集合）

- `app.lifecycle` — 行程 open／close
- `stream.edge` — Twitch live／offline 邊界
- `window.action` — 視窗 open／close，`origin` 為 `app_requested`｜`observed`｜`unknown`
- `page.snapshot` — 診斷啟用期間約每 30 秒快照（不改 claim／refresh 期限）：`has_video`、`ready_state`、`paused`、`media_current_time`／`media_time_delta`、content-gate presence／attempt／result、`document_visibility`、`cdp`。未建立 CDP 連線時記為 unavailable；轉台或頁面歸屬改變時記為 unknown，不把目的頻道的影片或點數歸屬原頻道。
- `watch.progress` — 每頁約每 30 秒記錄本機播放驗證狀態、完整第二餘額（讀不到為 null）、點數停滯計時、重試次數及同時受管頁數；`server_watch_credit` 一律為 `unknown`。點數增加也不能證明某場連續觀看已認列。

CDP 失敗時只記錄 `cdp=unavailable|unknown` 與不可用欄位，**絕不**暗示 `paused` 或 offline。

### 禁止寫入的欄位

不記錄 title、完整 URL、聊天內容、cookies、tokens、profile 路徑。

## 保留政策（30 天）

- 使用**單一** observation 檔，跨重啟追加以保留同一研究紀錄。
- 僅在**啟用新的 observation 行程時**檢查保留：若檔案最後活動時間距今超過 30 天，才刪除該 observation 檔。
- 進行中、近期仍有寫入的研究**不會**被修剪。
- 不刪除 `stream_monitor.log`、設定、資料庫或 browser profile。

可測試入口：`stream_monitor.watch_observation.should_expire_observation_file`／`apply_observation_retention`。

## 外部對照表（人工）

空白範本：[`watch-streak-external-checklist.csv`](./watch-streak-external-checklist.csv)（一列一場直播）。以 `unknown` 表示未知值；勿填入私密頻道名稱後再公開分享。若出現恢復通知，另填通知時間、期限、官方標示的合格內容類型，以及恢復前後可見 streak 結果。

### Twitch 官方 Watch Streak 檢查清單（摘要）

依據 [Twitch Help：Watch Streaks](https://help.twitch.tv/s/article/recover-watch-streaks)／Channel Points 說明：

1. 必須已登入 Twitch 帳號
2. 該頻道必須啟用 Channel Points
3. 每一場直播長度至少 10 分鐘
4. 上一場結束與下一場開始之間至少間隔 30 分鐘
5. 需實際觀看每一場連續直播以維持 streak
6. 官方目前說明：原 streak 至少 3 場才符合 Watch Streak Recovery 資格；若提供恢復通知，依 Twitch 當下提示列出的合格內容與期限處理。官方說明的恢復期限為第一場漏看的直播結束後 24 小時，並列出符合標記的 Stories／Clips（各至少 5 秒）、VOD（至少 5 分鐘）或期限內同頻道新直播（至少 5 分鐘）。逐場表應記錄通知時間、期限、採用內容與恢復前後結果。

本工具的 NDJSON **不能**證明 Twitch 伺服器是否認列觀看；僅提供本機開／關窗、live 邊界與 page-assist 可見狀態證據。

## 唯讀現場重跑

在原始碼環境執行以下指令，指定實際 EXE 使用的專用 profile。工具會先查核端點歸屬，再對當時現有的 Twitch 頻道頁取樣；不開新直播、不刷新、不點擊獎勵，僅在完整點數需懸停提示時移動頁內游標。

```powershell
uv run python scripts/observe_managed_playback.py --profile D:\Tools\HelloStreamer\browser_profile --output artifacts/watch-check.ndjson --samples 12 --interval 30
```

三個有效影片樣本才能建立本機播放證據。多頁會各自取樣，單頁失敗記為 unknown；該次只開一頁時，不能把結果宣稱為真實 Twitch 多開驗收。跨場次 streak 結果仍需對照上方逐場檢查表。

六頁隔離 Chrome 回歸測試可用 `uv run pytest tests/test_playback_health_browser.py` 重跑。五頁採用真實 MediaStream 時鐘，另一頁刻意凍結 `currentTime` 並保留未暫停狀態；測試只證明本機能分辨停滯，並不連到 Twitch。
