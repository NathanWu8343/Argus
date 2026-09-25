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
  questionTool: 'AskUserQuestion',
  // AskUserQuestion can be deferred, and ToolSearch is how it gets loaded
  alsoAllowed: ['ToolSearch'],
  answerOf: (response, question) => response?.answers?.[question.question],
  denyReason:
    'confirm-first alignment in progress: the user has not confirmed yet, so every tool except AskUserQuestion (and ToolSearch to load it) is paused. Finish the restatement, then ask for confirmation with AskUserQuestion (header: argus).',
};

if (require.main === module) core.run(adapter);

module.exports = { adapter, decide: (input, pending) => core.decide(input, pending, adapter) };
