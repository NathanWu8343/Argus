'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { decide } = require('../hooks/argus.js');

const SCRIPT = path.join(__dirname, '..', 'hooks', 'argus.js');
const DAY_MS = 24 * 60 * 60 * 1000;

// UserPromptSubmit, PreToolUse, Stop, and SessionEnd fields come from probed hook stdin;
// the AskUserQuestion response comes from a transcript's toolUseResult
const OPTIONS = [
  { label: '正確，開始執行', description: '依覆述執行' },
  { label: '需要修正', description: '補充要改的地方' },
  { label: '取消此請求', description: '停手' },
];

const base = (event, fields) => ({ session_id: 's1', transcript_path: 't.jsonl', cwd: '.', hook_event_name: event, ...fields });
const prompt = (text) => base('UserPromptSubmit', { prompt_id: 'p1', permission_mode: 'default', prompt: text });
const tool = (name) => base('PreToolUse', { tool_name: name, tool_input: {} });
const stop = (active) => base('Stop', { stop_hook_active: active });

function answer(reply, { header = 'argus', before = [] } = {}) {
  const question = { question: '以上理解正確嗎？', header, options: OPTIONS, multiSelect: false };
  const questions = [...before, question];
  const answers = Object.fromEntries(before.map((q) => [q.question, q.options[0].label]));
  answers[question.question] = reply;
  return base('PostToolUse', {
    tool_name: 'AskUserQuestion',
    tool_input: { questions },
    tool_response: { questions, answers, annotations: {} },
  });
}

// ── Layer 1: state machine ──

test('invoking the skill enters pending, leading whitespace included', () => {
  assert.deepEqual(decide(prompt('/argus:confirm-first create a file'), false), { pending: true });
  assert.deepEqual(decide(prompt('  /argus:confirm-first'), false), { pending: true });
});

test('ordinary prompts and other slash commands leave the state unchanged', () => {
  assert.deepEqual(decide(prompt('create a file for me'), false), { pending: false });
  assert.deepEqual(decide(prompt('/confirm-first create a file'), false), { pending: false });
  assert.deepEqual(decide(prompt('/argus:confirm-firstx'), false), { pending: false });
  assert.deepEqual(decide(prompt('switch to plan B'), true), { pending: true });
});

test('pending denies every tool except AskUserQuestion', () => {
  for (const name of ['Write', 'Edit', 'Bash', 'Agent', 'Read', 'Grep', 'Glob', 'ToolSearch', 'mcp__context7__query-docs']) {
    const { pending, output } = decide(tool(name), true);
    assert.equal(pending, true);
    assert.equal(output.hookSpecificOutput.hookEventName, 'PreToolUse');
    assert.equal(output.hookSpecificOutput.permissionDecision, 'deny');
    assert.match(output.hookSpecificOutput.permissionDecisionReason, /AskUserQuestion/);
  }
});

test('pending still allows AskUserQuestion', () => {
  assert.deepEqual(decide(tool('AskUserQuestion'), true), { pending: true });
});

test('idle never denies', () => {
  for (const name of ['Write', 'Read']) assert.deepEqual(decide(tool(name), false), { pending: false });
});

test('the confirm button returns to idle with an anchoring message', () => {
  const { pending, output } = decide(answer('正確，開始執行'), true);
  assert.equal(pending, false);
  assert.equal(output.hookSpecificOutput.hookEventName, 'PostToolUse');
  assert.match(output.hookSpecificOutput.additionalContext, /latest restatement/);
});

test('the cancel button returns to idle without a message', () => {
  assert.deepEqual(decide(answer('取消此請求'), true), { pending: false });
});

test('the needs-changes button stays pending', () => {
  assert.deepEqual(decide(answer('需要修正'), true), { pending: true });
});

test('free-text input is not a confirmation even when it starts with the confirm prefix', () => {
  assert.deepEqual(decide(answer('正確，但檔名改成 a.txt'), true), { pending: true });
});

