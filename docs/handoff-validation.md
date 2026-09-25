# session-relay Skill 驗收紀錄

日期：2026-09-23。Windows、Python 3.11.9、Claude Code 2.1.280（`claude -p --plugin-dir`）。報告只記錄結構與判定，不含私人對話正文。

驗收期間 Skill 從 `handoff` 改名為 `session-relay`，Agent 從 `handoff-reconstructor` 改名為 `session-relay-reconstructor`。以下一律寫新名稱；改名前的紀錄行為相同。

## 結果

- 單元測試：`test_handoff_prepare.py` 7 個通過；Reader 49 個照常通過。Node 32 個通過、1 個既有測試略過；`build-codex.js --check` 一致，Codex 套件沒有納入 session-relay。
- Case 1–4：以 `tests/handoff_fixtures.py` 產生合成 Codex Session，實際跑 Reader → prepare → Sub-agent 重建，逐條比對預期事實，**29 條全部符合**。
- Case 5：4 份 fixture 的 handoff.md 都沒有出現 `SOURCE_METHOD_TERMS`（來源中刻意寫了「用 apply_patch…再用 exec_command…」）。兩個真實 Session 的 handoff 中，Codex 只出現在 front matter、檔案路徑或專案主題，沒有出現在操作指示裡。
- Case 6 與端到端：真正載入 plugin 後，走完 Read → Reconstruct → Confirm → Correct → Promote → Continue。Main Agent 的工具呼叫只有 prepare、`argus:session-relay-reconstructor` 與 Read `handoff.md`，從未讀取 `source.*`。

| Case | 情境 | 關鍵判定 |
|---|---|---|
| 1 Planning | 5 輪需求討論，webhook 與快取被推翻 | 最終需求正確；SQLite 快取與 webhook 列在 Rejections；Completed 為無；draft PR 列為未決 |
| 2 Interrupted | 實作中途 `usage_limit_exceeded`，沒有最後回覆 | auth.py 與測試已建立；main.py 被標為錯誤的全域掛法；明確寫出 2 failed；conftest 修正標為未完成 |
| 3 Compact | 可讀摘要（MongoDB、JWT）後，改成 PostgreSQL 與 session cookie | 採用 Compact 後的決策；摘要中仍有效的需求保留；還指出 architecture.md 的通知管道已過時 |
| 4 Compact + Interrupted | 加密摘要、Reviewer 建議 Celery、CSV→XLSX、job 寫到一半中斷 | 決策全部是最新版；Celery 只出現在 Rejections；model 與 migration 已完成；export job 標為未完成；API 延到下一輪 |
| 6 Correction | 使用者否決第 3 點的 conftest 做法 | 修正寫回 handoff.md 的 8 個位置並記在 User Corrections；Confirmation View 重新產生並再次詢問；沒有重新分析來源 |

## Continue 實測

依 Case 2 來源最後的紀錄建立 FastAPI 專案（全域驗證的 bug，pytest 重現 2 failed、6 passed），執行 `/argus:session-relay` 後回覆「正確，開始接手」。Main Agent 先查看 git 與工作區、重跑測試，再自行修改 main.py、items router 與測試 fixture；獨立重跑的結果是 **8 passed**，`handoff.md` 標為 `status: verified`。在沒有程式碼的空專案中，它會停下來詢問程式碼位置，不會憑空實作。

## 真實 Session

用 `claude -p --plugin-dir` 從頭跑到 Confirm。額度那筆跑了三次完整流程：第一次在 Confirm 規則調整前，第二次在第一次調整後，第三次用最終版 Skill。

