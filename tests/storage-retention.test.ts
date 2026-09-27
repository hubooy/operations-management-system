import "./storage-retention-policy.test.mjs";
import "./github-dependency-archive.test.mjs";
import assert from "node:assert/strict";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { fixture, approvedRecord, installCandidateEntrypoints } from "./fixtures/worker-release-rotation";
import { resolveEffectiveReleaseChain, publishSuccessorRecord } from "../tools/worker-local-release-rotation.mjs";
import { assertReleaseWorkerLaunchAllowed } from "../tools/worker-authority-guard.mjs";
import { candidatePath } from "../tools/storage-dependency-cleanup.mjs";
import { runProcess } from "../tools/worker-local-release.mjs";

test("native cleanup operator rejects active or uninspectable processes and escaped paths", { skip: process.platform !== "win32" }, async () => {
  const script = fileURLToPath(new URL("./storage-dependency-cleanup.test.ps1", import.meta.url));
  const result = await runProcess("powershell.exe", ["-NoProfile", "-NonInteractive", "-File", script], { label: "isolated cleanup operator tests" });
  assert.match(result.stdout.toString(), /checks passed/);
});

test("cleanup only accepts exact historical dependency targets and rejects retained or unrelated releases", () => {
  const old = "20260901T010000Z-0123456789abcdef";
  const current = "20260912T010000Z-0123456789abcdef";
  const plan = { scope: "worker-node-modules-only", retainedReleaseIds: [current], candidates: [{ releaseId: old }] };
  const root = path.resolve(tmpdir(), "synthetic-runtime");
  assert.equal(candidatePath(plan, old, root).target, path.join(root, "releases", old, "node_modules"));
  for (const unsafe of [current, "../node_modules", "20260902T010000Z-0123456789abcdef", old + "/../other"]) {
    assert.throws(() => candidatePath(plan, unsafe, root));
  }
  assert.throws(() => candidatePath({ ...plan, candidates: [plan.candidates[0], plan.candidates[0]] }, old, root));
  assert.throws(() => candidatePath({ ...plan, scope: "database" }, old, root));
});

test("historical dependency removal preserves full chain evidence and only the current launch identity", async () => {
  const item = await fixture();
  try {
    const initial = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
    const record = await approvedRecord(item, initial);
    await installCandidateEntrypoints(item);
    await publishSuccessorRecord(item.runtime, record, initial.bootstrap);
    const options = { runtimeRoot: item.runtime, allowTestRuntimeRoot: true, verifyInstalledHead: true };
    const before = await resolveEffectiveReleaseChain(options);
    const historicalDependencies = path.join(item.bootstrapRelease.releaseRoot, "node_modules");
    await mkdir(historicalDependencies);
    await writeFile(path.join(historicalDependencies, "synthetic.js"), "dependency");
    assert.equal(path.dirname(item.runtime).toLowerCase(), path.resolve(tmpdir()).toLowerCase());
    assert.ok(historicalDependencies.startsWith(item.runtime + path.sep));
    await rm(historicalDependencies, { recursive: true });
    const after = await resolveEffectiveReleaseChain(options);
    assert.equal(after.chainStateSha256, before.chainStateSha256);
    assert.equal(after.head.releaseId, item.candidate.releaseId);
    await assertReleaseWorkerLaunchAllowed({ manifestPath: item.candidate.manifestPath,
      manifestSha256: item.candidate.manifestSha256, runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
    await assert.rejects(assertReleaseWorkerLaunchAllowed({ manifestPath: item.bootstrapRelease.manifestPath,
      manifestSha256: item.bootstrapRelease.manifestSha256, runtimeRoot: item.runtime, allowTestRuntimeRoot: true }), /effective head/);
    const guardPath = path.join(item.bootstrapRelease.releaseRoot, "audit", "legacy-worker-guard-receipt.json");
    const original = await readFile(guardPath);
    await writeFile(guardPath, Buffer.concat([original, Buffer.from(" ")]));
    await assert.rejects(resolveEffectiveReleaseChain(options), /canonical|SHA-256/);
  } finally {
    assert.equal(path.dirname(item.runtime).toLowerCase(), path.resolve(tmpdir()).toLowerCase());
    assert.ok(path.basename(item.runtime).startsWith("teruisi-worker-rotation-"));
    await rm(item.runtime, { recursive: true, force: true });
  }
});
