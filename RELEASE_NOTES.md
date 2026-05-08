# Hello Streamer v0.3.6

## 修復重點

- **修復 Linux 建置失敗問題**
  - 補回遺漏的 `libpython3.11`（PyInstaller 打包所需的 Python 共享庫）
  - 補回 `gcc` + `libc-dev`（ARM64 需從原始碼編譯部分 Python 套件）

- **修復 Linux/Raspberry Pi 無法新增頻道與開啟連結**（v0.3.5 修正，延續至本版）
  - PyInstaller `--onefile` 修改 `LD_LIBRARY_PATH` 導致 DNS 解析失敗與瀏覽器無法啟動
  - 在 app 啟動時恢復原始 `LD_LIBRARY_PATH`

## 下載建議

- Windows 請下載 `HelloStreamer-v0.3.6-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.3.6-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.3.6-linux-arm64.tar.gz`
- 32-bit Raspberry Pi OS 不支援 ARM64 發布檔，請改用 64-bit OS 或從原始碼執行
