# Hello Streamer v0.9.4

## 離線列顯示優化

### 雙層離線狀態（對齊 LIVE 列）
頻道離線時，左側時間欄顯示**已下播多久**（與 LIVE 的「已開播多久」相同格式），右側狀態欄仍顯示 `OFFLINE`。滑鼠提示可顯示上次直播標題與下播經過時間。

### 錄播連結按鈕
右側 🔗 按鈕在有可用的錄播 URL 時會開啟錄播頁面：

- **YouTube**：使用上一場直播的 `watch?v=` 連結（下播後即為重播）
- **Twitch**：下播確認後查詢最新 ARCHIVE VOD；若查詢失敗則仍開啟頻道首頁

## 下載檔案

- Windows 請下載 `HelloStreamer-v0.9.4-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.9.4-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.9.4-linux-arm64.tar.gz`

## 升級提醒

- 從 v0.9.3 升級可直接覆蓋執行檔；`config.json` 與 Profile 資料夾無需變更。
- 下播時間以程式**確認下播當下**的時間為準（與 LIVE 的開播時間邏輯一致），非平台提供的精確結束時間。
