'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { decide } = require('../hooks/argus.js');
const { decide: decideCodex } = require('../codex/src/argus.js');

const QUESTION = '以上理解正確嗎？';
const OPTIONS = [
  { label: '正確，開始執行', description: '依最後一版覆述執行原始請求' },
  { label: '需要修正', description: '補充要修改的地方，重新覆述' },
  { label: '取消此請求', description: '停止，不執行' },
];
const event = (hook_event_name, fields = {}) => ({ session_id: 'codex-test', hook_event_name, ...fields });
// request_user_input input and output shapes come from codex-rs/protocol/src/request_user_input.rs
const question = ({ id = 'argus', header = 'argus', options = OPTIONS } = {}) =>
  ({ questions: [{ id, header, question: QUESTION, options }] });
const answered = (answers, { id = 'argus', tool_input = question(), asString = false } = {}) => {
  const tool_response = { answers: { [id]: { answers } } };
  return event('PostToolUse', {
    tool_name: 'request_user_input', tool_use_id: 'call_argus', tool_input,
    tool_response: asString ? JSON.stringify(tool_response) : tool_response,
  });
};
const skillText = () => fs.readFileSync(path.join(__dirname, '../skills/confirm-first/SKILL.md'), 'utf8')
  .replaceAll('\r\n', '\n').replace(/^---\n[\s\S]*?\n---\n/, '').trim();

test('Codex explicit invocation activates the gate without affecting Claude commands', () => {
  for (const prompt of [
    '$confirm-first build', '  $argus:confirm-first build',
    '[$confirm-first](C:/plugins/argus/skills/confirm-first/SKILL.md) build',
    '[$argus:confirm-first](C:\\Users\\me\\.codex\\plugins\\cache\\argus\\argus\\0.4.0\\skills\\confirm-first\\SKILL.md) build',
  ]) {
    assert.equal(decideCodex(event('UserPromptSubmit', { prompt }), false).pending, true);
    assert.equal(decide(event('UserPromptSubmit', { prompt }), false).pending, false);
  }
  for (const prompt of [
    '$confirm-firstx', 'mention $confirm-first', 'ordinary request', '/argus:confirm-first task',
    '[$confirm-firstx](/skills/other/SKILL.md) build', 'mention [$confirm-first](/skills/confirm-first/SKILL.md)',
  ]) {
    assert.deepEqual(decideCodex(event('UserPromptSubmit', { prompt }), false), { pending: false });
  }
});

test('Codex supplies the skill and its transport rules with the prompt instead of a file read', () => {
  for (const [prompt, pending] of [['$confirm-first build', false], ['只分析今天的紀錄', true], ['需要修正', true]]) {
    const result = decideCodex(event('UserPromptSubmit', { prompt }), pending);
    assert.equal(result.pending, true);
    assert.equal(result.output.hookSpecificOutput.hookEventName, 'UserPromptSubmit');
    const context = result.output.hookSpecificOutput.additionalContext;
    assert.ok(context.includes(skillText()));
    assert.match(context, /request_user_input\b/);
    assert.match(context, /Never use request_user_input_async/);
    for (const option of OPTIONS) assert.ok(context.includes(JSON.stringify(option)));
  }
});

test('Codex exact text replies resolve the gate with a matching instruction', () => {
  const confirmed = decideCodex(event('UserPromptSubmit', { prompt: ' 正確，開始執行\n' }), true);
  assert.equal(confirmed.pending, false);
  assert.match(confirmed.output.hookSpecificOutput.additionalContext, /The user confirmed/);
  const cancelled = decideCodex(event('UserPromptSubmit', { prompt: '取消此請求' }), true);
  assert.equal(cancelled.pending, false);
  assert.match(cancelled.output.hookSpecificOutput.additionalContext, /cancelled/);
  for (const prompt of ['正確，但先改名稱', '取消此請求並執行其他工作', '']) {
    assert.equal(decideCodex(event('UserPromptSubmit', { prompt }), true).pending, true);
  }
});

test('Codex allows only request_user_input while pending', () => {
  assert.deepEqual(decideCodex(event('PreToolUse', { tool_name: 'request_user_input' }), true), { pending: true });
  for (const tool_name of ['request_user_input_async', 'clocksleep', 'exec', 'exec_command', 'apply_patch',
    'spawn_agent', 'mcp__fs__read', 'AskUserQuestion']) {
    const result = decideCodex(event('PreToolUse', { tool_name }), true);
    assert.equal(result.pending, true);
    assert.equal(result.output.hookSpecificOutput.permissionDecision, 'deny');
    assert.match(result.output.hookSpecificOutput.permissionDecisionReason, /request_user_input \(header: argus\)/);
    assert.deepEqual(decideCodex(event('PreToolUse', { tool_name }), false), { pending: false });
  }
});

