import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { activationFenceRelativePath, canonicalJson, salesRetirementMigrationSha256, sha256Bytes, windowsPathSha256, withPayloadSha256, workerGuardCheckNames, workerGuardEntrypointPaths, workerGuardForbiddenScans, workerReleaseActivationFence } from "../../tools/worker-local-release.mjs";
import { releaseBinding, resolveEffectiveReleaseChain, rotationPlanPayload, publishRotationPlan, successorPayload } from "../../tools/worker-local-release-rotation.mjs";
import { createD1RetirementReceipt, d1ReceiptVersion, d1ReceiptRelativePath } from "../../tools/d1-retirement-proof.mjs";
import { syntheticD1Proof } from "./d1-retirement-proof";
const hex = (character: string) => character.repeat(64);

export async function writeCanonical(target: string, value: unknown) {
  const raw = Buffer.from(`${canonicalJson(value)}\n`, "utf8");
  await writeFile(target, raw);
  return sha256Bytes(raw);
}

export async function makeRelease(
  runtime: string,
  protectedRoot: string,
  releaseId: string,
  entrypointText: string,
  {
    includeRotationEntrypoint = true,
    createdAt = "2026-08-30T00:00:00.000Z",
    sourceFingerprint = hex("1"),
    buildFingerprint = hex("6"),
    entrypointTextByPath = {} as Record<string, string>,
  } = {},
) {
  const releaseRoot = path.join(runtime, "releases", releaseId);
  await mkdir(path.join(releaseRoot, "audit"), { recursive: true });
  await mkdir(path.join(releaseRoot, "tools"), { recursive: true });
  const entrypointPaths = includeRotationEntrypoint
    ? [...workerGuardEntrypointPaths]
    : workerGuardEntrypointPaths.filter((relativePath) => ![
      "tools/worker-local-release-rotation.mjs",
      "tools/d1-retirement-proof.mjs",
      "tools/collect-d1-retirement-proof.mjs",
      activationFenceRelativePath,
    ].includes(relativePath));
  const activationFence = workerReleaseActivationFence({ createdAt, sourceFingerprint, buildFingerprint });
  const entrypoints = [];
  const entrypointBytes = new Map<string, Buffer>();
  for (const relativePath of entrypointPaths) {
    const content = entrypointTextByPath[relativePath]
      ?? (relativePath === "tools/worker-local-service.ps1" ? entrypointText : "stable");
    const raw = relativePath === activationFenceRelativePath
      ? Buffer.from(`${canonicalJson(activationFence)}\n`, "utf8")
      : Buffer.from(`${content}:${relativePath}`, "utf8");
    await mkdir(path.dirname(path.join(releaseRoot, ...relativePath.split("/"))), { recursive: true });
    await writeFile(path.join(releaseRoot, ...relativePath.split("/")), raw);
    entrypointBytes.set(relativePath, raw);
    entrypoints.push({ relativePath, sha256: sha256Bytes(raw) });
  }
  const sourceD1Path = path.join(runtime, "state.sqlite");
  const persistRoot = path.join(runtime, "persist");
  const guard = withPayloadSha256({
    version: "teruisi-legacy-worker-guard-receipt-v1",
    generatedAt: "2026-08-30T00:00:00.000Z",
    sourceFingerprint,
    status: "passed",
    bindings: {
      protectedSourceRoot: protectedRoot,
      protectedSourceRootPathSha256: windowsPathSha256(protectedRoot),
      persistRoot,
      persistRootPathSha256: windowsPathSha256(persistRoot),
      sourceD1Path,
      sourceD1PathSha256: windowsPathSha256(sourceD1Path),
      authorityRelativePath: "state/sales-postgresql-authority.json",
      authoritySidecarRelativePath: "state/sales-postgresql-authority.json.sha256",
    },
    checks: Object.fromEntries(workerGuardCheckNames.map((name) => [name, true])),
    entrypoints,
    forbiddenLegacyDirectCommands: workerGuardForbiddenScans.map((item) => ({ ...item, matches: [] })),
  }, "receiptPayloadSha256");
  const guardSha256 = await writeCanonical(path.join(releaseRoot, "audit", "legacy-worker-guard-receipt.json"), guard);
  const contractSha256 = await writeCanonical(path.join(releaseRoot, "audit", "sales-retired-code-receipt.json"), { status: "passed" });
  const manifest = withPayloadSha256({
    version: "teruisi-local-worker-release-v1",
    releaseId,
    createdAt,
    source: { sourceFingerprint },
    build: { buildFingerprint },
    runtime: {
      runtimeRootPathSha256: windowsPathSha256(runtime),
      releaseRootPathSha256: windowsPathSha256(releaseRoot),
      sourceD1Path,
      sourceD1PathSha256: windowsPathSha256(sourceD1Path),
      persistRoot,
      persistRootPathSha256: windowsPathSha256(persistRoot),
      protectedSourceRoot: protectedRoot,
      protectedSourceRootPathSha256: windowsPathSha256(protectedRoot),
      host: "127.0.0.1",
      port: 3000,
      cliOverridesAllowed: false,
      helperMode: "supervisor_managed_immutable_bundle",
      helperHost: "127.0.0.1",
      helperPort: 5791,
      helperMutableRoot: protectedRoot,
      helperMutableRootPathSha256: windowsPathSha256(protectedRoot),
      devVars: {},
    },
    artifacts: {
      keyFiles: entrypoints.map((item) => ({ ...item })),
      guardReceipt: {
        version: "teruisi-legacy-worker-guard-receipt-v1",
        relativePath: "audit/legacy-worker-guard-receipt.json",
        sha256: guardSha256,
      },
      contractReceipt: {
        version: "teruisi-sales-retired-code-receipt-v1",
        relativePath: "audit/sales-retired-code-receipt.json",
        sha256: contractSha256,
      },
    },
    processIdentity: {
      supervisorEntrypoint: "tools/worker-local-runtime-supervisor.mjs",
      serviceControl: "tools/worker-local-service.ps1",
      manifestFile: "deployment-manifest.json",
      processReceipt: "state/worker-process.json",
      processReceiptVersion: "teruisi-local-worker-process-v1",
      wranglerEntrypoint: "node_modules/wrangler/bin/wrangler.js",
      wranglerCliEntrypoint: "node_modules/wrangler/wrangler-dist/cli.js",
      fixedWranglerArguments: [],
      helperEntrypoint: "helper/tmall-workflow-helper.mjs",
      fixedHelperArguments: ["serve", "--port", "5791"],
    },
  }, "manifestPayloadSha256");
  const manifestPath = path.join(releaseRoot, "deployment-manifest.json");
  const authorityRaw = await readFile(path.join(runtime, "state", "sales-postgresql-authority.json")).catch(() => null);
  if (authorityRaw) {
    const authority = JSON.parse(authorityRaw.toString("utf8"));
    const proof = syntheticD1Proof({ runtimeRootPathSha256: windowsPathSha256(runtime), sourceD1PathSha256: windowsPathSha256(sourceD1Path),
      persistRootPathSha256: windowsPathSha256(persistRoot), bootstrapAuthoritySha256: sha256Bytes(authorityRaw),
      adoptionPredecessorManifestSha256: authority.workerReleaseManifestSha256 });
    const receipt = createD1RetirementReceipt(proof, manifest);
    Object.assign(manifest.artifacts, { d1RetirementReceipt: { version: d1ReceiptVersion, relativePath: d1ReceiptRelativePath,
      sha256: await writeCanonical(path.join(releaseRoot, ...d1ReceiptRelativePath.split("/")), receipt) } });
    const core = { ...manifest };
    delete core.manifestPayloadSha256;
    Object.assign(manifest, withPayloadSha256(core, "manifestPayloadSha256"));
  }
  const manifestSha256 = await writeCanonical(manifestPath, manifest);
  return {
    releaseId,
    releaseRoot,
    manifestPath,
    manifestSha256,
    guardReceiptSha256: guardSha256,
    contractReceiptSha256: contractSha256,
    entrypointSha256: entrypoints.find((item) => item.relativePath === "tools/worker-local-service.ps1")!.sha256,
    entrypointRaw: entrypointBytes.get("tools/worker-local-service.ps1")!,
    entrypointBytes,
    entrypoints,
  };
}

