import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const activeRoutes = [
  "ai/route.ts",
  "annotations/route.ts",
  "annotations/worker/route.ts",
  "daily-coverage/route.ts",
  "images/[hash]/route.ts",
  "images/cache/route.ts",
  "images/repair/route.ts",
  "import/route.ts",
  "master/execute/route.ts",
  "master/route.ts",
  "overview/route.ts",
  "trend/route.ts",
];

test("every active market API is a principal-aware thin Django edge with no D1 fact fallback", async () => {
  const sources = await Promise.all(activeRoutes.map(async (path) => ({
    path,
    source: await readFile(new URL(`../app/api/market/${path}`, import.meta.url), "utf8"),
  })));
  for (const { path, source } of sources) {
    if (path === "annotations/worker/route.ts") {
      assert.match(source, /agentToken\(request\)/,
        `${path} must retain the bounded local-agent bearer principal`);
    } else {
      assert.match(source, /requireAppPrincipal/,
        `${path} must retain real public principal enforcement`);
    }
    assert.match(source, /requestDjangoMarketService|runClaimedDjangoMarketVisionTask/,
      `${path} must terminate in the Django market contract`);
    assert.doesNotMatch(source, /getMarketDatabase|market\/database|market\/admin-service|market\/annotation-service/);
    assert.doesNotMatch(source, /env\.DB\.(?:prepare|batch|exec)|getDb\(\)/);
  }
});

test("scheduled market execution reaches only Django-owned queues and the Django netshop projection", async () => {
  const [worker, annotation, projection, image, aiTools, pageTools] = await Promise.all([
    readFile(new URL("../worker/index.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/django-annotation-scheduled.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/django-netshop-projection-runner.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/django-image-cache-runner.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/market/ai-tools.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/ai/page-data-tools.ts", import.meta.url), "utf8"),
  ]);
  assert.match(worker, /runScheduledDjangoMarketAnnotation/);
  assert.match(worker, /runDjangoMarketImageCacheBatch/);
  assert.match(worker, /runDjangoMarketNetshopProjectionSync/);
  assert.doesNotMatch(worker, /runScheduledCloudAnnotation|runMarketImageCacheBatch|ensureMarketNetshopProjection/);
  for (const source of [annotation, projection, image, aiTools, pageTools]) {
    assert.match(source, /DjangoMarket|django/i);
    assert.doesNotMatch(source, /getMarketDatabase|market\/database|market\/admin-service|market\/annotation-service/);
  }
  assert.match(projection, /readDjangoNetshopConsumer/);
});

test("market edge configuration exposes separate fixed reader and writer origins", async () => {
  const [service, settings] = await Promise.all([
    readFile(new URL("../lib/django/market-service.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/teruisi_backend/settings.py", import.meta.url), "utf8"),
  ]);
  assert.match(service, /TERUISI_DJANGO_MARKET_READER_BASE_URL/);
  assert.match(service, /TERUISI_DJANGO_MARKET_WRITER_BASE_URL/);
  assert.match(service, /readerBaseUrl\.origin === writerBaseUrl\.origin/);
  assert.match(service, /http:[\s\S]*!loopback/);
  assert.match(settings, /DJANGO_PROCESS_ROLE.*market_reader/);
  assert.match(settings, /DJANGO_PROCESS_ROLE.*market_writer/);
  assert.match(settings, /DJANGO_MARKET_AUTHORITY_EPOCH/);
});

test("market daily coverage edge and Django reader share the exact ranking identity contract", async () => {
  const [route, query] = await Promise.all([
    readFile(new URL("../app/api/market/daily-coverage/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/market/query.py", import.meta.url), "utf8"),
  ]);
  for (const field of ["category", "scope", "rankingDimension", "priceBandFilter", "startDate", "endDate"]) {
    assert.match(route, new RegExp(`${field}:`), `edge payload must include ${field}`);
    assert.match(query, new RegExp(`"${field}"`), `Django daily coverage must accept ${field}`);
  }
  assert.doesNotMatch(route, /categories:/);
  assert.match(query, /period_start=F\("period_end"\)/);
  assert.match(query, /"presentDates"/);
  assert.match(query, /"missingDates"/);
  assert.match(query, /"rowCounts"/);
});
