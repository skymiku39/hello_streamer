# Hello Streamer v0.4.1

## 重點更新

- 新增「直播結束時關閉瀏覽器視窗」流程，監控器會偵測 live -> offline 的狀態轉換。
- 通知器會記錄由 Hello Streamer 開啟的瀏覽器視窗 HWND，讓程式能針對對應直播 URL 關閉視窗。
- 瀏覽器設定對話框新增離線關閉相關選項，並透過事件佇列安全地在 UI 執行緒處理關閉動作。
- 離線事件會保留最後已知的直播 URL / 標題，避免關閉時找不到對應視窗。
- 可選擇讓彈出的瀏覽器視窗從 Windows 工作列與 Alt+Tab 隱藏，適合拿來當背景播放器視窗。

## 下載檔案

- Windows 請下載 `HelloStreamer-v0.4.1-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.4.1-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.4.1-linux-arm64.tar.gz`
