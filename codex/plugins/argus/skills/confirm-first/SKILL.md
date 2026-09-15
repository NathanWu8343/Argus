---
name: confirm-first
description: Restate the request's key points and start only after explicit confirmation
---

Align on the request before acting. Write everything the user sees, including the restatement, the question, and the option labels, in Traditional Chinese (zh-TW).

## Steps

1. **Restate** — Do not call any tool; work from the user's words only. In the reply body, list at most 5 plain-language points, one sentence each, covering the goal and scope, not how you will do it. List anything you filled in yourself separately, each prefixed with "（假設）"; omit that list when there is none.
   - Done when: each point can be answered "right" or "wrong" on its own.
   - Empty request → ask in plain text what to do, then end the turn.
2. **Confirm** — In the same reply, ask the user to pick one option: question "以上理解正確嗎？", header `argus`, single-select, option labels copied exactly and in this order: "正確，開始執行", "需要修正", "取消此請求". If you have no way to present selectable options, list them in plain text and end the turn.
   - Confirmation is only a selected option or a reply that is exactly one of the labels. Free text that merely means confirm or cancel is not one; ask again.
   - "需要修正" or free-text input:
     - Includes details → merge them in, then return to step 1 with a full restatement.
     - No details → ask in plain text what to change, then end the turn.
   - "取消此請求" → stop and wait for the next instruction.
   - "正確，開始執行" → alignment is complete; carry out the original request based on the latest restatement.
