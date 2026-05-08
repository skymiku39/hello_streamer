# Hello Streamer v0.3.8

## 修復重點

- **修復 YouTube 偵測在 Linux/Raspberry Pi 上失敗的問題**
  - YouTube 在沒有 cookie 的全新 session 中會回傳隱私同意頁面而非頻道實際內容
  - 預設設置 `CONSENT` 和 `SOCS` cookie 繞過同意頁，讓 fetcher 能正確解析 `ytInitialData`
  - 此修正同時適用於 Windows 和 Linux，提升穩定性

- **修復「新增頻道」對話框空白**（延續 v0.3.7）
  - `CTkToplevel` 在 Linux 上呼叫 `update()` 後再 `grab_set()`
  - 字型改用通用 `sans-serif`

## 下載建議

- Windows 請下載 `HelloStreamer-v0.3.8-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.3.8-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.3.8-linux-arm64.tar.gz`