test('a question whose header is not argus leaves the state unchanged', () => {
  assert.deepEqual(decide(answer('正確，開始執行', { header: 'other' }), true), { pending: true });
});

test('with several questions in one call, only the argus question counts', () => {
  const before = [{ question: '用哪個檔名？', header: 'name', options: [{ label: 'a.txt' }, { label: 'b.txt' }] }];
  assert.equal(decide(answer('正確，開始執行', { before }), true).pending, false);
});

test('idle ignores button answers', () => {
  assert.deepEqual(decide(answer('正確，開始執行'), false), { pending: false });
});

test('pending blocks ending the turn once, then allows it while staying pending', () => {
  const first = decide(stop(false), true);
  assert.equal(first.pending, true);
  assert.equal(first.output.decision, 'block');
  assert.match(first.output.reason, /AskUserQuestion/);
  assert.deepEqual(decide(stop(true), true), { pending: true });
});

test('idle does not block ending the turn', () => {
  assert.deepEqual(decide(stop(false), false), { pending: false });
});

test('SessionEnd returns to idle', () => {
  assert.deepEqual(decide(base('SessionEnd', { reason: 'other' }), true), { pending: false });
});

// ── Layer 2: script I/O ──

function tempDir(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'argus-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

function run(input, dir, env = {}) {
  const result = spawnSync(process.execPath, [SCRIPT], {
    input: typeof input === 'string' ? input : JSON.stringify(input),
    env: { ...process.env, ARGUS_STATE_DIR: dir, ARGUS_DEBUG: '', ...env },
    encoding: 'utf8',
  });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout;
}

test('script: invoke → Write and Read denied → click confirm → allowed', (t) => {
  const dir = tempDir(t);
  const stateFile = path.join(dir, 's1.pending');

  assert.equal(run(prompt('/argus:confirm-first create a file'), dir), '');
  assert.ok(fs.existsSync(stateFile));

  assert.equal(JSON.parse(run(tool('Write'), dir)).hookSpecificOutput.permissionDecision, 'deny');
  assert.equal(JSON.parse(run(tool('Read'), dir)).hookSpecificOutput.permissionDecision, 'deny');

  assert.match(JSON.parse(run(answer('正確，開始執行'), dir)).hookSpecificOutput.additionalContext, /confirmed/);
  assert.ok(!fs.existsSync(stateFile));

  assert.equal(run(tool('Write'), dir), '');
  assert.equal(run(tool('Read'), dir), '');
});

test('script: SessionEnd removes this session\'s state file', (t) => {
  const dir = tempDir(t);
  run(prompt('/argus:confirm-first'), dir);
  run(base('SessionEnd', { reason: 'other' }), dir);
  assert.deepEqual(fs.readdirSync(dir), []);
});

test('script: SessionStart removes leftovers older than a day and keeps recent ones', (t) => {
  const dir = tempDir(t);
  const stale = path.join(dir, 'old.pending');
  const fresh = path.join(dir, 'new.pending');
  fs.writeFileSync(stale, '');
  fs.writeFileSync(fresh, '');
  const twoDaysAgo = new Date(Date.now() - 2 * DAY_MS);
  fs.utimesSync(stale, twoDaysAgo, twoDaysAgo);

  run(base('SessionStart', { source: 'startup' }), dir);

  assert.ok(!fs.existsSync(stale));
  assert.ok(fs.existsSync(fresh));
});

test('script: non-JSON stdin is allowed through with no output', (t) => {
  assert.equal(run('not json', tempDir(t)), '');
});

test('script: with ARGUS_DEBUG set, stdin and errors are logged and the action is still allowed', (t) => {
  const dir = path.join(tempDir(t), 'not-created-yet');
  assert.equal(run('not json', dir, { ARGUS_DEBUG: '1' }), '');
  const log = fs.readFileSync(path.join(dir, 'debug.log'), 'utf8');
  assert.match(log, /^not json\n/);
  assert.match(log, /ERROR SyntaxError/);
});
