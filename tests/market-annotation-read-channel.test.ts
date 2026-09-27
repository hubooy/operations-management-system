import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { AnnotationReadError, createAnnotationReadChannel } from "../lib/market/annotation-read-channel";

type Progress = { job: { id: string } };
const valid = (value: unknown): value is Progress => Boolean(value && typeof value === "object" && (value as Progress).job?.id);
const response = (id: string) => Response.json({ job: { id } });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

test("503 preserves the last good progress and clears only after a successful retry", async () => {
  let error = "";
  let last = "old";
  let status = 503;
  const channel = createAnnotationReadChannel({ label: "任务进度读取", onError: (value) => { error = value; },
    fetchImpl: async (_url, init) => {
      assert.equal(init?.cache, "no-store");
      assert.equal(init?.method, undefined, "refresh never submits or retries a command");
      return status === 503 ? Response.json({ error: "Django 市场服务暂时不可用" }, { status }) : response("new");
    } });
  await assert.rejects(channel.read("/progress", valid, (value) => { last = value.job.id; }), AnnotationReadError);
  assert.equal(last, "old");
  assert.match(error, /任务进度读取失败/);
  assert.equal(channel.hasError(), true);
  status = 200;
  await channel.read("/progress", valid, (value) => { last = value.job.id; });
  assert.equal(last, "new");
  assert.equal(error, "");
  assert.equal(channel.hasError(), false);
});

test("progress recovery cannot erase review errors", async () => {
  let progressError = "";
  let reviewError = "";
  const progress = createAnnotationReadChannel({ label: "进度", onError: (value) => { progressError = value; }, fetchImpl: async () => response("job") });
  const review = createAnnotationReadChannel({ label: "复核", onError: (value) => { reviewError = value; }, fetchImpl: async () => new Response("unavailable", { status: 503 }) });
  await assert.rejects(review.read("/review", valid, () => undefined), AnnotationReadError);
  await progress.read("/progress", valid, () => undefined);
  assert.equal(progressError, "");
  assert.match(reviewError, /复核.*失败/);
  assert.equal(review.hasError(), true);
});

test("a superseded task cannot publish late data or clear the new task's failure", async () => {
  const old = deferred<Response>();
  let error = "";
  const published: string[] = [];
  let oldSignal: AbortSignal | null | undefined;
  const channel = createAnnotationReadChannel({ label: "进度", onError: (value) => { error = value; },
    fetchImpl: async (url, init) => {
      if (url === "/old") { oldSignal = init?.signal; return old.promise; }
      return Response.json({}, { status: 503 });
    } });
  const first = channel.read("/old", valid, (value) => published.push(value.job.id));
  await assert.rejects(channel.read("/new", valid, (value) => published.push(value.job.id)), AnnotationReadError);
  assert.equal(await first, undefined);
  assert.equal(oldSignal?.aborted, true);
  old.resolve(response("old"));
  await Promise.resolve();
  assert.deepEqual(published, []);
  assert.match(error, /失败/);
});

test("late failure from an old filter cannot replace the current successful result", async () => {
  const old = deferred<Response>();
  let error = "";
  let published = "";
  const channel = createAnnotationReadChannel({ label: "复核", onError: (value) => { error = value; },
    fetchImpl: async (url) => url === "/old" ? old.promise : response("new") });
  const first = channel.read("/old", valid, () => assert.fail("old response published"));
  await channel.read("/new", valid, (value) => { published = value.job.id; });
  old.resolve(Response.json({}, { status: 503 }));
  await first;
  assert.equal(published, "new");
  assert.equal(error, "");
});

test("cleanup cancels both network and pending body reads without displaying an error", async () => {
  for (const useSignal of [false, true]) {
    const body = deferred<unknown>();
    const started = deferred<void>();
    const controller = new AbortController();
    let requestSignal: AbortSignal | null | undefined;
    const channel = createAnnotationReadChannel({ label: "进度", onError: () => assert.fail("cancelled request changed errors"),
      fetchImpl: async (_url, init) => {
        requestSignal = init?.signal;
        return { ok: true, json: () => { started.resolve(); return body.promise; } } as Response;
      } });
    const pending = channel.read("/progress", valid, () => assert.fail("unmounted result published"), controller.signal);
    await started.promise;
    if (useSignal) controller.abort(); else channel.cancel();
    assert.equal(await pending, undefined);
    assert.equal(requestSignal?.aborted, true);
    body.resolve({ job: { id: "late" } });
  }
});

test("timeout releases polling even when a fetch ignores cancellation, and next read recovers", async () => {
  let error = "";
  let requestSignal: AbortSignal | null | undefined;
  const channel = createAnnotationReadChannel({ label: "任务进度读取", timeoutMs: 10, onError: (value) => { error = value; },
    fetchImpl: async (url, init) => {
      if (url === "/hang") { requestSignal = init?.signal; return new Promise<Response>(() => undefined); }
      return response("recovered");
    } });
  await assert.rejects(channel.read("/hang", valid, () => assert.fail("timeout published")), /超时/);
  assert.equal(requestSignal?.aborted, true);
  assert.match(error, /超时/);
  await channel.read("/next", valid, (value) => assert.equal(value.job.id, "recovered"));
  assert.equal(error, "");
});

test("malformed or wrong-job responses remain failures and do not erase prior data", async () => {
  for (const reply of [new Response("invalid json"), Response.json({}), response("wrong-job")]) {
    let error = "";
    const channel = createAnnotationReadChannel({ label: "进度", onError: (value) => { error = value; }, fetchImpl: async () => reply });
    await assert.rejects(channel.read("/expected", (value): value is Progress => valid(value) && value.job.id === "expected",
      () => assert.fail("invalid data published")), AnnotationReadError);
    assert.match(error, /失败/);
  }
});

test("annotation UI uses independent read errors and preserves command errors during refresh", async () => {
  const source = await readFile(new URL("../app/market-annotation-view.tsx", import.meta.url), "utf8");
  assert.match(source, /label: "任务进度读取", onError: setProgressError/);
  assert.match(source, /label: "复核列表读取", onError: setReviewError/);
  assert.match(source, /if \(!\(reason instanceof AnnotationReadError\)\) setError\(message\)/);
  const polling = source.slice(source.indexOf("    let disposed = false;"), source.indexOf("  const concurrencyFor"));
  assert.doesNotMatch(polling, /setError/);
  assert.match(polling, /loadJobProgress\(backgroundJobId, controller.signal\)/);
  assert.match(polling, /reviewReader.hasError\(\)/);
  assert.match(polling, /controller.abort\(\)/);
  assert.match(source, /重新读取进度/);
  assert.match(source, /重新读取复核列表/);
});
