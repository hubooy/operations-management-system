import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { resolveEffectiveReleaseChain } from "./worker-local-release-rotation.mjs";
import { sha256Bytes, workerRuntimeRoot } from "./worker-local-release.mjs";
import { dependencyRetentionPlan } from "./storage-retention-policy.mjs";

export async function planDependencyRetention() {
  const chain = await resolveEffectiveReleaseChain({ verifyInstalledHead: true });
  const bindings = [chain.bootstrap.binding, ...chain.records.map((record) => record.value.successor)];
  const releases = [];
  for (const binding of bindings) {
    const raw = await readFile(path.join(workerRuntimeRoot, "releases", binding.releaseId, "deployment-manifest.json"));
    if (sha256Bytes(raw) !== binding.manifestSha256) throw new Error("Manifest changed during planning");
    const manifest = JSON.parse(raw);
    releases.push({ releaseId: binding.releaseId, manifestSha256: binding.manifestSha256, tree: manifest.build.nodeModulesTree });
  }
  const after = await resolveEffectiveReleaseChain({ verifyInstalledHead: true });
  if (after.chainStateSha256 !== chain.chainStateSha256) throw new Error("Release chain changed during planning");
  return dependencyRetentionPlan({ releases, headReleaseId: chain.head.releaseId, chainStateSha256: chain.chainStateSha256 });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv.length !== 2) throw new Error("Read-only planner accepts no arguments");
  planDependencyRetention().then((result) => process.stdout.write(`${JSON.stringify(result)}\n`)).catch((error) => {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  });
}
