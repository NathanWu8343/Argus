// Development shim so codex/src/argus.js resolves the shared core from the repo.
// The build ships hooks/core.js itself next to the adapter; this file is not copied.
module.exports = require('../../hooks/core');
