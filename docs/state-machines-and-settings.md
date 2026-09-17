# 狀態機、設定連動與各狀態 UI 呈現

- 版本：stream-monitor 1.2.0
- 範圍：主視窗（頻道清單／控制列）與瀏覽器設定對話框
- 目的：說明專案有哪些狀態機、設定之間的連動（前置／互斥／解鎖）關係，以及「每一種狀態在 UI 上如何被呈現」，並標註對應的驗證測試。

> 本文的每一條「狀態 → 呈現」規則都有單元測試佐證（見各節的「測試佐證」）。修改互鎖規則時，請同步更新對應測試，勿為了讓測試通過而放寬斷言。

---

## 1. 專案的狀態機總覽

專案的狀態被刻意分層，各層只負責單一職責，透過事件（Pub/Sub）與純決策函式連接，而非互相直接呼叫。

| 層級 | 狀態機 | 擁有者 | 值域 | 說明 |
|------|--------|--------|------|------|
| 控制 | 監看模式 | `MonitorController.mode` | `idle` / `trigger` / `watch` / `trigger_once` / `watch_once` | 全域運行狀態，決定控制列四種啟動按鈕與停止按鈕的可用性。 |
| 資料 | 頻道即時狀態 | `ChannelStatus`（`frozen`） | live / upcoming / offline / unknown | 由輪詢引擎產生，以 `state` 或 `is_live/is_upcoming/is_offline/is_unknown` 判讀，不再用魔術 `__eq__`。 |
| 資料 | 直播狀態預覽層級 | `monitor/preview.py`（tier-1 / tier-2） | — | 結構性不變式：tier-1 不得把已快取的 LIVE 降級為 offline（`_tier1_may_overwrite_cached`）。 |
| 動作 | 每頻道副作用決策 | `channel_policy.py`（純函式） | `LiveActionDecision` / `ActionPlan` | 集中「全域模式 × 頻道模式 × 觸發設定 × 直播狀態」的判斷，供 `event_bridge` 使用。 |
| UI | 每列啟用／頻道模式 | `ChannelRow`（`enabled` / `channel_mode`） | 啟用 / 暫停 / 監聽＋觸發 / 僅監聽 / 僅監聽含通知 | 卡片外觀、眼睛按鈕顏色與懸停提示；眼睛按鈕不負責一次性全域模式。 |
| UI | 拖曳手勢（列本地） | `ChannelRow._drag_phase` | `idle` / `pending` / `armed` | 長按 → 待命 → 啟動的輸入手勢，餵給 app 層的重排會話。 |
| UI | 重排會話（app 層） | `ChannelReorderMode` | idle → active → engaged → committed/cancelled | 卡片位移預覽與落點提交，與列本地手勢分離。 |
| 副作用 | 瀏覽器視窗登錄 | `browser_win32._WindowRegistry` | 每 URL 的視窗清單／關閉中／標題回退封鎖 | 單一擁有者，集中管理受監聽觸發開啟的視窗生命週期。 |

管理與關聯方式：資料流為 `Monitor.publish → MonitorEventBus → event_bridge.tick → App 副作用`。跨層一律以事件與純決策函式銜接；UI 只讀取決策結果並反映到畫面，不反向依賴引擎內部狀態。

### 1.1 開播觸發設定

`TriggerSettings` 將原本的複合 action 拆成獨立維度：

| 維度 | 設定 | 規則 |
|------|------|------|
| 通知 | `notify_on_live` | LIVE 開播時是否送出一般通知。 |
| 開啟 | `open_on_live` | LIVE 開播時是否自動開啟直播頁。 |
| 開啟後 | `after_open` | `none` / `stop_monitor` / `exit_app`；只有開啟成功時生效。 |
| 預定直播 | `notify_on_upcoming` / `open_on_upcoming` | 預定直播不繼承 LIVE 的停止或結束效果。 |
| 錯誤恢復 | `notify_on_open_failure` | 瀏覽器啟動失敗時是否提供可手動開啟的恢復通知。 |

