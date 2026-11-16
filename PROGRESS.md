# PROGRESS

最後更新：2026-11-17

待辦一律放 GitHub issues（標題前綴 `[P1]`–`[P3]`，待觀察者加 `needs-observation` label）。一個 issue 對一個分支對一個 PR，PR 內文寫 `Closes #n`。本檔只留決策、現行規則與部署紀錄。

## 專案目標
Hello Streamer（套件名 `stream_monitor`）：桌面實況監控工具（CustomTkinter），追蹤 Twitch / YouTube 開播，通知、開直播頁，並以 CDP 頁面輔助處理 Twitch 播放恢復、Channel Points 寶箱領取與劇院模式。僅提供可攜版（資料都在執行檔同層）。

## 已定決策
- 只做可攜版，不寫 AppData / XDG：搬資料夾即搬設定、資料庫、log、瀏覽器 Profile。
- 寶箱領取只以 Twitch 伺服器的原生確認（ack）判定成功：按鈕被隱藏不等於成功，Twitch 可能樂觀隱藏卻回 `IntegrityCheckFailed`。
- 離線需連續兩次確認才觸發事件：避免平台短暫查詢異常造成誤通知／誤關窗。
- 自動整理（下播關窗等）只在「程式專用帳號 + 獨立視窗」可用：避免誤關使用者自己的瀏覽器。
- 測試只收 `tests/`（pyproject 的 testpaths）：release／artifacts 內可能有舊版套件副本。
- 登入觀看位置維持 2 個（`viewer_engagement.points_slot_count`，預設 2，限 1–4，無 UI）：2026-11-14 實測 3 個位置時第 3 台 30 分鐘 0 點，此帳號 Twitch 只認列同時 2 台，多開還會觸發停滯重啟打斷正常的兩台。不做 UI（#30 關閉）：只有 2 有效，加 UI 等於提供無效選項。
- 2026-11-16 改用 GitHub issues 管理待辦（使用者同意）：PROGRESS.md 的待辦清單混雜過時內容、編號混亂。
- 領取紀錄不持久化（2026-11-16，#29 關閉）：只在部署重啟時歸零，後果是各台多輪一次；持久化要處理場次辨識，不划算。

## Twitch 點數位置分配（現行規則，v1.3.2）
目標（使用者釐清）：名單中每個開播頻道每場都要至少輪過一次，排序靠上者優先。實作在 `app.py` `refresh_points_priority_allocation` 與 `stream_monitor/claim_registry.py`，測試 `tests/test_points_allocation_offline.py`。
- **輪過**＝（伺服器確認領到寶箱 或 點數增加 2 次）**且** 本場在位置上累計滿 10 分鐘（`MIN_TURN_WATCH_SECONDS = 600`）。下播清除；領取紀錄只存記憶體，重啟歸零。
  - 10 分鐘的原因：Twitch 會在剛進場時就給一個寶箱，11/14 jipandan 開窗 21 秒領到就被關，整場沒連觀。10 分鐘是 Claude 選的值，對齊「點數增加 2 次約 10 分鐘」，若仍中斷可調高。
  - 點數增加 2 次的原因：使用者確定「點數沒跳就一定沒被認列」，短場（如 10 分鐘）等不到寶箱。
