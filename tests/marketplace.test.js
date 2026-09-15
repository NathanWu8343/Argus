'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const root = path.resolve(__dirname, '..');
const read = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));

test('repository marketplaces route each host to its own package', () => {
  const codex = read(path.join(root, '.agents/plugins/marketplace.json'));
  const claude = read(path.join(root, '.claude-plugin/marketplace.json'));
  assert.equal(codex.name, 'argus');
  assert.deepEqual(codex.plugins.find(p => p.name === 'argus').source,
    { source: 'local', path: './codex/plugins/argus' });
  assert.equal(claude.plugins.find(p => p.name === 'argus').source, '.');
});

test('Codex installs the native package from a repository containing both marketplaces', {
  skip: process.env.CODEX_CLI ? false : 'Set CODEX_CLI to the Codex CLI JavaScript entry point',
}, (t) => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'argus-marketplace-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const run = (...args) => {
    const result = spawnSync(process.execPath, [process.env.CODEX_CLI, ...args], {
      encoding: 'utf8', timeout: 30000,
      env: { ...process.env, CODEX_HOME: home },
    });
    assert.equal(result.status, 0, result.stderr || result.error?.message);
    return JSON.parse(result.stdout);
  };
  const marketplace = run('plugin', 'marketplace', 'add', root, '--json');
  assert.equal(marketplace.marketplaceName, 'argus');
  const result = run('plugin', 'add', 'argus@argus', '--json');
  assert.ok(result.installedPath, JSON.stringify(result));
  const installed = result.installedPath;
  const expected = path.join(root, 'codex/plugins/argus');
  for (const file of ['.codex-plugin/plugin.json', 'hooks/hooks.json', 'hooks/argus.js',
    'hooks/core.js', 'skills/confirm-first/SKILL.md', 'skills/confirm-first/agents/openai.yaml']) {
    assert.deepEqual(fs.readFileSync(path.join(installed, file)), fs.readFileSync(path.join(expected, file)));
  }
  assert.equal(require(path.join(installed, 'hooks/argus.js')).adapter.stateNamespace, 'codex');
  const hooks = read(path.join(installed, 'hooks/hooks.json')).hooks;
  assert.match(hooks.PostToolUse[0].matcher, /request_user_input/);
});
