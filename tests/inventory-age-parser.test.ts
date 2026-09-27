import assert from "node:assert/strict";
import test from "node:test";
import { parseErpReferenceXlsx, type InventoryAgeImportRow } from "../lib/imports/erp-reference";
import { createXlsxWorkbookBytes } from "../lib/imports/xlsx-write";

test("吉客云库龄报表的库龄(天)表头会写入库龄天数", () => {
  const workbook = createXlsxWorkbookBytes([{
    name: "库龄分析",
    rows: [
      ["仓库", "货品编号", "货品名称", "库存数量", "库龄(天)", "固定成本价"],
      ["测试仓", "SKU-AGE-001", "测试货品", 8, 123, 25.5],
    ],
  }]);

  const parsed = parseErpReferenceXlsx("inventory_age", workbook);
  assert.deepEqual(parsed.errors, []);
  assert.equal(parsed.rows.length, 1);
  const row = parsed.rows[0] as InventoryAgeImportRow;
  assert.equal(row.inventoryAgeDays, 123);
  assert.equal(row.unitCostCents, 2550);
  assert.equal(row.stockValueCents, 20_400);
});
