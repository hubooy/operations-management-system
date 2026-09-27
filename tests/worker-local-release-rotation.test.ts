import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { writeCanonical, makeRelease, fixture, lineage, approvedTransition, approvedRecord, installCandidateEntrypoints } from "./fixtures/worker-release-rotation";

import {
  activationFenceRelativePath,
  salesRetirementMigrationSha256,
  sha256Bytes,
  sha256Canonical,
  withPayloadSha256,
  workerGuardEntrypointPaths,
} from "../tools/worker-local-release.mjs";
import {
  applyApprovedRotationPlanForTest,
  buildEntrypointPlan,
  publishSuccessorRecord,
  publishRotationPlan,
  publicationStagingDirectoryName,
  atomicInstallFile,
  releaseBinding,
  resolveEffectiveReleaseChain,
  rotationConsumptionDirectoryName,
  rotationPlanPayload,
  successorDirectoryName,
  successorPayload,
} from "../tools/worker-local-release-rotation.mjs";
import { assertReleaseWorkerLaunchAllowed } from "../tools/worker-authority-guard.mjs";
import { createD1RetirementReceipt, d1ReceiptRelativePath } from "../tools/d1-retirement-proof.mjs";

const hex = (character: string) => character.repeat(64);

test("successor sequence remains bounded while allowing continued forward releases", () => {
  const predecessor = releaseBinding({
    releaseId: "20260903T000000Z-0000000000000001",
    manifestSha256: hex("1"),
    guardReceiptSha256: hex("2"),
  });
  const successor = releaseBinding({
    releaseId: "20260903T000001Z-0000000000000002",
    manifestSha256: hex("3"),
    guardReceiptSha256: hex("4"),
  });
  const input = {
    predecessor,
    successor,
    lineage: {},
    approvedPlanSha256: hex("5"),
    activatedAt: "2026-09-03T00:00:00.000Z",
  };

  assert.equal(successorPayload({ ...input, sequence: 33 }).sequence, 33);
  assert.equal(successorPayload({ ...input, sequence: 128 }).sequence, 128);
  assert.equal(successorPayload({ ...input, sequence: 129 }).sequence, 129);
  assert.equal(successorPayload({ ...input, sequence: 256 }).sequence, 256);
  for (const sequence of [0, -1, 1.5, 257, Infinity]) {
    assert.throws(() => successorPayload({ ...input, sequence }), /sequence/);
  }
});

test("full successor verification crosses the old 128 boundary without dropping history", async () => {
  const item = await fixture();
  try {
    let chain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
    let previous = item.bootstrapRelease;
    for (let sequence = 1; sequence <= 129; sequence++) {
      const createdAt = new Date(Date.UTC(2026, 8, 6, 0, 0, sequence)).toISOString();
      const releaseId = createdAt.replace(/[-:]/g, "").replace(/\.000Z$/, "Z") + "-" + sequence.toString(16).padStart(16, "0");
      const next = await makeRelease(item.runtime, item.protectedRoot, releaseId, `service-${sequence}`, {
        createdAt, buildFingerprint: sequence.toString(16).padStart(64, "0"),
      });
      const transition = await approvedTransition(item, chain, next, previous, createdAt);
      await publishSuccessorRecord(item.runtime, transition.record, chain.bootstrap);
      chain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
      assert.equal(chain.successorCount, sequence);
      previous = next;
    }
    const records = await readdir(path.join(item.runtime, "state", successorDirectoryName));
    assert.equal(records.length, 258);
    const firstSidecar = path.join(item.runtime, "state", successorDirectoryName, records.find((name) => name.endsWith(".sha256"))!);
    await writeFile(firstSidecar, "0".repeat(64) + "\n");
    await assert.rejects(resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true }), /sidecar 无效/);
  } finally {
    assert.equal(path.dirname(item.runtime).toLowerCase(), path.resolve(tmpdir()).toLowerCase());
    assert.ok(path.basename(item.runtime).startsWith("teruisi-worker-rotation-"));
    await rm(item.runtime, { recursive: true, force: true });
  }
});

async function protectedSnapshot(
  protectedRoot: string,
  entrypoints: Array<{ relativePath: string }>,
) {
  const result: Record<string, string | null> = {};
  for (const { relativePath } of entrypoints) {
    try {
      result[relativePath] = sha256Bytes(await readFile(path.join(protectedRoot, ...relativePath.split("/"))));
    } catch (error) {
      if (error && typeof error === "object" && "code" in error && error.code === "ENOENT") result[relativePath] = null;
      else throw error;
    }
  }
  return result;
}

async function releaseManifestContext(release: Awaited<ReturnType<typeof makeRelease>>) {
  return {
    manifestPath: release.manifestPath,
    manifestSha256: release.manifestSha256,
    manifest: JSON.parse(await readFile(release.manifestPath, "utf8")),
  };
}

