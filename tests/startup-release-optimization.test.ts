import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { verifyPreparationHeadUnchanged } from "../tools/worker-local-release-rotation.mjs";

for (const shell of ["powershell.exe", "pwsh.exe"]) {
  test(`prepared Django deployment and overlapping readers (${shell})`, { skip: process.platform !== "win32", timeout: 120_000 }, () => {
    const result = spawnSync(shell, ["-NoProfile", "-NonInteractive", "-File", "tests/startup-release-optimization.test.ps1"],
      { encoding: "utf8", timeout: 110_000, windowsHide: true });
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}\n${result.error ?? ""}`);
    assert.match(result.stdout, /PASS:/);
  });
}

test("online preparation verifies the exact predecessor at every boundary and propagates unknown process failures", async () => {
  const before = { chainStateSha256: "a".repeat(64), head: { bindingSha256: "b".repeat(64) } };
  let verified = 0;
  const verify = async () => { verified++; };
  for (let boundary = 0; boundary < 4; boundary++) {
    assert.equal((await verifyPreparationHeadUnchanged(before, structuredClone(before), verify)).status, "preparation_only");
  }
  assert.equal(verified, 4);
  await assert.rejects(verifyPreparationHeadUnchanged({}, {}, verify), /complete predecessor bindings/);
  for (const after of [
    { ...before, chainStateSha256: "c".repeat(64) },
    { ...before, head: { bindingSha256: "c".repeat(64) } },
  ]) {
    await assert.rejects(verifyPreparationHeadUnchanged(before, after, verify), /predecessor changed/);
  }
  assert.equal(verified, 4);
  await assert.rejects(verifyPreparationHeadUnchanged(before, before, async () => { throw new Error("unknown process identity"); }), /unknown process identity/);
});