| Session | 性質 | source.json → source.md | 耗時 | Sub-agent 讀取 |
|---|---|---|---|---|
| `01a03d6d-cb6e-7570-acee-bd7eaebb7b46` 第一次 | 額度耗盡（failed，`usage_limit_exceeded`），對話 6 則、evidence 117 筆 | 15.7 MB → 175 KB（3836 行） | 155 秒 | 對話全讀；E#48–E#83 約 1240 行沒有逐行讀，改從 source.json 抽出文字 |
| `01a03d6d-…` 第二次 | 同上 | 同上 | 266 秒 | 6 頁讀完全部 3836 行，再用 source.json 補查 |
| `01a03d6d-…` 第三次 | 同上 | 同上 | 263 秒 | 5 頁讀完全部 3836 行 |
| `01a0c8d9-b993-7990-8892-a983a3f85663` | Plan + 加密 Compact（raw_fallback），對話 15 則、沒有 evidence | 34 KB → 31 KB（252 行） | 87 秒 | 一次讀完 |

- Context 隔離：每次的 Main Agent 都只有 prepare、`argus:session-relay-reconstructor`、Read `handoff.md`（有時另外用 ToolSearch 找 AskUserQuestion，`-p` 沒有，改走純文字選項）。
- handoff.md：13 個必要段落加 Source Limitations、User Corrections 都在，`status: draft`；沒有來源的工具或事件名稱。
- 事實抽查：
  - 額度那筆：最後兩筆 evidence 是第二版主視覺生成，之後沒有檢查。第一版已複製進專案，但圖檔沒有透明通道。HTML 與 brand-spec 只出現在指示文字，沒有寫檔紀錄。三次 handoff 的「HTML 與 brand-spec 未開始、第一版不能用」都相符。第二次另外從 PNG 標頭判斷第二版也沒有透明通道；解碼後 color type 是 2（RGB），相符。
  - Compact 那筆：最後一筆是 plan，之後沒有使用者回覆，也沒有 evidence。使用者在選項中選的範圍與相容策略，和確認重點第 2、3 點相符。「計畫未核准、未改檔」相符。

### Confirmation View 調整

第一次額度那筆有 3 點寫成 2–3 句，所以調整 SKILL 第 3 步：每點一句、只講一件事，已經標了（推定）或（需確認）就不再加句子解釋，並附「不要／要」的例子。調整後完整重跑時，有一點為了壓成一句而寫錯（「只生成了一張」，handoff.md 記錄的是兩張），因此再補一條規則：省略可以，但不能寫成跟 handoff.md 矛盾。

「沿用」是指保留既有的 handoff.md，只重跑 Confirm。這樣輸入相同，只有 Skill 不同。

| Skill | 輸入 | 次數 | 每點句數 | 與 handoff.md 一致 |
|---|---|---|---|---|
| 調整前 | 額度那筆完整流程 | 1 | 1、2、1、3、2 | 是 |
| 調整前 | Compact 那筆完整流程 | 1 | 全部 1 | 是 |
| 每點一句 | 沿用第一次額度的 handoff | 2 | 全部 1 | 是 |
| 每點一句 | 沿用 Compact 的 handoff | 1 | 全部 1 | 是 |
| 每點一句 | 額度那筆完整流程 | 1 | 全部 1 | **否**：第 4 點「只生成了一張」 |
| 加上不可矛盾 | 沿用上一列的 handoff | 3 | 全部 1 | 是 |
| 加上不可矛盾 | 沿用 Compact 的 handoff | 1 | 全部 1 | 是 |
| 加上不可矛盾 | 額度那筆完整流程 | 1 | 全部 1 | 是 |

另外只渲染了 `01a0a9c4-…`（8 次 Compact、335 則訊息）：1.35 MB → 233 KB，8 個 Compact 邊界都插在正確位置。

### Agent 改名與讀取規則、Sonnet 5 / Opus 5.5

Agent 改名為 `session-relay-reconstructor` 後，新增兩條讀取規則：沒逐筆讀的範圍要寫進 Source Limitations；搜尋 `source.md` 改用 Grep 工具。使用者預設模型在這期間改成 Sonnet 5，所以兩個模型都跑了完整流程。

| 模型 | 額度那筆 | Compact 那筆 | Sub-agent 讀取 |
|---|---|---|---|
| Sonnet 5 | 3 次：231、362、270 秒 | 2 次：157、149 秒 | 額度那筆 3 次都跳過中段 evidence（E#8–E#111 之間的 Figma 瀏覽紀錄），3 次都寫進了 Source Limitations |
| Opus 5.5 | 1 次：283 秒 | 1 次：252 秒 | 讀完全部 3836 行；另外把幾張截圖與生成圖解碼到系統暫存目錄檢視，結束前已刪除，沒有寫進工作區 |