async function directoryNamesOrEmpty(target: string) {
  try {
    return (await readdir(target)).sort();
  } catch (error) {
    if (error && typeof error === "object" && "code" in error && error.code === "ENOENT") return [];
    throw error;
  }
}

test("adopted proof admits two successive releases after the isolated D1 file disappears", async () => {
  const item = await fixture();
  try {
    let adoptions = 0;
    const testDependencies = { verifyD1Adoption: async () => { adoptions++; } };
    const firstChain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
    const first = await approvedTransition(item, firstChain);
    await applyApprovedRotationPlanForTest({ runtimeRoot: item.runtime, approvedPlanSha256: first.planSha256,
      cutoverEvidence: item.cutoverEvidence, testDependencies });
    await rm(path.join(item.runtime, "state.sqlite"));
    const launch = (release: typeof item.candidate) => assertReleaseWorkerLaunchAllowed({ runtimeRoot: item.runtime,
      manifestPath: release.manifestPath, manifestSha256: release.manifestSha256, allowTestRuntimeRoot: true });
    assert.equal((await launch(item.candidate)).status, "allowed");
    const next = await makeRelease(item.runtime, item.protectedRoot, "20260906T000000Z-3333333333333333", "next-service",
      { createdAt: "2026-09-06T00:00:00.000Z", buildFingerprint: hex("7") });
    const nextChain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
    const second = await approvedTransition(item, nextChain, next, item.candidate, "2026-09-06T00:01:00.000Z");
    await applyApprovedRotationPlanForTest({ runtimeRoot: item.runtime, approvedPlanSha256: second.planSha256,
      cutoverEvidence: item.cutoverEvidence, testDependencies });
    assert.equal((await launch(next)).status, "allowed");
    assert.equal(adoptions, 1);
    await assert.rejects(launch(item.candidate), /effective head/);
    await writeFile(path.join(next.releaseRoot, ...d1ReceiptRelativePath.split("/")), "{}");
    await assert.rejects(launch(next), /digest mismatch/);
  } finally {
    assert.equal(path.dirname(item.runtime).toLowerCase(), path.resolve(tmpdir()).toLowerCase());
    assert.ok(path.basename(item.runtime).startsWith("teruisi-worker-rotation-"));
    await rm(item.runtime, { recursive: true, force: true });
  }
});

test("failed initial D1 adoption revalidation leaves protected entrypoints untouched", async () => {
  const item = await fixture();
  try {
    const chain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
    const approved = await approvedTransition(item, chain);
    const before = await protectedSnapshot(item.protectedRoot, item.candidate.entrypoints);
    await assert.rejects(applyApprovedRotationPlanForTest({ runtimeRoot: item.runtime, approvedPlanSha256: approved.planSha256,
      cutoverEvidence: item.cutoverEvidence, testDependencies: { verifyD1Adoption: async () => { throw new Error("changed adoption evidence"); } } }), /changed adoption/);
    assert.deepEqual(await protectedSnapshot(item.protectedRoot, item.candidate.entrypoints), before);
    assert.equal((await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true })).successorCount, 0);
  } finally {
    assert.equal(path.dirname(item.runtime).toLowerCase(), path.resolve(tmpdir()).toLowerCase());
    assert.ok(path.basename(item.runtime).startsWith("teruisi-worker-rotation-"));
    await rm(item.runtime, { recursive: true, force: true });
  }
});

test("successors cannot remove or replace inherited retirement evidence before installing any entrypoint", async () => {
  for (const mode of ["remove", "replace"]) {
    const item = await fixture();
    try {
      const bootstrap = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
      const initial = await approvedTransition(item, bootstrap);
      await applyApprovedRotationPlanForTest({ runtimeRoot: item.runtime, approvedPlanSha256: initial.planSha256,
        cutoverEvidence: item.cutoverEvidence });
      const next = await makeRelease(item.runtime, item.protectedRoot, "20260906T000000Z-4444444444444444", "next-service");
      const manifest = JSON.parse(await readFile(next.manifestPath, "utf8"));
      if (mode === "remove") delete manifest.artifacts.d1RetirementReceipt;
      else {
        const receiptPath = path.join(next.releaseRoot, ...d1ReceiptRelativePath.split("/"));
        const receipt = JSON.parse(await readFile(receiptPath, "utf8"));
        delete receipt.proof.proofSha256;
        receipt.proof.sourceSchemaSha256 = hex("f");
        const replacement = createD1RetirementReceipt(withPayloadSha256(receipt.proof, "proofSha256"), manifest);
        manifest.artifacts.d1RetirementReceipt.sha256 = await writeCanonical(receiptPath, replacement);
      }
      delete manifest.manifestPayloadSha256;
      next.manifestSha256 = await writeCanonical(next.manifestPath, withPayloadSha256(manifest, "manifestPayloadSha256"));
      const chain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
      const approved = await approvedTransition(item, chain, next, item.candidate);
      const before = await protectedSnapshot(item.protectedRoot, next.entrypoints);
      await assert.rejects(applyApprovedRotationPlanForTest({ runtimeRoot: item.runtime, approvedPlanSha256: approved.planSha256,
        cutoverEvidence: item.cutoverEvidence }), /proof downgrade|evidence changed across successors/);
      assert.deepEqual(await protectedSnapshot(item.protectedRoot, next.entrypoints), before);
    } finally {
      assert.equal(path.dirname(item.runtime).toLowerCase(), path.resolve(tmpdir()).toLowerCase());
      assert.ok(path.basename(item.runtime).startsWith("teruisi-worker-rotation-"));
      await rm(item.runtime, { recursive: true, force: true });
    }
  }
});

