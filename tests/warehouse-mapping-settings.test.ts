import assert from "node:assert/strict";
import test from "node:test";

import {
  filterWarehouseMappings,
  warehouseMappingRows,
} from "../app/warehouse-mapping-settings";
import {
  classifyInventoryWarehouse,
  inventoryWarehouseCategoryLabels,
  inventoryWarehouseCategoryOrder,
} from "../lib/inventory/warehouse-classification";
import {
  buildWarehouseMappingWorkbook,
  parseWarehouseMappingWorkbook,
  WarehouseMappingWorkbookError,
} from "../lib/inventory/warehouse-mapping-workbook";
import { parseWarehouseMappingPayload } from "../lib/inventory/warehouse-mapping-contract";

test("warehouse mapping workspace exposes the complete verified source", () => {
  assert.equal(warehouseMappingRows.length, 284);
  assert.equal(warehouseMappingRows.filter((row) => row.includeInInventory).length, 66);
  assert.equal(warehouseMappingRows.filter((row) => !row.includeInInventory).length, 218);
  assert.deepEqual(
    warehouseMappingRows.find((row) => row.warehouse === "膳师傅仓库"),
    {
      warehouse: "膳师傅仓库",
      category: "dropship",
      label: "代发仓",
      includeInInventory: true,
      pendingConfirmation: false,
    },
  );
});

test("warehouse mapping filters combine category, inventory scope, and search", () => {
  const includedDropship = filterWarehouseMappings(warehouseMappingRows, {
    query: "膳师傅",
    category: "dropship",
    inventory: "included",
    confirmation: "all",
  });
  assert.deepEqual(includedDropship.map((row) => row.warehouse), ["膳师傅仓库"]);

  const excludedJd = filterWarehouseMappings(warehouseMappingRows, {
    query: "",
    category: "jd",
    inventory: "excluded",
    confirmation: "all",
  });
  assert.equal(excludedJd.length, 0);

  const labelSearch = filterWarehouseMappings(warehouseMappingRows, {
    query: "异常仓",
    category: "all",
    inventory: "all",
    confirmation: "all",
  });
  assert.equal(labelSearch.length, 1);
  assert.equal(labelSearch[0]?.warehouse, "自营异常仓（不要审核发货）");
});

test("newly discovered warehouses can be isolated for manual confirmation", () => {
  const pending = {
    ...warehouseMappingRows[0]!,
    warehouse: "首次发现测试仓",
    category: "selfOperated" as const,
    label: "自营仓",
    pendingConfirmation: true,
  };
  const filtered = filterWarehouseMappings([...warehouseMappingRows, pending], {
    query: "",
    category: "all",
    inventory: "all",
    confirmation: "pending",
  });
  assert.deepEqual(filtered, [pending]);

  const parsed = parseWarehouseMappingPayload({
    rows: [pending],
    mappingRevision: "a".repeat(64),
    pendingConfirmationCount: 1,
    updatedAt: null,
    updatedBy: null,
  });
  assert.equal(parsed.pendingConfirmationCount, 1);
  assert.equal(parsed.rows[0]?.category, "selfOperated");
});

test("inventory views and settings share the same warehouse category contract", () => {
  assert.deepEqual(inventoryWarehouseCategoryOrder.slice(0, 9), [
    "jd", "dropship", "afterSales", "guangdong", "sample", "cainiao", "overseas", "virtual", "exception",
  ]);
  assert.equal(inventoryWarehouseCategoryLabels.overseas, "海外仓");
  assert.deepEqual(classifyInventoryWarehouse("膳师傅仓库"), {
    warehouseType: "other",
    warehouseCategory: "dropship",
    includeInInventory: true,
    mappingLabel: "代发仓",
    mappingSource: "configured",
  });
});

test("warehouse mapping workbook export round-trips through the guarded importer", () => {
  const bytes = buildWarehouseMappingWorkbook(warehouseMappingRows);
  const parsed = parseWarehouseMappingWorkbook(bytes);
  assert.equal(parsed.length, 284);
  assert.deepEqual(parsed.find((row) => row.warehouse === "一个小太阳仓"), {
    warehouse: "一个小太阳仓",
    category: "dropship",
    includeInInventory: false,
  });
  assert.deepEqual(parsed.find((row) => row.warehouse === "膳师傅仓库"), {
    warehouse: "膳师傅仓库",
    category: "dropship",
    includeInInventory: true,
  });
});

test("warehouse mapping workbook importer rejects duplicate warehouses", async () => {
  const { utils, write } = await import("xlsx");
  const sheet = utils.aoa_to_sheet([
    ["仓库", "自定义仓库类型", "计入库存", "备注"],
    ["重复仓", "代发仓", "是", ""],
    ["重复仓", "京东仓", "否", ""],
  ]);
  const workbook = utils.book_new();
  utils.book_append_sheet(workbook, sheet, "mapping");
  const bytes = write(workbook, { type: "array", bookType: "xlsx" }) as Uint8Array;
  assert.throws(() => parseWarehouseMappingWorkbook(bytes), WarehouseMappingWorkbookError);
});
