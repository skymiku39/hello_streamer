# PROGRESS

最後更新：2026-11-16

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
   - 結構限制：只有兩個登入位置，同時開播超過兩台時，排序後面的頻道必然漏看。（2026-11-14 實測：開到 3 個位置，第 3 台 0 點，Twitch 只認列 2 台，見 item 16。）
   - 待使用者確認：提醒出現在哪個頻道；是否把分配改成黏著（非指定頻道不被後開播的高順位頻道擠掉）。
8. [已完成，14:42 部署] 2026-11-10 14:29 使用者按停止再開始後，hayabi_zr 開了兩個視窗。原因：開播動作（open_keep）與點數分配開窗（points_priority_open）在不同 worker 同時呼叫 `notifier.open_url`，兩邊都先通過「已開／啟動中」檢查才登記 pending，造成重複啟動。修法：受管開窗依 URL 加鎖序列化，第二個呼叫會看到 pending 而重用。測試 `tests/test_twitch_watch_policy.py::test_concurrent_managed_opens_of_same_url_launch_once`；完整測試 1464 通過。舊版備份 `D:ToolsHelloStreamer_backup_20261110_pre-dup-window-fix`。部署後重啟，hayabi_zr、gracya_leejia 各只開一個視窗。
9. [已完成，14:42 部署] 2026-11-10 下方控制區偶爾變三行：狀態副標只依「RunRunLuna」預留寬度，長頻道名（最多 66 字）會撐寬 CTkLabel，把「間隔」擠到下一行。修法：`app_ui._fit_status_subline` 依像素寬度截短名字。測試 `tests/test_status_subline_fit.py`。
10. [已完成，待部署] 2026-11-11 改成「領獎保底」分配：11/10 夜間黛儚_卡索（86 場）、老郭です（18 場）、拉麵餃子組（14 場）連續紀錄中斷，三台整場 0 次觀看——原因是 11/10 的黏著規則讓兩個位置被 kspksp（指定）、gracya_leejia、hanasakich 等佔滿數小時（依 `watch_streak_observation.ndjson` 重建，時間為台灣時間）。使用者釐清原意：名單中每個開播頻道每場都要至少領過一次獎勵，排序靠上者優先。新規則：未領過者優先（指定清單→主清單順序）；未領過的佔位者保留到領獎（不中途踢掉）；領過後若有未領過者在等就讓位；沒人等時已領過者續佔。「領過」＝伺服器確認的領取（`stream_monitor/claim_registry.py`，僅存記憶體，下播清除、重啟歸零）。測試 `tests/test_points_allocation_offline.py`。取代 item 7 主因二的黏著規則。
11. [已完成，09:16 部署] 2026-11-12 領獎保底加上「輪替時限」：11/11 晚上黛儚_卡索（21:30–02:12）整場沒被分到位置。經過（查 `D:\Tools\HelloStreamer\logs`，台灣時間）：item 10 已於 11/11 09:20 部署；16:31 兩個受管視窗同時消失（原因不明），重開時 `acquire_cdp_debugging_port` 仍查到舊瀏覽器程序在聽 58812（reason=process），因此新視窗沒帶除錯埠參數，舊程序隨即結束，新瀏覽器沒有除錯端點（`profile_nondebug`），page assist 從 16:31 到隔天 06:42 完全連不上，整晚 0 次領取。item 10 的規則「未領過的佔位者保留到領獎」因此讓 hanasakich（20:09 起）、jipandan（21:23 起）永久佔位。修法：未領過的佔位者佔滿 20 分鐘（`claim_registry.UNCLAIMED_TURN_SECONDS`，寶箱約 15 分鐘一次）仍沒領到，就視為已輪過，有其他未輪過的頻道在等時讓位；下播清除。原因：領取可能長時間不可行（CDP 失聯、Twitch 不發寶箱），不能讓分配卡死。測試 `tests/test_points_allocation_offline.py`（16 通過）。
    - 拉麵餃子組（ramengyozaset）：11/11 14:09 至 11/12 09:04 的 340 次輪詢全部回報未開播，沒有任何開播事件；需向使用者確認實際開播時間或是否為別的頻道。
    - 已修（同批部署，commit `69fe910`）：16:31 那種「瀏覽器重啟後失去除錯端點」。`notifier._launch_debugging_port`：重用以程序查到的固定埠（reason=process）時，啟動參數仍帶同一個 `--remote-debugging-port`；瀏覽器還活著會忽略，若它正在結束，新瀏覽器就保有端點。測試 `tests/test_launch_debugging_port.py`；完整測試 1476 通過。部署重啟本身重現了該情境：09:16:45 兩個視窗同時重開，新瀏覽器主程序帶著 `--remote-debugging-port=59568` 啟動，runrunluna 2 秒內 attach。
    - 殘留：同一情境下第二個視窗的啟動會交給正在結束的舊瀏覽器而遺失，要等 90 秒的啟動等待期滿才重開（sumitaninasi 09:16:45 遺失、09:18:16 重開並 attach）。影響小，未處理。另外若埠仍被舊程序佔住導致新瀏覽器綁不上，仍會落入 `profile_nondebug`，沒有自動恢復。
    - 舊版備份 `D:\Tools\HelloStreamer_backup_20261112_pre-turn-timeout`。
