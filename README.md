# argus

Argus 會先用白話文覆述需求重點，等你確認才動手。支援 Claude Code 與 Codex，共用確認流程與 hook 狀態機。

Claude Code 使用 `/argus:confirm-first`，確認前 hook 會擋下確認題以外的所有工具。Codex 使用 `$confirm-first`，hook 保護支援的本機工具呼叫；平台差異見下方說明。

## Claude Code 使用

```
/argus:confirm-first 幫我把登入頁改成雙欄版面
```

1. Claude 不呼叫任何工具，只根據你的描述整理成最多 5 條重點。
2. 跳出確認按鈕，你有三個選擇：
   - **正確，開始執行**：放行，依最後一版覆述執行
   - **需要修正**：補充要改的地方，Claude 重新覆述
   - **取消此請求**：停手，並解除閘門

## Claude Code 安裝

本機測試：

```
claude --plugin-dir D:\repo\Argus
```

需要 Node.js 在 PATH 上。hook 改動要重啟 Claude Code 才會生效。

## Codex 安裝與使用

`codex/plugins/argus` 是完整、可獨立安裝的產生套件，應與共用來源一起提交。內附 marketplace 名稱為 `personal`。安裝前先確認既有同名來源（包含 `~/.agents/plugins/marketplace.json`）是否指向本 repo 的 `codex` 目錄；若指向其他位置，先停止，不要覆寫來源或直接執行 `argus@personal` 安裝，以免影響原本的外掛。這份 repo 不會替你改名或遷移已安裝的 marketplace。

確認沒有來源衝突後，從此 repo 根目錄註冊內附 marketplace 並安裝：

```powershell
codex plugin marketplace add ./codex
codex plugin add argus@personal
```

安裝後開新任務，依 Codex 提示檢視並信任外掛 hooks；需要 Node.js 在 PATH 上。

```text
$confirm-first 幫我把登入頁改成雙欄版面
```

