import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";

test("ordinary Drizzle generation fails closed after operator-only sales retirement", async () => {
  const packageJson = JSON.parse(
    await readFile(new URL("../package.json", import.meta.url), "utf8"),
  ) as { scripts?: Record<string, string> };
  assert.equal(packageJson.scripts?.["db:generate"], "node tools/drizzle-generate-guard.mjs");

  const scriptPath = fileURLToPath(new URL("../tools/drizzle-generate-guard.mjs", import.meta.url));
  const result = spawnSync(process.execPath, [scriptPath], {
    encoding: "utf8",
    windowsHide: true,
  });

  assert.equal(result.status, 1);
  assert.match(result.stderr, /Blocked: ordinary Drizzle migration generation is disabled/);
  assert.match(result.stderr, /0092_sales_domain_retirement\.sql is operator-only/);
  assert.match(result.stderr, /0093_finance_write_authority\.sql/);
  assert.match(result.stderr, /0094_netshop_write_authority\.sql/);
  assert.match(result.stderr, /0095_market_netshop_projection\.sql/);
  assert.match(result.stderr, /0096_netshop_domain_retirement\.sql/);
  assert.match(result.stderr, /0097_market_write_authority\.sql/);
  assert.match(result.stderr, /0098_market_domain_retirement\.sql/);
  assert.match(result.stderr, /0099_product_write_authority\.sql/);
  assert.match(result.stderr, /0100_product_domain_retirement\.sql/);
  assert.match(result.stderr, /0101_inventory_write_authority\.sql/);
  assert.match(result.stderr, /0102_inventory_domain_retirement\.sql/);
  assert.equal(result.stdout, "");
});