12. [已完成，10:49 部署] 2026-11-12 讓位條件加上「點數增加數次」（使用者決定：領到寶箱 **或** 點數有增加數次即算輪過）。原因：使用者只能從 Twitch 首頁通知得知連續紀錄中斷，且確定「點數沒跳就一定沒被認列觀看」；11/11 漣韵（4lianyun）只播 21:54–22:04 十分鐘、3 場紀錄中斷，等寶箱（約 15 分鐘）或 20 分鐘時限都救不到。實作：page assist 偵測到餘額上升時呼叫 `claim_registry.record_points_increase`，累計 `POINTS_INCREASES_FOR_TURN = 2` 次（「數次」取 2：被動加點約 5 分鐘一次，3 次就已到寶箱時間）即 `had_turn`；下播清除。（20 分鐘時限已由 item 13 改為重啟流程。）測試 `tests/test_points_allocation_offline.py`；完整測試 1478 通過。
    - 更正 item 11：拉麵餃子組 11/11 晚上沒有開播，最近一場結束於 11/11 03:18（11/10 晚那場，屬 item 10 的已知事件）。11/11 晚實際中斷的是黛儚_卡索（86 場）與漣韵（3 場），與使用者的 Twitch 通知截圖一致。
13. [已完成，10:49 部署] 2026-11-12 移除「佔滿 20 分鐘就讓位」（使用者決定：時間本身不是讓位條件；20 分鐘點數都沒跳代表一定有問題，要刷新重啟或走特殊流程）。取代 item 11 的輪替時限。現行規則：讓位條件只有「領到寶箱」或「點數增加 2 次」。佔位者 20 分鐘（`claim_registry.STALLED_SLOT_SECONDS`）內點數完全沒增加、也沒領到 → 分配關閉**所有**佔位視窗再重開（只重開一個會回到同一個壞掉的瀏覽器；全部關閉才會讓瀏覽器重啟並重新取得除錯端點），並記 warning `Twitch points stalled for …; restarting playback windows`。每次點數增加會重新起算 20 分鐘。每場最多重啟 2 次（`STALL_RESTART_LIMIT`）；重啟兩次後再 20 分鐘仍無點數，該頻道才放棄本場的順位讓給等待者——這一步是我（Claude）加的保險，避免一台壞掉的頻道整晚卡住其他台，使用者已於 2026-11-12 確認保留。page assist 原有的「連續 30 分鐘有效觀看無點數 → F5，最多 2 次」流程不變。測試 `tests/test_points_allocation_offline.py`；完整測試 1479 通過。部署後兩個視窗 1 秒內 attach；舊版備份 `D:\Tools\HelloStreamer_backup_20261112_pre-points-turn`。
14. [已完成，18:08 部署] 2026-11-12 已領過者的位置改依排序分配（使用者決定）。經過（台灣時間）：17:37 漣韵開播（未領過）需要一個位置，黛儚（主清單第 1、已領 9 次）與早日（第 6）都已輪過，舊規則「前任佔位者優先」依內部佔位順序留下早日、關掉黛儚；17:45 漣韵領到後三台都已輪過，「沒人等時續佔」讓漣韵、早日一直留著，黛儚要等有人下播才回來。新規則：未領過者的處理不變（優先、佔位者保留到輪過）；未領過者用不到的位置一律依排序（指定清單→主清單）給已輪過者——讓位的是排序最低者，其他台輪過後排序高者會拿回位置。取代 item 10 的「沒人等時已領過者續佔」。測試 `tests/test_points_allocation_offline.py`（21 通過，只跑相關檔案）。
    - 部署（commit `9f8ec59`）：重啟後領取紀錄歸零，三台都算未領過，位置依排序給黛儚、漣韵（18:08:35 兩個視窗 attach，播放與劇院模式正常），早日視窗關閉，等其中一台輪過才回來。舊版備份 `D:\Tools\HelloStreamer_backup_20261112_pre-rank-slots`。尚未實際觀察到「都輪過後依排序還位」這一步。
    - 同日觀察（10:49 部署後）：sumitaninasi、runrunluna、黛儚、早日、漣韵五台都有伺服器確認領取；漣韵開播 1 秒內拿到位置、8 分鐘後領到；停滯重啟未觸發過，尚未實際驗證。
    - 2026-11-14 01:30 檢查（log 只涵蓋 11/13 16:31 起）：部署版 exe 與 `_internal` 和 `dist` hash 一致（= `9f8ec59`），程序自 11/12 18:08 持續執行。「輪過後依排序還位」已實際觀察到：19:02（jipandan 加點 2 次）、22:11（oldpot_o_0）、22:37（hanasakich 領到）後都把位置還給排序較高的 gracya_leejia；下播（hayabi_zr 19:41、jipandan 23:55）沒有再被重開。52 次領取全部伺服器確認，無 IntegrityCheck。
    - 缺口（已修，見 item 15）：kspksp（指定）20:38 拿到位置，20:49 已加點 2 次算輪過，之後 20:49–22:04、22:05–01:15 共約 3 小時點數不動（播放器顯示 playing），page assist 的 F5（21:19、21:49、22:35、23:06）沒救回，01:11 的定期刷新後才恢復。item 13 的「20 分鐘無點數 → 重啟全部視窗」在 `claim_registry.track_slot_holders` 只對**未輪過**的佔位者生效，已輪過者（含指定頻道）不會觸發，所以整晚 0 次 `points stalled`。與 item 13 字面「佔位者 20 分鐘點數完全沒增加 → 重啟」不一致。
    - 未處理的小問題：下播關窗後 1 秒被重開又關（runrunluna 11:33、sumitaninasi 12:19，`Closed unallocated` 重複 40 多行，只有日誌雜訊）；黛儚 15:30 視窗在沒有關閉紀錄下被重開一次，原因未查。
