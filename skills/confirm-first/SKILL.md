---
name: confirm-first
description: Restate the request's key points and start only after button confirmation
argument-hint: "<task>"
disable-model-invocation: true
---

Align on the request before acting. Alignment is complete only when the user clicks "正確，開始執行". Write everything the user sees, including the restatement, questions, and button labels, in Traditional Chinese (zh-TW).

## Steps

1. **Restate** — Do not call any tool; work from the user's words only. In the reply body, list at most 5 plain-language points, one sentence each, covering the goal and scope, not how you will do it. List anything you filled in yourself separately, each prefixed with "（假設）"; omit that list when there is none.
   - Done when: each point can be answered "right" or "wrong" on its own.
   - Empty request → ask in plain text what to do, then end the turn.
2. **Confirm** — In the same reply, call AskUserQuestion: header `argus`, single-select, question "以上理解正確嗎？", option labels copied exactly and in this order: "正確，開始執行", "需要修正", "取消此請求".
   - "需要修正" and free-text input are not button confirmations:
     - Includes details → merge them in, then return to step 1 with a full restatement.
     - No details → ask in plain text what to change, then end the turn.
     - Means confirm or cancel → ask the same question again so the user clicks a button.
   - "取消此請求" → stop and wait for the next instruction.
   - "正確，開始執行" → alignment is complete; carry out the original request based on the latest restatement.
