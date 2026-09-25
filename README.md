# Argus

Argus 是一套給 **Claude Code** 與 **OpenAI Codex** 使用的 AI 協作工具包，以官方 Plugin 形式安裝。目前包含：

| 工具 | 用途 | 支援平台 |
| --- | --- | --- |
| [confirm-first](#confirm-first確認優先) | 動手前先用白話覆述需求重點，你確認後才放行工具 | Claude Code、OpenAI Codex |
| [session-relay](#session-relay接手-codex-session) | 在 Claude Code 接手 Codex App Session 的工作，Codex 額度用完也能用 | Claude Code |

## 安裝指南

執行環境需求：

- **Node.js**：confirm-first 的 Hook 使用。
- **Python 3.11+**：session-relay 使用，只需標準函式庫。

### Claude Code

啟動 Claude Code 時載入本機外掛目錄，所有工具都會一起載入：

```bash
claude --plugin-dir /path/to/Argus
```

### OpenAI Codex

透過 Codex 官方 Plugin Marketplace 註冊並安裝（目前提供 confirm-first）：

```powershell
codex plugin marketplace add .
codex plugin add argus@argus
codex features enable default_mode_request_user_input
```

> **提示**：`default_mode_request_user_input` 讓 Default 模式支援選項卡片。如欲關閉實驗功能警告，可在 `config.toml` 中加入 `suppress_unstable_features_warning = true`。

## confirm-first：確認優先

在 AI 執行任務或呼叫任何工具前，先用白話文提煉需求重點與假設，並在獲得你的明確確認後才放行工具執行，防止 AI 誤解需求或產生非預期的程式碼變動。

### 特色與效益

- **精準對齊需求**：AI 僅根據輸入提煉最多 5 點白話需求重點與假設，杜絕「你說 A、它做 B」。
- **工具底層硬性攔截**：在確認前，Hook 會硬性擋下所有工具呼叫（檔案讀寫、終端指令等），絕不偷偷執行。
- **節省 Token 與還原成本**：一次對齊共識，避免因 AI 理解偏差而需繁瑣重試或使用 `git reset` 救災。

### 適用情境

- **架構重構與跨模組修改**：改動範圍大時，防止 AI 隨意改動非目標檔案。
- **長篇或模糊的需求**：先讓 AI 整理出理解與「（假設）」清單，釐清盲點後再動工。
- **關鍵與不可逆操作**：資料庫結構、生產設定或敏感邏輯調整時，提供人工確認閘門。

### 使用方式

在提示詞開頭輸入對應指令即可啟用閘門：

- **Claude Code**：
  ```text
  /argus:confirm-first 幫我把登入頁改成雙欄版面
  ```
- **OpenAI Codex**：
  ```text
  $confirm-first 幫我把登入頁改成雙欄版面
  ```

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

### 運作原理

採用輕量的雙狀態機（`idle` / `pending`）：

1. **觸發進入 Pending**：收到啟動指令後進入 `pending` 狀態，並注入確認技能規則。
2. **工具調用攔截**：在 `pending` 期間，`PreToolUse` hook 會拒絕除提問工具（Claude 的 `AskUserQuestion` 或 Codex 的 `request_user_input`）以外的所有工具調用；Claude 另外放行用來載入 `AskUserQuestion` 的 `ToolSearch`。
3. **放行回歸 Idle**：使用者選擇「正確，開始執行」或「取消此請求」後，狀態清除回歸 `idle`，放行或結束操作。

## session-relay：接手 Codex Session

在 Claude Code 接手 Codex App Session 的工作。直接讀取本機的 Codex Session 紀錄，不需要 Codex 可用，額度耗盡、無法登入時也能接手。

### 特色與效益

- **不佔主對話 Context**：原始紀錄只由隔離的 Sub-agent 讀取，主對話只拿重建後的 `handoff.md`，長 Session 也不會塞爆 Context。
- **交接的是目前狀態**：像 `/compact` 一樣壓縮成接續用的摘要，但只留目前有效的內容：後面的決定蓋過前面的；中斷的 Session 依執行紀錄判斷哪些真的完成、哪些只做到一半；推測的內容標「（推定）」，可能沒做完或待確認的標「（需確認）」。
- **先確認再接手**：列出 5 點以內的重點讓你確認或修正，確認後才以這份內容繼續工作。
- **私人紀錄不進版控**：來源與交接檔都留在專案的 `.claude/handoffs/<id>/`，並自動加上 `.gitignore`，不會被 commit。

### 使用方式

```text
/argus:session-relay <Codex-Session-UUID>
```

流程是 **Read → Reconstruct → Confirm → Promote → Continue**：

1. **Read**：讀出 Session，寫成 `source.json`（完整內容）與 `source.md`（可讀版）。
2. **Reconstruct**：隔離的 Sub-agent 讀完整份來源，重建 Session 結束當下的狀態，寫成 `handoff.md`。
3. **Confirm**：列出重點，選擇「正確，開始接手」、「需要修正」或「取消交接」。
4. **Promote → Continue**：確認後 `handoff.md` 標記為 `verified`，Claude Code 以它為準接續工作；需要細節時再回頭查來源。

設計與驗收見 [Handoff 文件](docs/handoff-skill.md)。

### 單獨使用 Session Reader

讀取步驟也可以單獨執行，把 Session 轉成與 Agent 無關的 JSON，供其他工具交接使用：

```powershell
python -B scripts/codex_session_reader.py <Session-ID> --output D:\handoff\context.json
```

執行契約、錯誤碼與測試方式見 [Session Reader 文件](docs/codex-session-reader.md)；本機 Schema 調查見 [分析紀錄](docs/codex-session-schema.md)。

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
# confirm-first 與外掛套件
node --test "tests/*.test.js"

# session-relay
python -B -m unittest discover -s tests
```

### 除錯排查

設定環境變數 `ARGUS_DEBUG=1`，confirm-first 的執行歷程與 hook stdin 將寫入各平台狀態目錄中的 `debug.log`。