test("append-only successor resolves an effective head without rewriting bootstrap current or authority", async () => {
  const item = await fixture();
  try {
    const beforePointer = await readFile(path.join(item.runtime, "current-deployment.json"));
    const beforeAuthority = await readFile(path.join(item.runtime, "state", "sales-postgresql-authority.json"));
    const chain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true, verifyInstalledHead: true });
    const record = await approvedRecord(item, chain);
    await installCandidateEntrypoints(item);
    await assert.rejects(
      resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true, verifyInstalledHead: true }),
      /effective head protected entrypoint 未匹配 guard/,
    );
    await publishSuccessorRecord(item.runtime, record, chain.bootstrap);
    const after = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true, verifyInstalledHead: true });
    assert.equal(after.head.releaseId, item.candidate.releaseId);
    assert.equal(after.successorCount, 1);
    assert.deepEqual(await readFile(path.join(item.runtime, "current-deployment.json")), beforePointer);
    assert.deepEqual(await readFile(path.join(item.runtime, "state", "sales-postgresql-authority.json")), beforeAuthority);
  } finally {
    await rm(item.runtime, { recursive: true, force: true });
  }
});

test("release launch guard authorizes only the append-only effective head after activation", async () => {
  const item = await fixture();
  try {
    const chain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
    const record = await approvedRecord(item, chain);
    await installCandidateEntrypoints(item);
    await publishSuccessorRecord(item.runtime, record, chain.bootstrap);
    await assert.rejects(assertReleaseWorkerLaunchAllowed({
      manifestPath: item.bootstrapRelease.manifestPath,
      manifestSha256: item.bootstrapRelease.manifestSha256,
      runtimeRoot: item.runtime,
      allowTestRuntimeRoot: true,
    }), /effective head release/);
    const candidateAllowed = await assertReleaseWorkerLaunchAllowed({
      manifestPath: item.candidate.manifestPath,
      manifestSha256: item.candidate.manifestSha256,
      runtimeRoot: item.runtime,
      allowTestRuntimeRoot: true,
    });
    assert.equal(candidateAllowed.mode, "release-post-cutover-successor");
  } finally {
    await rm(item.runtime, { recursive: true, force: true });
  }
});

test("successor publication is same-tuple idempotent and repairs only its exact missing sidecar", async () => {
  const item = await fixture();
  try {
    const chain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
    const record = await approvedRecord(item, chain);
    const first = await publishSuccessorRecord(item.runtime, record, chain.bootstrap);
    await rm(first.sidecarPath);
    const repaired = await publishSuccessorRecord(item.runtime, record, chain.bootstrap);
    assert.equal(repaired.sha256, first.sha256);
    const conflicting = successorPayload({ ...record, approvedPlanSha256: hex("7") });
    await assert.rejects(publishSuccessorRecord(item.runtime, conflicting, chain.bootstrap), /批准元组不一致/);
  } finally {
    await rm(item.runtime, { recursive: true, force: true });
  }
});

test("resolver rejects incomplete sidecars and unreachable fork records", async () => {
  const item = await fixture();
  try {
    const chain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
    const record = await approvedRecord(item, chain);
    const publication = await publishSuccessorRecord(item.runtime, record, chain.bootstrap);
    await rm(publication.sidecarPath);
    await assert.rejects(
      resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true }),
      /未知文件|不完整|孤立存在/,
    );
    await writeFile(publication.sidecarPath, `${publication.sha256}\n`, "ascii");
    const root = path.join(item.runtime, "state", successorDirectoryName);
    const orphanSidecar = path.join(root, `${hex("f")}.json.sha256`);
    await writeFile(orphanSidecar, `${hex("e")}\n`, "ascii");
    await assert.rejects(
      resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true }),
      /未知文件|不完整|孤立存在/,
    );
    await rm(orphanSidecar);
    const fakePredecessor = releaseBinding({
      releaseId: "20260830T000002Z-3333333333333333",
      manifestSha256: hex("8"),
      guardReceiptSha256: hex("9"),
    });
    const fork = successorPayload({ ...record, predecessor: fakePredecessor, sequence: 2 });
    const forkPath = path.join(root, `${fakePredecessor.bindingSha256}.json`);
    const forkSha = await writeCanonical(forkPath, fork);
    await writeFile(`${forkPath}.sha256`, `${forkSha}\n`, "ascii");
    await assert.rejects(
      resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true }),
      /不可达记录、分叉或伪造前驱/,
    );
  } finally {
    await rm(item.runtime, { recursive: true, force: true });
  }
});

