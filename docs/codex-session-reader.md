# Codex App Session Reader

**Contract：`Codex Session ID → Agent-neutral Normalized Session Context`。** 執行時只需 Python 3.11+ 標準函式庫與本機 Session；不需要 Codex、登入、網路、API key 或任何模型。Reader 只做 Parse → Order → Structural Filter → Normalize，不摘要、不推論需求優先序、決策或下一步，也不做 Agent tool mapping。

## 執行與輸入

```powershell
python -B D:\repo\Argus\scripts\codex_session_reader.py <完整-Session-UUID>
python -B D:\repo\Argus\scripts\codex_session_reader.py <完整-Session-UUID> --output D:\handoff\context.json
python -B D:\repo\Argus\scripts\codex_session_reader.py <完整-Session-UUID> --inspect
python -B D:\repo\Argus\scripts\codex_session_reader.py <完整-Session-UUID> --raw --output D:\handoff\diagnostic.json
```

輸入必須是完整 UUID。資料根目錄優先序：`--codex-home` → `CODEX_HOME` → `~/.codex`。預設輸出 UTF-8 JSON 至 stdout；`--output` 成功後原子替換，父目錄須存在，且不可位於 Codex 資料根目錄內。Python 可呼叫 `read_session(session_id, home=None)`，得到同一個 dict。`--inspect` 只顯示計數與狀態；`--raw` 輸出原始 JSONL 與來源，僅供診斷，**不是** Handoff Contract。

## Output v2

```json
{
  "schema_version": 2,
  "session": {"id": "00000000-0000-4000-8000-000000000001", "created_at": "2026-09-23T00:00:00Z", "working_directory": "D:\\repo"},
  "state": {"status": "interrupted", "last_turn": {"id": "turn-id", "status": "interrupted", "position": 40, "reason": "interrupted"}, "turns": [{"id": "turn-id", "status": "interrupted"}], "source_completeness": "complete"},
  "context_basis": "raw_fallback",
  "context_completeness": "summary_unavailable",
  "compact": {"count": 1, "boundaries": [{"position": 30, "timestamp": "2026-09-23T00:00:00Z"}], "latest": {"position": 30, "timestamp": "2026-09-23T00:00:00Z", "summary": {"availability": "encrypted"}}},
  "conversation": [{"sequence": 1, "position": 3, "turn_id": "turn-id", "timestamp": "2026-09-23T00:00:00Z", "role": "user", "kind": "message", "content": [{"type": "text", "text": "請執行測試"}]}],
  "supporting_context": [],
  "execution_evidence": [{"position": 44, "timestamp": "2026-09-23T00:00:00Z", "kind": "process_result", "operation": {"name": "command", "input": "pytest"}, "content": [{"type": "data", "value": {"command": "pytest", "exit_code": 1, "stdout": "1 failed"}}]}],
  "artifacts": [{"path": "D:/repo/a.py", "turn_id": "turn-id", "status": "completed", "last_recorded_position": 42}],
  "limitations": ["只包含已持久化的紀錄；附件路徑及檔案變更不代表目前磁碟狀態。"]
}
```

| 欄位 | 消費端規則 |
|---|---|
| `conversation` | 主要交接資料。依 1 起算的 `sequence` 讀取。含 user/assistant message、plan、語音逐字稿、明確的互動問題／答案。`position` 是來源行序，timestamp 不作排序。原文、換行、程式碼、Markdown、連結與圖片不改寫。 |
| `context_basis` | `raw_conversation`：未 Compact；`latest_compact`：最後摘要可讀，conversation 只含其後訊息；`raw_fallback`：最後摘要不可讀，提供全部可讀原始對話。 |
| `compact` | 全部邊界與最後摘要的 `available`／`encrypted`／`absent`。只有 `available` 有 `summary.content`。不重播 `replacement_history`。多次 Compact 只採最後一份摘要。 |
| `context_completeness` | `available`、`summary_unavailable` 或 `encrypted_agent_context`。後兩者表示存在無法讀取的背景；另見 `limitations`。 |
| `state` | 最後 turn 的 `completed`、`failed`、`interrupted`、`incomplete`，或無 turn 時 `unknown`。失敗保留錯誤類別／原文。`turns` 依序列出目前分支每個 turn 的 id 與狀態（rollback 移除的不列）。活動 head 未寫完尾行時 `source_completeness=partial`。 |
| `execution_evidence` | 只在最後 turn 非 completed 時輸出其已保存結果與沒有結果的操作。保留 process exit/stdout/stderr、檔案變更及其他結果；不判斷它們是哪種工作。來源有獨立操作紀錄時附 `operation: {name, input}`（工具名稱與參數內容，JSON 參數解析成 object），用來判讀結果屬於哪個操作；檔案變更、附件與沒有操作紀錄的結果不附。 |
| `artifacts` | 每一路徑最後一次明確 FileChange 的位置／狀態，表示歷史紀錄，不宣稱磁碟目前狀態。 |
| `supporting_context` | 可讀的 Agent 間訊息、Hook 或 Review，與 user/assistant conversation 分開。密文只標記不可讀。 |

