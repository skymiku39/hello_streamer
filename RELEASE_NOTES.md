# Hello Streamer v0.3.3

## 修復重點

- 修正 Linux release workflow 在最後輸出 glibc 版本時因 pipefail / SIGPIPE 導致 job 失敗的問題。
- 延續 v0.3.2 的 Raspberry Pi 修正：Linux x64 / ARM64 仍以 Debian Bookworm 為基準建置，降低 Raspberry Pi OS Bookworm 相容性問題。
- Linux 建置環境補齊 `libpython3.11`、PyGObject 與 GTK bindings，讓 PyInstaller 與系統匣後端能取得必要 runtime。

## 下載建議

- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.3.3-linux-arm64.tar.gz`。
- 32-bit Raspberry Pi OS 不能執行 ARM64 發布檔，請改用 64-bit Raspberry Pi OS，或從原始碼執行。
