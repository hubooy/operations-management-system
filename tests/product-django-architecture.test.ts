import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const onlinePaths = [
  "../app/api/products/summary/route.ts",
  "../app/api/imports/product-shipping-rates/route.ts",
  "../app/api/imports/product-shipping-rates/chunks/route.ts",
  "../lib/products/summary.ts",
  "../lib/products/shipping-rate-import-service.ts",
  "../lib/products/chunked-upload.ts",
];

test("product online paths terminate in Django and expose no D1/R2 fallback", async () => {
  for (const path of onlinePaths) {
    const source = await readFile(new URL(path, import.meta.url), "utf8");
    assert.doesNotMatch(
      source,
      /shipping-rate-database|getInventoryDatabase|env\.DB|SALES_IMPORT_FILES|inventory\/chunked-upload/,
      path,
    );
  }
  const summary = await readFile(new URL("../lib/products/summary.ts", import.meta.url), "utf8");
  const imports = await readFile(new URL("../lib/products/shipping-rate-import-service.ts", import.meta.url), "utf8");
  const chunks = await readFile(new URL("../lib/products/chunked-upload.ts", import.meta.url), "utf8");
  assert.match(summary, /createDjangoProductsService/);
  assert.match(imports, /PRODUCTS_IMPORTS_PATH/);
  assert.match(chunks, /PRODUCTS_UPLOADS_PATH/);
});

test("inventory sync consumes only the bounded Django projection and excludes 刷刷仓 at its owner", async () => {
  const [source, inventoryConsumer] = await Promise.all([
    readFile(new URL("../lib/products/inventory-projection-sync.ts", import.meta.url), "utf8"),
    readFile(new URL("../backend/inventory/consumers.py", import.meta.url), "utf8"),
  ]);
  assert.match(source, /createDjangoInventoryConsumerReader/);
  assert.match(source, /operation: "stock_projection"/);
  assert.match(source, /PAGE_SIZE = 1_000/);
  assert.match(source, /MAX_ROWS = 20_000/);
  assert.match(source, /begin_sync[\s\S]+stage_page[\s\S]+activate_sync/);
  assert.match(source, /PRODUCTS_INVENTORY_PROJECTION_PATH/);
  assert.doesNotMatch(source, /getInventoryDatabase|getD1Database|env\.DB/);
  assert.match(inventoryConsumer, /exclude\(warehouse="刷刷仓"\)/);
  assert.match(inventoryConsumer, /grouped\.setdefault\(row\.product_code\.strip\(\)/);
});

test("AI and global search consume the revisioned products reader", async () => {
  const [ai, search] = await Promise.all([
    readFile(new URL("../lib/ai/operations-tools.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/search/global-search.ts", import.meta.url), "utf8"),
  ]);
  assert.match(ai, /ProductsConsumerReader/);
  assert.match(ai, /operation: "product_performance"/);
  assert.match(search, /createDjangoProductsConsumerReader/);
  assert.match(search, /operation: "import_batch_search"/);
  assert.doesNotMatch(ai, /shipping-rate-database/);
  assert.doesNotMatch(search, /FROM\s+product_shipping_rate_import_batches/i);
});

test("product upload UI uses the same 20 MiB and 1 MiB chunk contract as Django", async () => {
  const source = await readFile(new URL("../app/import-module-view.tsx", import.meta.url), "utf8");
  assert.match(
    source,
    /key: "sku_shipping_rates"[^\n]+maxFileSize: MAX_INVENTORY_FILE_SIZE[^\n]+chunkSize: INVENTORY_UPLOAD_CHUNK_SIZE/,
  );
});
