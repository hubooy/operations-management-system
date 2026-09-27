import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import childProcess from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { patchMiniflareHeap } from "../tools/install-workerd-heap-patch.mjs";
import { assertWorkerdHeapAdapter, heapPatchedMiniflareSha256, immutableWorkerEnvironment } from "../tools/worker-local-runtime-supervisor.mjs";

test("heap adapter is digest-pinned, idempotent, and refuses unknown bytes", async () => {
  const source = await readFile(new URL("../node_modules/miniflare/dist/src/index.js", import.meta.url), "utf8");
  assert.equal(patchMiniflareHeap(source), source);
  assert.equal(createHash("sha256").update(source).digest("hex"), heapPatchedMiniflareSha256);
  assert.throws(() => patchMiniflareHeap(source + "\n"), /unknown dependency digest/);
  await assertWorkerdHeapAdapter(path.resolve("."));
});

test("immutable worker pins 3072 MiB and removes case-insensitive inherited overrides", () => {
  const runtimeRoot = path.resolve("tmp/heap-runtime");
  const env = immutableWorkerEnvironment({ runtimeRoot, releaseRoot: path.join(runtimeRoot, "releases/test"), persistRoot: path.resolve("tmp/heap-persist"), inheritedEnvironment: { teruisi_workerd_heap_mb: "99999", TERUISI_WORKERD_HEAP_MB: "64" } });
  assert.equal(env.TERUISI_WORKERD_HEAP_MB, "3072");
  assert.equal(env.teruisi_workerd_heap_mb, undefined);
});

test("real isolated workerd receives serialized 3072 MiB flag and serves a synthetic request", { timeout: 30000 }, async () => {
  const originalSpawn = childProcess.spawn;
  const configBuffers: Buffer[] = [];
  // Observe only child processes created by this isolated test; no live process attachment.
  childProcess.spawn = function (...args: Parameters<typeof childProcess.spawn>) {
    const child = originalSpawn.apply(this, args as never);
    if (String(args[0]).includes("workerd") && child.stdin) {
      const originalWrite = child.stdin.write.bind(child.stdin);
      child.stdin.write = ((chunk: unknown, ...rest: unknown[]) => {
        if (Buffer.isBuffer(chunk)) configBuffers.push(Buffer.from(chunk));
        return originalWrite(chunk as never, ...rest as []);
      }) as typeof child.stdin.write;
    }
    return child;
  } as typeof childProcess.spawn;
  const previous = process.env.TERUISI_WORKERD_HEAP_MB;
  process.env.TERUISI_WORKERD_HEAP_MB = "3072";
  const { Miniflare } = await import("miniflare");
  const mf = new Miniflare({ modules: true, script: 'export default { fetch() { return new Response("heap-probe-ok"); } }', compatibilityDate: "2026-05-15", host: "127.0.0.1", port: 0 });
  try {
    const response = await mf.dispatchFetch("http://heap-probe.invalid/");
    assert.equal(await response.text(), "heap-probe-ok");
    assert.ok(configBuffers.length > 0);
    assert.ok(configBuffers.some(buffer => buffer.includes(Buffer.from("--max-old-space-size=3072"))));
  } finally {
    await mf.dispose();
    childProcess.spawn = originalSpawn;
    if (previous === undefined) delete process.env.TERUISI_WORKERD_HEAP_MB;
    else process.env.TERUISI_WORKERD_HEAP_MB = previous;
  }
});
