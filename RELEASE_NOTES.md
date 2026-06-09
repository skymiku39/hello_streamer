# Hello Streamer v0.9.12

## 休眠／喚醒穩定性

- **喚醒驗證輪詢**：偵測長時間暫停（如睡眠）後，先多問一次 API；結果與現狀相同才刷新 UI，不同或無回應則保留狀態等下一輪正式輪詢
- **監控執行緒存活**：poll 例外不再終止背景 thread；dead thread 每 10 秒或即時自動重啟，且**保留** `_last_status`、strikes 等記憶體狀態
- **fetch 失敗統一**：`fetch exception` 與 `None` 同樣計入離線防抖，不再永久凍結在 LIVE
- **RequestException 重試**：Twitch / YouTube HTTP 短暫失敗會重試（與 Timeout 對齊）
- **TIDUS 失敗分流**：HTTP 失敗回 `None`（計 strike），真空白 feed 才走 fallback
- **close_on_offline 保護**：喚醒驗證輪期間不關閉播放器

## 下載檔案

- Windows 請下載 `HelloStreamer-v0.9.12-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.9.12-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.9.12-linux-arm64.tar.gz`

## 升級提醒

- 從 v0.9.11 升級可直接覆蓋執行檔；`config.json` 無需變更。
- 喚醒後可在 log 搜尋 `wake_verify_confirmed` / `wake_verify_deferred` 觀察驗證行為。
- 監聽頻道較多時，建議檢查間隔維持 ≥30 秒，以降低 Twitch rate limit 風險。