- **未輪過者優先**（指定清單→主清單順序），未輪過的佔位者保留到輪過，不中途踢掉。
- **剩下的位置依排序給已輪過者**：讓位的是排序最低者，其他台輪過後排序高者拿回位置。（取代過去的「佔位到下播」黏著規則與「前任佔位者優先」。）
- **停滯重啟**：任何佔位者 20 分鐘（`STALLED_SLOT_SECONDS`）無點數、無領取 → 關閉**所有**佔位視窗再重開（只重開一個會回到同一個壞掉的瀏覽器）。每次最多 2 次（`STALL_RESTART_LIMIT`），點數上升或領到會恢復次數。用完後：未輪過者放棄本場順位（Claude 加的保險，使用者確認保留），已輪過者照排序保留、不再重啟。時間本身不是讓位條件（使用者決定）。
- **瀏覽器沒有除錯端點**（#21）：取得埠的結果是 `profile_nondebug` 時，Windows 上仍配一個新埠放進啟動參數，啟動後再做探索。原因：舊瀏覽器可能正在結束，新瀏覽器會變成主程序；若舊瀏覽器其實還活著，它會忽略轉送過來的參數。探索失敗、或重啟後復原的視窗遇到 `profile_nondebug` 時，請分配關閉**所有**佔位視窗再重開。限制：兩次重啟至少間隔 5 分鐘，在成功 attach 之前最多 2 次，之後交給停滯重啟處理。分配只在同一輪會重開視窗時才關窗。
- **下播**：確認下播立即套用該列狀態；開啟「下播關窗」時清理也涵蓋下播頻道殘留視窗；下播關窗後 120 秒內不把該頻道當開播（根因不再追查，#26 關閉：代價只是下播後 2 分鐘內重開台會晚一點開窗）。
- page assist 原有「連續 30 分鐘有效觀看無點數 → F5，最多 2 次」流程不變。

