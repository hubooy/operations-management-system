import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";

for (const shell of ["powershell.exe", "pwsh.exe"]) {
  test(`lifecycle interleavings and persistent maintenance (${shell})`, { skip: process.platform !== "win32", timeout: 120_000 }, () => {
    const result = spawnSync(shell, ["-NoProfile", "-NonInteractive", "-File", "tests/system-lifecycle-race.test.ps1", "-ChildShell", shell],
      { encoding: "utf8", timeout: 110_000, windowsHide: true });
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}\n${result.error ?? ""}`);
    assert.match(result.stdout, /PASS:/);
  });
}
