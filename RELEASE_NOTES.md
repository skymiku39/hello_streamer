# Hello Streamer v0.3.7

## 修復重點

- **修復 Linux/Raspberry Pi 上「新增頻道」對話框空白的問題**
  - CustomTkinter 的 `CTkToplevel` 在 Linux window manager（LXDE、Wayfire 等）上如果過早呼叫 `grab_set()`，視窗內容不會渲染
  - 在呼叫 `grab_set()` 前先 `update()` 確保視窗已完成映射
  - Linux 字型改用 `sans-serif` 通用名稱，避免因找不到特定中文字型而影響渲染

## 下載建議

- Windows 請下載 `HelloStreamer-v0.3.7-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.3.7-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.3.7-linux-arm64.tar.gz`
