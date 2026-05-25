# Hello Streamer v0.8.0

## 重點更新

- 新增「登入此 Profile」工具，可用獨立 Profile 開啟一般瀏覽器視窗，讓使用者先登入 Twitch / YouTube，之後自動開播視窗可沿用該 Profile 的 cookies。
- 未設定獨立 Profile 時，程式會提示風險，並在 Windows 上停用不可靠的視窗追蹤、座標補套用與標題關閉 fallback，避免誤關使用者原本的瀏覽器視窗。
- 關閉「使用獨立 Profile」時會明確保存為共用瀏覽器模式，不會被設定遷移自動補回 `browser_profile/`。
- `close_on_offline`、`close_on_stop`、`close_off_topic_pages` 與視窗幾何設定現在會依 Profile 隔離狀態採取更保守的安全行為。
- 新增完整瀏覽器設定組合矩陣測試，涵蓋 Profile 隔離、App Mode、獨立視窗、共用分頁、幾何設定與三種關閉行為的交互情境。
- 多語系文案補齊登入 Profile、未隔離警告與登入失敗提示。
- README 已更新 Profile 登入工具、未隔離安全降級與下載檔名說明。

## 下載檔案

- Windows 請下載 `HelloStreamer-v0.8.0-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.8.0-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.8.0-linux-arm64.tar.gz`
