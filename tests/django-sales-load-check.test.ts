import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";
import {
  LoadCheckError,
  LoadCheckThresholdError,
  parseLoadCheckArgs,
  runLoadCheck,
  validateLoadCheckBaseUrl,
  type LoadCheckConfig,
} from "../tools/django-sales-load-check";

const secret = "test-only-django-load-secret-32-bytes-minimum";

function config(overrides: Partial<LoadCheckConfig> = {}): LoadCheckConfig {
  return {
    baseUrl: new URL("http://127.0.0.1:8001"),
    startDate: "2026-08-01",
    endDate: "2026-08-27",
    concurrency: 2,
    rounds: 2,
    views: ["full"],
    timeoutMs: 1_000,
    maxResponseBytes: 64 * 1024,
    ...overrides,
  };
}

function jsonResponse(payload: unknown, revision = "8:5", headers: HeadersInit = {}): Response {
  return Response.json(payload, {
    headers: {
      "cache-control": "no-store",
      "x-sales-data-revision": revision,
      "x-sales-source-revision": revision,
      "x-sales-overview-cache": "bypass",
      ...Object.fromEntries(new Headers(headers)),
    },
  });
}

test("load check arguments require explicit bounded inputs and safe base URLs", () => {
  const parsed = parseLoadCheckArgs([
    "--base-url", "https://sales.example.com",
    "--start-date", "2026-08-01",
    "--end-date", "2026-08-27",
    "--concurrency", "4",
    "--rounds", "3",
    "--view", "full,dashboard,category",
  ]);
  assert.equal(parsed.baseUrl.origin, "https://sales.example.com");
  assert.deepEqual(parsed.views, ["full", "dashboard", "category"]);
  assert.equal(parsed.timeoutMs, 10_000);
  assert.equal(parsed.maxResponseBytes, 4 * 1024 * 1024);
  assert.equal(parsed.category, undefined);
  assert.equal(parsed.thresholds, undefined);

  const detail = parseLoadCheckArgs([
    "--base-url", "http://127.0.0.1:8001",
    "--start-date", "2026-08-01",
    "--end-date", "2026-08-27",
    "--concurrency", "2",
    "--rounds", "5",
    "--view", "category-detail",
    "--category", "饮水设备",
    "--timeout-ms", "20000",
    "--p95-ms", "2000.5",
    "--p99-ms", "3000",
    "--max-ms", "5000",
  ]);
  assert.equal(detail.category, "饮水设备");
  assert.deepEqual(detail.thresholds, { p95Ms: 2000.5, p99Ms: 3000, maxMs: 5000 });

  for (const unsafe of [
    "http://sales.example.com",
    "http://127.0.0.1.evil.example.com",
    "http://localhost.evil.example.com",
    "https://user:password@sales.example.com",
    "https://sales.example.com/api",
  ]) {
    assert.throws(() => validateLoadCheckBaseUrl(unsafe), LoadCheckError, unsafe);
  }
  assert.throws(() => parseLoadCheckArgs([
    "--base-url", "http://127.0.0.1:8001",
    "--start-date", "2026-08-01",
    "--end-date", "2026-08-27",
    "--concurrency", "1",
    "--rounds", "1",
    "--secret", secret,
  ]), /unknown option: --secret/);

  const required = [
    "--base-url", "http://127.0.0.1:8001",
    "--start-date", "2026-08-01",
    "--end-date", "2026-08-27",
    "--concurrency", "1",
    "--rounds", "1",
  ];
  assert.throws(() => parseLoadCheckArgs([...required, "--view", "category-detail"]), /--category is required/);
  assert.throws(() => parseLoadCheckArgs([...required, "--category", "饮水设备"]), /--category is only allowed/);
  for (const invalidCategory of [" 饮水设备", "饮水设备 ", "bad\nname", "品".repeat(101)]) {
    assert.throws(
      () => parseLoadCheckArgs([...required, "--view", "category-detail", "--category", invalidCategory]),
      /--category must be an exact non-empty value/,
    );
  }
  for (const [flag, value] of [["--p95-ms", "0"], ["--p99-ms", "1.234"], ["--max-ms", "30001"]]) {
    assert.throws(() => parseLoadCheckArgs([...required, flag, value]), new RegExp(flag));
  }
  assert.throws(() => parseLoadCheckArgs([...required, "--timeout-ms", "100", "--p95-ms", "101"]), /must not exceed --timeout-ms/);
  assert.throws(() => parseLoadCheckArgs([...required, "--p95-ms", "20", "--p99-ms", "10"]), /--p95-ms must not exceed --p99-ms/);
  assert.throws(() => parseLoadCheckArgs([
    ...required.slice(0, -4),
    "--concurrency", "32",
    "--rounds", "8",
    "--view", "full,dashboard,category,category-detail",
    "--category", "饮水设备",
  ]), /planned request count exceeds 1000/);
});

