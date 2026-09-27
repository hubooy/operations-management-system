// Test process only: relocate the fixed runtime/port coordinates. All restart,
// process receipt, effective-head, retirement proof and artifact checks execute.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const config = JSON.parse(readFileSync(process.argv[2], "utf8"));
assert.equal(path.dirname(config.runtime).toLowerCase(), path.resolve(tmpdir()).toLowerCase());
assert.match(path.basename(config.runtime), /^teruisi-worker-rotation-/);
assert.ok(Number.isSafeInteger(config.port) && config.port > 20000 && config.port < 65536);
assert.ok(["worker", "helper"].includes(config.kind));
const releaseUrl = pathToFileURL(path.join(config.sourceRoot, "tools/worker-local-release.mjs")).href;
registerHooks({ load(url, context, nextLoad) {
  const result = nextLoad(url, context);
  if (url !== releaseUrl) return result;
  let source = String(result.source);
  for (const [before, after] of [
    ['export const workerRuntimeRoot = "D:\\\\teruisi-runtime\\\\teruisi-worker-sales";', `export const workerRuntimeRoot = ${JSON.stringify(config.runtime)};`],
    [`export const ${config.kind === "worker" ? "workerPort = 3000" : "workerHelperPort = 5791"};`,
      `export const ${config.kind === "worker" ? "workerPort" : "workerHelperPort"} = ${config.port};`],
  ]) {
    assert.equal(source.split(before).length, 2, "test coordinate must match exactly once");
    source = source.replace(before, after);
  }
  return { ...result, source };
} });
const supervisor = await import(pathToFileURL(path.join(config.sourceRoot, "tools/worker-local-runtime-supervisor.mjs")).href);
const { monitorLocalWorkerLiveness } = await import(pathToFileURL(path.join(config.sourceRoot, "tools/start-local-worker.mjs")).href);
const abort = new AbortController();
process.on("message", message => { if (message === "stop") abort.abort(); });
try {
  await supervisor[config.kind === "worker" ? "superviseImmutableWorker" : "superviseImmutableHelper"]({
    releaseRoot: config.releaseRoot, manifest: config.manifest, manifestPath: config.manifestPath,
    manifestSha256: config.manifestSha256, runtimeRoot: config.runtime, signal: abort.signal,
    ...(config.kind === "worker" ? { livenessMonitor: (options) => monitorLocalWorkerLiveness({
      ...options, initialDelayMs: 100, intervalMs: 100, failureThreshold: 3,
    }) } : {}),
  });
} catch (error) {
  process.send?.({ outcome: "rejected", message: error.message });
  process.exitCode = 1;
} finally {
  process.disconnect?.();
}
