# Hello Streamer v0.2.6

## 修復重點

- 修正 README 中「開發模式會寫入開機自啟 registry」的過期說法；開機自啟維持只支援封裝版 `HelloStreamer.exe`。
- 補齊 `minimize_to_tray` 設定的預設值、載入驗證與保存流程，避免縮小至系統匣設定在 config 正規化時遺失。

## 改善

- README 全面更新為目前功能狀態，補充 YouTube UPCOMING、狀態 badge、右側連結按鈕、系統匣、開機自啟與專案結構說明。
- 補強 config manager 測試，涵蓋 `minimize_to_tray` 的非法值 fallback 與合法值保存。
