# Hello Streamer v0.4.0

## 重點更新

- 全新瀏覽器設定流程：支援瀏覽器路徑解析、Chrome / Edge / Firefox 相容性提示、獨立 Profile、每頻道 Profile、幾何設定開關與測試頁。
- App Mode 行為更直覺：勾選 App Mode 時會自動啟用並鎖定獨立視窗，避免 UI 狀態和實際瀏覽器行為不一致。
- 測試開啟改用本機 HTML 測試頁，App Mode 測試會使用專用暫時 Profile，避免 `about:blank` 或既有瀏覽器程序干擾判斷。
- Windows 上加強瀏覽器視窗控制：在新視窗出現後用 Win32 API 套用位置、大小與最小化狀態，減少 Chrome / Edge 忽略啟動參數的問題。
- 關閉瀏覽器設定視窗時，若有未儲存變更會詢問是否存檔；按取消則維持直接放棄變更。

## 下載檔案

- Windows 請下載 `HelloStreamer-v0.4.0-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.4.0-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.4.0-linux-arm64.tar.gz`
