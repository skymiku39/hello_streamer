# Hello Streamer v0.9.10

## 兩梯次輪詢

每輪檢查拆成兩個梯次，開播觸發更快：

- **梯次 1**：確認是否直播，立即觸發通知／開瀏覽器等動作
- **梯次 2**：再更新離線狀態、錄播連結、待機室、UI 與 DB 寫入

可在 log 搜尋 `tier1=` 觀察觸發延遲與整輪耗時（`total=`）。

## YouTube 待機室與下播時間

- 待機室若 `/streams` 頁沒有排程時間，改在梯次 2 補抓 watch 頁取得開播倒數
- 下播時間不再設 48 小時上限；有錄播結束時間就採用，即使錄播是數天前

## Twitch 離線錄播（延續 v0.9.9）

- 冷啟動離線的 Twitch 頻道，若有 ARCHIVE 錄播，顯示 **OFFLINE** 與 🔗 錄播連結
- 從未開播且抓不到錄播時，維持 `--`

## 下載檔案

- Windows 請下載 `HelloStreamer-v0.9.10-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.9.10-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.9.10-linux-arm64.tar.gz`

## 升級提醒

- 從 v0.9.9 升級可直接覆蓋執行檔；`config.json` 無需變更。
- 監聽頻道較多時，建議檢查間隔維持 ≥30 秒，以降低 Twitch rate limit 風險。
