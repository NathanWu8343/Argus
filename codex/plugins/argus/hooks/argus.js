#!/usr/bin/env node
// argus for Codex: the host adapter handed to the shared gate in core.js
'use strict';

const os = require('os');
const path = require('path');
const fs = require('fs');
const core = require('./core');

const TOOL = 'request_user_input';
const ASYNC_TOOL = 'request_user_input_async';
const QUESTION = '以上理解正確嗎？';
// Option labels are user-facing and defined in SKILL.md; request_user_input requires a description per option
const OPTIONS = [
  { label: core.CONFIRM, description: '依最後一版覆述執行原始請求' },
  { label: '需要修正', description: '補充要修改的地方，重新覆述' },
  { label: core.CANCEL, description: '停止，不執行' },
];

const TRANSPORT = `Codex host rules for the Confirm step:
- Ask with the ${TOOL} tool: exactly one question with id "argus", header "argus", question "${QUESTION}" and options ${JSON.stringify(OPTIONS)}. The tool waits for the user and returns the chosen label. Do not repeat the question or the labels as text around the call.
- If ${TOOL} is unavailable in this mode or the call fails, end the reply with the three labels as plain text on separate lines and end the turn; the user replies with one label verbatim.
- Never use ${ASYNC_TOOL} (its question card is dismissed when the turn ends), sleep or any other tool while waiting for confirmation.`;
const CANCELLED_CONTEXT = 'The user cancelled the request. Acknowledge the cancellation and stop; do not carry it out.';
const FALLBACK_CONTEXT = `${TOOL} returned no answer, so nothing is confirmed. End the reply with the three labels as plain text on separate lines and end the turn; the user replies with one label verbatim.`;

function parse(response) {
  if (typeof response !== 'string') return response;
  try {
    return JSON.parse(response);
  } catch {
    return undefined;
  }
}

// The gate denies file reads, so the skill text travels with the prompt instead of being read from disk.
// The installed package keeps the skill beside hooks/; the repository keeps it at the root.
function confirmationContext() {
  let file = path.join(__dirname, '../skills/confirm-first/SKILL.md');
  if (!fs.existsSync(file)) file = path.join(__dirname, '../../skills/confirm-first/SKILL.md');
  const skill = fs.readFileSync(file, 'utf8').replaceAll('\r\n', '\n').replace(/^---\n[\s\S]*?\n---\n/, '').trim();
  return `The confirm-first skill is supplied here; do not read it with a tool.\n\n${skill}\n\n${TRANSPORT}`;
}

const adapter = {
  stateNamespace: 'codex',
  stateDir: path.join(process.env.CODEX_HOME || path.join(os.homedir(), '.codex'), 'argus'),
  invocation: /^\s*(?:\$(?:argus:)?confirm-first|\[\$(?:argus:)?confirm-first\]\([^\r\n]+\))(\s|$)/,
  questionTool: TOOL,
  // request_user_input answers are keyed by question id and hold the selected labels as an array
  answerOf: (response, question) => {
    const picked = parse(response)?.answers?.[question.id]?.answers;
    return picked?.length === 1 ? picked[0] : undefined;
  },
  // No stopReason: a plain-text answer arrives as the next user prompt, so the turn must be allowed to end
  denyReason:
    `confirm-first alignment in progress: the user has not confirmed yet, so every tool except ${TOOL} is paused. Finish the restatement, then ask with ${TOOL} (header: argus); if it is unavailable, list the three labels as plain text and end the turn. Do not use ${ASYNC_TOOL}, sleep or polling tools.`,
};

function decide(input, pending) {
  const next = core.decide(input, pending, adapter);
  const resolved = core.resolution(input, pending, adapter);
  switch (input.hook_event_name) {
    case 'UserPromptSubmit':
      if (next.pending) return core.withContext(next, 'UserPromptSubmit', confirmationContext());
      if (!resolved) return next;
      return core.withContext(next, 'UserPromptSubmit',
        resolved === core.CONFIRM ? core.CONFIRMED_CONTEXT : CANCELLED_CONTEXT);
    case 'PostToolUse':
      if (resolved === core.CANCEL) return core.withContext(next, 'PostToolUse', CANCELLED_CONTEXT);
      // Still pending after a question call: a revision answer the model can read itself, or a call that produced no answer
      if (next.pending && input.tool_name === TOOL && !parse(input.tool_response)?.answers) {
        return core.withContext(next, 'PostToolUse', FALLBACK_CONTEXT);
      }
      return next;
    default:
      return next;
  }
}

if (require.main === module) core.run(adapter, decide);

module.exports = { adapter, decide };
