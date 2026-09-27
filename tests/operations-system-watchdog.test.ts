import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";

test("watchdog isolates failures, fences maintenance, limits retries, and reserves notifications", {
  skip: process.platform !== "win32",
  timeout: 60_000,
}, () => {
  const result = spawnSync("pwsh.exe", ["-NoProfile", "-NonInteractive", "-File", "tests/operations-system-watchdog.test.ps1"], {
    encoding: "utf8", windowsHide: true, timeout: 50_000,
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  const receipt = JSON.parse(result.stdout.trim());
  assert.ok(receipt.passed >= 28);
  assert.equal(receipt.productionServicesTouched, false);
  assert.equal(receipt.messagesSent, false);
});

test("watchdog GUI launcher suppresses console creation and preserves child exit status", {
  skip: process.platform !== "win32",
  timeout: 30_000,
}, () => {
  const result = spawnSync("pwsh.exe", ["-NoProfile", "-NonInteractive", "-File", "tests/watchdog-no-console.test.ps1"], {
    encoding: "utf8", windowsHide: true, timeout: 25_000,
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  const receipt = JSON.parse(result.stdout.trim());
  assert.equal(receipt.childConsole, "none");
  assert.equal(receipt.exitCodePropagated, true);
});