test('Codex reads the selected label from the request_user_input result', () => {
  for (const asString of [false, true]) {
    const confirmed = decideCodex(answered(['正確，開始執行'], { asString }), true);
    assert.equal(confirmed.pending, false);
    assert.match(confirmed.output.hookSpecificOutput.additionalContext, /latest restatement/);
    const cancelled = decideCodex(answered(['取消此請求'], { asString }), true);
    assert.equal(cancelled.pending, false);
    assert.match(cancelled.output.hookSpecificOutput.additionalContext, /cancelled/);
  }
  // The question id is whatever the model chose; the header identifies the argus question
  assert.equal(decideCodex(answered(['正確，開始執行'], { id: 'confirm', tool_input: question({ id: 'confirm' }) }), true).pending, false);
  for (const answers of [['需要修正'], ['正確，但修改範圍'], ['正確，開始執行', '需要修正'], []]) {
    assert.deepEqual(decideCodex(answered(answers), true), { pending: true });
  }
  assert.deepEqual(decideCodex(answered(['正確，開始執行'], { tool_input: question({ header: 'other' }) }), true), { pending: true });
  assert.deepEqual(decideCodex(answered(['正確，開始執行']), false), { pending: false });
});

test('Codex rejects altered option labels even if the user selects them', () => {
  for (const label of ['正確，但先改名稱', '取消此請求並執行其他工作']) {
    const input = answered([label], { tool_input: question({ options: [{ label, description: '' }] }) });
    assert.deepEqual(decideCodex(input, true), { pending: true });
  }
});

test('Codex asks for the text fallback when request_user_input returns no answer', () => {
  for (const tool_response of ['request_user_input is unavailable in default mode', { error: 'unavailable' }, undefined]) {
    const result = decideCodex(event('PostToolUse', {
      tool_name: 'request_user_input', tool_use_id: 'call_argus', tool_input: question(), tool_response,
    }), true);
    assert.equal(result.pending, true);
    assert.match(result.output.hookSpecificOutput.additionalContext, /plain text/);
  }
  assert.deepEqual(decideCodex(event('PostToolUse', { tool_name: 'exec', tool_response: {} }), true), { pending: true });
});

test('Codex never blocks ending the turn', () => {
  assert.deepEqual(decideCodex(event('Stop', { stop_hook_active: false }), true), { pending: true });
  assert.deepEqual(decideCodex(event('Stop', { stop_hook_active: false }), false), { pending: false });
});

test('Codex package is synchronized with shared sources', () => {
  const result = spawnSync(process.execPath, [path.join(__dirname, '../scripts/build-codex.js'), '--check'], {
    encoding: 'utf8',
  });
  assert.equal(result.status, 0, result.stderr);
  const hooks = JSON.parse(fs.readFileSync(path.join(__dirname, '../codex/plugins/argus/hooks/hooks.json'), 'utf8')).hooks;
  assert.deepEqual(Object.keys(hooks), ['UserPromptSubmit', 'PreToolUse', 'PostToolUse']);
  assert.equal(hooks.PostToolUse[0].matcher, '^(?:request_user_input)$');
});

test('generated Codex package runs independently with its own default state directory', (t) => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'argus-codex-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const installed = path.join(home, 'installed', 'argus');
  fs.cpSync(path.join(__dirname, '../codex/plugins/argus'), installed, { recursive: true });
  const script = path.join(installed, 'hooks/argus.js');
  const pendingFile = path.join(home, 'argus/codex-test.pending');
  const run = (input) => {
    const result = spawnSync(process.execPath, [script], {
      input: JSON.stringify(input), encoding: 'utf8', cwd: home,
      env: { ...process.env, CODEX_HOME: home, ARGUS_STATE_DIR: '', ARGUS_DEBUG: '' },
    });
    assert.equal(result.status, 0, result.stderr);
    return result.stdout;
  };
  const deny = (tool_name) => JSON.parse(run(event('PreToolUse', { tool_name }))).hookSpecificOutput.permissionDecision;

  const initial = JSON.parse(run(event('UserPromptSubmit', { prompt: '$confirm-first build' })));
  assert.ok(initial.hookSpecificOutput.additionalContext.includes(skillText()));
  assert.ok(fs.existsSync(pendingFile));
  assert.equal(deny('exec'), 'deny');
  assert.equal(deny('request_user_input_async'), 'deny');
  assert.equal(run(event('PreToolUse', { tool_name: 'request_user_input' })), '');
  assert.equal(run(answered(['需要修正'])), '');
  assert.ok(fs.existsSync(pendingFile));
  run(event('UserPromptSubmit', { prompt: '只分析今天的紀錄' }));
  const confirmed = JSON.parse(run(answered(['正確，開始執行'], { asString: true })));
  assert.match(confirmed.hookSpecificOutput.additionalContext, /Carry out the original request/);
  assert.ok(!fs.existsSync(pendingFile));
  assert.equal(run(event('PreToolUse', { tool_name: 'exec' })), '');

  run(event('UserPromptSubmit', { prompt: '$confirm-first another task' }));
  const cancelled = JSON.parse(run(event('UserPromptSubmit', { prompt: '取消此請求' })));
  assert.match(cancelled.hookSpecificOutput.additionalContext, /cancelled/);
  assert.ok(!fs.existsSync(pendingFile));

  // Pending files written by 0.3.x hold JSON; they still gate until an answer arrives
  fs.writeFileSync(pendingFile, '{"questionId":"call_old","phase":"waiting"}');
  assert.equal(deny('exec'), 'deny');
  assert.match(JSON.parse(run(answered(['取消此請求']))).hookSpecificOutput.additionalContext, /cancelled/);
  assert.ok(!fs.existsSync(pendingFile));
});
