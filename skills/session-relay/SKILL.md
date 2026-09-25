---
name: session-relay
description: Take over work from a Codex App session by its session ID, even when Codex is out of quota or unavailable. Reads the session locally, has an isolated subagent reconstruct the current state, asks the user to confirm it, then continues the discussion, planning, or implementation. Use whenever the user gives a Codex session ID and wants to hand off, 接手, 交接, or continue that session in Claude Code.
argument-hint: "<codex-session-id>"
allowed-tools: Bash(python -B "${CLAUDE_SKILL_DIR}/scripts/prepare_source.py" *)
---

Continue another agent's work without reading its raw history yourself: **Read → Reconstruct → Confirm → Promote → Continue**. Write everything the user sees in Traditional Chinese (zh-TW).

Three kinds of context stay separate:

- **Source context** (`source.json`, `source.md`): the whole normalized source session. Only the `argus:session-relay-reconstructor` subagent reads it. Never open either file yourself, not even to check one detail: it can be hundreds of thousands of tokens of history, and pulling it into your context defeats the handoff.
- **Full handoff context** (`handoff.md`): the reconstructed state. This is what you work from.
- **Confirmation view**: at most 5 points shown to the user, derived from `handoff.md`. It is only for the user to check your understanding; your working context stays the full `handoff.md`.

## Steps

1. **Read** — When this skill loaded, `prepare_source.py` already ran on the session ID in the current working directory (the user's project). Its output:

   ```
   !`python -B "${CLAUDE_SKILL_DIR}/scripts/prepare_source.py" $ARGUMENTS 2>&1 || true`
   ```

   On success it is JSON with only paths, counts, and the last status; the source is kept under `.claude/handoffs/<session-id>/` (ignored by git). If the block holds no such JSON or error (the command did not run, for example because shell execution is disabled), run `python -B "${CLAUDE_SKILL_DIR}/scripts/prepare_source.py" <session-id>` yourself in the current working directory, without `cd` first.
   - No session ID was given → ask for the full Codex session UUID in plain text and end the turn.
   - An error → tell the user its `error.code` and message, then stop.
   - `handoff_exists: true` → a handoff for this session already exists and may carry the user's corrections. Ask in plain text whether to reuse it (go to step 3) or rebuild it from the source (step 2), then end the turn.

2. **Reconstruct** — Call the Agent tool with `subagent_type: "argus:session-relay-reconstructor"` and a prompt that holds only the mode and paths:

   ```
   mode: reconstruct
   source_md: <source_md>
   source_json: <source_json>
   handoff_md: <handoff_md>
   ```

   When it returns, Read `handoff.md` in full.

3. **Confirm** — List at most 5 plain-language points drawn from `handoff.md` as a numbered list, since the user refers to points by number when correcting. Use this priority: objective; scope and confirmed requirements; key decisions and constraints; current state; next step or the most important open question. If the subagent's reply named points it is least sure about, give the ones that bear on the next step priority among the five; they should already be marked in `handoff.md`. If one is not, add exactly `（推定）` or `（需確認）` to its line there before you confirm, without changing the wording: after Promote an unmarked point reads as settled. A point already listed as a question under Pending needs no mark. A reused handoff has no such reply. The user answers "right" or "wrong" for each point as a whole, so each point is one short sentence about one thing, roughly 40 Chinese characters at most; a point that runs longer, strings sentences together, or needs a semicolon is holding more than one thing and hides which part is wrong. When a topic holds several facts, keep the one that most affects the next step or is most likely wrong, and leave the rest, with all implementation detail, in `handoff.md`. Leaving facts out is fine, but the point must stay true against `handoff.md`: do not word it so it denies what you left out, such as 「只完成 API」 when `handoff.md` also records a finished migration, nor so it claims more is done than `handoff.md` shows, such as 「程式已改完」 when some changes are still `（需確認）`. Mark anything the handoff marks `（推定）` or `（需確認）` the same way; the mark says enough, so do not add a sentence explaining it.

   ```
   不要：目前進度：搜尋 API 已完成。前端頁面還沒做。還有 3 個測試失敗。
   要：  目前進度：搜尋 API 已完成，但還有 3 個測試失敗。
   不要：範圍只含搜尋頁。這是來源 agent 自己判斷的，你還沒確認過（需確認）。
   要：  範圍只含搜尋頁（需確認）。
   ```

   Add one line starting with "注意：" only when it changes how far the handoff can be trusted: `source_working_directory` differs from the current project, the last status is not `completed`, or Source Limitations say important context was unreadable.

   Before sending, reread the list once: shorten any point over about 40 characters or with a semicolon, and make sure the "注意：" line names every condition above that applies.

   Then ask with AskUserQuestion, header `handoff`, single-select, option labels exactly and in this order: "正確，開始接手", "需要修正", "取消交接". Put the list itself in the `question`: the numbered points, the "注意：" line if any, a blank line, then "以上交接理解正確嗎？". The question card is what the user reads while answering, and a list left only in the reply text can go missing, leaving a card with nothing to check. If you cannot present options, send the same list and question with the labels in plain text and end the turn. Confirmation is only a selected option or a reply that is exactly one of the labels.

4. **Correct** — On "需要修正" or free text:
   - With details → update `handoff.md` itself, not just the view: fix every section the correction touches (a changed decision can also change Pending or Next), and add one line to User Corrections recording what the user said. The user's correction outranks the source. Then return to step 3 with a fresh full view.
   - If the correction contradicts the handoff in a way you cannot resolve, or needs facts the handoff lacks, ask the subagent one targeted question (step 7) before editing. Rebuild from scratch (step 2) only when the correction overturns what the work is for (Requests & Intent).
   - No details → ask in plain text what to change, then end the turn.
   - "取消交接" → stop and wait. Leave the files in place.

5. **Promote** — On "正確，開始接手", set `status: verified` in the front matter of `handoff.md`. From now on the verified `handoff.md` is your authoritative working context: you do not need the source session, its tool history, or how the source agent got its results. Tell the user in one line that the handoff is adopted, with the file path.

6. **Continue** — Carry on toward Next, choosing your own tools, skills, subagents, and workflow; the handoff says what and why, and how is yours to decide. When the source stopped mid-execution, first establish the actual workspace state for the artifacts that matter, since the handoff records history, not the current disk, then resume from what is really done. When Next or Pending needs the user, raise it with them first. If your context is later compacted, reread the verified `handoff.md`.

7. **Look up the source only when needed** — When the work needs a detail the handoff lacks (the exact wording of an agreed API contract, an error message, why an option was rejected), call `argus:session-relay-reconstructor` with:

   ```
   mode: query
   question: <one specific question>
   source_md: <source_md>
   source_json: <source_json>
   handoff_md: <handoff_md>
   ```

   Use the answer; if it adds a fact later work depends on, add it to the matching section of `handoff.md` with its citation.
