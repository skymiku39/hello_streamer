# Hello Streamer v1.2.0

## 頻道拖曳排序（體驗完善）

- **直覺拖曳**：長按把手後需實際移動或滾輪才會改變順序；拖回原位可取消，放開不寫入設定。
- **座標穩定**：改用 canvas 內容座標對齊，邊拖曳邊捲動時插入點不再偏移。
- **滾輪調序**：拖曳中滾輪調整插入位置，不再誤捲動外層列表。
- **預覽流暢**：`after(0)` 即時重排與幾何鎖定，減少預覽抖動。
- **操作保護**：支援 Esc 取消；拖曳中刪除頻道會先安全結束排序；修正把手外放開時狀態殘留。

## 下載檔案

- Windows 請下載 `HelloStreamer-v1.2.0-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v1.2.0-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v1.2.0-linux-arm64.tar.gz`

## 升級提醒

- 從 v1.1.0 或 v1.0.x 升級可直接覆蓋執行檔；`config.json` 無需變更。

---

# Hello Streamer v1.1.0

## 頻道拖曳排序

- **Trello 式拖曳**：頻道列 ▲▼ 之間新增把手，長按後可上下拖曳調整順序。
- **推擠預覽**：拖曳時鄰近列即時讓位，一格一位移；放開後寫入 `config.json` 並同步監控順序。
- **流暢重排**：相鄰一格用增量 `pack`、跨格用局部或全量重排，並以 `after_idle` 合批，減少閃爍。

## 下載檔案

- Windows 請下載 `HelloStreamer-v1.1.0-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v1.1.0-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v1.1.0-linux-arm64.tar.gz`

## 升級提醒

- 從 v1.0.x 升級可直接覆蓋執行檔；`config.json` 無需變更。

---

# Hello Streamer v1.0.1

## 監控操作修正

- **停止按鈕即時回應**：停止監聽時先更新介面，再在背景結束輪詢執行緒，避免長時間輪詢或開啟瀏覽器時按停止沒反應。
- **開播即開窗**：Tier-1 確認 LIVE 後立即觸發通知與開啟直播頁，不再等整輪 Tier-2 細節更新完成才統一開窗。
- **開窗不阻塞 UI**：通知與瀏覽器啟動改在背景執行，主視窗在觸發開播動作時保持可操作。

## 下載檔案

- Windows 請下載 `HelloStreamer-v1.0.1-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v1.0.1-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v1.0.1-linux-arm64.tar.gz`

## 升級提醒

- 從 v1.0.0 或更早版本升級可直接覆蓋執行檔；`config.json` 無需變更。
