'use strict';

const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const output = path.join(root, 'codex', 'plugins', 'argus');
const check = process.argv.includes('--check');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8').replaceAll('\r\n', '\n');
const json = (value) => JSON.stringify(value, null, 2) + '\n';
const { adapter } = require(path.join(root, 'codex', 'src', 'argus.js'));

const metadata = JSON.parse(read('.claude-plugin/plugin.json'));
const plugin = { ...metadata, ...JSON.parse(read('codex/src/plugin.json')) };
plugin.interface.developerName = metadata.author.name;

// Same events and commands; Codex's plugin root variable and its question tools
const hooks = JSON.parse(read('hooks/hooks.json').replaceAll('${CLAUDE_PLUGIN_ROOT}', '${PLUGIN_ROOT}'));
hooks.description = `confirm-first gate: blocks every tool except ${adapter.questionTools.join(' and ')} until the user confirms`;
hooks.hooks.PostToolUse[0].matcher = `^(?:${adapter.questionTools.join('|')})$`;
// Codex caps SessionEnd hooks at three seconds; keep Claude's timeout unchanged
for (const group of hooks.hooks.SessionEnd) {
  for (const hook of group.hooks) hook.timeout = 3;
}

// Drop the Claude-only front matter
const skill = read('skills/confirm-first/SKILL.md')
  .replace(/^argument-hint:.*\n/m, '')
  .replace(/^disable-model-invocation: true\n/m, '');

const files = {
  '.codex-plugin/plugin.json': json(plugin),
  'skills/confirm-first/SKILL.md': skill,
  'skills/confirm-first/agents/openai.yaml': read('codex/src/openai.yaml'),
  'hooks/hooks.json': json(hooks),
  'hooks/core.js': read('hooks/core.js'),
  'hooks/argus.js': read('codex/src/argus.js'),
};

for (const [name, content] of Object.entries(files)) {
  const destination = path.join(output, name);
  if (check) {
    if (!fs.existsSync(destination) || fs.readFileSync(destination, 'utf8').replaceAll('\r\n', '\n') !== content) {
      throw new Error(`Codex 套件未同步：${name}；請執行 node scripts/build-codex.js`);
    }
  } else {
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.writeFileSync(destination, content);
  }
}
console.log(check ? 'Codex 套件與共用來源一致。' : `已產生 Codex 套件：${output}`);
