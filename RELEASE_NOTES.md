# Hello Streamer v0.2.4

## 修復重點

- 修復 YouTube 新版 `/streams` 頁面使用 `lockupViewModel` 時，UPCOMING 直播間沒有被辨識出來的問題。
- 修復停止監聽時，背景檢查流程可能在停止後仍送出舊直播事件，或提前把 YouTube 直播標記為已看過而漏通知的狀態一致性問題。
- 修復 YouTube fallback LIVE 與 TIDUS 恢復之間可能重複通知、錯誤抑制或離線後仍保留抑制標記的問題。
- 修復多次檢查狀態時，停用/重新啟用頻道可能沿用過期狀態而漏掉下一次開播通知的問題。

## 改善

- YouTube UPCOMING 現在會顯示倒數時間；若同一頻道有多個待機室，會顯示最近開始的那一個。
- LIVE 現在會顯示直播已持續時間；若同一頻道有多個 LIVE 項目，會顯示直播最久的那一個。
- UPCOMING / LIVE 狀態 badge 現在可以直接點擊進入對應直播間，並保留原本外觀。
- 右側連結按鈕會優先開啟目前的 UPCOMING / LIVE 直播間，沒有活動時才開啟頻道頁。
- Twitch LIVE 狀態會使用 Twitch 回傳的開播時間顯示直播時長。
