# Hello Streamer v0.6.2

## 重點更新

- 修正舊設定可能出現「每頻道 Profile 已啟用，但 Profile 根目錄為空」的狀態。
- `config.json` 載入時會自動遷移缺漏欄位與舊版瀏覽器設定，並在可寫入時回存乾淨版本。
- 當每頻道 Profile 啟用但 `user_data_dir` 為空時，會自動使用程式目錄下的 `browser_profile/` 作為預設根目錄。
- 降低 Chrome / Edge master process 共用造成 App Mode、視窗座標、視窗追蹤與「只保留直播相關視窗」誤判的機率。
- 測試補齊設定遷移、瀏覽器 Profile fallback、舊設定自我修復與瀏覽器參數解析情境。
- README 已更新 Profile 自動修復與下載檔名說明。

## 下載檔案

- Windows 請下載 `HelloStreamer-v0.6.2-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.6.2-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.6.2-linux-arm64.tar.gz`
