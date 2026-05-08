# Hello Streamer v0.3.2

## 修復重點

- 修正 Raspberry Pi 64-bit 發布檔相容性：Linux x64 / ARM64 現在改在 Debian Bookworm 容器中建置，避免 Ubuntu 24.04 glibc 過新導致 Raspberry Pi OS Bookworm 無法執行。
- Linux ARM64 發布檔仍維持 `HelloStreamer-vX.Y.Z-linux-arm64.tar.gz`，適用於 64-bit Raspberry Pi OS。
- Linux 建置環境補齊 `libpython3.11` 與 PyGObject / GTK bindings，避免 PyInstaller 與系統匣後端缺少必要 runtime。

## 改善

- Linux release workflow 會輸出 `file` 與 `ldd --version` 資訊，方便確認實際產物架構與 glibc 基準。
- README 補上 Raspberry Pi 常見失敗原因：32-bit OS 不能執行 ARM64 binary，請改用 64-bit OS 或從原始碼執行。
