'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

test('shared state override isolates gates, confirmation, cleanup, and logs across hosts', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'argus-isolation-'));
  t.after(() => {
    assert.equal(path.dirname(path.resolve(root)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(root).startsWith('argus-isolation-'));
    fs.rmSync(root, { recursive: true, force: true });
  });
  const scripts = {
    claude: path.join(__dirname, '../hooks/argus.js'),
    codex: path.join(__dirname, '../codex/plugins/argus/hooks/argus.js'),
  };
  const run = (host, hook_event_name, fields = {}) => {
    const result = spawnSync(process.execPath, [scripts[host]], {
      input: JSON.stringify({ session_id: 'same-session', hook_event_name, ...fields }),
      encoding: 'utf8',
      env: { ...process.env, ARGUS_STATE_DIR: root, ARGUS_DEBUG: '1' },
    });
    assert.equal(result.status, 0, result.stderr);
    return result.stdout ? JSON.parse(result.stdout) : null;
  };
  const denied = (host) => {
    assert.equal(run(host, 'PreToolUse', { tool_name: 'Bash' }).hookSpecificOutput.permissionDecision, 'deny');
  };

  run('claude', 'UserPromptSubmit', { prompt: '/argus:confirm-first claude-only' });
  denied('claude');
  assert.equal(run('codex', 'PreToolUse', { tool_name: 'Bash' }), null);
  run('codex', 'SessionEnd');
  denied('claude');

  run('codex', 'UserPromptSubmit', { prompt: '$confirm-first codex-only' });
  denied('codex');
  run('codex', 'UserPromptSubmit', { prompt: '正確，開始執行' });
  assert.equal(run('codex', 'PreToolUse', { tool_name: 'Bash' }), null);
  denied('claude');

  run('codex', 'UserPromptSubmit', { prompt: '$confirm-first codex-only' });
  run('claude', 'SessionEnd');
  assert.equal(run('claude', 'PreToolUse', { tool_name: 'Bash' }), null);
  denied('codex');

  const staleFiles = ['claude', 'codex'].map((host) => path.join(root, host, 'old.pending'));
  const old = new Date(Date.now() - 2 * 24 * 60 * 60 * 1000);
  for (const file of staleFiles) {
    fs.writeFileSync(file, '');
    fs.utimesSync(file, old, old);
  }
  run('codex', 'SessionStart');
  assert.ok(fs.existsSync(staleFiles[0]));
  assert.ok(!fs.existsSync(staleFiles[1]));
  run('claude', 'SessionStart');
  assert.ok(!fs.existsSync(staleFiles[0]));
  denied('codex');

  const claudeLog = fs.readFileSync(path.join(root, 'claude', 'debug.log'), 'utf8');
  const codexLog = fs.readFileSync(path.join(root, 'codex', 'debug.log'), 'utf8');
  assert.match(claudeLog, /claude-only/);
  assert.doesNotMatch(claudeLog, /codex-only/);
  assert.match(codexLog, /codex-only/);
  assert.doesNotMatch(codexLog, /claude-only/);
});
