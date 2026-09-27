import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));

test("finance cutover keeps the existing UI and public paths with Django as the only backend", async () => {
  const [page, salesModule, importModule, analysis, targets, targetImport, imports, service] = await Promise.all([
    readFile(path.join(root, "app", "page.tsx"), "utf8"),
    readFile(path.join(root, "app", "sales-module-view.tsx"), "utf8"),
    readFile(path.join(root, "app", "import-module-view.tsx"), "utf8"),
    readFile(path.join(root, "app", "api", "finance", "analysis", "route.ts"), "utf8"),
    readFile(path.join(root, "app", "api", "finance", "targets", "route.ts"), "utf8"),
    readFile(path.join(root, "app", "api", "finance", "targets", "import", "route.ts"), "utf8"),
    readFile(path.join(root, "app", "api", "imports", "finance", "route.ts"), "utf8"),
    readFile(path.join(root, "lib", "django", "finance-service.ts"), "utf8"),
  ]);
  assert.match(page, /createReloadableLazy\("sales", \(\) => import\("\.\/sales-module-view"\)\)/);
  assert.match(salesModule, /\/api\/finance\/analysis/);
  assert.match(salesModule, /\/api\/finance\/targets/);
  assert.match(importModule, /\/api\/imports\/finance/);
  assert.doesNotMatch(`${page}\n${salesModule}\n${importModule}`, /TERUISI_DJANGO_FINANCE_MODE|finance-normalized-v1/);
  for (const route of [analysis, targets, targetImport, imports]) {
    assert.match(route, /createDjangoFinanceService/);
    assert.doesNotMatch(route, /getFinanceDatabase|ensureFinanceSchema|observeFinanceShadow/);
  }
  assert.match(service, /\?\? "django"/);
  assert.match(service, /type FinanceBackendMode = "django"/);
  assert.doesNotMatch(service, /catch[\s\S]{0,120}return\s+["']legacy["']/i);
  assert.doesNotMatch(imports, /importFinanceReportBytes|Shadow mode/);
});

test("finance routes have no D1 branch and only opt-in raw attestation adds a dispatch", async () => {
  const [analysis, targets, targetImport, imports] = await Promise.all([
    readFile(path.join(root, "app", "api", "finance", "analysis", "route.ts"), "utf8"),
    readFile(path.join(root, "app", "api", "finance", "targets", "route.ts"), "utf8"),
    readFile(path.join(root, "app", "api", "finance", "targets", "import", "route.ts"), "utf8"),
    readFile(path.join(root, "app", "api", "imports", "finance", "route.ts"), "utf8"),
  ]);
  for (const source of [analysis, targets, targetImport, imports]) {
    assert.doesNotMatch(source, /getFinanceDatabase|mode === "(?:legacy|shadow|django)"/);
  }
  const importPost = imports.slice(imports.indexOf("export async function POST"));
  const rawAttestation = importPost.indexOf("if (await rawBytesAttestationEnabled())");
  assert.ok(rawAttestation > 0);
  assert.equal((importPost.slice(0, rawAttestation).match(/createDjangoFinanceService\(\)/g) ?? []).length, 1);
  const optIn = importPost.slice(rawAttestation);
  assert.equal((optIn.match(/createDjangoFinanceService\(\)/g) ?? []).length, 1);
  assert.match(optIn, /attestRawWorkbook/);
  const targetPost = targets.slice(targets.indexOf("export async function POST"), targets.indexOf("export async function DELETE"));
  assert.equal((targetPost.match(/createDjangoFinanceService\(\)/g) ?? []).length, 1);
  const targetImportPost = targetImport.slice(targetImport.indexOf("export async function POST"));
  assert.equal((targetImportPost.match(/createDjangoFinanceService\(\)/g) ?? []).length, 1);
  // The template GET reads shop options once; importing still dispatches once.
  assert.equal((targetImport.match(/createDjangoFinanceService\(\)/g) ?? []).length, 2);
});