`content` 區塊可為 `text`（原文，可有 annotations）、`image`（path、URL 或 data URI）與 `data`（結構化值）。`interaction_response` 是明確的選項回答；一般工具的 accepted 回執不會冒充使用者。已完成討論的工具歷史不放入主要對話。

## 定位、排序與 Filter

1. 唯讀查詢 `state_5.sqlite.threads.id → rollout_path` 定位目前分支，並驗證 `session_meta.id`。沒有索引時只接受唯一、身分相符的候選，絕不猜最新檔案。
2. 依 `history_base` 的 byte prefix 與 ordinal 重建繼承鏈。Fork 已複製的歷史不再接一次；`subagent_history_start_ordinal` 排除子 Agent 的繼承背景。
3. JSONL 行序是可靠事件序。同 item ID 更新採最後內容與首次位置；模型鏡像只按明確 turn／ID／精確文字配對。rollback 僅移除明確撤回的尾端 turns。
4. `item_completed` 的 UserMessage／AgentMessage／Plan 與語音逐字稿成為對話；互動問題及明確 answers 也保留。中斷回合結果移到 `execution_evidence`。FileChange 路徑進 `artifacts`。
5. 排除 reasoning、token usage、UI 狀態、settings、生命週期協定、一般 tool invocation、模型訊息鏡像及 `guardian_history`；生命週期只用來判定 `state`。未知 event/item/content 明確報錯。

本機實測的 Compact 與中斷語意見 [設計與證據](codex-session-reader-v2-design.md)，完整 Schema 見 [調查紀錄](codex-session-schema.md)。

## 錯誤、測試與版本風險

成功退出 0；讀取／資料錯誤退出 1，stderr 為 `{"error":{"code":"…","message":"…"}}`，stdout 不輸出半份 JSON，既有 `--output` 不改動。參數錯誤退出 2。常見碼：`invalid_session_id`、`session_not_found`、`ambiguous_session`、`index_error`、`identity_mismatch`、`invalid_history`、`missing_history`、`incomplete_record`、`invalid_json`、`unsupported_schema`、`source_changed`、`invalid_output`、`io_error`。活動 head 的未換行尾行會忽略並標記 partial；繼承邊界或完整行損壞仍報錯。

```powershell
python -B -m unittest discover -s tests -p test_codex_session_reader.py -v
python -B scripts/validate_codex_sessions.py <Session-ID-1> <Session-ID-2>
```

單元測試使用臨時合成 JSONL/SQLite；真實驗證器另行重讀來源 byte prefix，比對 SHA-256、角色、原文、數量與順序。結果見 [驗證報告](codex-session-validation.md)。

- 支援本機觀察到的 paginated JSONL、`state_5.sqlite` 及遷移至此格式的舊 Session；未遷移 legacy 或未知 schema 明確拒絕。Windows App 實測版本 `26.917.6896.0`；持久化 Schema 不是公開穩定 API。
- 本機驗證集 32 筆 Compact 的摘要皆為密文。Reader 不解密，也不宣稱 raw fallback 等同原模型 context。明文摘要分支由合成測試驗證，本機未找到真實明文案例。
- 加密的 Agent 間訊息、未寫入的串流、已刪除附件及工具截斷內容無法還原。`artifacts` 不是磁碟快照；命令造成但未被明確記錄的外部變更不能推測。
- 某些 nested 工具結果缺少共同 ID；未完成回合可能包含重複或大型圖片。Reader 不依語意裁切，大型 Session 會佔用較多記憶體。
