# Hello Streamer v0.9.8

## 平台分離處理

YouTube 與 Twitch 的監聽機制本質不同，離線狀態與連結補齊各自獨立：

| | YouTube | Twitch |
|---|---------|--------|
| 偵測 | TIDUS（`/streams` + `videoId/style`） | GQL 布林 live + ARCHIVE VOD |
| 待機室 | 有（`UPCOMING` 狀態與待機連結） | **無**（無獨立待機頁） |
| 離線 🔗 | 7 日內待機室 > 錄播 > 頻道首頁 | 錄播 > 頻道首頁 |

## Twitch 離線顯示修正

- 從未在本輪監聽中確認開播的 Twitch 頻道，不再誤顯示 `<1m` 的 OFFLINE
- 須先 witness 過 LIVE，下播後才顯示 OFFLINE 與「已下播多久」

## YouTube 連結修正

- 直播與 fallback 觸發統一使用 `watch?v=` URL
- `/streams` 抓不到資料時，fallback 可感知待機（`UPCOMING`）狀態
- 離線錄播優先選有結束時間的直播錄影
- 待機連結在排程開播後 30 分鐘自動清除
- **排程超過 7 天的待機室會被忽略**，離線 🔗 改走錄播方案

## UI

- 離線且有 YouTube 待機連結時，狀態列可點擊開啟待機室
- 左側平台徽章一律開頻道首頁，右側 🔗 依平台優先順序開啟

## 下載檔案

- Windows 請下載 `HelloStreamer-v0.9.8-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.9.8-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.9.8-linux-arm64.tar.gz`

## 升級提醒

- 從 v0.9.6 / v0.9.7 升級可直接覆蓋執行檔；`config.json` 無需變更。
