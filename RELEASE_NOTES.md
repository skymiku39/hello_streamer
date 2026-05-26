# Hello Streamer v0.9.0

## 系統性 UX 改版

這版針對「進階選項看起來能勾、實際上不會生效」的根本問題做了一輪系統性整修，讓 UI、設定檔遷移與 runtime 三邊一致。

### 「儲存即生效」自動修正（Config Migration #2）

從 v0.8.0 加入了一條 Migration #1（修補 orphan per-channel profile）；v0.9.0 進一步加入 **Migration #2**：

- 觸發條件：`enabled=True` 且 `user_data_dir=""` 且至少勾選一個 isolation-dependent 進階選項（純淨視窗 / 視窗最小化 / 不顯示在工作列 / 自動關閉視窗 [離線、停止、離題]）。
- 行為：儲存時自動把 `user_data_dir` 填回 `<安裝目錄>/browser_profile`，並把 `per_channel_profile` 設為 `True`。
- 結果：你之前的歷史 per-channel profile 資料夾會被沿用、cookies/登入不會掉、`hide_from_taskbar` / `apply_geometry` / `close_on_*` 全部會真的執行。
- 既有共用模式偏好者不受影響：只要沒有勾任何 opt-in 進階選項，`user_data_dir=""` + `per_channel_profile=False` 會被保留。

### App Mode 一致化

之前 App Mode 在程式碼三個地方說的話不一致（hint 說「可能需要」、UI 不灰、runtime 卻會降級）。這版統一：
- `app_mode` 加入 `_ISOLATION_DEPENDENT_FLAGS`，被勾選時會觸發 Migration #2。
- UI：未啟用 Profile 時 App Mode 的 checkbox 會跟其他進階選項一起灰掉。
- Hint 文案改為明確「需要搭配獨立 Profile 才會穩定生效」。

### 對話框 UX 修正

**Browser Settings**：
- **User-tab 上方新增動態 banner**：當你勾選了會觸發 Migration #2 的進階選項但 Profile 還沒打開時出現。點 banner 直接跳到「進階」分頁。
- **「登入此 Profile」按鈕從「測試工具」搬到「獨立 Profile」卡片**：它本來就是 Profile 的前置步驟、不是測試工具。
- **儲存時預先套用 Migration #2**：如果按儲存會觸發自動修正，會先在對話框內顯示「儲存時自動啟用了獨立 Profile（…）」訊息，等你看一眼確認再按一次儲存才寫入。round-trip 從此不再「我沒改卻被改」。
- **「測試開啟 / 測試關閉 / 登入此 Profile」三個按鈕補上 tooltip**：明確說明各自做什麼。
- **Cancel 與右上 [X] 行為一致**：兩個都會在有未存變更時跳出儲存提示，不再有「按 Cancel 比按 X 風險高」的怪現象。
- **未隔離警告文案重寫**（5 語）：說明 Migration #2 會自動接手，使用者可以保存後直接生效，不需要再手動勾選一遍。
- **Advanced 分頁標題**（5 語）：從「開發者與純淨視窗」改成「進階：純淨視窗與獨立 Profile」，反映這個分頁實際上是進階功能的前置。
- **獨立 Profile 描述**（5 語）：明確列出每個依賴它的進階功能。

**Add Channel**：
- URL 驗證等待期間「取消」按鈕保持可用——之前 fetcher 無 timeout 時，使用者會被卡住完全關不掉對話框。

**Channel 刪除**：
- 加上 yes/no 確認對話框——之前誤點 ✕ 會直接消失，狀態 / 監控設定一併丟失。

**主視窗**：
- 開機啟動切換失敗時跳 messagebox 提示「無法寫入啟動設定，請確認權限」——之前只會默默把 switch 彈回原位。
- 主畫面「⚙ 瀏覽器設定」按鈕 tooltip 更新（5 語），列出完整功能面向（自動關閉 / 隱藏工作列 / 獨立 Profile / 一次性登入工具）。

### 測試與品質

- 新增 6 個 `app_mode` 進入 `_ISOLATION_DEPENDENT_FLAGS` 的 parametrize case，總測試從 483 → 484+。
- `ruff` / `pytest` 全綠。

## 下載檔案

- Windows 請下載 `HelloStreamer-v0.9.0-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.9.0-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.9.0-linux-arm64.tar.gz`

## 升級提醒

- 第一次啟動會自動把舊版的 `browser_settings` 補齊新欄位（Migration #1 + #2），並把修正寫回 `config.json`。如果 log 出現 `config.json self-healed` 是正常的，不會影響你的頻道列表 / 動作 / 監測模式等任何其他設定。
- 若你之前是 `user_data_dir=""` + `per_channel_profile=False` 但勾了像 `hide_from_taskbar` 等進階功能，v0.9.0 啟動後會自動切到 `<安裝目錄>/browser_profile` 並打開 per-channel。原本各台主播 profile 的 cookies 會自動沿用。
- 若你真心要維持共用瀏覽器，請把所有 isolation-dependent 進階選項取消勾選，再把「使用獨立 Profile」也取消，這樣 Migration #2 就不會觸發。
