# session-relay Skill（V1：Codex App → Claude Code）

`/argus:session-relay <Codex-Session-UUID>`：在 Claude Code 接手 Codex App Session 的工作。流程是 **Read → Reconstruct → Confirm → Promote → Continue**。來源 Agent 可以完全不可用（例如 Codex 額度已耗盡）：不呼叫 Codex、不 Fork、不要求 Codex Summary，也不做 Tool Mapping。

## 三種 Context

| Context | 內容 | 誰讀 |
|---|---|---|
| Source Context | `source.json`（Reader 完整輸出）與 `source.md`（給 Sub-agent 讀的版本） | 只有隔離的 `argus:session-relay-reconstructor` |
| Full Handoff Context | `handoff.md`：重建後的 Current Authoritative State | Main Agent；確認後是它的工作依據 |
| Confirmation View | 由 `handoff.md` 產生、最多 5 點的白話重點 | 只給使用者確認 |

Main Agent 不讀 `source.*`。完整 Context 給 Agent，精簡重點給使用者；兩者不共用同一份內容。

## 流程

1. **Read**：Skill 載入時以 `` !`…` `` 預先執行 `skills/session-relay/scripts/prepare_source.py <id>`，輸出直接放進 Skill 內容，Main Agent 不用再呼叫一次 Bash。`allowed-tools` 預先放行這個指令（pattern 要含路徑的引號）；指令失敗時以 `|| true` 把錯誤 JSON 交給 Main Agent；shell 執行被停用時，Main Agent 自己執行同一個指令。腳本以 subprocess 呼叫 [Session Reader](codex-session-reader.md) CLI，只依它的 Contract 互動（JSON、exit code、stderr 錯誤 JSON），並寫入專案內 `.claude/handoffs/<id>/`。腳本在目前工作目錄執行，不先 `cd`，否則資料會寫到外掛目錄。該目錄自帶內容為 `*` 的 `.gitignore`，私人對話不會被 commit。stdout 只印路徑、數量與最後狀態。
   - `source.md` 保留完整對話原文，並依 position 插入 Compact 邊界；例外是 data URI 與整段 base64 換成標記、ANSI 色碼移除。entry 以 `T<n>` 標示所屬 turn，時間只標在每個 turn 的第一筆，不列來源 position（引用一律用 `#n`／`S#n`／`E#n`）。標頭列出沒有正常完成或沒留下對話的 turn（原本只在 `source.json` 的 `state.turns`），以及截斷處數；沒有截斷時寫明不需要查 `source.json`。
   - Evidence 每筆先列產生它的操作（前 200 字），結果放在 code fence 裡並改成 `key: value`。檔案變更與最後 10 筆有結果的紀錄完整呈現，其中超過 2,400 字元的字串只留頭 800、尾 1,200；更早的只留狀態與輸出開頭 150、結尾 300 字元（shell 的 exit code 在第一行）。supporting 沿用 2,400／800／1,200。每處截斷都標出 `source.json` 的完整位置。
   - 實測 15.7 MB 的額度耗盡 Session 轉成約 115 KB（分層前約 175 KB）；本機 181 個中斷 Session 合計少 28.9%，全部 1919 個 Session 少 13.6%。
   - 跟來源 Agent 有關的只有 `read_source()` 一處；之後新增其他來源，只要輸出同一份 normalized contract（schema_version 2），後面流程都能共用。
