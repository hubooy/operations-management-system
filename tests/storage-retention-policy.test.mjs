import assert from "node:assert/strict";
import test from "node:test";
import { dependencyRetentionPlan, backupRetentionPlan, assertPrivateRepository, assertArchiveProof, dependencyAlgorithm } from "../tools/storage-retention-policy.mjs";

const hash = (digit) => digit.repeat(64);
const tree = { algorithm: dependencyAlgorithm, fileCount: 2, sha256: hash("a") };
function releases(count = 8) {
  return Array.from({ length: count }, (_, i) => ({
    releaseId: `20260912T000000Z-${i.toString(16).padStart(16, "0")}`,
    manifestSha256: hash("b"), tree,
  }));
}
function input() {
  const all = releases();
  return { releases: all, headReleaseId: all.at(-1).releaseId, chainStateSha256: hash("c") };
}

test("dependency retention preserves current and two predecessors, grouping duplicate contents", () => {
  const args = input();
  const plan = dependencyRetentionPlan(args);
  assert.equal(plan.candidates.length, 5);
  assert.deepEqual(plan.retainedReleaseIds, args.releases.slice(-3).map((r) => r.releaseId));
  assert.equal(plan.archiveGroups.length, 1);
  assert.equal(plan.archiveGroups[0].releaseIds.length, 5);
  assert.equal(plan.scope, "worker-node-modules-only");
});

test("a short chain never produces destructive candidates", () => {
  for (const count of [1, 2, 3]) {
    const all = releases(count);
    assert.equal(dependencyRetentionPlan({ ...input(), releases: all, headReleaseId: all.at(-1).releaseId }).candidates.length, 0);
  }
});

test("planning rejects reordered tips, duplicate releases, traversal, unknown trees and unsafe retention", () => {
  assert.throws(() => dependencyRetentionPlan({ ...input(), keepLatest: 1 }));
  assert.throws(() => dependencyRetentionPlan({ ...input(), headReleaseId: input().releases[0].releaseId }));
  for (const replacement of [
    { releaseId: "../../current" }, { manifestSha256: "not-a-digest" },
    { tree: { ...tree, fileCount: 0 } }, { tree: { ...tree, algorithm: "sha1" } },
    { tree: { ...tree, fileCount: 100001 } },
  ]) {
    const args = input();
    args.releases[0] = { ...args.releases[0], ...replacement };
    assert.throws(() => dependencyRetentionPlan(args));
  }
  const args = input();
  args.releases[0] = args.releases[1];
  assert.throws(() => dependencyRetentionPlan(args));
});

test("private destination binds both owner/name and numeric repository identity", () => {
  const repo = { full_name: "owner/backups", private: true, visibility: "private", id: 123, archived: false };
  assert.equal(assertPrivateRepository(repo, "owner/backups", 123), 123);
  for (const value of [
    { ...repo, private: false }, { ...repo, visibility: "public" },
    { ...repo, archived: true }, { ...repo, id: 456 },
    { ...repo, full_name: "other/backups" },
  ]) assert.throws(() => assertPrivateRepository(value, "owner/backups", 123));
});

test("cloud upload alone is never proof of recoverability", () => {
  const proof = { version: "teruisi-dependency-archive-proof-v1", repositoryId: 123, tree,
    downloadVerified: true, extractionVerified: true, archiveSha256: hash("d"), archiveBytes: 1234, assetId: 2, releaseId: 3 };
  assertArchiveProof(proof, tree, 123);
  for (const replacement of [
    { downloadVerified: false }, { extractionVerified: false }, { archiveBytes: 2 ** 31 },
    { tree: { ...tree, sha256: hash("e") } }, { repositoryId: 456 }, { assetId: 0 },
  ]) assert.throws(() => assertArchiveProof({ ...proof, ...replacement }, tree, 123));
});

test("backup planning protects age floor, newest seven and pinned evidence; grants no delete authority", () => {
  const backups = Array.from({ length: 10 }, (_, i) => ({
    backupId: `daily-20260801T000000Z-${i.toString(16).padStart(12, "0")}`,
    completedAt: `2026-08-${String(10 - i).padStart(2, "0")}T00:00:00Z`,
    manifestSha256: hash("a"), verified: true, pinned: i === 8,
  }));
  const planned = backupRetentionPlan(backups, { now: "2026-09-13T00:00:00Z" });
  assert.equal(planned.filter((b) => b.action === "retain-local").length, 8);
  assert.equal(planned.every((b) => b.deletionAuthorized === false), true);
  assert.throws(() => backupRetentionPlan(backups, { now: "2026-09-13T00:00:00Z", localDays: 7 }));
  assert.throws(() => backupRetentionPlan([{ ...backups[0], verified: false }], { now: "2026-09-13T00:00:00Z" }));
  assert.throws(() => backupRetentionPlan([backups[0], backups[0]], { now: "2026-09-13T00:00:00Z" }));
  assert.throws(() => backupRetentionPlan(backups, { now: "2026-07-13T00:00:00Z" }));
});