test("load check sends the exact signed principal envelope and reports stable concurrent metrics", async () => {
  const observed: Array<{ target: URL; headers: Headers }> = [];
  const samples: unknown[] = [];
  let requestSequence = 0;
  const report = await runLoadCheck(config(), {
    environment: { TERUISI_DJANGO_INTERNAL_SECRET: secret },
    now: () => 1_788_000_000_000,
    requestId: () => `load-check-test-${++requestSequence}`,
    onSample: (sample) => samples.push(sample),
    fetchImpl: async (input, init) => {
      const target = new URL(String(input));
      const headers = new Headers(init?.headers);
      observed.push({ target, headers });
      return jsonResponse({ current: { netSalesCents: 100 }, nested: { b: 2, a: 1 } });
    },
  });

  assert.equal(observed.length, 4);
  assert.equal(samples.length, 4);
  assert.equal(report.revision, "8:5");
  assert.equal(report.overall.requests, 4);
  assert.equal(report.overall.p99Ms, report.overall.maxMs);
  assert.equal(report.byView.full?.requests, 4);
  assert.equal(report.byView["category-detail"], null);
  assert.equal(report.thresholds, null);
  assert.equal(report.passed, true);
  assert.equal(report.samples.every((sample) => sample.overviewCache === "bypass"), true);
  assert.equal(new Set(report.samples.map((sample) => sample.jsonSha256)).size, 1);

  const first = observed[0]!;
  const principalBase64 = first.headers.get("x-teruisi-principal");
  assert.ok(principalBase64);
  assert.deepEqual(JSON.parse(Buffer.from(principalBase64, "base64url").toString("utf8")), {
    email: "django-sales-load-check@internal.invalid",
    displayName: "Django Sales Load Check",
    role: "admin",
    scope: null,
  });
  const canonical = [
    "v1",
    "1788000000",
    "load-check-test-1",
    "GET",
    first.target.pathname,
    first.target.search.slice(1),
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    principalBase64,
  ].join("\n");
  assert.equal(
    first.headers.get("x-teruisi-signature"),
    `v1=${createHmac("sha256", secret).update(canonical).digest("hex")}`,
  );
  assert.equal(first.headers.has("authorization"), false);
  assert.equal([...first.headers.values()].some((value) => value.includes(secret)), false);
});

test("category detail load uses only the explicit exact category and the bounded real endpoint", async () => {
  const observed: URL[] = [];
  const report = await runLoadCheck(config({
    views: ["category-detail"],
    category: "饮水设备",
    concurrency: 2,
    rounds: 1,
  }), {
    environment: { TERUISI_DJANGO_INTERNAL_SECRET: secret },
    fetchImpl: async (input) => {
      observed.push(new URL(String(input)));
      return jsonResponse({
        range: { startDate: "2026-08-01", endDate: "2026-08-27" },
        category: "饮水设备",
        totals: { netSalesCents: 100, platformCount: 1, shopCount: 1 },
        platforms: [{ platform: "京东", shops: [{ shop: "测试店铺" }] }],
        pagination: { total: 1, returned: 1, truncated: false, limit: 500 },
      });
    },
  });

  assert.equal(observed.length, 2);
  for (const target of observed) {
    assert.equal(target.pathname, "/api/sales/category-analysis/detail");
    assert.deepEqual([...target.searchParams], [
      ["startDate", "2026-08-01"],
      ["endDate", "2026-08-27"],
      ["category", "饮水设备"],
    ]);
  }
  assert.equal(report.byView["category-detail"]?.requests, 2);
  assert.equal(report.overall.p99Ms, report.overall.maxMs);
});

