# Hello Streamer v1.0.0

## 正式版

Hello Streamer 1.0.0 為首個正式版里程碑，涵蓋 Twitch / YouTube 監聽、通知、瀏覽器自動開啟與長期常駐所需的穩定性改進。

## 瀏覽器設定改版

- 單頁流程取代舊版「使用者 / 開發者」分頁，以「如何開啟直播頁」「使用哪個帳號」「視窗位置與大小」「自動整理」等區塊引導設定。
- 不可用選項會隱藏而非僅灰掉，減少誤解。
- 自動整理（下播關窗、停止時關閉等）僅在程式專用帳號搭配獨立視窗時可用。

## YouTube 監測與輪詢（延續 v0.9.17）

- LIVE 真實開播時間：Tier-2 從 watch 頁讀取 `startTimestamp`。
- 穩定輪詢精簡：已確認 LIVE／離線的頻道省略重操作。
- Tier-1 先 YouTube，再跑 Twitch。
- 429 限速與 cooldown。
- `watch?v=` 網址可解析頻道名稱。

## 長期運作

- Log 自動輪替（約 12 MB 上限）。
- 每 24 小時清理 `seen_videos.db` 舊紀錄與 YouTube watch 快取。
- 移除頻道時同步清理監控記憶體狀態。

## 下載檔案

- Windows 請下載 `HelloStreamer-v1.0.0-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v1.0.0-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v1.0.0-linux-arm64.tar.gz`

## 升級提醒

- 從 v0.9.x 升級可直接覆蓋執行檔；`config.json` 會自動遷移，無需手動修改。
