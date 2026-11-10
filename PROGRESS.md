# PROGRESS

最後更新：2026-11-10

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
2. [中] 原生視窗測試整套跑時偶發失敗、單獨跑必過：先後見過 `tests/test_app_native_integration.py::test_dialog_buttons_use_application_style[channel]` 與 `tests/test_window_tracking_integration.py::test_separate_topmost_windows_have_distinct_native_bounds`。推測是真實桌面（使用者正在使用的視窗、HelloStreamer 運行中）干擾，尚未確認根因；2026-11-09 完整測試 1453 通過、1 偶發失敗。
3. [中] 確認 v1.3.1 是否要從 `codex/release-v1.3.1` 合併回 `main`（目前領先 main 約 80 個 commit）。
4. [已完成] `.gitignore` 已涵蓋 logs、browser_profile、seen_videos.db*、config.json、build、dist、artifacts、release、backups、*.log。
6. [已完成] 2026-11-10 修正「下播頻道關窗後約 1 秒又被重開、佔住登入觀看位置」並部署（02:05）；舊版備份 `D:\Tools\HelloStreamer_backup_20261110_pre-offline-reopen-fix`。原因：事件橋每輪只套用 3 列狀態且開播優先，下播列仍顯示開播，點數分配讀到「開播中但視窗不見」就重開（hayabi_zr 19:37、gracya_leejia 00:24）；另外下播列沒有網址，清理多餘視窗時漏掉，殘留視窗佔位數小時（jipandan 00:41–01:45 因此沒加點）。修法：確認下播時立即套用該列狀態（不受每輪上限、拖曳中會延到恢復後優先套用）；開啟「下播關窗」時，分配清理也涵蓋下播頻道的殘留視窗（關閉時保留，維持原行為）。測試 `tests/test_points_allocation_offline.py`；完整測試 1459 通過。部署後首輪即關閉兩個殘留視窗。
7. [高] 2026-11-10 調查「仍收到連續觀看中斷提醒」（依 `watch_streak_observation.ndjson` 重建 11/09 時間軸，時間為台灣時間）：
   - 主因一（已於 02:05 部署修正）：hayabi_zr 19:37 下播、gracya_leejia 00:24 下播後視窗被重開，一路佔位到 02:05；期間開播的 ramengyozaset（20:59–02:35）、hanasakich（21:03–22:32、22:35–00:06）完全沒被看，enmei_suiin 只看 6 分鐘。
   - 主因二（2026-11-10 已修，10:26 已部署；舊版備份 `D:\Tools\HelloStreamer_backup_20261110_pre-sticky-slots`）：使用者決定改成黏著——已佔位的頻道保留到下播，只有指定頻道開播可搶位；空出的位置再依主清單順序補。測試在 `tests/test_points_allocation_offline.py`。原狀況：`app.py` `refresh_points_priority_allocation` 每次都依主清單順序重算兩個位置，沒有「已分配者看到下播為止」的黏著；docstring 與 docs 說會保留，實作不符。實例：20:28 enmei_suiin 開播把看了 50 分鐘的 oldpot_o_0 擠掉，20:34 daemoncasoul 開播又把 enmei_suiin 擠掉。指定頻道（taiga_hikari、kspksp）搶位是預期行為。
   - 結構限制：只有兩個登入位置，同時開播超過兩台時，排序後面的頻道必然漏看。
   - 待使用者確認：提醒出現在哪個頻道；是否把分配改成黏著（非指定頻道不被後開播的高順位頻道擠掉）。
8. [已完成，14:42 部署] 2026-11-10 14:29 使用者按停止再開始後，hayabi_zr 開了兩個視窗。原因：開播動作（open_keep）與點數分配開窗（points_priority_open）在不同 worker 同時呼叫 `notifier.open_url`，兩邊都先通過「已開／啟動中」檢查才登記 pending，造成重複啟動。修法：受管開窗依 URL 加鎖序列化，第二個呼叫會看到 pending 而重用。測試 `tests/test_twitch_watch_policy.py::test_concurrent_managed_opens_of_same_url_launch_once`；完整測試 1464 通過。舊版備份 `D:ToolsHelloStreamer_backup_20261110_pre-dup-window-fix`。部署後重啟，hayabi_zr、gracya_leejia 各只開一個視窗。
9. [已完成，14:42 部署] 2026-11-10 下方控制區偶爾變三行：狀態副標只依「RunRunLuna」預留寬度，長頻道名（最多 66 字）會撐寬 CTkLabel，把「間隔」擠到下一行。修法：`app_ui._fit_status_subline` 依像素寬度截短名字。測試 `tests/test_status_subline_fit.py`。
5. [已完成] 2026-11-09 17:56 已重新打包並部署 v1.3.1（含領取 log）到 `D:\Tools\HelloStreamer`；舊版備份在 `D:\Tools\HelloStreamer_backup_20261109_pre-claim-log`。下次寶箱出現後，查 log 的 `claimable bonus detected` / `claim button clicked` 驗證。

