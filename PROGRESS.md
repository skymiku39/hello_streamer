# PROGRESS

最後更新：2026-11-09

## 專案目標
Hello Streamer（套件名 `stream_monitor`）：桌面實況監控工具（CustomTkinter），追蹤 Twitch / YouTube 開播，通知、開直播頁，並以 CDP 頁面輔助處理 Twitch 播放恢復、Channel Points 寶箱領取與劇院模式。僅提供可攜版（資料都在執行檔同層）。

## 已定決策
- 只做可攜版，不寫 AppData / XDG：搬資料夾即搬設定、資料庫、log、瀏覽器 Profile。
- 寶箱領取只以 Twitch 伺服器的原生確認（ack）判定成功：按鈕被隱藏不等於成功，Twitch 可能樂觀隱藏卻回 `IntegrityCheckFailed`。
- 離線需連續兩次確認才觸發事件：避免平台短暫查詢異常造成誤通知／誤關窗。
- 自動整理（下播關窗等）只在「程式專用帳號 + 獨立視窗」可用：避免誤關使用者自己的瀏覽器。
- 測試只收 `tests/`（pyproject 的 testpaths）：release／artifacts 內可能有舊版套件副本。
- 本機用 `uv run pytest`；此機器的 `python` 指令不可用。

## 目前計畫（優先序）
1. [高] 查清楚「本機測試時寶箱已出現卻沒自動領取」的原因（見「進行中」）。
2. [中] 調查 `tests/test_app_native_integration.py::test_dialog_buttons_use_application_style[channel]` 整套跑時偶發失敗、單獨跑會過的不穩定問題。
3. [中] 確認 v1.3.1 是否要從 `codex/release-v1.3.1` 合併回 `main`（目前領先 main 約 80 個 commit）。
4. [低] 檢查 `.gitignore` 是否涵蓋 `logs/`、`browser_profile/`、`seen_videos.db*`、`build/`、`dist/`、`artifacts/`。

## 已完成
- v1.3.1 發佈內容：Twitch 播放器錯誤 #2000 有限次數重試、設定儲存失敗不留待恢復檔、Windows CDP 連線埠探索修正。
- 近期 Twitch 相關修正：登入狀態下的播放視窗動態分配（填滿兩個 slot）、寶箱領取需伺服器確認、原生 scalar points ack。
- 修正 `test_start_resumes_only_owned_live_trigger_players` 4 個參數的測試替身（補 `refresh_points_priority_allocation`），commit `ff44cbd` 已推送到 `origin/codex/release-v1.3.1`。
- 完整測試（修正前）：1449 通過、5 失敗；其中 4 個已修，1 個為上列不穩定測試。

## 進行中 / 下一步
**寶箱沒領的調查**（尚未有結論）：
1. 向使用者確認：該視窗當時在前景／被遮住／最小化？用 `run_hello_streamer.bat` 還是打包 exe？發生時間點？
2. 依時間點查 `logs/stream_monitor.log`（目前最後更新 10/14，沒有該次測試紀錄，需確認 log 是否寫到別處）搜尋 `claim candidate`、`no claimable bonus`、`IntegrityCheckFailed`。
3. 若懷疑背景節流：看 `stream_monitor/twitch_page_assist.py` 的 `_FIND_CLAIM_JS`（約 :407）；`visible()` 與 `hitTestable()`（`elementFromPoint`）在最小化／被頁內元素遮住時會判定找不到；`document.visibilityState` 只被記錄、沒當條件。
4. 領取流程在 `_try_claim`（約 :2440）：需聊天室可見 → 探測 → 隨機等 2–5 秒 → 再探測 → 點擊 → 輪詢 ack；輪詢間隔 10–20 秒（`config.json` 的 `claim_poll_seconds_*`）。

## 已知問題與注意事項
- 已知平台限制：`artifacts/functional-fixes-20261106/final-claim-test-summary.json` 記錄 `claim_platform_block: IntegrityCheckFailed`（登入／手動領取確認待驗證）；`cross_broadcast: pending_real_next_broadcast`。
- 分支 `codex/release-v1.3.1` 與 main 差距大，合併前需檢視。
- 根目錄有大量本機產物（logs、browser_profile、artifacts、build、dist、seen_videos.db*），不要當成程式碼讀。
- 不保證 Twitch 認列觀看、點數或 Watch Streak（README／RELEASE_NOTES 已聲明）。
- 主要文件：`README.md`、`RELEASE_NOTES.md`、`docs/`（cdp-startup、twitch-player-recovery、state-machines-and-settings、watch-streak-observation 等）。