- 7 次都呼叫 `argus:session-relay-reconstructor`，Main Agent 沒讀 `source.*`；handoff.md 都有 15 個段落，沒有來源方法詞。Sub-agent 用 Grep 搜尋 `source.md`，Bash 只用來查 source.json（Opus 另有一次刪除暫存圖），沒有工具錯誤。
- **Sonnet 找到的 bug**：第一次 Compact 那筆的 Main Agent 先 `cd` 到外掛目錄才執行 prepare，交接資料因此寫進 Argus repo（被 `.gitignore` 忽略，已刪除）。原因是 SKILL 寫「from the project root」，有歧義。改成「在目前工作目錄執行、不先 `cd`，`${CLAUDE_PLUGIN_ROOT}` 只是腳本位置」後，之後 4 次完整流程都寫在正確的專案裡。

Confirmation View 在 Sonnet 上比較長，因此第 3 步又補了三條：用編號列出、每點約 40 字以內且不用分號、送出前自我檢查（包括「注意：」有沒有漏）。

| 模型 | Skill | 次數 | 每點字數 | 有分號的點 | 「注意：」漏寫工作目錄 |
|---|---|---|---|---|---|
| Opus 5.5 | 補規則前（完整或沿用） | 2 | 27–65 | 各 1 | 0 |
| Opus 5.5 | 編號＋40 字 | 2（完整） | 20–35 | 0 | 0 |
| Sonnet 5 | 補規則前（完整） | 3 | 37–103；1 次沒編號 | 各 1 | 1 |
| Sonnet 5 | 編號＋40 字 | 2（完整） | 33–76 | 各 1 | 1 |
| Sonnet 5 | 再加自我檢查 | 4（沿用） | 28–82；只有 1 次全部在 40 字內 | 3 次各 1 | 0 |

字數與分號的統計不含粗體與 code 標記；Sonnet 有時用半形標點，半形分號也計入。這幾次的內容都和各自的 handoff.md 一致。

## 發現並修正

- 真實 Session 的圖片生成結果是沒有 `data:` 前綴的整段 base64，已改為換成標記並附原值位置。
- Sub-agent 曾把 position 不連續誤判為紀錄遺失，當時在 source.md 標頭說明 `pos` 是來源行序；後來 source.md 不再列出 position（見 Compact 格式一節）。
- Confirmation View 曾有幾點寫成 2–3 句，壓成一句後又出現一次跟 handoff.md 矛盾的說法；兩者都已修正（見上表）。
- Sonnet 會把 prepare 執行在外掛目錄，已改寫 SKILL 第 1 步的說法修正。
- Sub-agent 跳過來源時不會說明；現在規定要把跳過的範圍寫進 Source Limitations，Sonnet 3 次跳過都有寫。

## 限制

- 語意驗收是逐條人工比對預期事實，不是自動評分。Case 4 在本機沒有真實案例（見 [Reader 驗證](codex-session-validation.md)），只用合成 fixture 驗收。
- `claude -p` 沒有 AskUserQuestion，確認走純文字選項的後備路徑。互動模式下的選項卡片沒有另外實測。
- 每點一句與不可矛盾在兩個模型上都成立。40 字與不用分號在 Opus 上成立，Sonnet 只有部分做到：常見 40–80 字，偶爾用分號。
- 大型來源不保證逐行讀完：Opus 4 次完整重建中 1 次跳過，Sonnet 3 次都跳過，跳過的都是中段 evidence。現在跳過的範圍會寫進 Source Limitations，抽查的關鍵事實都沒錯。

## source.md 精簡（2026-09-24）