## 已完成
- v1.3.1 發佈內容：Twitch 播放器錯誤 #2000 有限次數重試、設定儲存失敗不留待恢復檔、Windows CDP 連線埠探索修正。
- 近期 Twitch 相關修正：登入狀態下的播放視窗動態分配（填滿兩個 slot）、寶箱領取需伺服器確認、原生 scalar points ack。
- 修正 `test_start_resumes_only_owned_live_trigger_players` 4 個參數的測試替身（補 `refresh_points_priority_allocation`），commit `ff44cbd` 已推送到 `origin/codex/release-v1.3.1`。
- 完整測試（修正前）：1449 通過、5 失敗；其中 4 個已修，1 個為上列不穩定測試。

## 進行中 / 下一步
**寶箱沒領的調查**（尚未有結論）：
1. 向使用者確認：該視窗當時在前景／被遮住／最小化？用 `run_hello_streamer.bat` 還是打包 exe？發生時間點？
2. 依時間點查 **`D:\Tools\HelloStreamer\logs\stream_monitor.log`**（實際運行的部署版；repo 內 `logs/` 是 10/14 的舊 log，不要看）搜尋 `claim candidate`、`no claimable bonus`、`IntegrityCheckFailed`。2026-11-09 檢查：該 log 有 80 筆 `confirmed bonus claim`、無 IntegrityCheck 或領取失敗紀錄，領取整體運作正常；**已查明（2026-11-09）**：截圖是 `asbs5263`，餘額 141,857 只存在於 14:26:46–14:27:11，14:27:11 即有伺服器確認領取，寶箱約停 25 秒（輪詢 10–20 秒 + 隨機延遲 2–5 秒），無漏領。已在 `_try_claim` 補 log（偵測、點擊、被拒絕）；需重新打包部署到 `D:\Tools\HelloStreamer` 才會生效。另：`hayabi_zr` 16:40–17:10 點數停滯 30 分鐘，由 points-stall 自動重載恢復（Twitch 端未發點）。
3. 若懷疑背景節流：看 `stream_monitor/twitch_page_assist.py` 的 `_FIND_CLAIM_JS`（約 :407）；`visible()` 與 `hitTestable()`（`elementFromPoint`）在最小化／被頁內元素遮住時會判定找不到；`document.visibilityState` 只被記錄、沒當條件。
4. 領取流程在 `_try_claim`（約 :2440）：需聊天室可見 → 探測 → 隨機等 2–5 秒 → 再探測 → 點擊 → 輪詢 ack；輪詢間隔 10–20 秒（`config.json` 的 `claim_poll_seconds_*`）。

## 部署步驟（2026-11-10 整理）
1. `uv run python build.py` → `dist\HelloStreamer\`（HelloStreamer.exe + `_internal`）。
2. 備份 `D:\Tools\HelloStreamer\HelloStreamer.exe` 與 `_internal` 到 `D:\Tools\HelloStreamer_backup_<日期>_<說明>`。
3. 停止執行中的 HelloStreamer，換掉 exe 與 `_internal`（不動 config、logs、browser_profile、db）。
4. 若 `_internal` 內 VCRUNTIME DLL 被鎖無法刪除，可直接覆蓋其餘檔案，事後以 hash 比對 dist 與部署目錄確認一致。一定要帶 `--silent` 啟動，否則不會自動開始監控：用 WMI `Win32_Process.Create` 啟動 `"D:\Tools\HelloStreamer\HelloStreamer.exe" --silent`（工作目錄設為該資料夾），讓它脫離 shell 的 job。
5. 查 `logs\stream_monitor.log` 有 `Poll complete`，且直播中的頻道 page assist 有重新 attach。重啟時受管視窗會關掉再重開，屬正常。

## 本機運行環境（2026-11-09 檢查）
- 實際運行：`D:\Tools\HelloStreamer\HelloStreamer.exe`（v1.3.1，build 於 11/8 01:29，約等於 commit `dc843b2`）；資料與 log 在 `D:\Tools\HelloStreamer\`（`logs/`、`config.json`、`browser_profile/`）。
- 設定：`monitor_mode=trigger`、`claim_channel_points=true`，監控 21 個頻道，輪詢正常。
- 觀察到的警告：`ramengyozaset` 劇院模式 `state=unknown` 被略過（頻道離線，影響小）；16:44 有 4 個 YouTube 頻道 `ytInitialData` 解析失敗，已當作狀態不明、未誤判下播。

## 已知問題與注意事項
- 已知平台限制：`artifacts/functional-fixes-20261106/final-claim-test-summary.json` 記錄 `claim_platform_block: IntegrityCheckFailed`（登入／手動領取確認待驗證）；`cross_broadcast: pending_real_next_broadcast`。
- 分支 `codex/release-v1.3.1` 與 main 差距大，合併前需檢視。
- 根目錄有大量本機產物（logs、browser_profile、artifacts、build、dist、seen_videos.db*），不要當成程式碼讀。
- 不保證 Twitch 認列觀看、點數或 Watch Streak（README／RELEASE_NOTES 已聲明）。
- 主要文件：`README.md`、`RELEASE_NOTES.md`、`docs/`（cdp-startup、twitch-player-recovery、state-machines-and-settings、watch-streak-observation 等）。
