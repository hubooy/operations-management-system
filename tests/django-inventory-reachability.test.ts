import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const productionEntryPoints = [
  "app/api/inventory/overview/route.ts",
  "app/api/inventory/age-analysis/route.ts",
  "app/api/inventory/inbound-monitor/route.ts",
  "app/api/inventory/replenishment/route.ts",
  "app/api/inventory/work-items/route.ts",
  "app/api/imports/inventory/route.ts",
  "app/api/imports/inventory/chunks/route.ts",
  "app/api/settings/route.ts",
  "app/api/settings/warehouse-mappings/route.ts",
  "lib/products/inventory-projection-sync.ts",
  "lib/inventory/system-cost-reference.ts",
  "lib/ai/operations-tools.ts",
  "lib/ai/page-data-tools.ts",
  "lib/search/global-search.ts",
] as const;

const retiredImports = /@\/lib\/inventory\/(?:overview|age-analysis|inbound-monitor|database|import-service|chunked-upload)/;

async function source(path: string) {
  return readFile(new URL(`../${path}`, import.meta.url), "utf8");
}

test("reachable inventory entry points use Django and cannot fall back to D1", async () => {
  const entries = await Promise.all(productionEntryPoints.map(async (path) => ({
    path,
    text: await source(path),
  })));
  for (const entry of entries) {
    assert.doesNotMatch(entry.text, retiredImports, `${entry.path} still reaches a retired D1 inventory module`);
  }

  const djangoInventoryEndpoints = productionEntryPoints.slice(0, 9).filter(
    (path) => path !== "app/api/inventory/work-items/route.ts",
  );
  for (const path of djangoInventoryEndpoints) {
    const entry = entries.find((candidate) => candidate.path === path)!;
    assert.match(entry.text, /django|Django/, `${path} is not visibly bound to the Django inventory path`);
  }
  assert.match(
    await source("lib/inventory/work-items.ts"),
    /createDjangoInventoryConsumerReader/,
    "inventory work items must validate their Django inventory source before writing the separate operations domain",
  );
  assert.match(await source("lib/products/inventory-projection-sync.ts"), /inventory-consumer-reader/);
  assert.match(await source("lib/inventory/system-cost-reference.ts"), /inventory-consumer-reader/);
  assert.match(await source("lib/search/global-search.ts"), /createDjangoInventoryConsumerReader/);
});

test("shared ERP endpoints route each source to its owning Django domain and cannot reach retired ERP D1", async () => {
  const direct = await source("app/api/imports/erp/route.ts");
  const chunks = await source("app/api/imports/erp/chunks/route.ts");
  assert.match(direct, /source === "inventory_age"[\s\S]*?importInventoryAgeToDjango/);
  assert.match(direct, /source === "inventory_age"[\s\S]*?INVENTORY_IMPORTS_PATH/);
  assert.match(direct, /createDjangoErpReferenceService/);
  assert.match(direct, /source: "products"[\s\S]*?ERP_REFERENCE_IMPORTS_PATH/);
  assert.match(direct, /source: "combos"[\s\S]*?ERP_REFERENCE_IMPORTS_PATH/);
  assert.match(direct, /importErpReferenceToDjango/);
  assert.doesNotMatch(direct, /@\/lib\/erp-reference\/(?:database|import-service|projection-outbox)/);
  assert.match(direct, /combinedLimit > 100/);
  assert.match(chunks, /body\.source === "inventory_age"[\s\S]*?beginDjangoInventoryUpload/);
  assert.match(chunks, /body\.source === "inventory_age"[\s\S]*?importInventoryAgeToDjango/);
  assert.match(chunks, /beginDjangoErpUpload/);
  assert.match(chunks, /receiveDjangoErpUploadChunk/);
  assert.match(chunks, /importErpReferenceToDjango/);
  assert.doesNotMatch(chunks, /@\/lib\/erp-reference\/(?:database|import-service|projection-outbox)/);
});

test("shared non-inventory chunk uploads initialize only their retained upload tables", async () => {
  const sharedUploader = await source("lib/inventory/chunked-upload.ts");
  assert.doesNotMatch(sharedUploader, /ensureInventorySchema/);
  assert.match(sharedUploader, /ensureSharedImportUploadSchema/);
  assert.match(sharedUploader, /CREATE TABLE IF NOT EXISTS inventory_import_uploads/);
  assert.match(sharedUploader, /CREATE TABLE IF NOT EXISTS inventory_import_upload_chunks/);
  assert.match(sharedUploader, /CREATE TABLE IF NOT EXISTS inventory_import_upload_results/);
  assert.doesNotMatch(sharedUploader, /CREATE TABLE IF NOT EXISTS (?:inventory_stock_lines|inventory_import_batches|erp_inventory_age_lines|inventory_age_metrics|replenishment_plan_items)/);
});