舊版 `action` 欄位只在設定載入時作為相容輸入，主程式的決策來源是
`trigger_settings`。因此通知關閉不會意外關閉瀏覽器，開啟關閉也不會隱含停止
監聽；若開啟關閉，`after_open` 會被視為 `none`。

### 1.2 單頻道副作用模式

單頻道的 `channel_mode` 是三態設定，與全域 `MonitorController.mode` 分開。主清單的眼睛按鈕只改變目前頻道的副作用，不會改變全域輪詢次數：

| 值 | UI 名稱 | 通知 | 開啟瀏覽器 | 開啟後停止／結束 | 下播關窗 |
|------|------|------|------|------|------|
| `trigger` | 監聽＋觸發 | 依全域設定 | 依全域設定 | 依全域設定 | 依瀏覽器設定 |
| `monitor` | 僅監聽 | 否 | 否 | 否 | 否 |
| `notify` | 僅監聽含通知 | 依全域通知開關 | 否 | 否 | 否 |

舊版 `monitor_only=true` 會載入為 `channel_mode=monitor`；舊欄位仍保留作為相容視圖。`channel_mode=notify` 會保留為通知型監聽，避免升級後改變使用者原本的眼睛按鈕選擇。主清單的眼睛按鈕依序循環 `trigger → monitor → notify → trigger`。

### 1.3 全域一次性輪詢模式

最下方控制列提供四種全域操作：

| 值 | UI 名稱 | 行為 |
|------|------|------|
| `trigger` | 監聽＋觸發 | 持續輪詢；LIVE 依通知／開啟／開啟後設定執行。 |
| `watch` | 僅監聽 | 持續輪詢；不通知、不開啟瀏覽器。 |
| `trigger_once` | 監聽＋觸發一次 | 完成一個完整 `PollStatusUpdate` 輪詢週期後停止；本輪仍依設定執行副作用。 |
| `watch_once` | 僅監聽一次 | 完成一個完整 `PollStatusUpdate` 輪詢週期後停止；本輪不通知、不開啟瀏覽器。 |

一次性模式的消費點是「按下按鈕後的下一個 `cycle_id` 對應的 `PollStatusUpdate`」，不會把按鈕按下前已在佇列中的輪詢當成這一次；且在本輪 LIVE／離線事件都派送完成後才停止，避免漏掉本輪事件。若程式在本輪完成前關閉，設定會保留一次性模式，下一次以靜默啟動時會繼續執行一次；完成後則保存為對應的持續模式，避免每次啟動重複執行。

### 1.4 狀態與「未知」

`ChannelStatus.status` 仍接受舊版的 `True`、`False` 與字串，以便既有資料和
測試平滑升級；新程式碼應使用 `ChannelStatus.state`。其中 `unknown` 代表尚未
驗證或資料不足，不能被當成 `offline`，因此不會觸發離線關窗或寫入狀態快取。
`pending` 是 UI 的驗證標記，不是另一個直播狀態：它表示畫面使用上一輪快取，
等待下一次輪詢確認。

---

## 2. 瀏覽器設定：四個維度 + 一個獨立群組

設定不是一堆平行開關，而是四個「維度」加上一個獨立群組。維度定義在 `browser_settings_model.py`。

| 維度 | 常數 | 選項 | 意義 |
|------|------|------|------|
| A 啟動方式 | `LAUNCH_SYSTEM` / `LAUNCH_PROGRAM` | 系統預設瀏覽器 / 自訂程式 | **所有其他設定的總前置**。 |
| B 身分 | `IDENTITY_LOCAL` / `IDENTITY_DEDICATED` | 沿用登入帳號 / 專用設定檔 | 解鎖自動管理與視窗管理。 |
| C 呈現方式 | `PLACEMENT_TAB` / `PLACEMENT_WINDOW` / `PLACEMENT_PLAYER` | 分頁 / 新視窗 / 純播放器（app mode） | 決定視窗是否可被追蹤／幾何是否有意義。 |
| D 自動管理 | `close_on_offline` / `close_on_stop` / `close_off_topic_pages` / `minimized` / `hide_from_taskbar` | 各自布林 | 需要「可被辨識並管理的獨立視窗」。 |

