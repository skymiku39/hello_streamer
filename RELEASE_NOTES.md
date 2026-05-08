# Hello Streamer v0.3.4

## 修復重點

- 重寫 ARM64 release 建置流程：改用 `python:3.11-bookworm` 完整映像（內建 tkinter），取代手動安裝系統 Python 的方式，解決 PyInstaller 找不到 tkinter 模組的問題。
- Release job 改為 `if: always()`：即使 ARM64 建置失敗，Windows 與 Linux x64 的 release 仍會正常發佈，不再整體阻塞。
- Linux x64 改用原生 runner（不走 Docker），大幅加速建置速度。
- ARM64 建置加入 60 分鐘 timeout 與 `file` 指令驗證輸出架構。

## 下載建議

- Windows 請下載 `HelloStreamer-v0.3.4-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.3.4-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.3.4-linux-arm64.tar.gz`
- 32-bit Raspberry Pi OS 不支援 ARM64 發布檔，請改用 64-bit OS 或從原始碼執行
