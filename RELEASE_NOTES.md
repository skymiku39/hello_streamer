# Hello Streamer v0.3.5

## 修復重點

- **修復 Linux/Raspberry Pi 無法新增頻道與開啟連結的問題**
  - PyInstaller `--onefile` 會修改 `LD_LIBRARY_PATH` 指向暫存目錄，導致 glibc DNS 解析失敗（無法連線驗證頻道）以及子程序無法啟動（瀏覽器開不了）
  - 在 app 啟動時恢復原始 `LD_LIBRARY_PATH`，同時修復網路連線與外部程式呼叫

- **修復 v0.3.4 ARM64 版本閃退問題**
  - ARM64 建置恢復使用 `debian:bookworm` + `--system-site-packages`，確保 PyGObject（pystray 系統匣所需）正確打包
  - v0.3.4 錯誤地改用隔離 venv 導致 `gi` 模組缺失

- Release job 改為 `if: always()`：即使 ARM64 建置失敗，Windows 與 Linux x64 的 release 仍會正常發佈

## 下載建議

- Windows 請下載 `HelloStreamer-v0.3.5-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.3.5-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.3.5-linux-arm64.tar.gz`
- 32-bit Raspberry Pi OS 不支援 ARM64 發布檔，請改用 64-bit OS 或從原始碼執行
