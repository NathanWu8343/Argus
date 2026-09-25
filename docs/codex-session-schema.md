# Codex Session 實測分析

調查日期：2026-09-23。先完成本文件的規則確認，再實作 Reader。

## 本機資料來源

- 本機資料根目錄：`C:\Users\natha\.codex`；可由 `CODEX_HOME` 改寫。
- `sessions/YYYY/MM/DD/rollout-*.jsonl`：原始紀錄；`archived_sessions/`：封存紀錄。本次盤點為 2,016 與 120 個檔案。
- `state_5.sqlite` 的 `threads.id → rollout_path` 是目前分支的定位索引。
- `thread_history_1.sqlite` 的 `thread_turns`、`thread_items` 是 App 對話投影；`thread_realtime_items` 是語音事件。這些資料可能落後目前 rollout，不可單獨當作完整歷史。
- `session_index.jsonl` 是名稱／查找索引；`history.jsonl` 不是完整 App 對話。
- 本機安裝套件 `OpenAI.Codex 26.917.6896.0`，目前 Session 標示 core `0.155.0-alpha.16`。舊紀錄已被 `legacy_to_paginated_v1` 遷移，因此舊 `cli_version` 不代表檔案仍是舊格式。

## Schema 與排序

JSONL 每一行是一個 `{timestamp, type, payload}`。行序才是事件先後；timestamp 可能相同或倒退，不拿它重新排序。

`session_meta.payload` 包含 `id`、`session_id`、`history_mode`、`cli_version`、`originator`、`source`、`context_window`，以及可選的 `history_base`、`forked_from_id`、`forked_from_ordinal_exclusive`、`subagent_history_start_ordinal`。實測子 Agent 的 `id` 是自己、`session_id` 可能是 `parent_thread_id`；定位必須以 `id` 為準。

`event_msg.payload.type=item_completed` 的 `item` 是已完成的顯示項目，另有 `thread_id`、`turn_id`、`started_at_ms`、`completed_at_ms`。已觀察 `UserMessage`、`AgentMessage`、`Plan`、`Reasoning`、`CommandExecution`、`McpToolCall`、`ContextCompaction` 與 `Extension` 等。資料庫改用 camelCase，如 `userMessage`、`agentMessage`；不能直接假設兩者 Schema 相同。

- `UserMessage.content[]` 保存文字、`text_elements`、`image` 的 URL/data URI、`localImage` 的路徑。
- `AgentMessage.content[]` 的 `Text.text` 保存回應；`phase` 區分 commentary、final 等階段。全部保留，不只擷取 final。
- `task_started`／`turn_context` 提供 turn ID；`task_complete`／`turn_aborted` 記錄生命週期。Turn 可以中斷，不能只輸出 completed turns。
- 同 role/kind/turn/item ID 的多次更新屬同一顯示項目，保留最後內容與第一次出現位置；不同 ID 即使文字相同也不能去重。實測 `request_user_input_async` 的可見 AgentMessage 與工具結果共用 call ID，所以去重鍵不能只有 ID。
- 投影資料庫的 `rollout_ordinal` 是原始事件的零起算 ordinal，`updated_at_ordinal` 是更新位置。它不是 timestamp，也不是每個 turn 重新起算的 sequence。

## 分支、Fork、Compact

實測同一 Session 有多段 `rollout-...-<session-id>_<segment-id>.jsonl`。`history_base={thread_id,end_ordinal_exclusive,end_byte_offset}` 指向歷史段；其 `thread_id` 可能是 segment ID，並非 `threads` 表可查到的 Session ID。

重建規則：先依 `threads.rollout_path` 選目前段，遞迴讀取指定歷史段的 byte prefix，再接目前段。父段的 inherited ordinal + prefix 實際行數必須等於 `end_ordinal_exclusive`。不可把所有同 ID 檔案按日期串接。

已實測三層鏈：根段繼承 151 行；下一段繼承其前 4 行，累積 ordinal 155；再下一段繼承前 78 行，累積 ordinal 233。未被繼承的尾端屬其他分支，不可混入。