調整內容見 [Handoff 文件](handoff-skill.md) 第 1 步：evidence 分層（檔案變更與最後 10 筆完整，更早的只留狀態與輸出頭尾）、每筆附上產生它的操作、turn 改用 `T<n>`、時間只標在每個 turn 的第一筆、移除 ANSI 色碼、evidence 改成放在 code fence 裡的 `key: value`。Reader contract 只新增欄位（`execution_evidence[].operation`、`state.turns`），仍是 schema_version 2。

### 自動檢查

- 單元測試：Reader 51 個、prepare 10 個、Node 20 個通過；`build-codex.js --check` 一致。Reader 驗證集 105 個真實 Session 全部 passed。
- 本機 1919 個 Session 全部能轉換。`source.md` 總量少 13.6%；181 個中斷 Session 少 28.9%，其中超過 50K 字元的 34 個少 37.3%。
- 額度那筆 164,856 → 113,395 字元（-31.2%）；Compact 那筆 18,624 → 17,699（-5.0%）。
- 結構：2,119 個 evidence fence 都有對應的結尾；2,136 筆 evidence 中 2,099 筆有 `operation`，沒有的是檔案變更、附件，以及 WebSearch／Extension 這類沒有獨立操作紀錄的結果。

### 端對端（Main Agent Opus 5.5、Sub-agent Sonnet 5，`claude -p --plugin-dir`）

耗時與成本取自 `claude -p` 回報的 `duration_ms`、`total_cost_usd`；Case 2、4、7 是修正後第二輪的數字。新增 Case 7：中斷前有 19 筆 evidence，較早的 pytest 失敗落在精簡層，之後修正並通過。

| Case | 耗時 | 成本 | 結果 |
|---|---|---|---|
| 1 Planning | 120 秒 | $0.48 | 7 條全部符合 |
| 2 Interrupted | 119 秒 | $0.44 | 7 條全部符合 |
| 3 Compact | 110 秒 | $0.42 | 7 條全部符合 |
| 4 Compact + Interrupted | 128 秒 | $0.46 | 7/8：Remaining 沒寫「測試」；改版前版本同一天重跑（Sub-agent 為 Opus 5.5）也沒寫，屬於需推論的預期，不是退步 |
| 7 Long evidence | 195 秒 | $0.56 | 7 條全部符合；最後的 12 passed 與 ruff 通過寫成目前狀態，較早的 3 failed 只當過程 |
| 額度那筆（真實） | 284 秒 | $0.95 | HTML 與 brand-spec 未開始、第一版插畫不透明，與先前一致 |
| Compact 那筆（真實） | 229 秒 | $0.63 | 計畫未核准、未改檔，範圍與相容策略相符 |

- Main Agent 都沒有讀 `source.*`，只呼叫 prepare、`argus:session-relay-reconstructor`、Read `handoff.md`，以及下面 Case 1 補標記用的編輯。Case 1 另外在確認前自行把 Sub-agent 回報的沒把握點補標（推定）／（需確認）：Sub-agent 回報了，但沒標進 `handoff.md`（當時已有要求標記的規則），而且其中一處寫成「（推定：依對話順序判定…）」。因此 SKILL 第 3 步改為：沒標的點由 Main Agent 在確認前補上標準標記、不改字句；Reconstructor 的自我檢查也加上這一項。重跑 Case 1（137 秒、$0.48）：Sub-agent 標了 3 點中的 2 點，漏標的那點（Completed 為「無」）由 Main Agent 只補上「（推定）」；另一點（draft PR 待問主管）Sub-agent 認為是已列在 Unresolved 的待決問題、不是推定，所以沒標，Main Agent 也沒補；它不會被當成定案，所以兩邊規則都補上「已列在 Unresolved／Blockers 的待決問題不用標」，並規定標記只寫「（推定）」或「（需確認）」、說明寫在句子裡。7 條預期全部符合，沒有來源操作詞。補上這兩條後再跑一次（105 秒、$0.41）：沒有非標準標記；待決的 draft PR 沒標也沒補；Sub-agent 另回報一點「Slack 文字的細節規格還沒定」並自行決定不標，Main Agent 也沒把它列進確認清單。預期事實中，Completed 寫成「需求討論與 V1 計畫已完成」而不是「無」，「尚未寫程式」寫在 Objective、Current State 與 Remaining，嚴格算是 6/7；這跟標記規則無關，前兩次都寫「無」。
- **發現並修正**：第一輪 Case 2、4、7 的 handoff 出現來源工具名稱（`apply_patch`、`exec_command`），都發生在只有名稱、沒有參數的「沒有結果的操作」。Reconstructor 補上「Unresolved 與 Source Limitations 也適用、改用 `E#` 與白話描述」和自我檢查項目後重跑，三個 Case 都是 0。兩個真實 Session 各有上百筆帶操作的 evidence，第一輪就沒有外洩。