export async function fixture() {
  const runtime = await mkdtemp(path.join(tmpdir(), "teruisi-worker-rotation-"));
  const protectedRoot = path.join(runtime, "protected");
  await mkdir(path.join(runtime, "releases"));
  await mkdir(path.join(runtime, "state"));
  await mkdir(protectedRoot);
  await mkdir(path.join(runtime, "persist"));
  await writeCanonical(path.join(runtime, "runtime-root.json"), withPayloadSha256({
    version: "teruisi-local-worker-release-v1",
    runtimeRootPathSha256: windowsPathSha256(runtime),
  }, "markerPayloadSha256"));
  const bootstrapRelease = await makeRelease(
    runtime, protectedRoot, "20260830T000000Z-1111111111111111", "bootstrap-service",
    { includeRotationEntrypoint: false, createdAt: "2026-08-30T00:00:00.000Z", buildFingerprint: hex("6") },
  );
  for (const [relativePath, raw] of bootstrapRelease.entrypointBytes) {
    const target = path.join(protectedRoot, ...relativePath.split("/"));
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, raw);
  }
  const database = new DatabaseSync(path.join(runtime, "state.sqlite"));
  try {
    database.exec("CREATE TABLE domain_retirement_receipts (domain TEXT, version TEXT, status TEXT)");
    database.exec("INSERT INTO domain_retirement_receipts VALUES ('sales','sales-domain-retirement-receipt-v1','completed')");
    for (const name of [
      "sales_import_upload_chunks", "sales_import_uploads", "sales_order_lines", "sales_import_batches",
      "sales_overview_response_cache", "sales_overview_cache_state", "sales_projection_outbox",
      "sales_projection_source_state", "sales_write_authority",
    ]) database.exec(`CREATE VIEW \`${name}\` AS SELECT 'sales-domain-retired-v1' AS \`retirement_tombstone\` WHERE 0`);
    for (const [shortName, tableName] of [
      ["fingerprints", "import_content_fingerprints"],
      ["attempts", "import_content_attempts"],
      ["scope_heads", "import_scope_heads"],
    ]) {
      database.exec(`CREATE TABLE \`${tableName}\` (domain TEXT)`);
      for (const operation of ["insert", "update", "delete"]) {
        const reference = operation === "update" ? "OLD.`domain` = 'sales' OR NEW.`domain` = 'sales'"
          : `${operation === "insert" ? "NEW" : "OLD"}.\`domain\` = 'sales'`;
        database.exec(`CREATE TRIGGER \`sales_retired_${shortName}_${operation}_guard\` BEFORE ${operation.toUpperCase()} ON \`${tableName}\` WHEN ${reference} BEGIN SELECT RAISE(ABORT, 'sales_domain_retired'); END`);
      }
    }
  } finally {
    database.close();
  }
  const pointer = withPayloadSha256({
    version: "teruisi-local-worker-current-v1",
    releaseId: bootstrapRelease.releaseId,
    manifestRelativePath: `releases/${bootstrapRelease.releaseId}/deployment-manifest.json`,
    manifestSha256: bootstrapRelease.manifestSha256,
  }, "pointerPayloadSha256");
  const pointerSha256 = await writeCanonical(path.join(runtime, "current-deployment.json"), pointer);
  const authority = withPayloadSha256({
    version: "teruisi-sales-postgresql-authority-v1",
    domain: "sales",
    authority: "postgresql",
    cutoverId: "sales-cutover-test-0001",
    workerReleaseId: bootstrapRelease.releaseId,
    workerReleaseManifestSha256: bootstrapRelease.manifestSha256,
    djangoDeploymentManifestSha256: hex("2"),
    guardReceiptSha256: bootstrapRelease.guardReceiptSha256,
    sourceD1PathSha256: windowsPathSha256(path.join(runtime, "state.sqlite")),
    persistRootPathSha256: windowsPathSha256(path.join(runtime, "persist")),
  }, "payloadSha256");
  const authorityPath = path.join(runtime, "state", "sales-postgresql-authority.json");
  const authoritySha256 = await writeCanonical(authorityPath, authority);
  await writeFile(`${authorityPath}.sha256`, `${authoritySha256}\n`, "ascii");
  const candidate = await makeRelease(
    runtime, protectedRoot, "20260830T000001Z-2222222222222222", "candidate-service",
    { createdAt: "2026-08-30T00:00:01.000Z", buildFingerprint: hex("7") },
  );
  const cutoverEvidence = {
    attestationPayloadSha256: hex("3"),
    attestationFileSha256: hex("4"),
    forwardRecoverySha256: hex("5"),
  };
  return { runtime, protectedRoot, bootstrapRelease, candidate, pointerSha256, authoritySha256, authority, cutoverEvidence };
}