test("rotation plan binds predecessor CAS, immutable evidence and protected-entrypoint before/after hashes", async () => {
  const item = await fixture();
  try {
    const chain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
    const plan = rotationPlanPayload({
      createdAt: "2026-08-30T00:01:00.000Z",
      chain,
      candidate: releaseBinding(item.candidate),
      lineage: lineage(item, item.bootstrapRelease.contractReceiptSha256, item.candidate.contractReceiptSha256),
      protectedEntrypoints: [{
        relativePath: "tools/worker-local-service.ps1",
        predecessorSha256: item.bootstrapRelease.entrypointSha256,
        candidateSha256: item.candidate.entrypointSha256,
      }],
    });
    assert.equal(plan.predecessorChainStateSha256, chain.chainStateSha256);
    assert.equal(plan.lineage.salesRetirementMigrationSha256, salesRetirementMigrationSha256);
    assert.equal(plan.protectedEntrypoints[0].predecessorSha256, item.bootstrapRelease.entrypointSha256);
    assert.equal(plan.protectedEntrypoints[0].candidateSha256, item.candidate.entrypointSha256);
    const planCore = { ...plan } as Record<string, unknown>;
    delete planCore.payloadSha256;
    assert.equal(plan.payloadSha256, sha256Canonical(planCore));
  } finally {
    await rm(item.runtime, { recursive: true, force: true });
  }
});

test("rotation planning accepts a stopped verifier upgrade mixed with predecessor protected entrypoints", async () => {
  const item = await fixture();
  try {
    const candidate = await makeRelease(
      item.runtime,
      item.protectedRoot,
      "20260830T000002Z-3333333333333333",
      "candidate-service",
      {
        createdAt: "2026-08-30T00:00:02.000Z",
        buildFingerprint: hex("8"),
        entrypointTextByPath: { "tools/worker-local-release.mjs": "candidate-verifier" },
      },
    );
    const verifier = candidate.entrypoints.find((entrypoint) => entrypoint.relativePath === "tools/worker-local-release.mjs")!;
    const verifierTarget = path.join(item.protectedRoot, ...verifier.relativePath.split("/"));
    await writeFile(verifierTarget, candidate.entrypointBytes.get(verifier.relativePath)!);
    const protectedEntrypoints = await buildEntrypointPlan(
      await releaseManifestContext(item.bootstrapRelease),
      await releaseManifestContext(candidate),
    );
    assert.equal(
      protectedEntrypoints.find((entrypoint) => entrypoint.relativePath === verifier.relativePath)?.candidateSha256,
      verifier.sha256,
    );
    await assert.rejects(
      resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true, verifyInstalledHead: true }),
      /protected entrypoint 未匹配 guard/,
    );
  } finally {
    await rm(item.runtime, { recursive: true, force: true });
  }
});

test("new rotation plans reject a candidate whose guarded batch entrypoint is absent from the immutable release", async () => {
  const item = await fixture();
  try {
    await rm(path.join(item.candidate.releaseRoot, "运行项目.bat"));
    await assert.rejects(buildEntrypointPlan(
      await releaseManifestContext(item.bootstrapRelease),
      await releaseManifestContext(item.candidate),
    ), /运行项目\.bat/);
  } finally {
    await rm(item.runtime, { recursive: true, force: true });
  }
});

test("new rotation plans reject manifest keyFiles drift before inspecting the protected root", async () => {
  const item = await fixture();
  try {
    const predecessor = await releaseManifestContext(item.bootstrapRelease);
    const candidate = await releaseManifestContext(item.candidate);
    candidate.manifest = {
      ...candidate.manifest,
      artifacts: {
        ...candidate.manifest.artifacts,
        keyFiles: candidate.manifest.artifacts.keyFiles.filter(
          (entrypoint: { relativePath: string }) => entrypoint.relativePath !== "运行项目.bat",
        ),
      },
    };
    await assert.rejects(buildEntrypointPlan(predecessor, candidate), /keyFiles.*运行项目\.bat/);
  } finally {
    await rm(item.runtime, { recursive: true, force: true });
  }
});

test("approved apply recovery skips absent candidate material when the protected batch and panel are already exact", async () => {
  const item = await fixture();
  try {
    const chain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
    const approved = await approvedTransition(item, chain);
    for (const relativePath of ["运行项目.bat", "tools/operations-system-control.ps1"]) {
      await rm(path.join(item.candidate.releaseRoot, ...relativePath.split("/")));
    }
    const recovered = await applyApprovedRotationPlanForTest({
      runtimeRoot: item.runtime,
      approvedPlanSha256: approved.planSha256,
      cutoverEvidence: item.cutoverEvidence,
    });
    assert.equal(recovered.status, "activated");
    for (const relativePath of ["运行项目.bat", "tools/operations-system-control.ps1"]) {
      assert.deepEqual(
        await readFile(path.join(item.protectedRoot, ...relativePath.split("/"))),
        item.candidate.entrypointBytes.get(relativePath),
      );
    }
  } finally {
    await rm(item.runtime, { recursive: true, force: true });
  }
});