test("category detail load fails closed on mismatched identity, range, and empty coverage", async () => {
  const valid = {
    range: { startDate: "2026-08-01", endDate: "2026-08-27" },
    category: "饮水设备",
    totals: { netSalesCents: 100, platformCount: 1, shopCount: 1 },
    platforms: [{ platform: "京东", shops: [{ shop: "测试店铺" }] }],
    pagination: { total: 1, returned: 1, truncated: false, limit: 500 },
  };
  const cases: Array<{ payload: unknown; pattern: RegExp }> = [
    { payload: { ...valid, category: "其他品类" }, pattern: /response category does not match/ },
    {
      payload: { ...valid, range: { ...valid.range, endDate: "2026-08-26" } },
      pattern: /response range does not match/,
    },
    {
      payload: {
        ...valid,
        totals: { ...valid.totals, netSalesCents: 0, platformCount: 0, shopCount: 0 },
        platforms: [],
        pagination: { ...valid.pagination, total: 0, returned: 0 },
      },
      pattern: /no non-empty, internally consistent shop coverage/,
    },
  ];

  for (const selected of cases) {
    await assert.rejects(
      runLoadCheck(config({
        views: ["category-detail"],
        category: "饮水设备",
        concurrency: 1,
        rounds: 1,
      }), {
        environment: { TERUISI_DJANGO_INTERNAL_SECRET: secret },
        fetchImpl: async () => jsonResponse(selected.payload),
      }),
      selected.pattern,
    );
  }
});

test("optional latency thresholds pass explicitly or fail closed with the completed report", async () => {
  const passing = await runLoadCheck(config({
    concurrency: 1,
    rounds: 1,
    thresholds: { p95Ms: 1_000, p99Ms: 1_000, maxMs: 1_000 },
  }), {
    environment: { TERUISI_DJANGO_INTERNAL_SECRET: secret },
    fetchImpl: async () => jsonResponse({ stable: true }),
  });
  assert.equal(passing.passed, true);
  assert.deepEqual(passing.thresholds, { p95Ms: 1_000, p99Ms: 1_000, maxMs: 1_000 });
  assert.deepEqual(passing.thresholdViolations, []);

  await assert.rejects(
    runLoadCheck(config({
      concurrency: 1,
      rounds: 1,
      thresholds: { p95Ms: 1, p99Ms: 1, maxMs: 1 },
    }), {
      environment: { TERUISI_DJANGO_INTERNAL_SECRET: secret },
      fetchImpl: async () => {
        await new Promise((resolve) => setTimeout(resolve, 15));
        return jsonResponse({ stable: true });
      },
    }),
    (error: unknown) => {
      assert.ok(error instanceof LoadCheckThresholdError);
      assert.equal(error.report.passed, false);
      assert.equal(error.report.overall.p99Ms > 1, true);
      assert.equal(error.report.thresholdViolations.some((item) => item.metric === "p99Ms"), true);
      assert.match(error.message, /latency thresholds exceeded/);
      return true;
    },
  );
});

test("load check fails closed on inconsistent JSON, revisions, status, and response limits", async () => {
  let sequence = 0;
  await assert.rejects(
    runLoadCheck(config({ concurrency: 2, rounds: 1 }), {
      environment: { TERUISI_DJANGO_INTERNAL_SECRET: secret },
      fetchImpl: async () => jsonResponse({ sequence: ++sequence }),
    }),
    /JSON digest changed/,
  );

  sequence = 0;
  await assert.rejects(
    runLoadCheck(config({ concurrency: 2, rounds: 1 }), {
      environment: { TERUISI_DJANGO_INTERNAL_SECRET: secret },
      fetchImpl: async () => jsonResponse({ stable: true }, ++sequence === 1 ? "8:5" : "9:5"),
    }),
    /revision changed/,
  );

  await assert.rejects(
    runLoadCheck(config({ concurrency: 1, rounds: 1 }), {
      environment: { TERUISI_DJANGO_INTERNAL_SECRET: secret },
      fetchImpl: async () => jsonResponse({ error: true }, "8:5", { "content-length": "999999" }),
    }),
    /response exceeds/,
  );

  await assert.rejects(
    runLoadCheck(config({ concurrency: 1, rounds: 1 }), {
      environment: { TERUISI_DJANGO_INTERNAL_SECRET: secret },
      fetchImpl: async () => Response.json({ error: true }, { status: 503 }),
    }),
    /HTTP 503/,
  );

  await assert.rejects(
    runLoadCheck(config({ concurrency: 1, rounds: 1 }), {
      environment: { TERUISI_DJANGO_INTERNAL_SECRET: secret },
      fetchImpl: async () => jsonResponse({ stable: true }, "8:5", { "x-sales-source-revision": "8:4" }),
    }),
    /revision headers are missing or inconsistent/,
  );

  await assert.rejects(
    runLoadCheck(config({ concurrency: 1, rounds: 1 }), { environment: {} }),
    /TERUISI_DJANGO_INTERNAL_SECRET/,
  );
});
