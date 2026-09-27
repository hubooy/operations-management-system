import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

function fixture() {
  const source = readFileSync("app/new-product-sales-followup-view.tsx", "utf8");
  const from = source.indexOf("  const load = useCallback(async () => {");
  const to = source.indexOf("  }, []);", from) + "  }, []);".length;
  assert.ok(from > 0 && to > from);
  const requests: { url: string; resolve: (value: unknown) => void; reject: (error: Error) => void }[] = [];
  const state: Record<string, unknown> = {};
  const timers = new Map<number, () => void>();
  let timerId = 0;
  const context = {
    AbortController, Promise, encodeURIComponent,
    weekStartRef: { current: "2026-09-07" }, loadControllerRef: { current: null as AbortController | null }, mountedRef: { current: true },
    useCallback: (f: unknown) => f, messageOf: (error: Error) => error.message,
    window: { setTimeout: (f: () => void) => { timers.set(++timerId, f); return timerId; }, clearTimeout: (id: number) => timers.delete(id) },
    setLoading: (value: unknown) => { state.loading = value; }, setError: (value: unknown) => { state.error = value; },
    setReport: (value: unknown) => { state.report = value; }, setLines: (value: unknown) => { state.lines = value; },
    // Deliberately let an aborted transport complete, exercising the commit
    // fence independently of fetch's best-effort cancellation.
    requestJson: (url: string) => new Promise((resolve, reject) => requests.push({ url, resolve, reject })),
  };
  const sandbox = vm.createContext(context);
  vm.runInContext(ts.transpileModule(source.slice(from, to) + "\nglobalThis.load = load;", { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, sandbox);
  const load = (sandbox as unknown as { load: () => Promise<void> }).load;
  const complete = (index: number, week: string) => { requests[index].resolve({ items: [week] }); requests[index + 1].resolve({ weekStart: week }); };
  return { state, requests, timers, context, load, complete };
}

test("late old week cannot overwrite newer data, errors, or loading", async () => {
  const f = fixture();
  const old = f.load();
  f.context.weekStartRef.current = "2026-09-14";
  const current = f.load();
  f.complete(0, "old"); await old;
  assert.equal(f.state.loading, true);
  assert.equal(f.state.report, null);
  f.complete(2, "2026-09-14"); await current;
  assert.deepEqual(f.state.report, { weekStart: "2026-09-14" });
  assert.equal(f.state.loading, false);
  assert.equal(f.timers.size, 0);
});

test("old failure arriving after new success is ignored", async () => {
  const f = fixture();
  const old = f.load(); const current = f.load();
  f.complete(2, "current"); await current;
  f.requests[0].reject(new Error("old failure")); f.requests[1].resolve({}); await old;
  assert.deepEqual(f.state.report, { weekStart: "current" });
  assert.equal(f.state.error, "");
});

test("a saved callback refreshes the current week after an asynchronous write", async () => {
  const f = fixture(); const savedCallback = f.load;
  f.context.weekStartRef.current = "2026-09-14";
  const pending = savedCallback();
  assert.match(f.requests[1].url, /weekStart=2026-09-14$/);
  f.complete(0, "current"); await pending;
});

test("unmount and timeout prevent later responses from publishing", async () => {
  const f = fixture(); const pending = f.load();
  f.context.mountedRef.current = false; f.context.loadControllerRef.current?.abort();
  f.complete(0, "unmounted"); await pending;
  assert.equal(f.state.report, null);
  assert.equal(f.requests.length, 2);
  await f.load(); assert.equal(f.requests.length, 2);

  const timed = fixture(); const slow = timed.load();
  [...timed.timers.values()][0]();
  const timeoutError = timed.state.error;
  assert.equal(timed.state.loading, false);
  assert.ok(timeoutError);
  timed.complete(0, "too late"); await slow;
  assert.equal(timed.state.report, null);
  assert.equal(timed.state.error, timeoutError);
});