test("test-only apply core activates and idempotently replays an approved first successor", async () => {
  const item = await fixture();
  try {
    const beforePointer = await readFile(path.join(item.runtime, "current-deployment.json"));
    const chain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
    const approved = await approvedTransition(item, chain);
    let startupVerifications = 0;
    const testDependencies = { installAndVerifyStartup: async () => { startupVerifications += 1; } };
    const first = await applyApprovedRotationPlanForTest({
      runtimeRoot: item.runtime,
      approvedPlanSha256: approved.planSha256,
      cutoverEvidence: item.cutoverEvidence,
      testDependencies,
    });
    assert.equal(first.status, "activated");
    assert.equal(first.releaseId, item.candidate.releaseId);
    const after = await resolveEffectiveReleaseChain({
      runtimeRoot: item.runtime, allowTestRuntimeRoot: true, verifyInstalledHead: true,
    });
    assert.equal(after.successorCount, 1);
    assert.deepEqual(await readFile(path.join(item.runtime, "current-deployment.json")), beforePointer);
    const replay = await applyApprovedRotationPlanForTest({
      runtimeRoot: item.runtime,
      approvedPlanSha256: approved.planSha256,
      cutoverEvidence: item.cutoverEvidence,
      testDependencies,
    });
    assert.equal(replay.status, "already_activated");
    assert.equal(replay.consumptionSha256, first.consumptionSha256);
    assert.equal(startupVerifications, 2);
    assert.deepEqual(await readdir(path.join(item.runtime, "state", publicationStagingDirectoryName)), []);
  } finally {
    await rm(item.runtime, { recursive: true, force: true });
  }
});

test("apply resumes a half-installed protected entrypoint set while predecessor launch stays fail-closed", async () => {
  const item = await fixture();
  try {
    const chain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
    const approved = await approvedTransition(item, chain);
    let installed = 0;
    await assert.rejects(applyApprovedRotationPlanForTest({
      runtimeRoot: item.runtime,
      approvedPlanSha256: approved.planSha256,
      cutoverEvidence: item.cutoverEvidence,
      testDependencies: {
        afterEntrypointInstalled: async () => {
          installed += 1;
          if (installed === 1) throw new Error("simulated entrypoint crash");
        },
      },
    }), /simulated entrypoint crash/);
    assert.equal(installed, 1);
    assert.deepEqual(await directoryNamesOrEmpty(path.join(item.runtime, "state", successorDirectoryName)), []);
    await assert.rejects(assertReleaseWorkerLaunchAllowed({
      manifestPath: item.bootstrapRelease.manifestPath,
      manifestSha256: item.bootstrapRelease.manifestSha256,
      runtimeRoot: item.runtime,
      allowTestRuntimeRoot: true,
    }), /guard entrypoints|protected entrypoint|entrypoints 无效/);
    const recovered = await applyApprovedRotationPlanForTest({
      runtimeRoot: item.runtime,
      approvedPlanSha256: approved.planSha256,
      cutoverEvidence: item.cutoverEvidence,
    });
    assert.equal(recovered.status, "activated");
    const after = await resolveEffectiveReleaseChain({
      runtimeRoot: item.runtime, allowTestRuntimeRoot: true, verifyInstalledHead: true,
    });
    assert.equal(after.head.releaseId, item.candidate.releaseId);
  } finally {
    await rm(item.runtime, { recursive: true, force: true });
  }
});

test("apply repairs an exact record-without-sidecar publication and ignores staging crash debris", async () => {
  const item = await fixture();
  try {
    const chain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
    const approved = await approvedTransition(item, chain);
    const successorRoot = path.join(item.runtime, "state", successorDirectoryName);
    await mkdir(successorRoot);
    const recordPath = path.join(successorRoot, `${approved.record.predecessor.bindingSha256}.json`);
    const recordSha256 = await writeCanonical(recordPath, approved.record);
    const stagingRoot = path.join(item.runtime, "state", publicationStagingDirectoryName);
    await mkdir(stagingRoot, { recursive: true });
    await writeFile(path.join(stagingRoot, "publication-crash-debris.tmp"), "incomplete", "utf8");
    await assert.rejects(
      resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true }),
      /不完整/,
    );
    const recovered = await applyApprovedRotationPlanForTest({
      runtimeRoot: item.runtime,
      approvedPlanSha256: approved.planSha256,
      cutoverEvidence: item.cutoverEvidence,
    });
    assert.equal(recovered.status, "already_activated");
    assert.equal(recovered.successorSha256, recordSha256);
    assert.equal((await readFile(`${recordPath}.sha256`, "ascii")).trim(), recordSha256);
    assert.deepEqual(await directoryNamesOrEmpty(stagingRoot), ["publication-crash-debris.tmp"]);
    const after = await resolveEffectiveReleaseChain({
      runtimeRoot: item.runtime, allowTestRuntimeRoot: true, verifyInstalledHead: true,
    });
    assert.equal(after.successorCount, 1);
  } finally {
    await rm(item.runtime, { recursive: true, force: true });
  }
});

