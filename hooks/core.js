// argus: the gate for confirm-first. Two states, idle and pending; pending means the state file exists.
// Everything that differs between hosts comes from the adapter:
//   stateNamespace    host subdirectory when ARGUS_STATE_DIR overrides the default
//   stateDir          default directory for state files
//   invocation        RegExp matching a prompt that starts the gate
//   questionTool      tool name allowed while pending
//   alsoAllowed       optional list of other tool names allowed while pending
//   answerOf          (tool_response, question) → the label the user picked, or undefined
//   denyReason        text returned when a tool is denied
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

// CONFIRM or CANCEL when this event answers the pending gate; null otherwise.
// A reply that is exactly a label counts on every host; it is the only path when options cannot be shown
function resolution(input, pending, adapter) {
  if (!pending) return null;
  let label = null;
  if (input.hook_event_name === 'UserPromptSubmit') label = String(input.prompt ?? '').trim();
  if (input.hook_event_name === 'PostToolUse' && input.tool_name === adapter.questionTool) label = chosenLabel(input, adapter);
  return label === CONFIRM || label === CANCEL ? label : null;
}

const withContext = (next, hookEventName, additionalContext) =>
  ({ ...next, output: { hookSpecificOutput: { hookEventName, additionalContext } } });

function decide(input, pending, adapter) {
  const resolved = resolution(input, pending, adapter);
  switch (input.hook_event_name) {
    case 'UserPromptSubmit':
      if (resolved) return { pending: false };
      return { pending: pending || adapter.invocation.test(String(input.prompt ?? '')) };
    case 'PreToolUse':
      if (pending && input.tool_name !== adapter.questionTool && !adapter.alsoAllowed?.includes(input.tool_name)) {
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
    case 'PostToolUse':
      if (resolved === CONFIRM) return withContext({ pending: false }, 'PostToolUse', CONFIRMED_CONTEXT);
      if (resolved === CANCEL) return { pending: false };
      return { pending };
    default:
      return { pending };
  }
}

function stateDir(adapter) {
  return process.env.ARGUS_STATE_DIR
    ? path.join(process.env.ARGUS_STATE_DIR, adapter.stateNamespace)
    : adapter.stateDir;
}

// Pending survives the end of a session so a resumed session keeps its gate; files left behind by
// other, abandoned sessions are swept on each prompt once they are a day old
function removeStale(dir, keep) {
  const now = Date.now();
  for (const name of fs.readdirSync(dir)) {
    if (!name.endsWith('.pending')) continue;
    const file = path.join(dir, name);
    if (file !== keep && now - fs.statSync(file).mtimeMs > STALE_MS) fs.rmSync(file, { force: true });
  }
}

function main(adapter, decideEvent) {
  const raw = fs.readFileSync(0, 'utf8');
  const dir = stateDir(adapter);
  fs.mkdirSync(dir, { recursive: true });
  if (process.env.ARGUS_DEBUG) fs.appendFileSync(path.join(dir, 'debug.log'), raw.trim() + '\n');
  const input = JSON.parse(raw);
  const file = path.join(dir, `${String(input.session_id).replace(/[^\w-]/g, '_')}.pending`);
  if (input.hook_event_name === 'UserPromptSubmit') removeStale(dir, file);

  const pending = fs.existsSync(file);
  const next = decideEvent(input, pending, adapter);
  if (next.pending && !pending) fs.writeFileSync(file, new Date().toISOString());
  if (!next.pending && pending) fs.rmSync(file, { force: true });
  if (next.output) process.stdout.write(JSON.stringify(next.output));
}

function run(adapter, decideEvent = decide) {
  try {
    main(adapter, decideEvent);
  } catch (err) {
    // A failure inside argus always lets the action through; it must never block normal use
    try {
      if (process.env.ARGUS_DEBUG) fs.appendFileSync(path.join(stateDir(adapter), 'debug.log'), `ERROR ${err.stack}\n`);
    } catch {
      // Logging can fail for the same reason as the original state-directory operation
    }
  }
}

module.exports = { decide, resolution, withContext, run, CONFIRM, CANCEL, CONFIRMED_CONTEXT };
