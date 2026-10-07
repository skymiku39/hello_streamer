# Watch Streak 跨場次診斷觀察（opt-in）

診斷專用、預設關閉。不改動開窗／關窗／點擊／recovery／refresh／輪詢決策，也不寫入 `config.json`、`seen_videos.db`、`browser_profile/`。

## 啟用方式

來源碼執行（`run_hello_streamer.bat` 已轉發 `%*`）：

```bat
run_hello_streamer.bat --watch-observation
```

或：

```bat
python -m stream_monitor --watch-observation
```

未帶旗標時行為與既有快樂路徑相同。

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
- `page.snapshot` — page-assist/CDP 連線期間約每 30 秒快照（僅在 `--watch-observation` 時縮短 assist 等待以醒來取樣；不改 claim／refresh 期限，旗標關閉時等待不變）：`has_video`、`ready_state`、`paused`、`media_current_time`／`media_time_delta`、content-gate presence／attempt／result、`document_visibility`、`cdp`。若 app 管理的視窗未建立 CDP 連線，開窗時會記一筆 `cdp=unavailable` 的未知樣本，不代表播放器正常或已觀看。

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