test("startup install or readback failure leaves consumption unpublished and the same plan recoverable", async () => {
  const item = await fixture();
  try {
    const chain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
    const approved = await approvedTransition(item, chain);
    await assert.rejects(applyApprovedRotationPlanForTest({
      runtimeRoot: item.runtime,
      approvedPlanSha256: approved.planSha256,
      cutoverEvidence: item.cutoverEvidence,
      testDependencies: {
        installAndVerifyStartup: async () => { throw new Error("simulated VerifyStartup failure"); },
      },
    }), /simulated VerifyStartup failure/);
    const activated = await resolveEffectiveReleaseChain({
      runtimeRoot: item.runtime, allowTestRuntimeRoot: true, verifyInstalledHead: true,
    });
    assert.equal(activated.head.releaseId, item.candidate.releaseId);
    assert.deepEqual(
      await directoryNamesOrEmpty(path.join(item.runtime, "state", rotationConsumptionDirectoryName)),
      [],
    );
    const recovered = await applyApprovedRotationPlanForTest({
      runtimeRoot: item.runtime,
      approvedPlanSha256: approved.planSha256,
      cutoverEvidence: item.cutoverEvidence,
      testDependencies: { installAndVerifyStartup: async () => {} },
    });
    assert.equal(recovered.status, "already_activated");
    assert.deepEqual(
      await directoryNamesOrEmpty(path.join(item.runtime, "state", rotationConsumptionDirectoryName)),
      [`${approved.planSha256}.json`, `${approved.planSha256}.json.sha256`],
    );
  } finally {
    await rm(item.runtime, { recursive: true, force: true });
  }
});

test("a second successor succeeds when only its immutable activation fence changes", async () => {
  const item = await fixture();
  try {
    let chain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
    const first = await approvedTransition(item, chain);
    await applyApprovedRotationPlanForTest({
      runtimeRoot: item.runtime, approvedPlanSha256: first.planSha256, cutoverEvidence: item.cutoverEvidence,
    });
    chain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
    const secondCandidate = await makeRelease(
      item.runtime,
      item.protectedRoot,
      "20260830T000002Z-3333333333333333",
      "candidate-service",
      { createdAt: "2026-08-30T00:00:02.000Z", buildFingerprint: hex("8") },
    );
    const second = await approvedTransition(
      item, chain, secondCandidate, item.candidate, "2026-08-30T00:02:00.000Z",
    );
    const changed = second.plan.protectedEntrypoints.filter(
      (entrypoint) => entrypoint.predecessorSha256 !== entrypoint.candidateSha256,
    );
    assert.deepEqual(changed.map((entrypoint) => entrypoint.relativePath), [activationFenceRelativePath]);
    let secondInstalled = 0;
    await assert.rejects(applyApprovedRotationPlanForTest({
      runtimeRoot: item.runtime,
      approvedPlanSha256: second.planSha256,
      cutoverEvidence: item.cutoverEvidence,
      testDependencies: {
        afterEntrypointInstalled: async (entrypoint) => {
          secondInstalled += 1;
          assert.equal(entrypoint.relativePath, activationFenceRelativePath);
          throw new Error("simulated second-successor fence crash");
        },
      },
    }), /simulated second-successor fence crash/);
    assert.equal(secondInstalled, 1);
    const predecessorStillRecorded = await resolveEffectiveReleaseChain({
      runtimeRoot: item.runtime, allowTestRuntimeRoot: true,
    });
    assert.equal(predecessorStillRecorded.successorCount, 1);
    await assert.rejects(assertReleaseWorkerLaunchAllowed({
      manifestPath: item.candidate.manifestPath,
      manifestSha256: item.candidate.manifestSha256,
      runtimeRoot: item.runtime,
      allowTestRuntimeRoot: true,
    }), /protected entrypoint|入口未安装或已变更/);
    const result = await applyApprovedRotationPlanForTest({
      runtimeRoot: item.runtime,
      approvedPlanSha256: second.planSha256,
      cutoverEvidence: item.cutoverEvidence,
    });
    assert.equal(result.status, "activated");
    const after = await resolveEffectiveReleaseChain({
      runtimeRoot: item.runtime, allowTestRuntimeRoot: true, verifyInstalledHead: true,
    });
    assert.equal(after.successorCount, 2);
    assert.equal(after.head.releaseId, secondCandidate.releaseId);
    await assert.rejects(assertReleaseWorkerLaunchAllowed({
      manifestPath: item.candidate.manifestPath,
      manifestSha256: item.candidate.manifestSha256,
      runtimeRoot: item.runtime,
      allowTestRuntimeRoot: true,
    }), /effective head release/);
  } finally {
    await rm(item.runtime, { recursive: true, force: true });
  }
});