另有 **X 幾何**（`apply_geometry` + `x/y/width/height`）以及與上述完全獨立的 **觀看強化（Viewer Engagement）** 群組（自帶總開關）。

---

## 3. 設定之間的連動關係

「有 A 才能有 A1/A2」「有 A 就不能有 B」的規則，全部集中在少數純函式中，UI 只是照著這些函式即時開關控制項。

### 3.1 前置（要先有 A 才能有 A1、A2…）

| 前置 | 解鎖 | 判定函式 |
|------|------|----------|
| A＝自訂程式 | 全部其他設定（B/C/D/X） | `infer_launch_mode` |
| A＝自訂程式 **且** B＝專用設定檔 | 自動管理區塊（D）出現 | `auto_cleanup_ui_available(launch, identity)` |
| A＝自訂程式 **且** C∈{新視窗, 播放器} | 幾何欄位（X）出現 | `geometry_placement_available(launch, placement)` |
| A＝自訂程式 **且** B＝專用 **且** C∈{新視窗, 播放器} | 自動管理各項可勾選（D 生效） | `window_management_available(launch, identity, placement)` |
| 幾何出現 **且** 為 Chromium **且** 勾選套用幾何 | X/Y/W/H 可編輯 | `_refresh_geometry_state` |

### 3.2 互斥／強制關閉（有 A 就不能有 B）

當條件不成立時，`apply_ui_dimensions` 會在存檔前把不相容的旗標**強制歸零**，避免出現「畫面上勾了、實際上無效」的謊言：

| 情境 | 被強制關閉的設定 |
|------|------------------|
| B＝沿用登入帳號（非專用） | `user_data_dir`、`per_channel_profile`、`minimized`、`hide_from_taskbar`、`close_on_offline`、`close_on_stop`、`close_off_topic_pages` 全部歸零 |
| A＝系統預設 | `user_data_dir`、`per_channel_profile` 歸零（其餘維度亦不生效） |
| C＝分頁 | 視窗無法追蹤 → D 全體停用，且顯示「無法追蹤」提示 |
| 瀏覽器為 Firefox | 播放器（app mode）選項停用 |

### 3.3 能力晶片（capability chips）

`capability_summary(launch, identity, placement)` 把上述結果濃縮成四顆晶片，狀態 `ok`／`warn`／`off`：

| 啟動 | 身分 | 呈現 | launch | login | window | manage |
|------|------|------|--------|-------|--------|--------|
| 系統 | — | — | off | off | off | off |
| 程式 | 專用 | 播放器 | ok | warn | ok | ok |
| 程式 | 沿用登入 | 分頁 | ok | ok | warn | off |

> `login` 對「沿用登入帳號」是 `ok`（觀看計入最佳），對「專用設定檔」是 `warn`（需另行登入）。

**測試佐證**：`tests/test_browser_settings_model.py` 覆蓋上述所有 `*_available`、`capability_summary` 與 `apply_ui_dimensions` 的強制歸零串聯。

---

## 4. 主視窗：狀態 → UI 呈現

### 4.1 控制列（監看模式）

`monitor_mode_button_states(mode)` 為純決策，`App._apply_monitor_mode_buttons` 只負責套用。

| 模式 | 開始 | 觀看 | 停止 |
|------|------|------|------|
| `idle` | 可用 | 可用 | 停用 |
| `trigger` | 停用（目前模式） | 可用 | 可用 |
| `watch` | 可用 | 停用（目前模式） | 可用 |

**測試佐證**：`tests/test_app_ui.py::test_monitor_mode_buttons_*`。

### 4.2 頻道列狀態徽章

`ChannelRow._render_status_visuals` 依 `_status_state` 呈現徽章（顏色為穩定的視覺契約）：

| 狀態 | 文字色 | 底色 | 游標 |
|------|--------|------|------|
| 未知／閒置（`None`） | `#666677` | 透明 | — |
| 預定（upcoming） | 白 | `#e65100`（橘） | `hand2` |
| 直播中（live） | 白 | `#1b5e20`（綠） | `hand2` |
| 離線（offline） | `#999999` | 透明 | YouTube 有等待室連結時 `hand2` |
| 暫停（`enabled=False`） | `_CLR_TEXT_DISABLED` | 透明 | — |