15. [已完成，2026-11-14 01:40 部署] 停滯重啟擴及已輪過的佔位者（使用者決定）。原因見 item 14 的 kspksp 缺口。現行：所有佔位者 20 分鐘無點數、無領取 → 關閉所有佔位視窗重開，每次最多 2 次；點數上升或領到會恢復重啟次數（證明上次重啟有效），之後再停滯可以再重啟。重啟用完後：未輪過者放棄本場順位（不變），已輪過者照排序保留位置、不再重啟，直到點數再上升。代價：重啟會關掉所有佔位視窗，另一台正常的頻道也會中斷約十幾秒。commit `c7e10f2`；測試 `tests/test_points_allocation_offline.py`（23 通過），完整測試 1483 通過。部署後 kspksp、taiga_hikari 1 秒內 attach。舊版備份 `D:\Tools\HelloStreamer_backup_20261114_pre-served-stall`。尚未實際觀察到已輪過者觸發重啟。
16. [已完成，2026-11-14 部署] 登入觀看位置數量實測 + 小問題修正（使用者要求）。
    - 新設定 `viewer_engagement.points_slot_count`（預設 2，限制 1–4，沒有 UI，只能改 config.json），commit `d20c8f4`。
    - **3 個位置實測（02:08–02:39，30 分鐘）**：kspksp、taiga_hikari 各點數增加 8 次、領到 2 次；第 3 台 hanasakich 播放正常（playing、劇院模式、餘額有讀到 77230）卻 **0 次增加、0 次領取**，02:28 的停滯重啟也沒救回。hanasakich 在 2 個位置時是正常的（11/13 22:37 領到過）。結論：**此帳號 Twitch 只認列同時 2 台**，多開的位置拿不到點數，還會每 20 分鐘觸發停滯重啟、打斷正常的兩台。已改回 2。
    - **4 個位置未實測**：02:39 時只剩 3 台開播（oldpot_o_0 已下播）。3 台時第 3 台已經 0 點，4 台預期相同；若要再確認，等有 4 台同時開播時把設定改成 4 觀察 30 分鐘。
    - 修正「下播關窗後 1 秒被重開」（commit `b1fee77`）：11/13 19:41 hayabi_zr 下播關窗後，列狀態仍短暫顯示開播，分配就重開視窗，之後連續記 40 多行 `Closed unallocated`。根因是下播關窗和列狀態更新之間的時間差，沒有完全查清；改法是下播關窗後 120 秒內不把該頻道當成開播（收到新的 went_live 就解除），並且 `Closed unallocated` 每個頻道只記一次（被分配到位置後重置）。部署後 02:40 hanasakich 被關閉只記 1 行。
    - YouTube `ytInitialData` 解析失敗：4 台每次都同時失敗（約每 12 分鐘一次），是 YouTube 回了不同的頁面，處理方式本來就安全（當作狀態不明）。只在警告裡多記頁面大小與標題（commit `dadc261`），下次可以看出是同意頁、驗證頁還是錯誤頁。
    - 完整測試 1492 通過。舊版備份 `D:\Tools\HelloStreamer_backup_20261114_pre-slot-count`（含 config.json）。目前運行：2 個位置，kspksp、taiga_hikari。
