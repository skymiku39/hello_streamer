# Hello Streamer v0.3.0

## 發布重點

- 新增 Linux / Raspberry Pi 桌面環境支援，包含 `notify-send` 桌面通知與 XDG Autostart 開機自啟。
- 發布流程改為同時建置 Windows 與 Linux 版本，GitHub Release 會提供 Windows exe 與 Linux tar.gz 下載。
- 打包腳本支援依平台帶入不同的 PyInstaller hidden import，讓 Windows 與 Linux 都能正確載入系統匣後端。

## 改善

- README 更新為跨平台使用說明，補充 Linux 系統套件、Raspberry Pi 注意事項與打包輸出。
- CI 已涵蓋 Windows 與 Ubuntu，並在 Linux 環境安裝 Tk、AppIndicator 等桌面整合依賴。
