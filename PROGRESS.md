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
- 2026-11-17 GitHub Actions 額度用完：之後的 PR 改在本機驗證（`ruff`、`compileall`、完整測試跑 Python 3.14 與 3.11），Linux 無法驗證，PR 留言註明。本機預設是 3.14、CI 是 3.11，新測試要用 3.11 再跑一次（11/17 已遇到兩次只在 3.11 失敗）。
- 2026-11-17 待決 issue 的決定（使用者同意 Claude 的建議）：
  - #62 有頻道狀態不明時整個分配暫停：維持保守設計。23 小時的 log 沒有任何一次 Twitch 查詢不可用；改成排除該頻道，可能讓排序較低者佔位。真的遇到再處理。
  - #66 同一輪多台開播：後面的頻道照常通知與開窗，只拿掉停止／結束效果。原因：原本連通知都略過，與 README 不符，漏通知比多開窗糟。
  - #58 `foreground_hold_seconds`：移除。程式從來沒執行到，修好反而會每次開窗都搶前景 15 秒。
  - #49 刪除 6 支壞掉或一次性的腳本，`extract_youtube_probe.py` 跑起來會覆寫維護中的程式碼。
- 領取紀錄不持久化（2026-11-16，#29 關閉）：只在部署重啟時歸零，後果是各台多輪一次；持久化要處理場次辨識，不划算。

## Twitch 點數位置分配（現行規則，v1.3.2 + 11/17 修正）
- **開關**（#91）：`viewer_engagement.points_allocation_enabled` 決定是否套用位置上限，不再看優先清單是否為空。原因：使用者清空清單後，所有開播頻道都開窗，第 3 台不會得到點數。
目標（使用者釐清）：名單中每個開播頻道每場都要至少輪過一次，排序靠上者優先。實作在 `app.py` `refresh_points_priority_allocation` 與 `stream_monitor/claim_registry.py`，測試 `tests/test_points_allocation_offline.py`。
- **輪過**＝（伺服器確認領到寶箱 或 點數增加 2 次）**且** 本場在位置上累計滿 10 分鐘（`MIN_TURN_WATCH_SECONDS = 600`）。下播清除；領取紀錄只存記憶體，重啟歸零。
  - 10 分鐘的原因：Twitch 會在剛進場時就給一個寶箱，11/14 jipandan 開窗 21 秒領到就被關，整場沒連觀。10 分鐘是 Claude 選的值，對齊「點數增加 2 次約 10 分鐘」，若仍中斷可調高。
  - 點數增加 2 次的原因：使用者確定「點數沒跳就一定沒被認列」，短場（如 10 分鐘）等不到寶箱。
  - 追蹤中斷不算觀看（#60，11/17）：兩次分配間隔超過 `max(120 秒, 3 × 輪詢間隔)` 視為中斷（停止監控、電腦睡眠），不計觀看時間、停滯計時重新起算。原因：停止 15 分鐘再開始，整段被記給佔位者，進場寶箱的頻道立刻輪過讓位。上限跟著輪詢間隔走，因為 `check_interval` 沒有上限。
- **未輪過者優先**（指定清單→主清單順序），未輪過的佔位者保留到輪過，不中途踢掉。
- **剩下的位置依排序給已輪過者**：讓位的是排序最低者，其他台輪過後排序高者拿回位置。（取代過去的「佔位到下播」黏著規則與「前任佔位者優先」。）
- **停滯重啟**：任何佔位者 20 分鐘（`STALLED_SLOT_SECONDS`）無點數、無領取 → 關閉**所有**佔位視窗再重開（只重開一個會回到同一個壞掉的瀏覽器）。每次最多 2 次（`STALL_RESTART_LIMIT`），點數上升或領到會恢復次數。用完後：未輪過者放棄本場順位（Claude 加的保險，使用者確認保留），已輪過者照排序保留、不再重啟。時間本身不是讓位條件（使用者決定）。
  - 只在同一輪會重開時才重啟（#61，11/17）：條件和端點重啟相同（`allow_open`、`open_on_live`、trigger 模式、有選中頻道）；`track_slot_holders` 只回報，次數由 `consume_stall_restart` 在實際重啟時扣。原因：watch／idle 模式下會關窗卻不重開，次數也白扣。
- **瀏覽器沒有除錯端點**（#21）：取得埠的結果是 `profile_nondebug` 時，Windows 上仍配一個新埠放進啟動參數，啟動後再做探索。原因：舊瀏覽器可能正在結束，新瀏覽器會變成主程序；若舊瀏覽器其實還活著，它會忽略轉送過來的參數。探索失敗、或重啟後復原的視窗遇到 `profile_nondebug` 時，請分配關閉**所有**佔位視窗再重開。限制：兩次重啟至少間隔 5 分鐘，在成功 attach 之前最多 2 次，之後交給停滯重啟處理。分配只在同一輪會重開視窗時才關窗。
- **開窗後探索逾時**（#63，11/17）：不是 `profile_nondebug` 的探索失敗，30 秒後只重試 attach，最多 2 次，不關窗。原因：冷啟動慢時視窗播放但沒有 page assist，要等 20 分鐘停滯重啟、還連帶重開正常的另一台。
- **raid**（#65，11/17）：頁面網址不是該頻道時不領寶箱、ack 也不記到原頻道。
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
| 11/17 02:13 | #28 從頁面元素讀 ytInitialData（PR #39） | `2754157` | `..._20261117_pre-yt-data-element`（含 config.json） |
| 11/17 03:24 | 11/17 稽核後修正的 19 個 issue（#44–#67，PR #68–#85） | `e41ce08` | `..._20261117_pre-v1.3.3-fixes`（含 config.json） |
| 11/17 09:23 | #56 啟動補刷不算第二次離線確認（PR #88） | `7fac212` | `..._20261117_pre-startup-refresh`（含 config.json） |
| 11/17 10:46 | #91 點數位置上限的獨立開關（PR #92），部署版開啟 | `95ca461` | `..._20261117_pre-points-switch`（含 config.json） |
| 11/17 14:42 | 11/17 第二輪稽核：PR #93–#112（#49、#58、#66 與 16 個修正） | `54eda65` | `..._20261117_pre-audit-fixes`（含 config.json） |

