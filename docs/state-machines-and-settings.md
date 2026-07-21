# 狀態機、設定連動與各狀態 UI 呈現

- 版本：stream-monitor 1.1.6
- 範圍：主視窗（頻道清單／控制列）與瀏覽器設定對話框
- 目的：說明專案有哪些狀態機、設定之間的連動（前置／互斥／解鎖）關係，以及「每一種狀態在 UI 上如何被呈現」，並標註對應的驗證測試。

> 本文的每一條「狀態 → 呈現」規則都有單元測試佐證（見各節的「測試佐證」）。修改互鎖規則時，請同步更新對應測試，勿為了讓測試通過而放寬斷言。

---

## 1. 專案的狀態機總覽

專案的狀態被刻意分層，各層只負責單一職責，透過事件（Pub/Sub）與純決策函式連接，而非互相直接呼叫。

| 層級 | 狀態機 | 擁有者 | 值域 | 說明 |
|------|--------|--------|------|------|
| 控制 | 監看模式 | `MonitorController.mode` | `idle` / `trigger` / `watch` | 全域運行狀態，決定控制列三顆按鈕的可用性。 |
| 資料 | 頻道即時狀態 | `ChannelStatus`（`frozen`） | live / upcoming / offline | 由輪詢引擎產生，以 `is_live/is_upcoming/is_offline` 判讀，不再用魔術 `__eq__`。 |
| 資料 | 直播狀態預覽層級 | `monitor/preview.py`（tier-1 / tier-2） | — | 結構性不變式：tier-1 不得把已快取的 LIVE 降級為 offline（`_tier1_may_overwrite_cached`）。 |
| 動作 | 每頻道副作用決策 | `channel_policy.py`（純函式） | `LiveActionDecision` | 集中「模式 × 僅監測 × 動作 × 直播狀態」的判斷，供 `event_bridge` 使用。 |
| UI | 每列啟用／僅監測 | `ChannelRow`（`enabled` / `monitor_only`） | 啟用 / 暫停 / 僅監測 | 卡片外觀與兩顆切換鈕的視覺。 |
| UI | 拖曳手勢（列本地） | `ChannelRow._drag_phase` | `idle` / `pending` / `armed` | 長按 → 待命 → 啟動的輸入手勢，餵給 app 層的重排會話。 |
| UI | 重排會話（app 層） | `ChannelReorderMode` | idle → active → engaged → committed/cancelled | 卡片位移預覽與落點提交，與列本地手勢分離。 |
| 副作用 | 瀏覽器視窗登錄 | `browser_win32._WindowRegistry` | 每 URL 的視窗清單／關閉中／標題回退封鎖 | 單一擁有者，集中管理受監聽觸發開啟的視窗生命週期。 |

管理與關聯方式：資料流為 `Monitor.publish → MonitorEventBus → event_bridge.tick → App 副作用`。跨層一律以事件與純決策函式銜接；UI 只讀取決策結果並反映到畫面，不反向依賴引擎內部狀態。

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

### 4.3 啟用／暫停／僅監測

`_apply_enabled_visual` 依 `enabled` × `monitor_only` 切換整張卡片與兩顆鈕的視覺：

| 狀態 | 卡片 | 徽章 | 觸發行為 |
|------|------|------|----------|
| 啟用（一般） | 正常色 | 顯示即時狀態 | 依模式完整觸發 |
| 啟用 + 僅監測 | 正常色 | 保留即時狀態（不清空計時） | 抑制開窗等副作用 |
| 暫停（停用） | 暗色卡片 | 顯示「暫停」灰字 | 不輪詢副作用 |

規則：暫停／恢復一律清除 `monitor_only`；由暫停點「僅監測」等同恢復並進入僅監測；已啟用時只切換 `monitor_only`，不清空目前正在看的直播顯示（`reset_status=False`）。

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
