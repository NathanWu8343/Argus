# Argus

Argus 是一個專為 **Claude Code** 與 **OpenAI Codex** 設計的「確認優先（Confirm-First）」防護外掛。在 AI 執行任務或呼叫任何工具前，Argus 會先用白話文提煉需求重點與假設，並在獲得你的明確確認後才放行工具執行，徹底防止 AI 誤解需求或產生非預期的程式碼變動。

## 特色與效益

- **精準對齊需求**：AI 僅根據輸入提煉最多 5 點白話需求重點與假設，杜絕「你說 A、它做 B」。
- **工具底層硬性攔截**：在確認前，Hook 會硬性擋下所有工具呼叫（檔案讀寫、終端指令等），絕不偷偷執行。
- **支援官方 Plugin 生態**：完整遵循 Claude Code 與 OpenAI Codex 官方內建外掛規範，安裝管理簡潔俐落。
- **節省 Token 與還原成本**：一次對齊共識，避免因 AI 理解偏差而需繁瑣重試或使用 `git reset` 救災。

## 適用情境

- **架構重構與跨模組修改**：改動範圍大時，防止 AI 隨意改動非目標檔案。
- **長篇或模糊的需求**：先讓 AI 整理出理解與「（假設）」清單，釐清盲點後再動工。
- **關鍵與不可逆操作**：資料庫結構、生產設定或敏感邏輯調整時，提供人工確認閘門。

## 安裝指南

Argus 支援官方內建 Plugin 流程，執行環境需具備 Node.js。

### Claude Code

啟動 Claude Code 時載入本機外掛目錄：

```bash
claude --plugin-dir /path/to/Argus
```

### OpenAI Codex

透過 Codex 官方 Plugin Marketplace 註冊並安裝：

```powershell
codex plugin marketplace add .
codex plugin add argus@argus
codex features enable default_mode_request_user_input
```

> **提示**：`default_mode_request_user_input` 讓 Default 模式支援選項卡片。如欲關閉實驗功能警告，可在 `config.toml` 中加入 `suppress_unstable_features_warning = true`。

## 使用方式

### 1. 觸發指令

在提示詞開頭輸入對應指令即可啟用 Argus 閘門：

- **Claude Code**：
  ```text
  /argus:confirm-first 幫我把登入頁改成雙欄版面
  ```
- **OpenAI Codex**：
  ```text
  $confirm-first 幫我把登入頁改成雙欄版面
  ```

### 2. 確認流程

Argus 會先覆述重點並跳出確認選項：

```text
需求重點：
1. 將現有單欄登入表單調整為雙欄網格佈局
2. 左欄保留登入輸入框與送出按鈕
3. 右欄加入品牌宣傳橫幅與說明文字
4. （假設）維持既有表單驗證與 API 串接邏輯

[以上理解正確嗎？]
- 正確，開始執行：放行閘門，AI 嚴格依覆述重點開始執行任務
- 需要修正：補充修改說明，AI 將重新整理重點再次確認
- 取消此請求：中止任務並解除閘門
```

## 運作原理

Argus 採用輕量的雙狀態機（`idle` / `pending`）：

1. **觸發進入 Pending**：收到啟動指令後進入 `pending` 狀態，並注入確認技能規則。
2. **工具調用攔截**：在 `pending` 期間，`PreToolUse` hook 會拒絕除提問工具（Claude 的 `AskUserQuestion` 或 Codex 的 `request_user_input`）以外的所有工具調用。
3. **放行回歸 Idle**：使用者選擇「正確，開始執行」或「取消此請求」後，狀態清除回歸 `idle`，放行或結束操作。

## 開發與測試

### 建置 Codex 外掛套件

若修改了共用核心或 Codex adapter，需重新建置發布目錄：

```bash
# 建置產生套件
node scripts/build-codex.js

# 檢查產生套件是否與原始碼同步
node scripts/build-codex.js --check
```

### 執行測試

```bash
node --test "tests/*.test.js"
```

### 除錯排查

設定環境變數 `ARGUS_DEBUG=1`，執行歷程與 hook stdin 將寫入各平台狀態目錄中的 `debug.log`。
