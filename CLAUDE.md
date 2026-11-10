# CLAUDE.md

## 工作流程
- 開工先讀 `PROGRESS.md`，再決定做什麼；使用者說「繼續」時，就從「進行中 / 下一步」開始。
- 每完成一個階段、或做出重要決策，立即更新 `PROGRESS.md`（決策要附原因）。
- 不要為了回答問題而通讀整個專案：優先查 `PROGRESS.md`，不足時才讀相關檔案。

## 專案慣例
- 測試與工具一律用 `uv run`（例：`uv run pytest -q`、`uv run ruff check`）；此機器 `python` 不可用。完整測試約 5.5 分鐘，改動小時只跑相關檔案。
- 只有 `tests/` 屬於維護中的測試；`release/`、`artifacts/`、`build/`、`dist/` 內可能有舊版副本，不要讀或修改。
- `logs/`、`browser_profile/`、`seen_videos.db*`、`config.json` 是使用者的執行資料，除非任務明確要求，不要修改或刪除。
- 本機實際運行的是 `D:\Tools\HelloStreamer\`（打包版），要看執行狀況請查那裡的 `logs/`；repo 內的 `logs/` 是舊資料。
- 完成一項工作且驗證通過後，直接提交（commit），不要再詢問使用者；訊息要適當、不混入無關檔案。推送與開 PR 仍需使用者明確指示。

## 委派規則（subagent 定義在 `.claude/agents/`）
- 搜尋、讀大量檔案、整理日誌、跑測試並回報 → `worker-haiku`。
- 規格明確的程式碼修改、批次重構、補測試 → `worker-sonnet`。
- 架構決策、需求釐清、與使用者對話 → 留在主對話。
- 委派時簡報必須包含：目標、相關檔案、完成標準、不可更動的範圍。