2. **Reconstruct**：Sub-agent（`model: sonnet`、`effort: medium`）像 `/compact` 一樣，把來源壓縮成 Main Agent 接續用的 `handoff.md`。它讀完整份 `source.md`（只在截斷處依指標查 `source.json`；讀完的檔案不再 Grep；只抽查沒逐筆讀的範圍要寫進 Source Limitations），寫出 `handoff.md`，回給 Main Agent 的只有路徑與最不確定的三點。判斷規則：
   - 後面的明確資訊覆蓋前面；使用者決定，Assistant 只是提議；Supporting Context 不是使用者指示。
   - `raw_fallback` 時讀完整原始對話，Compact 邊界只是提示；`latest_compact` 時摘要是較早快照，之後的對話優先。
   - `failed`／`interrupted`／`incomplete` 時依 execution evidence 判斷進度：已完成（有證據）、可能部分完成（`（需確認）`）、未開始。「開始做」不等於「完成」，沒有結果的操作不算完成。
   - 交接 State／Intent／Result／Pending，不交接來源 Agent 的工具、事件、Skill 或 Workflow；操作用 `E#n` 加白話描述，不寫工具名稱。同一件事只寫一次；Current State 只寫一句，不能比 Files & Artifacts、Pending 說得更完成。需求用使用者原話，長篇只引用訂出要求的句子；Source Limitations 只寫來源缺什麼，不寫讀了或查了哪些檔；正文不提 `source.md`／`source.json` 與其欄位。
3. **Confirm**：Main Agent 依序從 Requests & Intent、Decisions & Constraints、Current State、Next／Pending 挑最多 5 點；Sub-agent 回報最沒把握、且影響下一步的點優先放入（這些點應已在 `handoff.md` 標（推定）或（需確認）；沒標的話 Main Agent 在確認前只補標記、不改字句；已列在 Pending 的待決問題不用標）。用編號列出（修正時可以說「第 3 點」），一點一句、只講一件事，約 40 字以內、不用分號；可以省略細節，但不能寫成跟 `handoff.md` 矛盾，也不能說得比 `handoff.md` 更完成。來源 cwd 與目前專案不同、來源未正常結束，或有重要內容不可讀時，另加一行「注意：」。送出前再自我檢查一次字數、分號與「注意：」。以 AskUserQuestion（header `handoff`）提供「正確，開始接手」「需要修正」「取消交接」，編號清單與「注意：」直接放在 question 裡，最後一行是「以上交接理解正確嗎？」：只寫在回覆本文的清單曾被漏掉，使用者只看到問題卡。
4. **Correct**：修正寫回 `handoff.md` 所有受影響的區塊，並記在 User Corrections（修正優先於來源），然後重新產生 Confirmation View。只有矛盾無法解決時才定向查詢來源；只有工作目的（Requests & Intent）被推翻時才重新 Reconstruct。
5. **Promote**：確認後 front matter 改為 `status: verified`，成為 Main Agent 的 Authoritative Working Context。
6. **Continue**：Main Agent 從 Next 接續，自選工具、Skill、Sub-agent 與 Workflow。若來源在執行中停止，先確認工作區實際狀態（`artifacts` 只是歷史紀錄）。
7. **Lazy Retrieval**：缺細節時以 `mode: query` 問 Sub-agent 一個具體問題，拿回附 `#n`／`S#n`／`E#n` 引用的答案；不把 Source Context 重新注入 Main Agent。

## handoff.md

Front matter 記錄 `status`、來源 Agent／Session／工作目錄／最後狀態。章節參考 `/compact` 的摘要，固定為 Requests & Intent、Decisions & Constraints、Files & Artifacts、Errors & Fixes、Current State、Pending、Next、Source Limitations、User Corrections；空的章節寫「無」。Planning 與 Execution 交接共用同一模板，長度隨來源大小。

## 驗收

```powershell
python -B -m unittest discover -s tests -p test_handoff_prepare.py -v
python -B tests/handoff_fixtures.py <臨時-CODEX_HOME>
```

`tests/handoff_fixtures.py` 依 Reader 實測 Schema 產生 Case 1–4 與 Case 7（長 evidence 分層）的合成 Codex Session，並列出每個 Case 的預期事實與不應出現的來源操作詞（Case 5）。結果見 [驗收紀錄](handoff-validation.md)。

## 限制

- V1 只支援 Codex App → Claude Code；反向交接、Router 與 Snapshot 不在範圍內。
- 確認是軟性閘門（由 Skill 指示結束 turn 等待），沒有接上 Argus hook：hook 一進入 pending 就會擋下 Reader 與 Sub-agent。
- 重建品質受 Reader 限制：Compact 摘要多為密文、Agent 間加密訊息不可讀、`artifacts` 不是磁碟快照。這些會寫進 Source Limitations，並在 Confirmation View 提醒。
