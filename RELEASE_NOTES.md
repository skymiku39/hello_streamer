# Hello Streamer v0.9.11

## YouTube 待機室誤判修正

- 新增 `youtube_upcoming_schedule_is_surfacable`：空排程、已過期或週表排程不再誤判為待機室
- lockup 頁移除 `SCHEDULED` 誤判；watch 頁 `offlineSlate` 改為條件化判斷

## 狀態機統一

- 常態待機室一律顯示 **OFFLINE** 列 + 開播倒數 + 🔗 待機直播間（不再顯示橘色 UPCOMING）
- 僅 fallback 偵測到 active premiere 時才顯示橘色 `UPCOMING`
- 梯次 1 的 UPCOMING 通知與 mark_seen 同樣須通過 surfacable 檢查

## Twitch 離線時序對稱

- API 回報 offline 與 fetch 回傳 None 的離線 commit 統一延到梯次 2 落地
- 梯次 1 不再提早清除 `_live_started_at`，避免短暫抖動誤判下播

## 下載檔案

- Windows 請下載 `HelloStreamer-v0.9.11-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.9.11-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.9.11-linux-arm64.tar.gz`

## 升級提醒

- 從 v0.9.10 升級可直接覆蓋執行檔；`config.json` 無需變更。
- 監聽頻道較多時，建議檢查間隔維持 ≥30 秒，以降低 Twitch rate limit 風險。