### 同日對照（額度那筆）

Main Agent 都是 Opus 5.5。reconstructor 的模型設定在這次改版期間從 `inherit` 改成 `sonnet`，所以另外用改版前的版本加上 Sonnet Sub-agent 跑一次，才能分開模型和 source.md 的影響。

| 版本 | Sub-agent | 成本 | 耗時 | Sub-agent 工具呼叫 | 讀取範圍 | handoff.md |
|---|---|---|---|---|---|---|
| 改版前 | Opus 5.5 | $2.22 | 343 秒 | Read 17、Bash 12 | 讀完 3,836 行 | 9,611 字元 |
| 改版前 | Sonnet 5 | $0.85 | 345 秒 | Read 5、Grep 6、Bash 2 | 約 60 筆 evidence 只抽查（有寫進 Source Limitations） | 5,182 字元 |
| 改版後 | Sonnet 5 | $0.95 | 284 秒 | Read 4、Grep 3、Bash 7 | 讀完 2,216 行 | 4,184 字元 |

- 成本從 $2.22 降到 $0.95 主要是換模型，不是 source.md 精簡。同樣用 Sonnet，兩版成本差不多。
- 精簡的效果在讀取範圍：改版前的 Sonnet 只抽查約一半 evidence，改版後同樣成本讀完整份。三次的關鍵事實（HTML 與 brand-spec 未開始、第一版插畫不透明）都相符。
- 改版前的 Opus Sub-agent 另外把 `source.json` 裡的兩張生成圖解碼查看，因此多寫出「第二張圖也沒有 alpha」；兩次 Sonnet 都照來源文字標為（需確認）。
- 每個版本只跑一次，數字含模型變異。

### 限制

- 每個 Case 只跑一到兩次；Main Agent 沒有在 Sonnet 5 上跑過這一版。
- Sub-agent 仍會為精簡層的 evidence 查 `source.json`（Case 7 查了全部 4 筆），節省幅度會因來源而異。

## 耗時優化（2026-09-24）

起因是一個真實 Session（`01a0cd3d-…`：對話 25 則、沒有 evidence、7 個 turn 中 4 個 interrupted 或 failed）。整個流程花了 410 秒，其中 Sub-agent 占 283 秒。實際跑工具只用了約 13 秒，其餘是模型思考與輸出：

- 查了 4 次 `source.json`。中間 turn 的狀態只存在 `state.turns`，`source.md` 沒有列出來，所以它必須去查。
- 對已經讀完的 `source.md` 又 Grep 了 2 次，其中一次用預設的 files_with_matches，只拿回檔名。
- handoff 正文 5 處寫到 `source.json`／`source.md` 的欄位。

另外，Main Agent 確認時沒有輸出編號清單，直接呼叫 AskUserQuestion，使用者只看到問題卡。

### 調整

- `source.md` 標頭多列兩行：沒正常完成或沒留下對話的 turn，以及截斷處數（沒有截斷時寫明不需要查 `source.json`）。
- Reconstructor：
  - 只在截斷處依指標查 `source.json`。
  - 讀完的檔案不再 Grep；需要 Grep 時用 `output_mode: "content"`。
  - Current State 寫目前狀態，不逐 turn 敘述。
  - 需求一條一行並引用。
  - 正文不提 `source.md`／`source.json` 與其欄位，自我檢查也加上這一項。
