# Hello Streamer v0.9.6

## 混合下播時間

離線列左側「已下播多久」採**混合模式**：

1. 程式確認下播的時間（監聽確認）
2. 若查得到上一場錄播／影片的結束時間，且在合理範圍內，則改用**平台時間**（通常更早、更接近實際下播）

- **Twitch**：最新 ARCHIVE VOD 的 `createdAt + lengthSeconds`
- **YouTube**：上一場 `DEFAULT` 影片的 `endTimestamp`，或 `uploadDate + lengthSeconds`

下播後 VOD 尚未生成時，會先顯示確認時間；穩定離線輪詢會重試補上錄播連結與時間。滑鼠提示會標示「依錄播時間」或「依監聽確認」。

## 離線連結分離

| 位置 | 行為 |
|------|------|
| 左側平台徽章（TWITCH / YOUTUBE） | 一律開啟**頻道首頁** |
| 右側 🔗 | 有上一場 VOD 時開**錄播**；否則開頻道首頁 |

## 下載檔案

- Windows 請下載 `HelloStreamer-v0.9.6-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.9.6-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.9.6-linux-arm64.tar.gz`

## 升級提醒

- 從 v0.9.5 升級可直接覆蓋執行檔；`config.json` 無需變更。
