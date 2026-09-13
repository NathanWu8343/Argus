#!/usr/bin/env node
// argus: the gate for confirm-first. Two states, idle and pending; pending means the state file exists.
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const HEADER = 'argus';
// Button label prefixes; the labels are user-facing and defined in SKILL.md
const CONFIRM_PREFIX = '正確';
const CANCEL_PREFIX = '取消';
const INVOCATION = /^\s*\/argus:confirm-first(\s|$)/;
const STALE_MS = 24 * 60 * 60 * 1000;

const DENY_REASON =
  'confirm-first alignment in progress: the user has not confirmed yet, so every tool except AskUserQuestion is paused. Finish the restatement, then ask for confirmation with AskUserQuestion (header: argus).';
const STOP_REASON =
  'confirm-first alignment in progress: no confirmation yet. If the restatement is done, end with AskUserQuestion (header: argus); if you are waiting for the user to provide details, end the turn.';
const CONFIRMED_CONTEXT = 'The user confirmed. Carry out the original request based on the latest restatement.';

function isInvocation(input) {
  return INVOCATION.test(String(input.prompt ?? ''));
}

// Label of the button clicked on the argus question; null for free-text input or when there is no argus question
function argusButton(input) {
  const question = (input.tool_input?.questions ?? []).find((q) => q.header === HEADER);
  if (!question) return null;
  const answer = input.tool_response?.answers?.[question.question];
  return (question.options ?? []).some((o) => o.label === answer) ? answer : null;
}

function decide(input, pending) {
  switch (input.hook_event_name) {
    case 'UserPromptSubmit':
      return { pending: pending || isInvocation(input) };
    case 'PreToolUse':
      if (pending && input.tool_name !== 'AskUserQuestion') {
        return {
          pending,
          output: {
            hookSpecificOutput: {
              hookEventName: 'PreToolUse',
              permissionDecision: 'deny',
              permissionDecisionReason: DENY_REASON,
            },
          },
        };
      }
      return { pending };
    case 'PostToolUse': {
      const button = pending && input.tool_name === 'AskUserQuestion' ? argusButton(input) : null;
      if (button?.startsWith(CONFIRM_PREFIX)) {
        return {
          pending: false,
          output: { hookSpecificOutput: { hookEventName: 'PostToolUse', additionalContext: CONFIRMED_CONTEXT } },
        };
      }
      if (button?.startsWith(CANCEL_PREFIX)) return { pending: false };
      return { pending };
    }
    case 'Stop':
      // stop_hook_active means we already blocked once: allow the stop but keep pending so the gate stays on
      if (pending && !input.stop_hook_active) return { pending, output: { decision: 'block', reason: STOP_REASON } };
      return { pending };
    case 'SessionEnd':
      return { pending: false };
    default:
      return { pending };
  }
}

function stateDir() {
  return process.env.ARGUS_STATE_DIR || path.join(os.homedir(), '.claude', 'argus');
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

function main() {
  const raw = fs.readFileSync(0, 'utf8');
  const dir = stateDir();
  fs.mkdirSync(dir, { recursive: true });
  if (process.env.ARGUS_DEBUG) fs.appendFileSync(path.join(dir, 'debug.log'), raw.trim() + '\n');
  const input = JSON.parse(raw);
  if (input.hook_event_name === 'SessionStart') removeStale(dir);

  const file = path.join(dir, `${String(input.session_id).replace(/[^\w-]/g, '_')}.pending`);
  const pending = fs.existsSync(file);
  const next = decide(input, pending);
  if (next.pending && !pending) fs.writeFileSync(file, new Date().toISOString());
  if (!next.pending && pending) fs.rmSync(file, { force: true });
  if (next.output) process.stdout.write(JSON.stringify(next.output));
}

if (require.main === module) {
  try {
    main();
  } catch (err) {
    // A failure inside argus always lets the action through; it must never block normal use
    if (process.env.ARGUS_DEBUG) fs.appendFileSync(path.join(stateDir(), 'debug.log'), `ERROR ${err.stack}\n`);
  }
}

module.exports = { decide };
