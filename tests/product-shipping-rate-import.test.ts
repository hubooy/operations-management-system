import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { registerHooks } from "node:module";
import test from "node:test";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import { strFromU8, strToU8, unzipSync, zipSync } from "fflate";
import * as XLSX from "xlsx";

import {
  ProductShippingRateWorkbookError,
  parseProductShippingRateXlsx,
} from "../lib/products/shipping-rate-xlsx";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "cloudflare:workers") {
      return { url: "data:text/javascript,export const env={};", shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const {
  countProductShippingRatesOwnedByBatch,
  ensureProductShippingRateSchema,
  readProductShippingRateOwnership,
  saveProductShippingRateImport,
} = await import("../lib/products/shipping-rate-database");
const {
  buildImportAttemptHash,
  buildImportContentFingerprint,
  ensureImportFingerprintSchema,
  nextImportScopeStateToken,
  readImportScopeStateToken,
  recordImportFingerprint,
  reserveImportFingerprint,
} = await import("../lib/imports/content-fingerprint");

function sqliteAdapter(sqlite: DatabaseSync) {
  return {
    prepare(sql: string) {
      let values: SQLInputValue[] = [];
      return {
        bind(...nextValues: unknown[]) { values = nextValues as SQLInputValue[]; return this; },
        async first<T>() { return (sqlite.prepare(sql).get(...values) ?? null) as T | null; },
        async all<T>() { return { results: sqlite.prepare(sql).all(...values) as T[] }; },
        async run() {
          const result = sqlite.prepare(sql).run(...values);
          return { meta: { changes: Number(result.changes) } };
        },
      };
    },
    async batch(statements: Array<{ run(): Promise<unknown> }>) {
      sqlite.exec("BEGIN");
      try {
        const results = [];
        for (const statement of statements) results.push(await statement.run());
        sqlite.exec("COMMIT");
        return results;
      } catch (error) {
        sqlite.exec("ROLLBACK");
        throw error;
      }
    },
  };
}

function workbookBytes(input: {
  cachedRate?: number;
  duplicateRate?: number | null;
  sheetName?: string;
} = {}) {
  const rows: unknown[][] = Array.from({ length: 5 }, () => Array(27).fill(null));
  rows[0][1] = "代码";
  rows[0][12] = "实际金额";
  rows[0][25] = "合计快递费";
  rows[0][26] = "快递费占比";
  rows[1][1] = "SKU-A";
  rows[1][12] = 100;
  rows[1][25] = 5;
  rows[1][26] = input.cachedRate ?? 0.05;
  rows[2][1] = "SKU-A";
  if (input.duplicateRate !== undefined && input.duplicateRate !== null) {
    rows[2][12] = 100;
    rows[2][25] = input.duplicateRate * 100;
    rows[2][26] = input.duplicateRate;
  }
  rows[3][1] = "SKU-B";
  rows[3][12] = -20;
  rows[3][25] = 4;
  rows[3][26] = -0.2;
  rows[4][1] = "代码";
  const sheet = XLSX.utils.aoa_to_sheet(rows);
  sheet.AA2 = { t: "n", v: input.cachedRate ?? 0.05, f: "IFERROR(Z2/M2,0)" };
  sheet.AA4 = { t: "n", v: -0.2, f: "IFERROR(Z4/M4,0)" };
  const workbook = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook, sheet, input.sheetName ?? "SKU累计");
  return new Uint8Array(XLSX.write(workbook, { type: "array", bookType: "xlsx", bookSST: true }));
}

function namespacePrefixedWorkbookBytes() {
  const archive = unzipSync(workbookBytes());
  for (const name of ["xl/workbook.xml", "xl/worksheets/sheet1.xml", "xl/sharedStrings.xml"]) {
    const entry = archive[name];
    assert.ok(entry, `${name} must exist in the XLSX fixture`);
    const prefixed = strFromU8(entry)
      .replace(
        'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"',
        'xmlns:x="http://schemas.openxmlformats.org/spreadsheetml/2006/main"',
      )
      .replace(/<(\/?)([A-Za-z_][\w.-]*)(?=[\s/>])/g, "<$1x:$2");
    archive[name] = strToU8(prefixed);
  }
  return zipSync(archive);
}

test("SKU累计 parser recalculates rates, collapses safe duplicates, and ignores repeated code headers", () => {
  const parsed = parseProductShippingRateXlsx(workbookBytes());
  assert.equal(parsed.sheetName, "SKU累计");
  assert.equal(parsed.sourceRowCount, 3);
  assert.equal(parsed.duplicateProductCodeCount, 1);
  assert.deepEqual(parsed.rows.map((row) => ({ code: row.productCode, rate: row.shippingRate })), [
    { code: "SKU-A", rate: 0.05 },
    { code: "SKU-B", rate: -0.2 },
  ]);
  assert.ok(parsed.warnings.some((issue) => issue.code === "DUPLICATE_PRODUCT_CODES_COLLAPSED"));
  assert.ok(parsed.warnings.some((issue) => issue.code === "NEGATIVE_SHIPPING_RATES"));
});

test("SKU累计 parser accepts valid namespace-prefixed SpreadsheetML", () => {
  const parsed = parseProductShippingRateXlsx(namespacePrefixedWorkbookBytes());
  assert.deepEqual(parsed.rows.map((row) => ({ code: row.productCode, rate: row.shippingRate })), [
    { code: "SKU-A", rate: 0.05 },
    { code: "SKU-B", rate: -0.2 },
  ]);
});

test("SKU累计 parser rejects stale cached rate values", () => {
  assert.throws(
    () => parseProductShippingRateXlsx(workbookBytes({ cachedRate: 0.06 })),
    (error: unknown) => error instanceof ProductShippingRateWorkbookError
      && error.issues.some((issue) => issue.code === "STALE_OR_INVALID_RATE" && issue.row === 2),
  );
});

test("SKU累计 parser rejects conflicting complete rows for one specification code", () => {
  assert.throws(
    () => parseProductShippingRateXlsx(workbookBytes({ duplicateRate: 0.08 })),
    (error: unknown) => error instanceof ProductShippingRateWorkbookError
      && error.issues.some((issue) => issue.code === "CONFLICTING_DUPLICATE_PRODUCT_CODE"),
  );
});

test("SKU累计 parser requires the exact worksheet identity", () => {
  assert.throws(
    () => parseProductShippingRateXlsx(workbookBytes({ sheetName: "SKU累积" })),
    (error: unknown) => error instanceof ProductShippingRateWorkbookError
      && error.issues.some((issue) => issue.code === "INVALID_SHEET_COUNT"),
  );
});

test("shipping-rate publication atomically replaces the complete specification set", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite) as never;
  await ensureProductShippingRateSchema(db);
  await ensureImportFingerprintSchema(db);

  const publish = async (rows: Array<{ productCode: string; shippingRate: number; sourceRowNumber: number }>) => {
    const fingerprint = await buildImportContentFingerprint({
      domain: "product-shipping-rates",
      scope: { dataset: "sku_cumulative", sheetName: "SKU累计" },
      lockScope: { dataset: "sku_cumulative" },
      rows,
      ignoredTopLevelKeys: ["sourceRowNumber"],
    });
    const currentStateToken = await readImportScopeStateToken(db, fingerprint);
    const importHash = await buildImportAttemptHash({ fingerprint, currentStateToken });
    const batchId = `sku-shipping-rates:${importHash}`;
    const rawFileHash = rows.length.toString(16).padStart(64, "0");
    const reservation = await reserveImportFingerprint(db, {
      ...fingerprint,
      batchId,
      importHash,
      rawFileHash,
      currentStateToken,
    });
    assert.equal(reservation.claimed, true);
    const saved = await saveProductShippingRateImport(db, {
      id: batchId,
      fileName: "annual-profit.xlsx",
      fileSizeBytes: 100,
      fileHash: importHash,
      rawFileHash,
      contentHash: fingerprint.contentHash,
      sheetName: "SKU累计",
      actor: "admin@example.com",
      sourceRowCount: rows.length,
      duplicateCount: 0,
      rows,
      warnings: [],
      totals: {},
      reservationFence: {
        domain: fingerprint.domain,
        scopeKey: fingerprint.scopeKey,
        batchId,
        attemptId: reservation.attemptId,
      },
    });
    const publishedStateToken = await nextImportScopeStateToken({
      previousStateToken: currentStateToken,
      batchId,
      contentHash: fingerprint.contentHash,
      rowCount: fingerprint.rowCount,
    });
    await recordImportFingerprint(db, {
      ...fingerprint,
      batchId,
      importHash,
      rawFileHash,
      attemptId: reservation.attemptId,
      publishedStateToken,
      outcome: "imported",
    });
    return saved;
  };

  const first = await publish([
    { productCode: "SKU-A", shippingRate: 0.05, sourceRowNumber: 2 },
    { productCode: "SKU-B", shippingRate: 0.12, sourceRowNumber: 3 },
  ]);
  assert.equal(first.batch.insertedCount, 2);
  assert.equal(first.batch.updatedCount, 0);

  const second = await publish([
    { productCode: "SKU-A", shippingRate: 0.08, sourceRowNumber: 2 },
    { productCode: "SKU-C", shippingRate: -0.2, sourceRowNumber: 4 },
  ]);
  assert.equal(second.batch.insertedCount, 1);
  assert.equal(second.batch.updatedCount, 1);
  assert.equal(await countProductShippingRatesOwnedByBatch(db, second.batch.id), 2);
  assert.deepEqual(await readProductShippingRateOwnership(db), [{ batchId: second.batch.id, rowCount: 2 }]);
  assert.deepEqual(
    sqlite.prepare("SELECT product_code, shipping_rate FROM product_shipping_rates ORDER BY product_code").all()
      .map((row) => ({ product_code: String(row.product_code), shipping_rate: Number(row.shipping_rate) })),
    [
      { product_code: "SKU-A", shipping_rate: 0.08 },
      { product_code: "SKU-C", shipping_rate: -0.2 },
    ],
  );
  sqlite.close();
});

