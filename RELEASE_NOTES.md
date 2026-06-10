# Hello Streamer v0.9.15

## YouTube 狀態顯示修正

- **待機室不再誤顯 LIVE**：開播邊緣回呼正確區分 upcoming / live / offline；若同一輪已有 Tier-2 快照，不再用粗糙的 `StreamInfo` 覆寫待機室或離線列（修復 v0.9.14 待機室被標成 LIVE）
- **回放不再誤判直播**：YouTube lockup 的 LIVE 僅依縮圖 badge 判斷；metadata 中的「直播時間：N 週前」等回放描述不再觸發 LIVE

## 喚醒驗證

- **TIDUS 待機室 bucket 對齊**：offline row 上的 `upcoming_url` 在喚醒驗證時歸類為 upcoming，與 probe 一致，避免穩定待機室狀態被不必要地 deferred

## 下載檔案

- Windows 請下載 `HelloStreamer-v0.9.15-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.9.15-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.9.15-linux-arm64.tar.gz`

## 升級提醒

- 從 v0.9.14 升級可直接覆蓋執行檔；`config.json` 無需變更。

---

# Hello Streamer v0.9.14

## 多頻道觸發與狀態顯示

- **批次派發開播事件**：同一輪輪詢中，所有頻道的開播事件會在 Tier-2 狀態提交後一次送出，避免「開啟並保持監聽」只開第一個頻道就停止，或「開啟並停止」在其他人尚未開啟前就結束監聽
- **即時更新列表狀態**：不論觸發模式或只監測模式，偵測到開播時立即更新頻道列 LIVE 標籤與標題，不必等到整輪輪詢完成

## 下載檔案

- Windows 請下載 `HelloStreamer-v0.9.14-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.9.14-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.9.14-linux-arm64.tar.gz`

## 升級提醒

- 從 v0.9.13 升級可直接覆蓋執行檔；`config.json` 無需變更。

---

# Hello Streamer v0.9.13

## 長期運行維護

- **YouTube watch 頁快取**：上限 256 筆，過期（5 分鐘）與超量時自動修剪，避免長期監聽記憶體緩慢累積
- **定期 housekeeping**：啟動、執行緒重啟與每 24 小時輪詢後，清理 `seen_videos.db` 超過 30 天的紀錄並修剪 YouTube 快取

## 瀏覽器自動關閉安全

- **隔離檢查**：未啟用獨立 Profile 時，跳過離題頁 prune，且下播關窗不使用標題關鍵字 fallback（只關 Hello Streamer 追蹤到的 HWND）
- **自訂瀏覽器啟動失敗**：若已勾選自動關閉相關選項，改走系統瀏覽器時會封鎖 title fallback，降低誤關其他分頁的風險
- **分頁模式誠實提示**：未勾選「獨立視窗」且未啟用 App Mode 時，程式**無法**登記 HWND／追蹤記憶體／自動關閉；設定介面會停用相關選項並顯示說明
- **YouTube lockup 多語言**：改善英文 LIVE、日文配信予定等 badge 的直播／待機室判斷

## 下載檔案

- Windows 請下載 `HelloStreamer-v0.9.13-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.9.13-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.9.13-linux-arm64.tar.gz`

## 升級提醒

- 從 v0.9.12 升級可直接覆蓋執行檔；`config.json` 無需變更。
- 若使用「直播結束自動關閉」或「只保留直播相關視窗」，建議確認已啟用獨立 Profile（儲存瀏覽器設定時會自動修復）。

---

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
