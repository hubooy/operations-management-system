import assert from "node:assert/strict";
import test from "node:test";

import { buildNetshopImportBatchListQuery } from "../lib/netshop/import-batch-list-query";

test("netshop batch list uses bounded pagination and one JSON binding per filter list", () => {
  const narrowed = buildNetshopImportBatchListQuery({
    page: 3, pageSize: 1, ids: ["batch-1"], sources: ["jd_sku_daily"], platforms: ["京东"], shops: ["测试店"],
  });
  assert.equal(narrowed.whereSql, [
    "id IN (SELECT CAST(value AS TEXT) FROM json_each(?))",
    "source IN (SELECT CAST(value AS TEXT) FROM json_each(?))",
    "platform IN (SELECT CAST(value AS TEXT) FROM json_each(?))",
    "shop_name IN (SELECT CAST(value AS TEXT) FROM json_each(?))",
  ].join(" AND "));
  assert.deepEqual(narrowed.bindings, [
    JSON.stringify(["batch-1"]),
    JSON.stringify(["jd_sku_daily"]),
    JSON.stringify(["京东"]),
    JSON.stringify(["测试店"]),
  ]);
  assert.deepEqual(
    { page: narrowed.page, pageSize: narrowed.pageSize, offset: narrowed.offset },
    { page: 3, pageSize: 1, offset: 2 },
  );

  const compatible = buildNetshopImportBatchListQuery({ limit: 2, sources: ["jd_product_master"] });
  assert.doesNotMatch(compatible.whereSql, /id IN/);
  assert.deepEqual(compatible.bindings, [JSON.stringify(["jd_product_master"])]);
  assert.deepEqual(
    { page: compatible.page, pageSize: compatible.pageSize, offset: compatible.offset },
    { page: 1, pageSize: 2, offset: 0 },
  );
});

test("netshop batch-list domain pagination stays finite and bounded", () => {
  const query = buildNetshopImportBatchListQuery({ page: Number.POSITIVE_INFINITY, pageSize: 500 });
  assert.deepEqual(
    { page: query.page, pageSize: query.pageSize, offset: query.offset },
    { page: 1, pageSize: 100, offset: 0 },
  );
});
