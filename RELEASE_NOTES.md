# Hello Streamer v0.9.17

## YouTube 監測與輪詢

- **LIVE 真實開播時間**：Tier-2 會從 watch 頁讀取 `startTimestamp`；首次偵測的暫時時間不會阻擋後續補正
- **穩定輪詢精簡**：已確認 LIVE／離線狀態的頻道，後續輪詢省略 VOD 重查、upcoming／LIVE watch enrich 等重操作
- **Tier-1 先 YouTube**：兩梯次輪詢改為先完成所有 YouTube probe，再跑 Twitch，減少 worker 搶占
- **429 限速**：YouTube 全局限速與 cooldown，降低大量頻道時的 rate limit
- **watch?v= 新增頻道**：貼上影片網址可透過 oEmbed／watch 頁解析頻道名稱

## 狀態顯示修正

- **離線時間**：修正 VOD 下播時間被輪詢當下時間覆蓋、冷啟動誤顯「已下播」等問題
- **深度檢查文案**：Tier-2 完成後清除 `pending`；穩定離線列不再與「待深度檢查」來回跳動
- **只監測 UI**：狀態列副行顯示目前檢查中的頻道；頻道列即時 partial 更新不再死鎖

## 下載檔案

- Windows 請下載 `HelloStreamer-v0.9.17-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.9.17-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.9.17-linux-arm64.tar.gz`

## 升級提醒

- 從 v0.9.16 升級可直接覆蓋執行檔；`config.json` 無需變更。
