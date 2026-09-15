#!/usr/bin/env node
// argus for Claude Code: the host adapter handed to the shared gate in core.js
'use strict';

const os = require('os');
const path = require('path');
const core = require('./core');

const adapter = {
  stateNamespace: 'claude',
  stateDir: path.join(os.homedir(), '.claude', 'argus'),
  invocation: /^\s*\/argus:confirm-first(\s|$)/,
  questionTools: ['AskUserQuestion'],
  answerOf: (response, question) => response?.answers?.[question.question],
  blockStop: true,
  denyReason:
    'confirm-first alignment in progress: the user has not confirmed yet, so every tool except AskUserQuestion is paused. Finish the restatement, then ask for confirmation with AskUserQuestion (header: argus).',
  stopReason:
    'confirm-first alignment in progress: no confirmation yet. If the restatement is done, end with AskUserQuestion (header: argus); if you are waiting for the user to provide details, end the turn.',
};

if (require.main === module) core.run(adapter);

module.exports = { adapter, decide: (input, pending) => core.decide(input, pending, adapter) };