舊 Fork 會把繼承歷史直接複製到子檔，`forked_from_id` 只是來源資訊；不能再把整份父 Session 加一次。新版 `history_base` 才是明確的外部繼承邊界。`subagent_history_start_ordinal` 限定子 Agent 自己的可見歷史起點。

`compacted` 有 `message`、`replacement_history`、`replacement_history_metadata`、window ID 等。它是模型 context 替換，並非 UI 刪除舊對話。驗證集 20 個真實 Session 的 32 次 Compact 均為 `message` 空、`replacement_history` 的 compaction 正文是 `encrypted_content`；同陣列的 user/developer message 只是一部分保留訊息，不是完整摘要。Reader 標出最後邊界與不可讀狀態，提供原始對話 fallback，不宣稱與原模型 context 相同。若未來遇到可讀明文摘要，預設輸出只交接最後摘要與其後對話，避免與壓縮前原文重複。

## 工具與上下文

`response_item` 的 `function_call`／`custom_tool_call` 與 `*_output` 以 `call_id` 對應。結果可為字串或多模態 content array。一般不輸出呼叫 arguments、input 和協定外殼；只有 `execution_evidence` 附上操作名稱與參數內容（`operation`），來源為 function／custom tool call 的參數、`DynamicToolCall`／`McpToolCall` 的 tool 與 arguments、`CommandExecution` 的 command。沒有它就判斷不出結果屬於哪個操作：本機 1798 筆中斷 turn 的工具結果中，1595 筆的輸出本身看不出是哪條指令。`call_id` 與 metadata 等外殼仍不輸出。

工具結果不能一概刪除：實測 `request_user_input` 的選項答案只存在 `function_call_output`，App 投影中沒有相應 userMessage。預設輸出保留明確的互動問題與 answers；`request_user_input_async` 的 `accepted` 回執不是使用者答案。

Code mode 的 `exec` 可包住多個工具。UI 的 CommandExecution/McpToolCall 與外層 exec 結果不一定有一對一 ID；外層也可能包含程式自行運算的值。因此只有最後未完成／中斷／失敗 turn 才輸出其執行證據，不以文字相似度刪除不同來源結果。這時可能仍含內外層重複內容；已完成討論不輸出整份工具歷史。

補充實測：`realtime_item.transcript_segment` 直接有 role/text，以原始行序保留；開始／結束語音事件排除。`inter_agent_communication_metadata.trigger_turn` 是內部排程資訊；`response_item.agent_message` 則可能包含加密正文。後者不能當成安全雜訊刪除；Reader 標示 `encrypted_agent_context` 限制，同時保留其餘可讀對話。

## Structural Filter

| 資料 | 規則 |
|---|---|
| UI UserMessage / AgentMessage / Plan | 保留原文與來源；文字不做 trim、摘要或需求判斷 |
| response_item.message | paginated 中是模型鏡像；不與 UI message 重複輸出 |
| role=user 的注入資訊 | `content_item_kinds` 實測含 plugins.recommendations、agents_md.instructions、environments.environment_context；不是使用者送出的訊息 |
| Tool result / 互動問題及答案 | 明確互動問題／answers 進 conversation；最後非 completed turn 的工具結果進 execution_evidence；普通已完成 turn 的工具協定與結果不進預設輸出 |
| Reasoning / encrypted_content | 排除；不是可見對話正文 |
| token_usage_record / token_count | 排除 |
| world_state / turn_context / settings / task lifecycle | 用於結構判定，不作對話正文 |
| compacted / ContextCompaction | 標示邊界及最後摘要可讀狀態；不重播 replacement_history |
| 未知 event/item/content | 明確失敗，不靜默丟棄 |

程式碼、Markdown、引用與檔案連結都在原文中原樣保留。UI 的 local_image 可能只存路徑，但模型鏡像的 input_image 有 data URI；Reader 用 turn 與 ID／精確文字配對後補回這份資料。沒有內嵌 bytes 的已刪除圖片、原本已截斷的工具輸出無法復原，也不讀取任意引用檔案。

官方文件僅用於交叉確認 thread/turn/item 的概念；上述磁碟格式與分支邊界來自本機實測，非公開穩定 API：[Codex App Server](https://learn.chatgpt.com/docs/app-server)。