目前部署內容等同 `main` 的 `54eda65`（v1.3.2 + 之後所有修正，版本號仍是 1.3.2）：exe 與 `_internal` 共 1060 個檔案與 `dist` 雜湊一致；`_internal\VCRUNTIME140_1.dll` 是舊版殘留（`VCRUNTIME140.dll` 被鎖住導致舊 `_internal` 沒刪乾淨），新版不使用。

11/17 14:42 部署後：hayabi_zr、jipandan 兩台 attach，另外 2 個復原視窗依上限關閉；一次 `profile_nondebug` 在 4 秒內自行恢復。前 2 分鐘 `Skipped duplicate pending action` 0 行（之前每次重啟約 170 行，PR #96），`went_live_suppressed` 每台只記 1 行（PR #97）。v1.3.2 已於 11/16 發行（tag 在 `771ab8e`）；v1.3.3 尚未發行。

11/17 03:24 部署後：兩台 7 秒內 attach；觀察紀錄第一次寫入就把 56 MB 的舊檔輪替成 `.ndjson.1`（#44）。PR #39 部署後到 03:24 約 70 分鐘，log 沒有任何 `ytInitialData missing`（之前每小時 8–16 次），#28 待 24 小時後確認關閉。09:23 重啟時啟動補刷正常完成，沒有誤判下播；3 個復原視窗都在 8 秒內 attach。

部署版 `config.json` 的 `viewer_engagement.points_priority_channels` 在 11/16 晚間由使用者清空（11/17 確認），當時點數位置分配因此停用，開播頻道各開一個視窗（11/17 08:33 起同時 3 個），第 3 台 80 分鐘沒有點數。#91 把上限改成獨立開關（`points_allocation_enabled`），清單空白時依主清單排序；舊設定沒有這個 key 時依清單是否為空推導。10:46 部署後開啟：啟動時復原的 5 個視窗關到 2 個，之後約每 10 分鐘換一次台（10:56–11:16 共 5 次）。

11/17 00:37 程式重啟過一次，exe 沒有更換，log 只有「前一個 session 沒有結束紀錄」，原因未查明（可能是使用者手動重啟，未確認）。

#21 的經過：11/16 14:36 部署時，舊瀏覽器在重啟期間仍開著，新瀏覽器（14:36:43）沒帶 `--remote-debugging-port`，兩個視窗都是 `profile_nondebug`，要手動結束專用 profile 的 Chrome 才恢復。修正部署後 17:48 重啟又遇到同樣情況：啟動自動帶埠 64163／64164，兩台 1.5 秒內 attach，不需手動處理；瀏覽器主程序帶 `--remote-debugging-port=64163`。尚未實際觀察到「瀏覽器還活著、需要關窗重開」那條路徑（log 會出現 `Twitch browser has no debugging endpoint; restarting playback windows`）。

## 部署步驟（2026-11-10 整理）
1. `uv run python build.py` → `dist\HelloStreamer\`（HelloStreamer.exe + `_internal`）。11/17 起會自動移除 bundle 內的 `__pycache__`（#43），在跑過測試的工作目錄打包也和乾淨 checkout 一致。
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
- 2026-11-17 runrunluna 斷連續觀看（VOD 2930986222，08:34–09:38）：程式全程開窗、page assist 正常，但整場 0 點數、0 寶箱，09:05 的 30 分鐘無點數 F5 也沒用；同時段 neipuduck、sumitaninasi 各 17 次點數上升。原因：優先清單在 11/16 晚間被清空，舊版以「清單非空」當分配開關，分配整個停用（08:00–10:46 沒有任何分配動作，7 次開窗全是 `open_keep`），同時開 3 台而第 3 台不被認列。不是負載問題。同期 hayabi_zr 10:18–10:46 也是 0 點數（部署後才拿到位置）。#91 部署（10:46）後 11 個開播頻道都有點數與寶箱。
  - 補救：Twitch 首頁「保住您的連續紀錄」會帶到漏掉那場的 VOD，11/18 01:15 在使用者的 Chrome 播放約 6 分鐘（VOD 第 407 秒）後提示消失、重新載入也沒再出現；連續天數未直接確認。Twitch 沒寫需要看多久，6 分鐘只是這次的觀察。

## 注意事項
- 已知平台限制：`artifacts/functional-fixes-20261106/final-claim-test-summary.json` 記錄 `claim_platform_block: IntegrityCheckFailed`。
- 根目錄有大量本機產物（logs、browser_profile、artifacts、build、dist、seen_videos.db*），不要當成程式碼讀。
- 不保證 Twitch 認列觀看、點數或 Watch Streak（README／RELEASE_NOTES 已聲明）。
- 主要文件：`README.md`、`RELEASE_NOTES.md`、`docs/`（cdp-startup、twitch-player-recovery、state-machines-and-settings、watch-streak-observation 等）。
