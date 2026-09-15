'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { decide } = require('../hooks/argus.js');
const { decide: decideCodex } = require('../codex/src/argus.js');

const event = (hook_event_name, fields = {}) => ({ session_id: 'codex-test', hook_event_name, ...fields });

test('Codex explicit invocation activates the shared gate without affecting Claude commands', () => {
  for (const prompt of [
    '$confirm-first build', '  $argus:confirm-first build',
    '[$confirm-first](C:/plugins/argus/skills/confirm-first/SKILL.md) build',
    '[$argus:confirm-first](/plugins/argus/skills/confirm-first/SKILL.md) build',
  ]) {
    assert.equal(decideCodex(event('UserPromptSubmit', { prompt }), false).pending, true);
    assert.equal(decide(event('UserPromptSubmit', { prompt }), false).pending, false);
  }
  for (const prompt of [
    '$confirm-firstx', 'mention $confirm-first', 'ordinary request',
    '[$confirm-firstx](/skills/other/SKILL.md) build',
    'mention [$confirm-first](/skills/confirm-first/SKILL.md)',
  ]) {
    assert.equal(decideCodex(event('UserPromptSubmit', { prompt }), false).pending, false);
  }
});

test('Codex allows its question tools and denies other observed local tools', () => {
  for (const tool_name of ['request_user_input', 'request_user_input_async']) {
    assert.deepEqual(decideCodex(event('PreToolUse', { tool_name }), true), { pending: true });
  }
  for (const tool_name of ['Bash', 'apply_patch', 'spawn_agent', 'mcp__fs__read', 'AskUserQuestion']) {
    const result = decideCodex(event('PreToolUse', { tool_name }), true);
    assert.equal(result.output.hookSpecificOutput.permissionDecision, 'deny');
  }
});

test('Codex synchronous answers are matched by question id and must be a single option', () => {
  const tool_input = { questions: [{ id: 'argus', header: 'argus', options: [
    { label: '正確，開始執行' }, { label: '需要修正' }, { label: '取消此請求' },
  ] }] };
  for (const [answers, pending] of [
    [['正確，開始執行'], false], [['取消此請求'], false], [['需要修正'], true],
    [['正確，但修改範圍'], true], [[], true], [['正確，開始執行', '需要修正'], true],
  ]) {
    const input = event('PostToolUse', {
      tool_name: 'request_user_input', tool_input,
      tool_response: { answers: { argus: { answers } } },
    });
    assert.equal(decideCodex(input, true).pending, pending);
  }
  assert.equal(decideCodex(event('PostToolUse', {
    tool_name: 'request_user_input_async', tool_input: { questions: [{
      title: '以上理解正確嗎？', options: ['正確，開始執行', '需要修正', '取消此請求'],
    }] },
    tool_response: { status: 'pending' },
  }), true).pending, true);
});

test('Codex asynchronous waiting preserves the gate until an exact user reply', () => {
  assert.deepEqual(decideCodex(event('Stop'), true), { pending: true });
  for (const prompt of ['需要修正', '正確，但先改名稱', '取消此請求並執行其他工作', '']) {
    assert.equal(decideCodex(event('UserPromptSubmit', { prompt }), true).pending, true);
  }
  for (const prompt of ['正確，開始執行', '取消此請求', '  正確，開始執行\n']) {
    assert.equal(decideCodex(event('UserPromptSubmit', { prompt }), true).pending, false);
  }
});

test('Codex rejects altered confirmation option labels even if the user selects them', () => {
  for (const label of ['正確，但先改名稱', '取消此請求並執行其他工作']) {
    const input = event('PostToolUse', {
      tool_name: 'request_user_input',
      tool_input: { questions: [{ id: 'argus', header: 'argus', options: [{ label }] }] },
      tool_response: { answers: { argus: { answers: [label] } } },
    });
    assert.equal(decideCodex(input, true).pending, true);
  }
});

test('Codex SessionEnd timeout respects the host limit without changing Claude hooks', () => {
  const readHooks = (file) => JSON.parse(fs.readFileSync(path.join(__dirname, file), 'utf8')).hooks;
  const codex = readHooks('../codex/plugins/argus/hooks/hooks.json');
  const claude = readHooks('../hooks/hooks.json');
  for (const group of codex.SessionEnd) {
    for (const hook of group.hooks) assert.ok(hook.timeout >= 1 && hook.timeout <= 3);
  }
  assert.equal(claude.SessionEnd[0].hooks[0].timeout, 10);
});

test('Codex package is synchronized with shared sources', () => {
  const result = spawnSync(process.execPath, [path.join(__dirname, '../scripts/build-codex.js'), '--check'], {
    encoding: 'utf8',
  });
  assert.equal(result.status, 0, result.stderr);
});

test('generated Codex package runs independently with its own default state directory', (t) => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'argus-codex-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const installed = path.join(home, 'installed', 'argus');
  fs.cpSync(path.join(__dirname, '../codex/plugins/argus'), installed, { recursive: true });
  const script = path.join(installed, 'hooks/argus.js');
  const run = (input) => {
    const result = spawnSync(process.execPath, [script], {
      input: JSON.stringify(input), encoding: 'utf8', cwd: home,
      env: { ...process.env, CODEX_HOME: home, ARGUS_STATE_DIR: '', ARGUS_DEBUG: '' },
    });
    assert.equal(result.status, 0, result.stderr);
    return result.stdout;
  };
  run(event('UserPromptSubmit', { prompt: '$confirm-first build' }));
  assert.ok(fs.existsSync(path.join(home, 'argus/codex-test.pending')));
  assert.equal(JSON.parse(run(event('PreToolUse', { tool_name: 'Bash' }))).hookSpecificOutput.permissionDecision, 'deny');
  run(event('UserPromptSubmit', { prompt: '正確，開始執行' }));
  assert.equal(run(event('PreToolUse', { tool_name: 'Bash' })), '');
  run(event('UserPromptSubmit', { prompt: '$confirm-first build' }));
  run(event('SessionEnd'));
  assert.ok(!fs.existsSync(path.join(home, 'argus/codex-test.pending')));
});
