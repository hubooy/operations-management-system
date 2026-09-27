import { createHash } from "node:crypto";

export const policyVersion = "teruisi-storage-retention-v1";
export const dependencyAlgorithm = "sha256-ordinal-path-length-content-v1";
const releaseIdPattern = /^\d{8}T\d{6}Z-[0-9a-f]{16}$/;
const digestPattern = /^[0-9a-f]{64}$/;

export function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function digest(value) {
  return createHash("sha256").update(canonical(value)).digest("hex");
}

export function validateTreeIdentity(tree) {
  if (!tree || Object.keys(tree).sort().join(",") !== "algorithm,fileCount,sha256"
      || tree.algorithm !== dependencyAlgorithm || !digestPattern.test(tree.sha256 ?? "")
      || !Number.isSafeInteger(tree.fileCount) || tree.fileCount < 1 || tree.fileCount > 100_000) {
    throw new Error("Invalid dependency tree identity");
  }
  return tree;
}

// Input order MUST come from the already verified successor chain, never from
// directory names, timestamps, an unverified current pointer or remote state.
export function dependencyRetentionPlan({ releases, headReleaseId, chainStateSha256, keepLatest = 3 }) {
  if (!Array.isArray(releases) || releases.length < 1 || releases.length > 257
      || !Number.isInteger(keepLatest) || keepLatest < 3 || keepLatest > 20
      || !digestPattern.test(chainStateSha256 ?? "")) throw new Error("Invalid retention input");
  const seen = new Set();
  for (const release of releases) {
    if (!releaseIdPattern.test(release.releaseId ?? "") || seen.has(release.releaseId)
        || !digestPattern.test(release.manifestSha256 ?? "")) throw new Error("Invalid release binding");
    validateTreeIdentity(release.tree);
    seen.add(release.releaseId);
  }
  if (releases.at(-1).releaseId !== headReleaseId) throw new Error("Head is not the verified chain tip");
  const retained = releases.slice(-keepLatest).map((release) => release.releaseId);
  const candidates = releases.slice(0, Math.max(0, releases.length - keepLatest));
  const grouped = new Map();
  for (const release of candidates) {
    const key = canonical(release.tree);
    if (!grouped.has(key)) grouped.set(key, { tree: release.tree, releaseIds: [] });
    grouped.get(key).releaseIds.push(release.releaseId);
  }
  const core = {
    version: policyVersion, scope: "worker-node-modules-only", headReleaseId, chainStateSha256,
    keepLatest, retainedReleaseIds: retained, candidates,
    archiveGroups: [...grouped.values()],
    preserved: ["all-release-manifests", "all-audit-receipts", "all-activation-fences", "all-successor-records", "source-snapshots", "dist", "helper", "tools", "secrets"],
  };
  return { ...core, planSha256: digest(core) };
}

export function assertPrivateRepository(repo, expectedName, expectedId = undefined) {
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*\/[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(expectedName)
      || !repo || repo.full_name?.toLowerCase() !== expectedName.toLowerCase()
      || repo.private !== true || repo.visibility !== "private" || repo.archived === true
      || !Number.isSafeInteger(repo.id) || repo.id < 1
      || (expectedId !== undefined && expectedId !== repo.id)) {
    throw new Error("Archive destination must be the exact private GitHub repository");
  }
  return repo.id;
}

export function assertArchiveProof(proof, tree, repoId) {
  validateTreeIdentity(tree);
  if (!proof || proof.version !== "teruisi-dependency-archive-proof-v1"
      || proof.repositoryId !== repoId || canonical(proof.tree) !== canonical(tree)
      || proof.downloadVerified !== true || proof.extractionVerified !== true
      || !digestPattern.test(proof.archiveSha256 ?? "")
      || !Number.isSafeInteger(proof.archiveBytes) || proof.archiveBytes < 1 || proof.archiveBytes >= 2 ** 31
      || !Number.isSafeInteger(proof.assetId) || proof.assetId < 1
      || !Number.isSafeInteger(proof.releaseId) || proof.releaseId < 1) {
    throw new Error("Missing exact download and extraction proof");
  }
}

// Plan only: this deliberately does NOT authorize deleting database backups.
// A cloud upload is insufficient without independent key recovery and an
// isolated PostgreSQL restore bound to the same original backup manifest.
export function backupRetentionPlan(backups, { now, localDays = 14, minimumSuccessful = 7 } = {}) {
  const time = Date.parse(now);
  if (!Number.isFinite(time) || !Number.isInteger(localDays) || localDays < 14 || localDays > 365
      || !Number.isInteger(minimumSuccessful) || minimumSuccessful < 7
      || !Array.isArray(backups) || backups.length > 10_000) throw new Error("Invalid backup policy");
  const seen = new Set();
  const ordered = backups.map((backup) => {
    const completed = Date.parse(backup.completedAt);
    if (!/^daily-\d{8}T\d{6}Z-[0-9a-f]{12}$/.test(backup.backupId ?? "")
        || seen.has(backup.backupId) || !digestPattern.test(backup.manifestSha256 ?? "")
        || !Number.isFinite(completed) || completed > time || backup.verified !== true) {
      throw new Error("Unverified or invalid backup inventory");
    }
    seen.add(backup.backupId);
    return { ...backup, completed };
  }).sort((a, b) => b.completed - a.completed || a.backupId.localeCompare(b.backupId));
  return ordered.map((backup, index) => ({
    backupId: backup.backupId, manifestSha256: backup.manifestSha256,
    action: index < minimumSuccessful || backup.completed >= time - localDays * 86400000
      || backup.pinned === true ? "retain-local" : "archive-before-considering-prune",
    deletionAuthorized: false,
  }));
}