test("forged evidence, foreign protected roots and cyclic candidates fail before activation mutation", async () => {
  const cases = ["evidence", "path", "cycle"] as const;
  for (const kind of cases) {
    const item = await fixture();
    try {
      const chain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
      let candidate = item.candidate;
      const predecessor = item.bootstrapRelease;
      let approved;
      if (kind === "path") {
        candidate = await makeRelease(
          item.runtime,
          path.join(item.runtime, "foreign-protected"),
          "20260830T000003Z-4444444444444444",
          "foreign-service",
          { createdAt: "2026-08-30T00:00:03.000Z", buildFingerprint: hex("9") },
        );
        approved = await approvedTransition(item, chain, candidate, predecessor, "2026-08-30T00:03:00.000Z");
      } else if (kind === "cycle") {
        candidate = item.bootstrapRelease;
        approved = await approvedTransition(item, chain, candidate, predecessor, "2026-08-30T00:04:00.000Z");
      } else {
        const forgedLineage = {
          ...lineage(item, predecessor.contractReceiptSha256, candidate.contractReceiptSha256),
          attestationFileSha256: hex("a"),
        };
        const plan = rotationPlanPayload({
          createdAt: "2026-08-30T00:05:00.000Z",
          chain,
          candidate: releaseBinding(candidate),
          lineage: forgedLineage,
          protectedEntrypoints: candidate.entrypoints.map((entrypoint) => ({
            relativePath: entrypoint.relativePath,
            predecessorSha256: predecessor.entrypoints.find((old) => old.relativePath === entrypoint.relativePath)?.sha256 ?? null,
            candidateSha256: entrypoint.sha256,
          })),
        });
        const publication = await publishRotationPlan(item.runtime, plan);
        approved = { plan, planSha256: publication.planSha256 };
      }
      const before = await protectedSnapshot(item.protectedRoot, candidate.entrypoints);
      await assert.rejects(applyApprovedRotationPlanForTest({
        runtimeRoot: item.runtime,
        approvedPlanSha256: approved.planSha256,
        cutoverEvidence: item.cutoverEvidence,
      }), kind === "evidence" ? /当前真实 cutover evidence/
        : kind === "path" ? /未保持 PostgreSQL authority\/receipt lineage/
          : /重复或形成环/);
      assert.deepEqual(await protectedSnapshot(item.protectedRoot, candidate.entrypoints), before);
      assert.deepEqual(await directoryNamesOrEmpty(path.join(item.runtime, "state", successorDirectoryName)), []);
    } finally {
      await rm(item.runtime, { recursive: true, force: true });
    }
  }
});

test("startup shortcut recognizes the immutable seven-entry predecessor then rebinds exactly to effective head", async () => {
  const item = await fixture();
  try {
    assert.equal(item.bootstrapRelease.entrypoints.length, 7);
    assert.equal(item.candidate.entrypoints.length, workerGuardEntrypointPaths.length);
    const chain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
    const approved = await approvedTransition(item, chain);
    await applyApprovedRotationPlanForTest({
      runtimeRoot: item.runtime, approvedPlanSha256: approved.planSha256, cutoverEvidence: item.cutoverEvidence,
    });
    const startupRoot = path.join(item.runtime, "startup-test");
    await mkdir(startupRoot);
    const shortcutPath = path.join(startupRoot, "TERUISI Operations Worker.lnk");
    const powershell = String.raw`
$ErrorActionPreference = "Stop"
. $env:TERUISI_TEST_SERVICE -RuntimeRoot $env:TERUISI_TEST_RUNTIME -AllowTestRuntimeRoot -StartupShortcutPath $env:TERUISI_TEST_SHORTCUT -FunctionsOnly
$shell = New-Object -ComObject WScript.Shell
$target = (Get-Command "powershell.exe").Source
function Get-Expected([string]$manifest) {
  $releaseRoot = Split-Path -Parent $manifest
  $service = Join-Path $releaseRoot "tools\worker-local-service.ps1"
  $quote = [char]34
  return [pscustomobject]@{
    Root = $releaseRoot
    Service = $service
    Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File {0}{1}{0} -Action Start -ManifestPath {0}{2}{0}" -f $quote, $service, $manifest
  }
}
$old = Get-Expected $env:TERUISI_TEST_OLD_MANIFEST
$oldIdentity = Get-ManifestIdentity $env:TERUISI_TEST_OLD_MANIFEST
Save-StartupShortcutAtomic $shell $target $old.Arguments $old.Root
$oldLink = $shell.CreateShortcut($env:TERUISI_TEST_SHORTCUT)
if (-not (Test-IsControlledStartupShortcut $oldLink)) {
  throw "seven-entry predecessor shortcut was not controlled"
}
$new = Get-Expected $env:TERUISI_TEST_NEW_MANIFEST
Save-StartupShortcutAtomic $shell $target $new.Arguments $new.Root
if (-not (Test-StartupShortcutExact $shell $target $new.Arguments $new.Root)) { throw "effective head shortcut did not verify" }
[ordered]@{ oldControlled = $true; newExact = $true; oldManifest = $env:TERUISI_TEST_OLD_MANIFEST; newManifest = $env:TERUISI_TEST_NEW_MANIFEST } | ConvertTo-Json -Compress
`;
    const result = spawnSync("powershell.exe", [
      "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", powershell,
    ], {
      encoding: "utf8",
      windowsHide: true,
      env: {
        ...process.env,
        TERUISI_TEST_SERVICE: path.resolve("tools/worker-local-service.ps1"),
        TERUISI_TEST_RUNTIME: item.runtime,
        TERUISI_TEST_SHORTCUT: shortcutPath,
        TERUISI_TEST_OLD_MANIFEST: item.bootstrapRelease.manifestPath,
        TERUISI_TEST_NEW_MANIFEST: item.candidate.manifestPath,
      },
    });
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
    const output = JSON.parse(result.stdout.trim());
    assert.equal(output.oldControlled, true);
    assert.equal(output.newExact, true);
    await assert.rejects(assertReleaseWorkerLaunchAllowed({
      manifestPath: item.bootstrapRelease.manifestPath,
      manifestSha256: item.bootstrapRelease.manifestSha256,
      runtimeRoot: item.runtime,
      allowTestRuntimeRoot: true,
    }), /effective head release/);
  } finally {
    await rm(item.runtime, { recursive: true, force: true });
  }
});

