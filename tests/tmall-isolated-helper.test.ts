import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { IsolatedHelperSlots, isolatedRequestIdentity, spawnIsolatedHelper, type HelperSlot, type SlotIdentity } from "../tools/tmall-isolated-helper";

function identity(storeKey: string, executionId: string): SlotIdentity {
  return { key: storeKey, storeKey, workflow: "tmall", executionId };
}
function fixture() {
  const finishes = new Map<string, (clean: boolean) => void>();
  const starts: string[] = [];
  const allowed = new Set(["tmall-lili", "tmall-yijiu", "tmall-yiyong", "tmall-tuofeng", "tmall-cuizhiwang", "tmall-masitu"]);
  const pool = new IsolatedHelperSlots(allowed, async (owner, finish) => {
    starts.push(owner.key); finishes.set(owner.key, finish);
    return { port: 12345, token: "synthetic", stop: async () => undefined };
  });
  return { pool, finishes, starts, allowed };
}

test("six independent Tmall slots start without waiting for another store", async () => {
  const { pool, allowed, starts } = fixture();
  const decisions = [...allowed].map((key, index) => pool.claim(identity(key, String(index))));
  assert.ok(decisions.every(item => item.status === "granted"));
  await Promise.all(decisions.map(item => item.slot!.ready));
  assert.equal(new Set(starts).size, 6);
  assert.equal(pool.summary().length, 6);
});

test("simultaneous same-store claims reserve synchronously and spawn once", async () => {
  const { pool, starts } = fixture();
  const first = pool.claim(identity("tmall-lili", "100"));
  assert.equal(pool.claim(identity("tmall-lili", "101")).status, "waiting");
  assert.equal(pool.claim(identity("tmall-lili", "100")).slot, first.slot);
  assert.equal(pool.claim(identity("tmall-yijiu", "100")).reason, "execution_store_context_mismatch");
  await first.slot!.ready;
  assert.deepEqual(starts, ["tmall-lili"]);
  assert.equal(pool.lookup(identity("tmall-lili", "101")), null);
});

test("one failed/finished store releases only itself, and late callbacks cannot remove a replacement", async () => {
  const { pool, finishes } = fixture();
  const first = pool.claim(identity("tmall-lili", "100"));
  const other = pool.claim(identity("tmall-yijiu", "200"));
  await Promise.all([first.slot!.ready, other.slot!.ready]);
  const finish = finishes.get("tmall-lili")!;
  finish(true);
  assert.equal(pool.lookup(identity("tmall-yijiu", "200")), other.slot);
  assert.equal(pool.claim(identity("tmall-lili", "100")).reason, "execution_already_finished");
  const retry = pool.claim(identity("tmall-lili", "101"));
  await retry.slot!.ready;
  finish(true);
  assert.equal(pool.lookup(identity("tmall-lili", "101")), retry.slot);
});

test("unknown termination quarantines that store while other Tmall stores run", async () => {
  const { pool, finishes } = fixture();
  await pool.claim(identity("tmall-lili", "100")).slot!.ready;
  finishes.get("tmall-lili")!(false);
  assert.match(pool.claim(identity("tmall-lili", "101")).reason!, /manual_action/);
  assert.equal(pool.claim(identity("tmall-yijiu", "200")).status, "granted");
});

test("legacy workflows retain shared exclusion; disabled/unknown stores cannot allocate", async () => {
  const { pool, starts } = fixture();
  assert.equal(pool.claim(identity("tmall-ledu", "1")).status, "rejected");
  const legacy = { key: "legacy", storeKey: null, workflow: "jd", executionId: "2" };
  const first = pool.claim(legacy);
  await first.slot!.ready;
  assert.equal(pool.claim(identity("tmall-lili", "3")).status, "waiting");
  assert.equal(pool.claim({ ...legacy, workflow: "jackyun", executionId: "4" }).status, "waiting");
  assert.deepEqual(starts, ["legacy"]);
});

test("router rejects missing/array identities and never infers Tmall from a legacy route", () => {
  const headers = { "x-teruisi-n8n-execution-id": "123", "x-teruisi-tmall-store-key": "tmall-lili", "x-teruisi-workflow-key": "tmall" };
  assert.deepEqual(isolatedRequestIdentity("/coordination/claim", headers), identity("tmall-lili", "123"));
  assert.deepEqual(isolatedRequestIdentity("/product-master-direct-v1", headers), identity("tmall-lili", "123"));
  for (const route of ["/unknown", "/product-master-direct-v1?store=other", "/health"]) assert.equal(isolatedRequestIdentity(route, headers), null);
  assert.equal(isolatedRequestIdentity("/plan", { ...headers, "x-teruisi-tmall-store-key": ["tmall-lili", "tmall-yijiu"] }), null);
  assert.equal(isolatedRequestIdentity("/plan", { ...headers, "x-teruisi-n8n-execution-id": "" }), null);
  assert.equal(isolatedRequestIdentity("/jd/plan", headers)?.key, "legacy");
});

test("real worker exits release only a clean store; a crash quarantines only its own store", async () => {
  const children: HelperSlot[] = [];
  const exits = new Map<string, Promise<boolean>>();
  const pool = new IsolatedHelperSlots(new Set(["tmall-lili", "tmall-yijiu", "tmall-yiyong"]), async (owner, finish) => {
    let exited!: (clean: boolean) => void;
    exits.set(owner.executionId, new Promise(resolve => { exited = resolve; }));
    const child = await spawnIsolatedHelper(fileURLToPath(new URL("fixtures/tmall-isolated-worker.mjs", import.meta.url)), owner, clean => { finish(clean); exited(clean); });
    children.push(child);
    return child;
  });
  async function send(child: HelperSlot, route: string) {
    const response = await fetch(`http://127.0.0.1:${child.port}${route}`, { headers: { "x-teruisi-helper-slot-token": child.token, connection: "close" }, signal: AbortSignal.timeout(5000) });
    assert.equal(response.status, 200);
    await response.text();
  }
  async function exited(id: string) {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try { return await Promise.race([exits.get(id)!, new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error("fixture exit timeout")), 5000); })]); }
    finally { clearTimeout(timer); }
  }
  try {
    const lili = pool.claim(identity("tmall-lili", "lili-first"));
    const yijiu = pool.claim(identity("tmall-yijiu", "yijiu-first"));
    const [first, other] = await Promise.all([lili.slot!.ready, yijiu.slot!.ready]);
    await send(first, "/finish");
    assert.equal(await exited("lili-first"), true);
    assert.equal(pool.lookup(identity("tmall-yijiu", "yijiu-first")), yijiu.slot);
    const retry = pool.claim(identity("tmall-lili", "lili-retry"));
    assert.equal(retry.status, "granted");
    await retry.slot!.ready;
    await send(other, "/crash");
    assert.equal(await exited("yijiu-first"), false);
    assert.equal(pool.claim(identity("tmall-yijiu", "yijiu-retry")).status, "rejected");
    assert.equal(pool.lookup(identity("tmall-lili", "lili-retry")), retry.slot);
    const third = pool.claim(identity("tmall-yiyong", "yiyong-first"));
    assert.equal(third.status, "granted");
    await third.slot!.ready;
  } finally { await Promise.all(children.map(child => child.stop())); }
});
