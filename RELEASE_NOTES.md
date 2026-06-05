# Hello Streamer v0.9.5

## 修復離線／直播列顯示

### Twitch 頻道 key 正規化
登入名統一為小寫（例如 `RunRunLuna` → `runrunluna`），避免監控 snapshot 與 UI 列 key 不一致而顯示錯誤狀態。

### 下播時間錨定
- 冷啟動即離線的頻道會在**第一次**確認離線時寫入 `ended_at`，後續 poll 保留同一時間戳，不再每輪重設導致永遠顯示 `0m`。
- LIVE → OFFLINE 過渡期間，在 anti-flap 確認下播前不再寫入空的離線 snapshot。

### 時間欄顯示
- 未滿 1 分鐘顯示 `<1m`（多語系），不再長時間卡在 `0m`。
- UI 每 30 秒重算「已開播／已下播」時間，無需等下一輪 API poll。

### 漏檢與診斷
- Twitch 回報離線時會**重試一次** GQL，降低短暫 `stream: null` 假陰性。
- 狀態長期不變時每 20 輪 poll 記一筆 `stable poll` log，便於確認有在輪詢。

## 下載檔案

- Windows 請下載 `HelloStreamer-v0.9.5-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.9.5-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.9.5-linux-arm64.tar.gz`

## 升級提醒

- 從 v0.9.4 升級可直接覆蓋執行檔；`config.json` 與 Profile 資料夾無需變更。
- 下播時間仍以程式**確認下播當下**為準，非平台提供的精確結束時間。
