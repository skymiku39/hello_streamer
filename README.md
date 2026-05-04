# 專案初始化指南

這份指南旨在幫助使用者按照有系統、計畫性的步驟來建立一個新專案。請依照以下步驟進行，確保所有必要的配置和結構都已妥善設置。

## 目標
本專案旨在提供一個統一的框架，方便開發者快速配置並啟動新專案，避免混亂或缺少必要的環境準備。

## 初始化步驟

### 1. **項目資料夾結構**
首先，建立一個清晰的資料夾結構，以方便後續的開發和管理：

```bash
my-project/
├── project_name/       # 源代碼目錄
│   ├── main.py         # 主入口
│   └── utils.py        # 工具函數
├── tests/              # 測試目錄
│   └── test_main.py    # 測試文件
├── docs/               # 文件目錄
├── configs/            # 配置文件
│   └── settings.yaml   # 設定檔案
├── .gitignore          # 忽略文件列表
├── README.md           # 專案說明文件
├── requirements.txt    # 依賴包列表，建議添加常見依賴項如 pytest 和 requests
└── settings.env        # 環境變數
```

### 2. **初始化 Git 儲存庫**
使用 git 來版本控制專案。

```bash
git init
git add .
git commit -m "Initial commit"
```

確保 .gitignore 文件已經配置正確，避免提交不必要的文件。

### 3. **建立虛擬環境**
為了隔離專案環境，建議建立一個虛擬環境並安裝必要的依賴包。

Unix/Linux/MacOS
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
Windows
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

權限設定
```bash
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
```

### 4. **編輯專案配置**
請確保配置文件已經根據您的需求進行了編輯。您可以修改 configs/settings.yaml 來適應特定的環境和需求。

範例配置：

```yaml
database:
  host: "localhost"
  port: 5432
  user: "admin"
  password: "password"
```

### 5. **撰寫測試**
撰寫單元測試是確保程式碼質量的重要部分，請在 tests/ 目錄下新增測試用例。

```python
def test_sample():
    assert True
```

執行測試：
如果你想在不影響目錄結構的情況下進行測試，可以使用 Python 的 unittest 模組或者 pytest。以下是使用這兩種方法的步驟：

使用 unittest
在 tests/test_main.py 中撰寫測試：

```python
import unittest
from my_package.main import your_function  # 根據你的實際情況修改

class TestYourFunction(unittest.TestCase):
    def test_example(self):
        self.assertEqual(your_function(), expected_result)  # 修改這裡

if __name__ == '__main__':
    unittest.main()
```
運行測試：

在 PowerShell 中，確保你在專案的根目錄下，然後運行：

```bash
python -m unittest discover -s tests
```

使用 pytest
安裝 pytest（如果尚未安裝）：

```bash
pip install pytest
```

在 tests/test_main.py 中撰寫測試：

```python
from my_package.main import your_function  # 根據你的實際情況修改

def test_example():
    assert your_function() == expected_result  # 修改這裡
```
運行測試：

在 PowerShell 中，確保你在專案的根目錄下，然後運行：

```bash
pytest tests
```
這兩種方法都能讓你在不影響目錄結構的情況下進行測試。如果你有任何其他問題或需要進一步的幫助，請隨時聯繫我們。

### 6. **文件撰寫**
在 docs/ 目錄中撰寫專案文檔，記錄使用方式、系統架構和開發流程。

### 常見問題
如何新增依賴？ 在開發過程中若需新增依賴包，請使用以下指令安裝，並更新 requirements.txt：

```bash
pip install <package-name>
pip freeze > requirements.txt
```

如何設定不同環境的配置？ 可以根據不同環境（開發、測試、正式環境）建立不同的設定檔案，如 settings_dev.yaml、settings_prod.yaml。
```yaml
database:
  host: "localhost"
  port: 5432
  user: "admin"
  password: "password"
```

### 結論
這份指南提供了一個有計畫性的專案初始化流程，能幫助你快速且有條理地建立新專案。請確保每一步都仔細遵循，以確保專案的高效管理與維護。