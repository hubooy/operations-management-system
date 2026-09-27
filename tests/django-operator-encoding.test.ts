import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import test from "node:test";

test("Windows PowerShell 5 parses every shipped operator without executing it", { skip: process.platform !== "win32" }, () => {
  const result = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", `
    $failures = @()
    $files = @(Get-ChildItem -LiteralPath $env:TERUISI_PS_PARSE_ROOT -Filter '*.ps1')
    foreach ($file in $files) {
      $parseErrors = $null
      [System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$null, [ref]$parseErrors) | Out-Null
      if ($parseErrors.Count) { $failures += $file.Name }
    }
    [ordered]@{ version=$PSVersionTable.PSVersion.Major; files=$files.Count; failures=@($failures) } | ConvertTo-Json -Compress
  `], { encoding: "utf8", windowsHide: true, timeout: 15000,
    env: { ...process.env, TERUISI_PS_PARSE_ROOT: path.resolve("tools") } });
  assert.equal(result.status, 0, result.stderr);
  const report = JSON.parse(result.stdout);
  assert.equal(report.version, 5);
  assert.ok(report.files > 20);
  assert.deepEqual(report.failures, []);
});