- 保留手動啟用：Codex 的 `agents/openai.yaml` 設定 `allow_implicit_invocation: false`。
- 技能不指定問題工具。Codex 有可用的選項式問題工具就用，沒有就以純文字列出三個選項並結束回合，等你回覆恰好是選項文字的訊息。
- 附帶修正、未送出選項、預選與逾時都不算確認。
- hooks 在 prompt 以 `$confirm-first`、`$argus:confirm-first` 或對應技能連結（例如 `[$confirm-first](path/to/SKILL.md)`）開頭時啟動；放在 prompt 其他位置的提及只受技能指示約束。
- Codex 的 Stop 不自動催促，讓非同步問題能等待下一則回覆；等待期間仍保留 pending。
- 狀態存於 `$CODEX_HOME/argus`，未設定時為 `~/.codex/argus`，與 Claude Code 分開。若設定 `ARGUS_STATE_DIR`，Claude 使用其下的 `claude/`，Codex 使用 `codex/`；狀態檔、過期清理與 `debug.log` 都限於各自目錄。`ARGUS_DEBUG` 仍可使用。
- hooks 未獲信任、被停用或不受支援的工具路徑，只能依賴技能指示。託管 WebSearch 等不受攔截，不能把這個閘門視為完整安全邊界。參考 [Codex hooks 官方規格](https://learn.chatgpt.com/docs/hooks)。

## 共用與維護

狀態機與 hook 主程式在 `hooks/core.js`，不含任何工具專屬內容。各工具的差異寫成一個 adapter 物件交給 core：啟動語法、確認工具名稱、答案格式、狀態目錄與工具命名空間、給模型的提示文字，以及是否阻擋回合結束（Codex 的答案可能在下一則 prompt 才到，不能擋）。兩邊都接受整則 prompt 恰為選項文字的回覆，規則寫在 core，不依工具而異。

新增工具時，使用獨立的 adapter、唯一的 `stateNamespace` 與獨立預設目錄。共用 core 的修正需跑過所有工具測試；安裝套件各自攜帶 core，不會在執行時讀取另一工具的安裝目錄。`CODEX_HOME` 應指向 Codex 專用目錄，勿設為 Claude 的設定目錄。

舊版若使用 `ARGUS_STATE_DIR`，升級後不再讀取原目錄頂層的 pending 與日誌，也不會自動搬移或刪除它們，避免把其他工具的狀態帶入。請先完成或取消既有確認流程，再重啟工具使用新版。

根目錄就是 Claude Code 套件：`hooks/argus.js` 是 Claude 的 adapter。`skills/confirm-first/SKILL.md` 兩邊完全共用，只描述要問什麼、什麼算確認，不提任何一家的工具名稱，由各工具的模型自己選擇提問方式。Codex 專屬來源放在 `codex/src/`：

- `argus.js`：Codex 的 adapter
- `core.js`：三行轉發檔，讓 adapter 在 repo 內能直接被測試；產生物放的是真正的 `hooks/core.js`，這個檔不會被複製
- `openai.yaml`、`plugin.json`：Codex 的技能與外掛介面欄位

`scripts/build-codex.js` 把兩邊組成 `codex/plugins/argus`：複製 core、Codex adapter 與技能，技能只移除 Claude 專屬的前置欄位，hooks.json 的變數換成 `PLUGIN_ROOT`、PostToolUse matcher 依 adapter 的確認工具產生。版本與作者從 `.claude-plugin/plugin.json` 讀取；發版時仍須同步 `.claude-plugin/marketplace.json` 的版本。產生內容不依賴 repo 外部檔案或符號連結。

```powershell
node scripts/build-codex.js
node scripts/build-codex.js --check
node --test "tests/*.test.js"
```

提交來源變更時，一併提交重新產生的 `codex/plugins/argus`。不要直接修改產生檔。更新已安裝外掛時，依 Codex 的 marketplace 更新／重新安裝流程操作，再開新任務並重新信任有變更的 hooks。

## 運作方式

`hooks/core.js` 只有 idle、pending 兩個狀態。以下是 Claude Code 行為，pending 以 `~/.claude/argus/<session_id>.pending` 存在表示；Codex 差異如上。

| 事件 | 行為 |
|---|---|
| UserPromptSubmit | prompt 以 `/argus:confirm-first` 開頭 → pending；pending 時整則 prompt 恰為「正確，開始執行」或「取消此請求」→ idle |
| PreToolUse | pending 時 deny AskUserQuestion 以外的所有工具，包含 MCP 工具 |
| PostToolUse (AskUserQuestion) | header 為 `argus` 的題目選「正確，開始執行」或「取消此請求」→ idle；其他輸入維持 pending |
| Stop | pending 時擋一次，要求以按鈕收尾；第二次放行但保留 pending |
| SessionStart | 清掉超過一天的殘留狀態檔 |
| SessionEnd | 刪除本 session 的狀態檔 |

hook 腳本自身出錯時一律放行，不會卡住一般使用。

語言分工：使用者看到的覆述、確認題目、按鈕、介面文案與建置訊息使用繁體中文；SKILL.md 說明、hook 給模型的提示、程式註解與測試名稱使用英文。

## 已知限制

- 每次工具呼叫都會啟動一次 hook 腳本，沒在用 confirm-first 時也一樣。Windows 上實測每次約 0.17 秒。
- hook 只檢查題目 header 與按鈕 label，不檢查題目文字。按下「正確，開始執行」前先看清楚問的是什麼。
- 在確認對話框按 Esc 或中斷回合，閘門不會解除。之後工具仍會被擋，Claude 會再請你確認，按「取消此請求」或直接回覆這句即可解除。
- `claude -p` 等非互動模式沒有 AskUserQuestion，確認流程走不完，該 session 的工具會一直被擋到結束。

## 測試

```
node --test "tests/*.test.js"
```

- 第 1 層：狀態機 `decide()` 在各事件下的轉移與輸出
- 第 2 層：以模擬 stdin 執行腳本，檢查輸出與狀態檔
- Codex 測試也會檢查產生套件與共用來源是否同步，避免漏掉重新產生。
- 隔離測試會讓兩個工具共用 `ARGUS_STATE_DIR` 與 session ID，驗證啟用、確認、結束、過期清理與日誌不互相影響。

排查問題時可設環境變數 `ARGUS_DEBUG=1` 再啟動 Claude Code，每個事件的 stdin 與腳本錯誤會寫進 `~/.claude/argus/debug.log`。log 含完整 prompt 與寫入內容，也不會自動輪替，查完記得關掉並刪除。`ARGUS_STATE_DIR` 可改狀態根目錄，各工具再使用自己的子目錄。
