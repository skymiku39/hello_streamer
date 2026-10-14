# CDP 冷啟動與設定檔端點驗證

此流程適用於啟用頁內輔助的受管 Twitch 獨立視窗。一般開啟瀏覽器不依賴 CDP 成功。

## 啟動流程

1. 設定檔已有可用端點時，核對 `DevToolsActivePort` 的埠、瀏覽器識別路徑與 `/json/version`；不能只相信先前記住的埠。
2. 舊版使用固定埠且沒有端點檔時，Windows 透過 `psutil` 原生程序 API 確認瀏覽器使用完全相同的設定檔，且監聽埠屬於該程序；不再啟動 PowerShell。查詢不可用時回報失敗，不將未知狀態視為可用設定檔。
3. 冷啟動傳入 `--remote-debugging-port=0`，由 Chrome 自行綁定可用埠。程式不再預選、釋放固定埠後交給 Chrome。
4. 瀏覽器成功啟動後立即開始視窗追蹤，再有界等待端點檔及活躍端點。多個冷啟動請求共用設定檔時，皆解析該設定檔實際產生的端點。
5. 預查詢預設等待預算為 5 秒，啟動後端點探索為 12 秒；失敗回報狀態並保留一般瀏覽器啟動結果。頁面 target 的附加另有既有等待上限。
6. 探索期間停用輔助或關閉受管視窗會取消啟動輔助；關閉全部視窗亦適用。

Chromium 將埠與瀏覽器識別路徑寫入設定檔的行為，可對照其 [DevTools HTTP handler 原始碼](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/content/browser/devtools/devtools_http_handler.cc)。

## 驗證

在專案目錄執行：

```powershell
uv run --locked pytest -q tests/test_cdp_discovery.py tests/test_cdp_discovery_integration.py tests/test_twitch_page_assist.py
```

`test_cdp_discovery_integration.py` 使用獨立暫存設定檔與 headless Chrome，結束時僅終止測試啟動的程序。需已安裝可解析的 Chrome；固定埠程序擁有者案例限 Windows。

隔離 Chrome 首次啟動在共用 CI 主機可能超過 12 秒，因此成功連線案例使用獨立的 30 秒啟動預算；產品仍使用 12 秒探索上限，逾時回報由專項測試另行驗證。

涵蓋冷啟動、同設定檔雙啟動、暖啟動、舊固定埠擁有者、埠被不同設定檔占用、舊端點檔、未開啟 CDP、查詢不可用、逾時、探索失敗仍追蹤視窗，以及探索期間停用／關窗。

這些是本機啟動與連線驗證；不代表真實 Twitch 長時間播放、觀看入帳或新版 Windows 封裝已完成驗收。
