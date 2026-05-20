# Hello Streamer v0.5.1

## 重點更新

- 新增開播 / 離線防抖保護，Twitch 或 YouTube 單次查詢短暫掉線時不會立刻判定直播結束。
- 修正 Twitch GQL 偶發回傳 `stream: null` 時，可能造成重複開播通知或誤觸發離線關閉的問題。
- 強化 YouTube TIDUS 與 fallback 監控流程，避免 LIVE 影片短暫從清單消失時切到 UPCOMING、重複通知或產生錯誤離線事件。
- 修正 YouTube fallback live 與 TIDUS live 互相接手時，舊 payload 可能被誤判為離線的情境。
- 測試補齊 Twitch / YouTube 單次抖動、連續離線確認、fallback 接手與 TIDUS payload 清理情境。
- README 已更新監控穩定性與下載檔名說明。

## 下載檔案

- Windows 請下載 `HelloStreamer-v0.5.1-windows-x64.exe`
- Linux x64 請下載 `HelloStreamer-v0.5.1-linux-x64.tar.gz`
- Raspberry Pi 64-bit 請下載 `HelloStreamer-v0.5.1-linux-arm64.tar.gz`