- SKILL 第 3 步：編號清單與「注意：」放進 AskUserQuestion 的 question。
- 版本 0.4.0 → 0.4.1。

### 自動檢查

Python 63 個通過（其中 prepare 11 個，新增標頭測試）；Node 32 個通過，1 個既有測試略過；`build-codex.js --check` 一致。

### 對照（同一個 Session，各 3 次）

設定：Claude Code 2.1.281，`claude -p --plugin-dir`，用 `--settings` 停用已安裝的 argus，舊版是 HEAD 的 `git archive`。Main Agent 是 Opus 5.5、Sub-agent 是 Sonnet 5，effort 都是 xhigh（使用者設定）。6 次同時平行執行，並從 transcript 確認各自載入的 plugin 目錄。

| 版本 | 總耗時（秒） | Sub-agent（秒） | 成本 | Sub-agent 工具呼叫 | Sub-agent 輸出 tokens | handoff 字元 |
|---|---|---|---|---|---|---|
| 舊 | 349、436、341（平均 375） | 284、383、296（平均 321） | 平均 $0.80 | Read 1、Bash 4–5（都是查 `source.json`）、Grep 0–3、Write 1–2 | 平均 29.8K | 6,179–7,774 |
| 新 | 270、327、255（平均 284） | 214、267、205（平均 229） | 平均 $0.67 | Read 1、Write 1 | 平均 20.9K | 5,685–6,276 |

- 總耗時少 24%，Sub-agent 少 29%，成本少 16%，Sub-agent 輸出 tokens 少 30%。舊版的 Sub-agent 耗時與起因那次（283 秒）相當。
- 新版 3 次都沒有查 `source.json`，也沒有 Grep，每次都是讀一次、寫一次。
- 事實檢查兩版都符合：v2 是目前版本、49 個測試與 105 個 Session、4 個需確認的檔案、中斷或失敗的 turn、15 個段落都齊全。
- 正文提到 `source.json`／`source.md`：舊版 3 次中有 2 次，新版 0 次。
- 貼上的需求原文不在來源裡：舊版 3 次都寫進 Source Limitations，新版有 1 次沒寫，只寫成「依使用者貼上的文件」。

### 限制

- 每版只跑 3 次，數字包含模型變異。
- `-p` 沒有 AskUserQuestion，所以「清單放進 question」要在互動模式下實際確認顯示效果。
- 剩下的 Sub-agent 時間，主要是一次長思考加上寫出 handoff，受 effort 設定影響。當時以為只能改使用者的 `modelSettings.claude-sonnet-5.effortLevel`；其實 agent frontmatter 可以設定 `effort`，見下一節。

## Compact 格式（2026-09-24）

目標改成像 `/compact`：Sub-agent 把來源壓縮成接續摘要，Main Agent 只讀這份摘要就能接手，context 不會快速累積。

### 調整

- handoff.md 從 15 段改成參考 `/compact` 摘要的 9 段：Requests & Intent、Decisions & Constraints、Files & Artifacts、Errors & Fixes、Current State、Pending、Next、Source Limitations、User Corrections。
- Reconstructor：
  - `model: sonnet`、`effort: medium`，只影響這個 Sub-agent。
  - 操作用 `E#n` 加白話描述，不寫工具名稱。
  - 同一件事只寫一次；Current State 只寫一句，而且不能比 Files & Artifacts、Pending 說得更完成。
  - Source Limitations 只寫來源缺什麼，不寫讀了或查了哪些檔。
- SKILL：
  - 第 1 步改在 Skill 載入時以 `` !`…` `` 執行 prepare，並用 `allowed-tools` 放行。
  - 第 3 步補上「不能說得比 handoff.md 更完成」，段落名稱改成新模板。
- `source.md` 不再列來源 position：entry 標題、Compact 邊界與 Artifacts 表都拿掉。

### 對照

設定：Claude Code 2.1.281，`claude -p --plugin-dir`，Main Agent 是 Opus 5.5。Compact 兩列各 6 次同時平行執行（真實 Session `01a0cd3d-…`、Case 7、Case 4 各 2 次）。舊版的真實 Session 取自上一節（3 次），Case 7、4 是同日用舊版各跑 1 次。