export function lineage(item: Awaited<ReturnType<typeof fixture>>, predecessorContract: string, successorContract: string) {
  return {
    bootstrapCurrentPointerSha256: item.pointerSha256,
    bootstrapAuthoritySha256: item.authoritySha256,
    cutoverId: item.authority.cutoverId,
    djangoDeploymentManifestSha256: item.authority.djangoDeploymentManifestSha256,
    sourceD1PathSha256: item.authority.sourceD1PathSha256,
    persistRootPathSha256: item.authority.persistRootPathSha256,
    ...item.cutoverEvidence,
    salesRetirementMigrationSha256,
    predecessorContractReceiptSha256: predecessorContract,
    successorContractReceiptSha256: successorContract,
  };
}

export async function approvedTransition(
  item: Awaited<ReturnType<typeof fixture>>,
  chain: Awaited<ReturnType<typeof resolveEffectiveReleaseChain>>,
  candidateRelease = item.candidate,
  predecessorRelease = item.bootstrapRelease,
  createdAt = "2026-08-30T00:01:00.000Z",
) {
  const candidate = releaseBinding(candidateRelease);
  const recordLineage = lineage(item, predecessorRelease.contractReceiptSha256, candidateRelease.contractReceiptSha256);
  const plan = rotationPlanPayload({
    createdAt,
    chain,
    candidate,
    lineage: recordLineage,
    protectedEntrypoints: candidateRelease.entrypoints.map((candidateEntrypoint) => ({
      relativePath: candidateEntrypoint.relativePath,
      predecessorSha256: predecessorRelease.entrypoints.find(
        (predecessorEntrypoint) => predecessorEntrypoint.relativePath === candidateEntrypoint.relativePath,
      )?.sha256 ?? null,
      candidateSha256: candidateEntrypoint.sha256,
    })),
  });
  const publication = await publishRotationPlan(item.runtime, plan);
  const record = successorPayload({
    sequence: chain.successorCount + 1,
    predecessor: chain.head,
    successor: candidate,
    lineage: recordLineage,
    approvedPlanSha256: publication.planSha256,
    activatedAt: plan.createdAt,
  });
  return { plan, planSha256: publication.planSha256, record };
}

export async function approvedRecord(item: Awaited<ReturnType<typeof fixture>>, chain: Awaited<ReturnType<typeof resolveEffectiveReleaseChain>>) {
  return (await approvedTransition(item, chain)).record;
}

export async function installCandidateEntrypoints(item: Awaited<ReturnType<typeof fixture>>) {
  await installReleaseEntrypoints(item.protectedRoot, item.candidate);
}

export async function installReleaseEntrypoints(
  protectedRoot: string,
  release: Awaited<ReturnType<typeof makeRelease>>,
) {
  for (const [relativePath, raw] of release.entrypointBytes) {
    const target = path.join(protectedRoot, ...relativePath.split("/"));
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, raw);
  }
}
