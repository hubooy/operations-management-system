import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const scriptPath = path.resolve("tools/start-n8n-service.ps1");

test("n8n service launcher is hidden-task safe and refuses an unhealthy occupied port", () => {
  const source = fs.readFileSync(scriptPath, "utf8");

  assert.match(source, /Split-Path -Parent \$PSScriptRoot/);
  assert.match(source, /npm\\node_modules\\n8n\\bin\\n8n/);
  assert.match(source, /Get-NetTCPConnection -State Listen -LocalPort 5678/);
  assert.match(source, /http:\/\/127\.0\.0\.1:5678\/healthz/);
  assert.match(source, /Port 5678 is occupied, but the n8n health endpoint is unavailable/);
  assert.match(source, /\$env:NODES_EXCLUDE = '\["n8n-nodes-base\.localFileTrigger"\]'/);
  assert.doesNotMatch(source, /\$env:NODES_EXCLUDE = '\[\]'/);
  assert.match(source, /Invoke-N8nNativeProcess -NodePath \$nodeCommand -EntryPath \$n8nEntry/);
  assert.doesNotMatch(source, /password|cookie|token|webhook/i);
});