17. [已完成，2026-11-16 09:48 部署] 2026-11-15 輪過條件加上「在位置上看滿 10 分鐘」（使用者回報 https://www.twitch.tv/videos/2928542265 沒連觀）。
    - 經過（台灣時間，查 `D:\Tools\HelloStreamer\logs`）：該影片是 jipandan（阿吉嘓）11/14 20:53–22:10 的場次。20:53:02 開窗（gracya_leejia 讓位），20:53:15 一進場就偵測到寶箱，20:53:23 伺服器確認領取，同一秒視窗被關、位置還給 gracya_leejia；之後 77 分鐘位置都在 4lianyun、gracya_leejia（兩台都已輪過、排序較高）手上，jipandan 整場只看了 21 秒。
    - 根因：item 10 的「領到寶箱＝輪過」假設寶箱要看約 15 分鐘才有，但 Twitch 會在觀眾剛進場時就給一個寶箱。同晚 oldpot_o_0 21:46 也是開窗 24 秒領到就被關；hanasakich 20:03 是 4.5 分鐘。
    - 修法（`claim_registry.MIN_TURN_WATCH_SECONDS = 600`）：輪過＝（領到寶箱 或 點數增加 2 次）**且** 本場在位置上累計滿 10 分鐘；下播清除。10 分鐘是我（Claude）選的：對齊「點數增加 2 次約 10 分鐘」；Twitch 認列連觀實際需要多久沒有官方數字，若仍中斷可再調高。停滯重啟用完而放棄順位（`_TURN_USED`）不受此限。
    - 代價：新開播頻道至少佔位 10 分鐘，排序最低的已輪過頻道會被擠掉這 10 分鐘。
    - 測試 `tests/test_points_allocation_offline.py`（36 通過）；完整測試 1494 通過。
    - 部署（commit `dac156c`）：dist 與部署目錄 hash 一致，09:48 重啟後輪詢正常；當時 20 個頻道全部離線，**尚未實際觀察到新規則**——下次有頻道在兩個位置已滿時開播，查 log 確認它開窗後至少留 10 分鐘才出現 `Closed unallocated`。舊版備份 `D:\Tools\HelloStreamer_backup_20261116_pre-min-watch`（含 config.json）。
5. [已完成] 2026-11-09 17:56 已重新打包並部署 v1.3.1（含領取 log）到 `D:\Tools\HelloStreamer`；舊版備份在 `D:\Tools\HelloStreamer_backup_20261109_pre-claim-log`。下次寶箱出現後，查 log 的 `claimable bonus detected` / `claim button clicked` 驗證。

## 已完成
- v1.3.1 發佈內容：Twitch 播放器錯誤 #2000 有限次數重試、設定儲存失敗不留待恢復檔、Windows CDP 連線埠探索修正。
- 近期 Twitch 相關修正：登入狀態下的播放視窗動態分配（填滿兩個 slot）、寶箱領取需伺服器確認、原生 scalar points ack。
- 修正 `test_start_resumes_only_owned_live_trigger_players` 4 個參數的測試替身（補 `refresh_points_priority_allocation`），commit `ff44cbd` 已推送到 `origin/codex/release-v1.3.1`。
- 完整測試（修正前）：1449 通過、5 失敗；其中 4 個已修，1 個為上列不穩定測試。

## 進行中 / 下一步
**2026-11-16 改用 GitHub 管理待辦**（使用者同意）：本分支已開 PR #20 到 `main`；未結事項已轉成 issues #21–#31（標題前綴 `[P?]`，P2 歸 milestone `v1.3.2`，待觀察者加 `needs-observation` label）。之後一個 issue 對一個分支對一個 PR，本檔只留決策與部署紀錄。#20 已於 2026-11-16 合併（merge commit `19c8ca9`，保留 19 個主題 commit）。v1.3.2 發行 PR 已開（分支 `release/v1.3.2`）：升版號、RELEASE_NOTES、`points_slot_count` 說明；`docs/watch-streak-observation.md` 的分配說明原本還是「佔位到下播」舊規則，已改成現行規則。發行 PR 合併後由使用者推 `v1.3.2` tag 觸發 release workflow。之後精簡本檔並刪除已合併分支（刪遠端分支需另行確認）。以下寶箱調查已於 11/9 查清無漏領，保留作紀錄。

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
