#!/usr/bin/env node
// argus for Codex: the host adapter handed to the shared gate in core.js
'use strict';

const os = require('os');
const path = require('path');
const core = require('./core');

const adapter = {
  stateNamespace: 'codex',
  stateDir: path.join(process.env.CODEX_HOME || path.join(os.homedir(), '.codex'), 'argus'),
  invocation: /^\s*(?:\$(?:argus:)?confirm-first|\[\$(?:argus:)?confirm-first\]\([^\r\n]+\))(\s|$)/,
  questionTools: ['request_user_input', 'request_user_input_async'],
  // request_user_input answers are keyed by question id and hold the selected labels as an array
  answerOf: (response, question) => {
    const picked = response?.answers?.[question.id]?.answers;
    return picked?.length === 1 ? picked[0] : undefined;
  },
  // Answers to plain-text or asynchronous questions arrive in the next user prompt, so the turn must be allowed to end
  blockStop: false,
  denyReason:
    'confirm-first alignment in progress: the user has not confirmed yet, so every tool except request_user_input and request_user_input_async is paused. Finish the restatement, then ask for confirmation using the tool schema (header: argus when supported), or ask in plain text and end the turn. This host accepts a later user prompt that exactly matches an option label.',
};

if (require.main === module) core.run(adapter);

module.exports = { adapter, decide: (input, pending) => core.decide(input, pending, adapter) };
