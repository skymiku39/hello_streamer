# Twitch #2000 播放器恢復

對應 [Issue #15](https://github.com/skymiku39/hello_streamer/issues/15)。此功能處理已顯示的播放器錯誤；不判定網路根因，也不保證 Twitch 的觀看或獎勵入帳。

## 適用範圍

沿用頁內輔助的啟用條件：觀看輔助及頁內輔助已啟用、專用 Chromium 帳號、受管獨立 Twitch 視窗。原始碼版及 Windows 封裝版使用相同程式；不需要開啟定時刷新。

探測只讀取播放器內可見的錯誤文字，支援繁中及英文。聊天室、隱藏及畫面外的文字不構成錯誤證據。重新載入按鈕必須位於該播放器內、可操作且未被其他元素遮住；同一個 DOM 節點會在操作前再次確認。網址改變、按鈕被移除或替換時取消當次操作。

## 重試與恢復判定

1. 每頁一般約 30 秒檢查一次。確認 #2000 後先等待 5 秒，再重新探測並嘗試播放器的重載按鈕。
2. 每次操作後最多以 1 秒間隔觀察 12 秒。必須錯誤消失、同一影片可播放、未暫停或結束，而且至少 3 次取樣有連續 2 次時間前進，才回報恢復。
3. 仍有 #2000 時，後續操作分別再等待 15、30 秒。每頁最多 3 次；不確定是否送出的 CDP 操作也占用一次額度。連線重建或 DOM 暫時消失不會補回額度。
4. 按鈕消失、頁面不可判定、暫停或離線時不操作；錯誤消失但沒有播放進度仍是「尚未確認恢復」。用整頁刷新、強制播放或劇院模式切換代替重載，都不屬於此恢復流程。
5. 額度耗盡後保留提示，繼續只讀觀察。只有播放進度通過驗證，或該頁的輔助被使用者重新啟動，才重新取得額度。

恢復期間暫停該 worker 的定時刷新及寶箱操作，避免其他操作繞過上限。其他正常頁面保有自己的排程。首次已確認的劇院模式紀錄不會重設；已有的暫停狀態也不會被強制播放。

監控器的確認離線事件會禁止重試，即使沒有開啟下播自動關窗。停止 worker、關閉受管視窗或停用頁內輔助會取消待執行操作。啟動瀏覽器途中停用輔助時，啟動完成後也不會依舊設定建立 worker。

## 記錄與手動處理

log 記錄 `code=2000`、`attempt=N/3`、`action` 及 `result`。`clicked_unverified` 只代表按鈕操作完成，`media_time_advanced` 才代表本機播放恢復的證據。主畫面提示區提供重試中、尚未確認恢復、重試耗盡三種狀態，支援既有的五種介面語言。

持續失敗時，可先手動重載播放器，再檢查網路、VPN／代理及擴充套件。這些是排查方向，不能由 #2000 本身斷定原因。手動恢復後也必須通過影片時間驗證，程式才會清除提示。

## 重跑驗證

```powershell
uv sync --locked --extra dev
uv run --locked ruff check .
uv run --locked python -m compileall -q -f stream_monitor build.py scripts/verify_player_recovery.py
uv run --locked pytest -q

# 原始碼端到端檢查；請將輸出放在工作樹外。
uv run --locked python scripts/verify_player_recovery.py --output-dir D:\verification\source

# 對已建好的完整 Windows onedir 封裝重跑同一套檢查。
uv run --locked python scripts/verify_player_recovery.py --output-dir D:\verification\frozen --exe D:\build-output\HelloStreamer\HelloStreamer.exe
```

端到端檢查會開啟暫存帳號的 headless Chrome 及僅限本機的 fixture，結束後關閉自己的瀏覽器。實際 app 的 `--self-check-player-recovery` 路徑只接受指定路徑的 loopback fixture，並驗證 fixture 標記；不使用現有帳號或真實 Twitch 分頁。成功案例播放 canvas 產生的 MediaStream，讀取真實 `HTMLVideoElement.currentTime`；其餘案例驗證上限、播放未恢復、取消及離線。結果 JSON 明確標記 `source`／`frozen`、模擬故障及是否通過。

## 2026-10-09 驗證紀錄（Asia/Taipei）

驗證結果與真實 Twitch 觀察分開保存於 `D:\skymiku\hello_streamer_recovery\20261009_issue15`。環境為 Windows 11、Python 3.14.2、uv 0.9.27、PyInstaller 6.20.0；相依套件經 `uv sync --locked --extra dev` 確認。

- 完整 `uv run --locked pytest -q`：**1,262 passed，124.95 秒**。Ruff、compileall 與 `git diff --check` 全部通過，沒有刪除或放寬既有驗收斷言。
- 原始碼端到端：**5/5 通過**。恢復案例在 8.21 秒完成，影片時間到 3.099 秒；持續失敗案例在 86.17 秒到達 3 次上限。影片未恢復時保留未驗證狀態，取消及確認離線案例在超過首次重試期限後仍為 0 次點擊。證據為 `final\source-recovery.json` 及同名 `.log`。
- Windows 實際 `HelloStreamer.exe` 端到端：**5/5 通過**。恢復案例在 8.21 秒完成，影片時間到 3.050 秒；持續失敗在 86.16 秒達到 3 次上限，其餘三項結果與原始碼一致。證據為 `final\frozen-recovery.json` 及同名 `.log`。
- Windows onedir 完整性檢查及 `HelloStreamer.exe --self-check` 通過；包內 69 個 Python 原始檔雜湊與工作樹一致。候選封裝位於 `windows-final\dist\HelloStreamer`，請保留整個資料夾及 `_internal`。執行檔 SHA-256 為 `e5aa1c5cae58cf223f534998bec80c63279b090aa316f5b832199d58dd39dc17`；逐檔證據為 `final\bundle-integrity.json`。這是本機測試封裝，尚未發行。
- 真實 Twitch：01:54 左右，在隔離帳號以唯讀方式觀察 `https://www.twitch.tv/monstercat`，連續 3 次辨識到離線播放器，未出現 #2000，沒有執行重載或注入故障。證據為 `live-twitch-observation.json`。
- 本機 fixture 的通過不等於已重現使用者的原始網路故障。自然發生的 #2000、真實 Twitch 重載按鈕的恢復結果，以及網路根因仍未驗證。
