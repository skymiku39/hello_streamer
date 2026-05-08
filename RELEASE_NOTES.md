# Hello Streamer v0.3.1

## 發布重點

- 新增 Linux ARM64 發布產物，提供 Raspberry Pi 64-bit 與其他 ARM64 Linux 桌面環境使用。
- Release workflow 改用 GitHub-hosted `ubuntu-24.04-arm` 原生 ARM runner 建置，不再透過 QEMU 模擬。
- GitHub Release 現在會同時提供 Windows x64、Linux x64、Linux ARM64 三種下載檔。

## 改善

- CI 新增 Ubuntu ARM64 檢查，讓 ARM 架構問題能在 pull request / push 階段提早發現。
- README 補上 ARM64 下載檔名稱與 Raspberry Pi 使用提示。