## 部署紀錄（`D:\Tools\HelloStreamer`）
| 時間 | 內容 | commit | 舊版備份（`D:\Tools\` 下） |
|---|---|---|---|
| 11/09 17:56 | v1.3.1 + 領取 log | — | `HelloStreamer_backup_20261109_pre-claim-log` |
| 11/10 02:05 | 下播後被重開、殘留視窗佔位 | — | `..._20261110_pre-offline-reopen-fix` |
| 11/10 10:26 | 黏著分配（已被取代） | — | `..._20261110_pre-sticky-slots` |
| 11/10 14:42 | 同 URL 受管開窗加鎖、狀態副標截短 | — | `..._20261110_pre-dup-window-fix` |
| 11/11 09:20 | 領獎保底分配 | — | — |
| 11/12 09:16 | 輪替時限（已被取代）、重用固定埠仍帶 `--remote-debugging-port` | `69fe910` | `..._20261112_pre-turn-timeout` |
| 11/12 10:49 | 點數增加 2 次算輪過、停滯重啟 | — | `..._20261112_pre-points-turn` |
| 11/12 18:08 | 已輪過者依排序分配 | `9f8ec59` | `..._20261112_pre-rank-slots` |
| 11/14 01:40 | 停滯重啟擴及已輪過者 | `c7e10f2` | `..._20261114_pre-served-stall` |
| 11/14 | `points_slot_count`、下播 120 秒緩衝、ytInitialData 警告細節 | `d20c8f4` `b1fee77` `dadc261` | `..._20261114_pre-slot-count`（含 config.json） |
| 11/16 09:48 | 佔位滿 10 分鐘才算輪過 | `dac156c` | `..._20261116_pre-min-watch`（含 config.json） |
| 11/16 14:36 | v1.3.2（程式與 `dac156c` 相同，只有版本號） | `4c0692f` | `..._20261116_pre-v1.3.2`（含 config.json） |
| 11/16 17:48 | #21 沒有除錯端點時自動恢復 | `6175218` | `..._20261116_pre-nondebug-recovery`（含 config.json） |
| 11/16 21:48 | #28 ytInitialData 嘗試每個指派並記錄後續內容（PR #38） | `76f4234` | `..._20261116_pre-yt-bootstrap` |

目前部署內容是 11/16 21:48 的 build（v1.3.2 + #21 + PR #38）。這一列是 11/17 依 exe 時間、備份資料夾與 21:49:45 的啟動紀錄補記的，當時沒有重新逐檔比對 `dist`。v1.3.2 已於 11/16 發行（tag 在 `771ab8e`，不含 #21 與 #38）。PR #39（#28 的第二版修正）尚未部署。

11/17 00:37 程式重啟過一次，exe 沒有更換，log 只有「前一個 session 沒有結束紀錄」，原因未查明。

#21 的經過：11/16 14:36 部署時，舊瀏覽器在重啟期間仍開著，新瀏覽器（14:36:43）沒帶 `--remote-debugging-port`，兩個視窗都是 `profile_nondebug`，要手動結束專用 profile 的 Chrome 才恢復。修正部署後 17:48 重啟又遇到同樣情況：啟動自動帶埠 64163／64164，兩台 1.5 秒內 attach，不需手動處理；瀏覽器主程序帶 `--remote-debugging-port=64163`。尚未實際觀察到「瀏覽器還活著、需要關窗重開」那條路徑（log 會出現 `Twitch browser has no debugging endpoint; restarting playback windows`）。

## 部署步驟（2026-11-10 整理）
1. `uv run python build.py` → `dist\HelloStreamer\`（HelloStreamer.exe + `_internal`）。
2. 備份 `D:\Tools\HelloStreamer\HelloStreamer.exe` 與 `_internal` 到 `D:\Tools\HelloStreamer_backup_<日期>_<說明>`。
3. 停止執行中的 HelloStreamer，換掉 exe 與 `_internal`（不動 config、logs、browser_profile、db）。
4. 若 `_internal` 內 VCRUNTIME DLL 被鎖無法刪除，可直接覆蓋其餘檔案，事後以 hash 比對 dist 與部署目錄確認一致。一定要帶 `--silent` 啟動，否則不會自動開始監控：用 WMI `Win32_Process.Create` 啟動 `"D:\Tools\HelloStreamer\HelloStreamer.exe" --silent`（工作目錄設為該資料夾），讓它脫離 shell 的 job。
5. 查 `logs\stream_monitor.log` 有 `Poll complete`，且直播中的頻道 page assist 有重新 attach。重啟時受管視窗會關掉再重開，屬正常。
6. 重啟時出現 `reason=profile_nondebug` 屬正常，程式會自動帶埠重開（#21）。若之後數分鐘仍沒有 `attached via CDP`，才手動結束 `--user-data-dir=D:\Tools\HelloStreamer\browser_profile` 的所有 chrome.exe，等分配重開，確認主程序帶 `--remote-debugging-port` 且 page assist 有 `attached via CDP`。

## 本機運行環境
- 實際運行：`D:\Tools\HelloStreamer\HelloStreamer.exe`；資料與 log 在同資料夾（`logs/`、`config.json`、`browser_profile/`）。repo 內 `logs/` 是 10/14 的舊 log。
- 設定：`monitor_mode=trigger`、`claim_channel_points=true`，2 個點數位置。

## 已結調查（紀錄）
- 2026-11-09「寶箱出現卻沒領」：截圖是 `asbs5263`，寶箱只存在 14:26:46–14:27:11，14:27:11 即伺服器確認領取，無漏領（輪詢 10–20 秒 + 隨機延遲 2–5 秒）。已補領取 log（`claimable bonus detected` / `claim button clicked`）。
- 2026-11-11 ramengyozaset 晚上其實沒開播；當晚中斷的是黛儚_卡索與漣韵。
- 2026-11-14 檢查：52 次領取全部伺服器確認，無 IntegrityCheck；「輪過後依排序還位」已實際觀察到。

## 注意事項
- 已知平台限制：`artifacts/functional-fixes-20261106/final-claim-test-summary.json` 記錄 `claim_platform_block: IntegrityCheckFailed`。
- 根目錄有大量本機產物（logs、browser_profile、artifacts、build、dist、seen_videos.db*），不要當成程式碼讀。
- 不保證 Twitch 認列觀看、點數或 Watch Streak（README／RELEASE_NOTES 已聲明）。
- 主要文件：`README.md`、`RELEASE_NOTES.md`、`docs/`（cdp-startup、twitch-player-recovery、state-machines-and-settings、watch-streak-observation 等）。
