import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import manifest from "../backend/system_datasets/manifest.json";
import { queryDatasetRecords } from "../lib/ai/dataset-records";

const actor = { email: "fixture@example.invalid", displayName: "Fixture", role: "admin" as const, scope: null };
const context = { principal: actor, surface: "ai_chat" as const, requestId: "fixture-records" };

test("record manifest has unique stable endpoints and accounts for 12 domains", () => {
  assert.equal(manifest.datasets.length, 219);
  assert.equal(new Set(manifest.datasets.map(v => v.id)).size, 219);
  assert.equal(new Set(manifest.datasets.map(v => v.domain)).size, 12);
  for (const spec of manifest.datasets) {
    assert.match(spec.id, /^rows_[a-z0-9_]{1,58}$/);
    assert.ok(spec.keys.length > 0);
    assert.deepEqual(spec.roles, ["admin"]);
    for (const key of Object.keys(spec.fields)) assert.doesNotMatch(key, /password|secret|encrypted|token|api_key|aes_key/);
  }
});

test("records source uses only the configured owning reader and authenticates exact payload", async t => {
  const previous = globalThis.fetch;
  const saved = { reader: process.env.TERUISI_DJANGO_ERP_READER_BASE_URL, secret: process.env.TERUISI_DJANGO_INTERNAL_SECRET };
  process.env.TERUISI_DJANGO_ERP_READER_BASE_URL = "http://127.0.0.1:18091";
  process.env.TERUISI_DJANGO_INTERNAL_SECRET = "isolated-datasets-transport-secret-abcdefghijklmnopqrstuvwxyz";
  t.after(() => {
    globalThis.fetch = previous;
    if (saved.reader === undefined) delete process.env.TERUISI_DJANGO_ERP_READER_BASE_URL; else process.env.TERUISI_DJANGO_ERP_READER_BASE_URL = saved.reader;
    if (saved.secret === undefined) delete process.env.TERUISI_DJANGO_INTERNAL_SECRET; else process.env.TERUISI_DJANGO_INTERNAL_SECRET = saved.secret;
  });
  const calls: Request[] = [];
  globalThis.fetch = async (input, init) => {
    calls.push(new Request(input, init));
    return Response.json({ dataset: "rows_erp_product_master", sourceDomain: "erp_reference", rows: [{ product_code: "fixture" }], returned: 1, consistency: "live_per_page" });
  };
  const data = await queryDatasetRecords({ dataset: "rows_erp_product_master", queryJson: '{"columns":["product_code"],"pageSize":1}' }, context);
  assert.equal(data.returned, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:18091/api/ai/dataset-records");
  assert.deepEqual(await calls[0].json(), { dataset: "rows_erp_product_master", query: { columns: ["product_code"], pageSize: 1 } });
  assert.deepEqual(JSON.parse(Buffer.from(calls[0].headers.get("x-teruisi-principal")!, "base64url").toString()), actor);
  for (const args of [{ dataset: "ai_models", queryJson: "{}" }, { dataset: "rows_erp_product_master", queryJson: "[]" }]) {
    await assert.rejects(queryDatasetRecords(args, context));
  }
  await assert.rejects(queryDatasetRecords({ dataset: "rows_erp_product_master", queryJson: "{}" }, { ...context, principal: { ...actor, role: "viewer" } }));
  process.env.TERUISI_DJANGO_ERP_READER_BASE_URL = "http://169.254.169.254";
  await assert.rejects(queryDatasetRecords({ dataset: "rows_erp_product_master", queryJson: "{}" }, context));
  assert.equal(calls.length, 1);
});

test("sales records reuse the deployed sales base URL contract", async t => {
  const originalFetch = globalThis.fetch;
  const keys = ["TERUISI_DJANGO_SALES_READER_BASE_URL", "TERUISI_DJANGO_SALES_BASE_URL", "TERUISI_DJANGO_INTERNAL_SECRET"];
  const saved = keys.map(key => process.env[key]);
  t.after(() => {
    globalThis.fetch = originalFetch;
    keys.forEach((key, i) => { if (saved[i] === undefined) delete process.env[key]; else process.env[key] = saved[i]; });
  });
  delete process.env.TERUISI_DJANGO_SALES_READER_BASE_URL;
  process.env.TERUISI_DJANGO_SALES_BASE_URL = "http://127.0.0.1:18001";
  process.env.TERUISI_DJANGO_INTERNAL_SECRET = "isolated-dataset-sales-secret-abcdefghijklmnopqrstuvwxyz";
  const urls: string[] = [];
  globalThis.fetch = async (input, init) => {
    urls.push(new Request(input, init).url);
    return Response.json({ dataset: "rows_sales_order_lines", sourceDomain: "sales", rows: [], returned: 0, consistency: "live_per_page" });
  };
  await queryDatasetRecords({ dataset: "rows_sales_order_lines", queryJson: "{}" }, context);
  process.env.TERUISI_DJANGO_SALES_READER_BASE_URL = "http://127.0.0.1:18002";
  await queryDatasetRecords({ dataset: "rows_sales_order_lines", queryJson: "{}" }, context);
  assert.deepEqual(urls, ["http://127.0.0.1:18001/api/ai/dataset-records", "http://127.0.0.1:18002/api/ai/dataset-records"]);
});

test("every reader provisioning entry point applies the explicit column contract", async () => {
  const files: Record<string, string[]> = {
    "tools/django-local-service.ps1": ["sales", "finance"],
    "tools/django-erp-reference.ps1": ["erp_reference"],
    "tools/django-netshop-service.ps1": ["netshop"],
    "tools/django-market-service.ps1": ["market"],
    "tools/django-products-service.ps1": ["products"],
    "tools/django-inventory-service.ps1": ["inventory"],
    "tools/django-workflow-service.ps1": ["workflow"],
    "tools/django-customer-service.ps1": ["customer_service"],
    "tools/django-access-control.ps1": ["access_control"],
    "tools/django-bi-service.ps1": ["bi"],
    "backend/ai_assistant/database_contract.py": ["ai_assistant"],
  };
  for (const [file, domains] of Object.entries(files)) {
    const source = await readFile(new URL("../" + file, import.meta.url), "utf8");
    for (const domain of domains) assert.match(source, new RegExp(`grant_columns\\((?:c|cursor), "${domain}"\\)`));
  }
  const grants = await readFile(new URL("../backend/system_datasets/permissions.py", import.meta.url), "utf8");
  assert.match(grants, /GRANT SELECT \(\{\}\) ON public/);
  assert.doesNotMatch(grants, /GRANT (?:ALL|UPDATE|INSERT|DELETE)|CREATE ROLE|BYPASSRLS/);
});
