// argus: the gate for confirm-first. Two states, idle and pending; pending means the state file exists.
// Everything that differs between hosts comes from the adapter:
//   stateNamespace    host subdirectory when ARGUS_STATE_DIR overrides the default
//   stateDir          default directory for state files
//   invocation        RegExp matching a prompt that starts the gate
//   questionTools     tool names allowed while pending
//   answerOf          (tool_response, question) → the label the user picked, or undefined
//   blockStop         true to block ending the turn once while pending, so the reply ends with the question;
//                     false when the answer arrives as the next user prompt and the turn must be allowed to end
//   denyReason        text returned when a tool is denied
//   stopReason        text returned when ending the turn is blocked (unused when blockStop is false)
'use strict';

const fs = require('fs');
const path = require('path');

const HEADER = 'argus';
// Option labels are user-facing and defined in SKILL.md
const CONFIRM = '正確，開始執行';
const CANCEL = '取消此請求';
const STALE_MS = 24 * 60 * 60 * 1000;
const CONFIRMED_CONTEXT = 'The user confirmed. Carry out the original request based on the latest restatement.';

// Label chosen on the argus question; null for free-text input or when there is no argus question
function chosenLabel(input, adapter) {
  const question = (input.tool_input?.questions ?? []).find((q) => q.header === HEADER);
  if (!question) return null;
  const answer = adapter.answerOf(input.tool_response, question);
  return (question.options ?? []).some((o) => o.label === answer) ? answer : null;
}

function decide(input, pending, adapter) {
  switch (input.hook_event_name) {
    case 'UserPromptSubmit': {
      const prompt = String(input.prompt ?? '');
      // A reply that is exactly a label counts on every host; it is the only path when options cannot be shown
      if (pending && [CONFIRM, CANCEL].includes(prompt.trim())) return { pending: false };
      return { pending: pending || adapter.invocation.test(prompt) };
    }
    case 'PreToolUse':
      if (pending && !adapter.questionTools.includes(input.tool_name)) {
        return {
          pending,
          output: {
            hookSpecificOutput: {
              hookEventName: 'PreToolUse',
              permissionDecision: 'deny',
              permissionDecisionReason: adapter.denyReason,
            },
          },
        };
      }
      return { pending };
    case 'PostToolUse': {
      const label = pending && adapter.questionTools.includes(input.tool_name) ? chosenLabel(input, adapter) : null;
      if (label === CONFIRM) {
        return {
          pending: false,
          output: { hookSpecificOutput: { hookEventName: 'PostToolUse', additionalContext: CONFIRMED_CONTEXT } },
        };
      }
      if (label === CANCEL) return { pending: false };
      return { pending };
    }
    case 'Stop':
      // stop_hook_active means we already blocked once: allow the stop but keep pending so the gate stays on
      if (pending && !input.stop_hook_active && adapter.blockStop) {
        return { pending, output: { decision: 'block', reason: adapter.stopReason } };
      }
      return { pending };
    case 'SessionEnd':
      return { pending: false };
    default:
      return { pending };
  }
}

function stateDir(adapter) {
  return process.env.ARGUS_STATE_DIR
    ? path.join(process.env.ARGUS_STATE_DIR, adapter.stateNamespace)
    : adapter.stateDir;
}

// SessionEnd does not fire on crashes, so leftover state files are removed at the next session start
function removeStale(dir) {
  const now = Date.now();
  for (const name of fs.readdirSync(dir)) {
    if (!name.endsWith('.pending')) continue;
    const file = path.join(dir, name);
    if (now - fs.statSync(file).mtimeMs > STALE_MS) fs.rmSync(file, { force: true });
  }
}

function main(adapter) {
  const raw = fs.readFileSync(0, 'utf8');
  const dir = stateDir(adapter);
  fs.mkdirSync(dir, { recursive: true });
  if (process.env.ARGUS_DEBUG) fs.appendFileSync(path.join(dir, 'debug.log'), raw.trim() + '\n');
  const input = JSON.parse(raw);
  if (input.hook_event_name === 'SessionStart') removeStale(dir);

  const file = path.join(dir, `${String(input.session_id).replace(/[^\w-]/g, '_')}.pending`);
  const pending = fs.existsSync(file);
  const next = decide(input, pending, adapter);
  if (next.pending && !pending) fs.writeFileSync(file, new Date().toISOString());
  if (!next.pending && pending) fs.rmSync(file, { force: true });
  if (next.output) process.stdout.write(JSON.stringify(next.output));
}

function run(adapter) {
  try {
    main(adapter);
  } catch (err) {
    // A failure inside argus always lets the action through; it must never block normal use
    try {
      if (process.env.ARGUS_DEBUG) fs.appendFileSync(path.join(stateDir(adapter), 'debug.log'), `ERROR ${err.stack}\n`);
    } catch {
      // Logging can fail for the same reason as the original state-directory operation
    }
  }
}

module.exports = { decide, run };
