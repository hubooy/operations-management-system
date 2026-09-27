import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { fetchWithTransientRetry } from "../lib/http/transient-retry";

test("transient fetch retry has no overhead on a successful request", async () => {
  let calls = 0;
  let waits = 0;
  const response = await fetchWithTransientRetry("/api/sales/summary", {}, {
    fetchImpl: async () => {
      calls += 1;
      return Response.json({ ok: true });
    },
    wait: async () => {
      waits += 1;
      return true;
    },
  });

  assert.equal(response.status, 200);
  assert.equal(calls, 1);
  assert.equal(waits, 0);
});

test("transient fetch retry recovers from bounded 5xx and network failures", async () => {
  const delays: number[] = [];
  let calls = 0;
  const response = await fetchWithTransientRetry("/api/sales/summary", {}, {
    delaysMs: [10, 20, 40],
    fetchImpl: async () => {
      calls += 1;
      if (calls === 1) return Response.json({ error: "D1 unavailable" }, { status: 500 });
      if (calls === 2) throw new TypeError("socket unavailable");
      return Response.json({ ok: true });
    },
    wait: async (delay) => {
      delays.push(delay);
      return true;
    },
  });

  assert.equal(response.status, 200);
  assert.equal(calls, 3);
  assert.deepEqual(delays, [10, 20]);
});

test("transient fetch retry does not retry application errors or unsafe writes", async () => {
  let calls = 0;
  const response = await fetchWithTransientRetry("/api/sales/summary", {}, {
    fetchImpl: async () => {
      calls += 1;
      return Response.json({ error: "invalid range" }, { status: 400 });
    },
    wait: async () => true,
  });

  assert.equal(response.status, 400);
  assert.equal(calls, 1);
  await assert.rejects(
    fetchWithTransientRetry("/api/import", { method: "POST" }, { fetchImpl: fetch }),
    /仅允许 GET 或 HEAD/,
  );
});

test("transient fetch retry stops immediately when the caller aborts", async () => {
  const controller = new AbortController();
  let calls = 0;
  await assert.rejects(
    fetchWithTransientRetry("/api/sales/summary", { signal: controller.signal }, {
      delaysMs: [10, 20],
      fetchImpl: async () => {
        calls += 1;
        return Response.json({ error: "temporary" }, { status: 503 });
      },
      wait: async () => {
        controller.abort();
        return false;
      },
    }),
    (error: unknown) => error instanceof DOMException && error.name === "AbortError",
  );
  assert.equal(calls, 1);
});

test("sales overview opts into bounded recovery without retrying write requests", async () => {
  const page = await readFile(new URL("../app/sales-module-view.tsx", import.meta.url), "utf8");

  assert.match(page, /fetchWithTransientRetry\(\s*`\/api\/sales\/summary/);
  assert.match(page, /delaysMs: \[1_000, 2_000, 4_000, 8_000, 12_000, 15_000\]/);
});