test("Windows atomic installer replaces an existing entrypoint in one rename and leaves no temporary file", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "teruisi-worker-atomic-install-"));
  try {
    const source = path.join(root, "candidate.ps1");
    const target = path.join(root, "installed.ps1");
    const candidate = Buffer.from("candidate-entrypoint", "utf8");
    await writeFile(source, candidate);
    await writeFile(target, "predecessor-entrypoint", "utf8");
    await atomicInstallFile(source, target, sha256Bytes(candidate));
    assert.deepEqual(await readFile(target), candidate);
    assert.deepEqual(await readFile(source), candidate);
    assert.deepEqual((await readdir(root)).sort(), ["candidate.ps1", "installed.ps1"]);
    const sourceText = await readFile(path.resolve("tools/worker-local-release-rotation.mjs"), "utf8");
    const installer = sourceText.slice(sourceText.indexOf("export async function atomicInstallFile"), sourceText.indexOf("async function installProtectedEntrypoints"));
    assert.match(installer, /await rename\(temporary, target\)/);
    assert.doesNotMatch(installer, /rm\(target|unlink\(target/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("production rotation CLI rejects caller-controlled runtime/source/command overrides", async () => {
  const source = await readFile(path.resolve("tools/worker-local-release-rotation.mjs"), "utf8");
  assert.match(source, /production plan 不接受路径、命令或测试覆盖/);
  assert.match(source, /production apply 只接受 --approved-plan-sha256/);
  assert.doesNotMatch(source, /--source-root|--dev-vars-source|--persist-root|--source-d1-path|--command/);
  const service = await readFile(path.resolve("tools/worker-local-service.ps1"), "utf8");
  assert.match(service, /worker-local-release-rotation\.mjs/);
  assert.match(service, /ManifestPath is not the authorized effective head release/);
  assert.match(source, /validateApprovedRotationBeforeMutation/);
  assert.match(source, /resolveEffectiveReleaseChain\(\{ allowTestRuntimeRoot, verifyInstalledHead: false \}\)/);
  assert.match(source, /dependencies\.verifyHeadStopped/);
  assert.equal((source.match(/await dependencies\.verifyHeadStopped/g) ?? []).length, 2);
  assert.match(source, /beforeMutation\.chain\.chainStateSha256 !== validated\.chain\.chainStateSha256/);
  assert.match(source, /dependencies\.assertCandidateStopped[\s\S]+installProtectedEntrypoints/);
  assert.match(source, /installProtectedEntrypoints[\s\S]+dependencies\.assertCandidateStopped[\s\S]+publishSuccessorRecord/);
  assert.match(source, /verifyInstalledGuardEntrypoints\(validated\.candidateManifest[\s\S]+publishSuccessorRecord/);
  assert.match(source, /worker-release-publication-staging/);
  assert.match(source, /InstallStartup[\s\S]+VerifyStartup/);
  const releaseSource = await readFile(path.resolve("tools/worker-local-release.mjs"), "utf8");
  assert.match(releaseSource, /\.runtime\/worker-release-activation-fence\.json/);
  assert.match(source, /Local\\\\TERUISI\.Worker\.LocalService\.v1/);
});
