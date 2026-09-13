# argus

`/argus:confirm-first` 會先用白話文覆述需求重點，等你按下「正確，開始執行」才動手。確認之前，hook 會擋下確認題以外的所有工具，不只靠 Claude 自律。

## 使用

```
/argus:confirm-first 幫我把登入頁改成雙欄版面
```

1. Claude 不呼叫任何工具，只根據你的描述整理成最多 5 條重點。
2. 跳出確認按鈕，你有三個選擇：
   - **正確，開始執行**：放行，依最後一版覆述執行
   - **需要修正**：補充要改的地方，Claude 重新覆述
   - **取消此請求**：停手，並解除閘門

## 安裝

本機測試：

```
claude --plugin-dir D:\repo\Argus
```

需要 Node.js 在 PATH 上。hook 改動要重啟 Claude Code 才會生效。

## 運作方式

`hooks/argus.js` 只有 idle、pending 兩個狀態，pending 以 `~/.claude/argus/<session_id>.pending` 存在表示。

| 事件 | 行為 |
|---|---|
| UserPromptSubmit | prompt 以 `/argus:confirm-first` 開頭 → pending |
| PreToolUse | pending 時 deny AskUserQuestion 以外的所有工具，包含 MCP 工具 |
| PostToolUse (AskUserQuestion) | header 為 `argus` 的題目按「正確…」或「取消…」→ idle；自由輸入不算按鈕 |
| Stop | pending 時擋一次，要求以按鈕收尾；第二次放行但保留 pending |
| SessionStart | 清掉超過一天的殘留狀態檔 |
| SessionEnd | 刪除本 session 的狀態檔 |

hook 腳本自身出錯時一律放行，不會卡住一般使用。

語言分工：使用者看到的覆述、確認題目與按鈕一律繁體中文；plugin 內容（SKILL.md 說明、hook 給 Claude 的提示、註解、測試）除本 README 外皆為英文。

## 已知限制

- 每次工具呼叫都會啟動一次 hook 腳本，沒在用 confirm-first 時也一樣。Windows 上實測每次約 0.17 秒。
- hook 只檢查題目 header 與按鈕 label，不檢查題目文字。按下「正確，開始執行」前先看清楚問的是什麼。
- 在確認對話框按 Esc 或中斷回合，閘門不會解除。之後工具仍會被擋，Claude 會再請你確認，按「取消此請求」即可解除。
- `claude -p` 等非互動模式沒有 AskUserQuestion，確認流程走不完，該 session 的工具會一直被擋到結束。

## 測試

```
node --test "tests/*.test.js"
```

- 第 1 層：狀態機 `decide()` 在各事件下的轉移與輸出
- 第 2 層：以模擬 stdin 執行腳本，檢查輸出與狀態檔

排查問題時可設環境變數 `ARGUS_DEBUG=1` 再啟動 Claude Code，每個事件的 stdin 與腳本錯誤會寫進 `~/.claude/argus/debug.log`。log 含完整 prompt 與寫入內容，也不會自動輪替，查完記得關掉並刪除。`ARGUS_STATE_DIR` 可改狀態目錄。
