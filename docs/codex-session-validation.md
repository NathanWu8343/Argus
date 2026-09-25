# Session Reader 驗證紀錄

日期：2026-09-23。Windows、Python 3.11.9、本機 Codex 套件 26.917.6896.0。

## 結果

- Reader 獨立單元／CLI 測試：49 個通過。
- 既有 Node 測試：32 個通過、1 個既有 Codex 安裝整合測試依其環境旗標略過。
- **105 個真實 Session 全部通過**原文、角色、數量、順序與來源雜湊檢查。最後狀態：82 completed、4 failed、8 interrupted、11 unknown（無可靠 terminal lifecycle）。
- 共核對 **2,829 則 user/assistant/plan**，涵蓋 212 段被選取的歷史、20 個 Session 的 32 次 Compact、192 個使用者附件。Fork 共用前綴會在不同 Session 的驗證中重複計入，並非全部互不重複的來源。
- 32 次真實 Compact 的摘要均是密文；Reader 明確輸出 `raw_fallback`／`summary_unavailable`，不宣稱還原 Codex 當時的摘要。真實 quota 與 capacity 失敗 Session 均正確輸出 `failed` 及保存的錯誤類別。
- 使用清空 PATH 的子程序執行 Python CLI，成功匯出語音 Session 的 UTF-8 JSON；沒有啟動 Codex、呼叫網路或模型。

完整計數與 Session ID 清單見 [機器可讀報告](codex-session-validation.json)。報告不含私人對話正文、圖片 bytes 或工具輸出。

## 驗證分層

1. 合成測試：UUID、索引身分、封存、WAL、父段缺失、ordinal/byte 截止點、多段繼承、Fork、可讀／加密 Compact、多次 Compact、Compact＋中斷執行、quota 失敗、pending operation、互動答案、圖片、語音、rollback、未知格式、壞行、partial tail、原子輸出與 snapshot 變更。
2. 真實檔案：Reader 完成後，驗證器獨立重讀每個來源的 byte prefix，比對 SHA-256，逐一比對 UI message 的全文、角色、位置與數量，檢查輸出 sequence 與原始 ordinal 順序。
3. 模型鏡像：Reader 使用 turn、ID 或精確文字配對顯示事件，不用語意相似度；已知真實輸入／回覆缺少對應事件時報錯。圖片的原始 data URI 可補回 UI 路徑表示。
4. App UI：使用原生視窗的 accessibility tree 與截圖交叉抽查三個真實任務。這是畫面抽查，不宣稱逐畫面檢查全部 105 個 Session。

## App UI 抽查

| Session | 覆蓋情況 | 結果 |
|---|---|---|
| `01a0c8d9-b993-7990-8892-a983a3f85663` | 一般對話、提問、Plan、Compact | Reader 保留 13 則訊息／計畫；畫面擷取的 13 個較長正文片段逐字對上 |
| `01a0c8da-21c7-7f80-be9a-01f3d01f2595` | 兩段 history_base、Compact 後的提問與計畫卡片 | 12 則訊息／計畫；67 個較長正文片段逐字對上；截圖確認 Compact 提示、提問與計畫的順序 |
| `01a0c8d5-4803-7e80-96f1-b27ae3b08ac4` | 四段歷史鏈、修正後分支、Hook、檔案變更 | 17 則訊息；3 個較長正文片段逐字對上；截圖確認最後一則使用者輸入與 Assistant 修正結果 |

畫面還包含側邊欄、工具 UI、按鈕及 Markdown 分割文字，不拿這些計入全文一致率。所有顯示項目的完整文字另由原始事件比對覆蓋。原始截圖只留本機暫存，不提交私人畫面。

## 實測發現並修正

- `session_id` 與 `id` 在子 Agent 上可不同，前者可能指父任務。
- `history_base.thread_id` 可能是檔名 suffix 的段 ID，不在 `threads` 表中。
- App 投影資料庫可落後最新 history_base 分支，不能直接當作完整輸出來源。
- `request_user_input` 的答案只在工具結果；`request_user_input_async` 的顯示訊息與工具結果可能共用 ID。去重鍵已包含 role/kind/turn，避免後者蓋掉前者。
- 只有設定／中斷、尚未開始工作的子任務可以是空 Session；只有壓縮摘要則不視為完整。

## 重要限制案例

`01a0a9c4-81cd-7a23-bea9-47e4f0b1a983` 的繼承鏈含 8 次 Compact 與加密 Agent 間訊息。Reader 成功還原 335 則可見訊息，同時標示摘要與 Agent 間密文不可讀；「105 個通過」指可見對話與結構驗證通過，**不表示密文內容被還原**。

在完整保留的實際 Session `01a0c7ec-fabd-7033-a482-df25d0d355d7`、`01a0b8e3-0505-7011-a998-3daf113ff671` 中，曾發生 Compact 後中斷，但後來又有新 turn 完成，所以目前 head 的最後狀態是 completed。本機抽樣未找到「最後 head 恰好停在 Compact 後且包含執行結果」的真實案例；該組合由依實測 Schema 建立的合成 fixture 驗收。Reader 不把歷史中斷誤當成目前狀態。

尚未以真實 rollback 或明文 Compact 事件驗證，只測過合成結構；尚未支援未遷移的 legacy 格式。未知版本／類型會明確失敗。

## 重跑

```powershell
python -B -m unittest discover -s tests -p test_codex_session_reader.py -v
node --test "tests/*.test.js"
python -B scripts/validate_codex_sessions.py 01a0c8da-21c7-7f80-be9a-01f3d01f2595 01a0c8d5-4803-7e80-96f1-b27ae3b08ac4 01a0c8d9-b993-7990-8892-a983a3f85663
```

如需重跑完整清單，從 JSON 報告的 `results[].session_id` 取得 ID，再分批傳给驗證器。真實資料持續變動，後續次數可能不同；每次結果以當次來源 SHA-256 為準。