| 版本 | 真實 Session 總／Sub-agent（秒） | Case 7 總／Sub-agent（秒） | Case 4 總／Sub-agent（秒） | 每次成本 |
|---|---|---|---|---|
| 舊版 15 段，Sonnet xhigh | 255–327／205–267 | 199／134（1 次） | 147／100（1 次） | $0.43–0.74 |
| Compact，Sonnet high（使用者設定） | 141–205／96–156 | 168–172／119–127 | 99–107／50–60 | $0.37–0.50 |
| Compact，Sonnet medium（agent 設定） | 138–146／89–92 | 108–109／62–64 | 88–96／42–43 | $0.34–0.48 |

- 最後一列 6 次的 Sub-agent transcript 都記錄 `effort: medium`。
- 同一個 Compact 模板用 Opus 5.5 當 Sub-agent 更快（總耗時 81–111 秒），但使用者指定用 Sonnet。
- 在 medium 下，Sub-agent 的輸出大部分已經是 handoff 本文（Case 4 輸出 3.4K tokens、handoff 2.9–3.1K 字元），所以沒有再測 low：能省的只有剩下的思考，而思考最多的正是需要分辨前後矛盾的 Case 7。

| 檢查 | Compact，Sonnet high | Compact，Sonnet medium |
|---|---|---|
| handoff 字元 | 真實 5.9–8.0K、Case 7 4.4–4.8K、Case 4 3.5K | 真實 5.5–6.4K、Case 7 3.1–3.7K、Case 4 2.9–3.1K |
| Current State | 315–1,398 字元、3–9 句 | 161–254 字元、1 句 |
| 正文出現來源工具名稱 | 4／6 | 1／6（Case 7 的 `apply_patch`） |
| 正文提到 `source.*` 或 `pos` | 5／6 | 5／6，多在 Source Limitations，照抄 source.md 的截斷指標與 `pos` |

- 預期事實：Case 7 一次 7/7，另一次因為 `apply_patch` 是 6/7；Case 4 兩次都是 7/8，Pending 沒寫「測試」，和之前相同。舊版 Case 7 把 cli.py 的除錯 print 寫成已完成，舊版 Case 4 的 Pending 漏了「通知管理員」；Compact 兩種設定都沒有這兩個問題。
- 真實 Session 兩次都寫出「貼上的需求原文不在來源裡」。
- 因為 Sub-agent 會照抄 `pos`，之後把 position 從 source.md 拿掉；截斷指標要留給 Sub-agent 查 `source.json`，所以保留。拿掉 position 之後只跑了單元測試，沒有再跑端對端。

### Skill 載入時執行 prepare

- 只省下一次模型往返，約 4 秒。第一次開 shell 的成本（Claude Code 建立 shell snapshot）只是從 Main Agent 的 Bash 呼叫挪到 Skill 載入。
- 6 次平行執行時，這段約 21–24 秒；單獨執行時，debug 紀錄的 snapshot 約 7 秒。所以前幾節平行量到的第一次 Bash 耗時偏高。
- 以 Haiku 驗證（每次約 $0.03）：
  - 不載入使用者設定、在預設權限模式下，`allowed-tools` 的 pattern 沒寫路徑的引號時，整個 Skill 會中止；補上引號後就能執行。
  - 錯誤的 ID、沒給 ID 時，錯誤 JSON 都會進到 Skill 內容，Main Agent 照著回覆。
  - `disableSkillShellExecution: true` 時，Main Agent 自己執行 prepare，並回報 `session_not_found`。

### 自動檢查

Python 63 個通過；Node 32 個通過，1 個既有測試略過；`build-codex.js --check` 一致。

### 限制

- 每個設定每個 Case 只跑 2 次（舊版的 Case 4、7 只有 1 次），數字包含模型變異。
- `-p` 沒有 AskUserQuestion，互動模式的確認卡片仍要實際確認。