test("shipping-rate production contract uses Django fencing while legacy D1 stays isolated test material", async () => {
  const [service, database, migration, authority, retirement, route, chunks, productSummary, productView] = await Promise.all([
    readFile(new URL("../lib/products/shipping-rate-import-service.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/products/shipping-rate-database.ts", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0088_product_shipping_rates.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0099_product_write_authority.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0100_product_domain_retirement.sql", import.meta.url), "utf8"),
    readFile(new URL("../app/api/imports/product-shipping-rates/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/imports/product-shipping-rates/chunks/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../lib/products/summary.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/product-module-view.tsx", import.meta.url), "utf8"),
  ]);
  assert.match(service, /PRODUCT_SHIPPING_RATE_IMPORT_VERSION = "product-shipping-rates-normalized-v1"/);
  assert.match(service, /shippingRatePpt/);
  assert.match(service, /createDjangoProductsService/);
  assert.match(service, /kind: "rejection"/);
  assert.match(database, /DELETE FROM product_shipping_rates[\s\S]*last_import_batch_id <> \?/);
  assert.match(database, /importReservationCommitFence/);
  assert.match(database, /db\.batch\(statements\)/);
  assert.match(migration, /product_shipping_rate_import_batches/);
  assert.match(migration, /product_shipping_rates/);
  assert.match(authority, /product_write_authority_not_d1/);
  assert.match(retirement, /products-domain-retirement-receipt-v1/);
  assert.match(retirement, /product_domain_retired/);
  assert.match(route, /requireAppPrincipal\(\["admin"\]\)/);
  assert.match(route, /MAX_DIRECT_FILE_BYTES = 2 \* 1024 \* 1024/);
  assert.doesNotMatch(route, /shipping-rate-database/);
  assert.match(chunks, /MAX_CHUNKED_PRODUCT_FILE_BYTES/);
  assert.match(chunks, /beginProductUpload/);
  assert.doesNotMatch(chunks, /inventory\/chunked-upload/);
  assert.match(productSummary, /createDjangoProductsService/);
  assert.doesNotMatch(productSummary, /FROM product_shipping_rates/);
  assert.match(productView, /<th>退货率<\/th><th>快递费率<\/th><th>操作<\/th>/);
  assert.doesNotMatch(productView, /<th>费用<\/th>/);
  assert.doesNotMatch(productView, /formatCurrencyFromCents\(item\.feeCents\)/);
  assert.match(productView, /colSpan=\{12\}/);
});
