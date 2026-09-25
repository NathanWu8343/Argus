# Session Reader v2：實測結論與輸出設計

本文件先確認本機 persistence，再定義實作規則。實測環境：Windows Codex App 26.917.6896.0，`C:\Users\natha\.codex`，2026-09-23。原始結構細節見 [schema 調查](codex-session-schema.md)。

## 補查 Compact 與中斷

- 105 個 Session 驗證集內有 20 個真實 Compact Session，共 32 筆 `compacted`。每筆的 `replacement_history` 都含 `type=compaction`，正文是 `encrypted_content`；`message` 均為空。`replacement_history` 同時有保留的 user/developer message，不能把那些訊息誤認成完整摘要。`guardian_history` 也不是新的對話。
- `compacted` 有 `window_id`、`previous_window_id`、`window_number`、`replacement_history`、`retained_context` 等；每次 Compact 的 rollout ordinal 就是可靠邊界。多次 Compact 以最後一筆為目前 context 邊界。原始 UI 對話在歷史鏈中仍可讀；替代摘要本機不可解密，因此無模型 Reader 不能宣稱還原 Codex 當時完整模型 context。`compacted.message` 不被推定為摘要；只有明確的 compaction item 正文可用作可讀摘要。
- Compact 之後發生 Fork 時，`history_base` 的 byte/ordinal prefix 決定實際繼承內容。應先重建目前分支，再找其最後 Compact；不從舊分支讀額外事件。
- `task_started` 建立 turn；`task_complete` 可含 `error`，有 error 時是失敗而非成功；`turn_aborted.reason=interrupted` 是中斷。也可能只有 `task_started` 而沒有終止事件，代表快照時未完成。資料庫 `thread_turns.status` 在本機有 completed 3540、failed 123、inProgress 58、interrupted 278；quota 失敗在 rollout 的 `task_complete.error` 也有保存，所以不必讓 runtime 依賴投影資料庫。
- 真實 quota 案例 `01a03d6d-cb6e-7570-acee-bd7eaebb7b46`：最後 turn 做完多個命令，最後 `task_complete` 帶 `usage_limit_exceeded`，沒有 final assistant message。真實 Compact 後中斷案例 `01a0c7ec-fabd-7033-a482-df25d0d355d7` 與 `01a0b8e3-0505-7011-a998-3daf113ff671` 都有 `turn_aborted`；這兩個中斷 turn 沒有已保存的命令結果。故另以相同實測 Schema 的合成 fixture 驗收 Compact → 執行 → 中斷。

## v2 Contract 決策

`read_session(id)` 與預設 CLI 輸出 `schema_version=2` 的 agent-neutral JSON。`conversation` 只含可見 user/assistant 訊息、計畫、語音逐字稿，以及明確記錄的互動問題/回答。保留原文、內容區塊、序號、turn、時間；不輸出 Codex tool 名稱與事件名稱。

`compact.boundaries` 列出每次邊界；`compact.latest.summary` 明示 `available`、`encrypted` 或 `absent`。若最後摘要是可讀明文，`conversation` 從最後邊界之後開始，`compact.latest.summary.content` 提供邊界前 context；先前原始紀錄只在 `--raw` 供稽核。若摘要加密或缺失，`conversation` 提供完整可讀原始對話，`context_basis=raw_fallback`；`context_completeness=summary_unavailable` 明示它不等同原模型 context。絕不重播 `replacement_history` 當新訊息，也不在預設輸出同時附上明文摘要和壓縮前全文。

`state` 包含最後 turn 的 `completed`、`failed`、`interrupted`、`incomplete`，原紀錄明確提供的錯誤類別與訊息，以及依序列出每個 turn 狀態的 `turns`。`execution_evidence` 只在最後 turn 非 completed 時包含該 turn 的已保存結果與尚無結果的操作；保留檔案變更、處理程序的 exit code/stdout/stderr、其他輸出及錯誤，避免根據文字猜測測試/建置用途。每筆結果附上產生它的 `operation`（名稱與參數內容），這是唯一輸出工具名稱的地方，只用來判讀結果，不屬於交接內容。`artifacts` 列出已記錄的檔案變更位置與狀態，表示歷史事實，不宣稱磁碟上的目前內容。普通已完成 Session 不輸出整份 tool history。

`--raw` 輸出含 Codex-specific schema/provenance 的診斷資料，`--inspect` 只輸出計數和狀態。`--raw` 不是 Handoff Contract。解析器遇到未知 schema 或損壞的完整 JSONL 行會明確失敗；活動 head 尾端未換行的 fragment 可以忽略，但輸出必須標記 `source_completeness=partial`。繼承段的 byte boundary 一律嚴格驗證。