**測試佐證**：`tests/test_app_status_bridge.py::test_status_badge_*`、`test_paused_row_shows_disabled_visual`。

### 4.3 啟用／暫停／單頻道三態模式

`_apply_enabled_visual` 依 `enabled` × `channel_mode` 切換整張卡片與兩顆鈕的視覺：

| 狀態 | 卡片 | 徽章 | 觸發行為 |
|------|------|------|----------|
| 啟用（監聽＋觸發） | 正常色 | 顯示即時狀態 | 依全域設定完整觸發 |
| 啟用（僅監聽） | 正常色 | 保留即時狀態（不清空計時） | 抑制通知、開窗與離線關窗 |
| 啟用（僅監聽含通知） | 紫色提示 | 保留即時狀態 | 只保留通知；不開窗、不執行開啟後生命週期 |
| 暫停（停用） | 暗色卡片 | 顯示「暫停」灰字 | 不輪詢副作用 |

規則：暫停／恢復一律回到 `trigger`；由暫停點眼睛按鈕會恢復並進入 `monitor`；已啟用時只切換 `channel_mode`，不清空目前正在看的直播顯示（`reset_status=False`）。一次性模式由最下方全域控制列管理，不會寫入單一頻道。

**測試佐證**：`tests/test_app_status_bridge.py::test_monitor_only_keeps_channel_enabled_and_flags_suppression`。

---

## 5. 設定對話框：狀態 → UI 呈現（漸進揭露）

設定對話框以「漸進揭露 + 即時停用 + 能力晶片」三種手法呈現連動（`app_dialogs.py`）：

| 觸發狀態 | UI 呈現 | 實作 |
|----------|---------|------|
| A＝系統預設 | 整個設定主體 `pack_forget` 隱藏；四顆晶片全 `off` | `_refresh_enabled_state`（`use_custom`） |
| A＝自訂程式 | 顯示設定主體 | 同上 |
| B＝專用設定檔 | 顯示設定檔路徑列、登入按鈕、每頻道獨立設定檔可勾 | `_identity_path_frame.pack` |
| B＝沿用登入 | 路徑列隱藏、路徑輸入與登入鈕停用 | `profile_entry_state="disabled"` |
| A＋B＝專用 | 自動管理卡片出現 | `auto_cleanup_ui_available` → `_auto_card.pack` |
| 自動管理卡片出現但 C＝分頁 | 卡片內各項停用，並顯示「需要獨立視窗」提示 | `_auto_window_required_hint.pack` |
| A＋C∈{新視窗,播放器} | 幾何卡片出現 | `geometry_placement_available` → `_geometry_card.pack` |
| 幾何出現但非 Chromium／未套用幾何 | X/Y/W/H 停用 | `_refresh_geometry_state` |
| C＝分頁 | 顯示「無法追蹤視窗」說明 | `_refresh_win32_management_state` |
| 瀏覽器＝Firefox | 播放器選項停用 | `_on_path_change` |

切換任一維度都會呼叫 `_on_dimension_change → _refresh_enabled_state`，一次重算所有顯藏／停用／晶片，確保畫面與可存檔的實際能力永遠一致。

**測試佐證**：驅動上述所有顯藏／停用的判定函式，均由 `tests/test_browser_settings_model.py` 完整覆蓋（互鎖規則是純函式，UI 只是照著套用）。

---

## 6. 一致性原則

1. **UI 不說謊**：任何在畫面上可勾的項目，其對應能力必然可用；不相容者一律停用或隱藏，並在存檔時強制歸零。
2. **決策與呈現分離**：所有「哪個狀態顯示什麼」的判斷都在純函式（`channel_policy`、`browser_settings_model`、`monitor_mode_button_states`）裡，可脫離 Tk 測試。
3. **狀態改變必重算**：模式切換、維度切換、啟用切換都各有單一入口重新套用視覺，不散落更新。
