import warehouseMappingData from "@/config/inventory-warehouse-mapping.json";

export type ClassifiedInventoryWarehouseType = "owned" | "jd_rdc" | "other";
export type InventoryWarehouseCategory =
  | "jd"
  | "dropship"
  | "afterSales"
  | "guangdong"
  | "sample"
  | "cainiao"
  | "overseas"
  | "virtual"
  | "exception"
  | "selfOperated";

export const inventoryWarehouseCategoryOrder: readonly InventoryWarehouseCategory[] = [
  "jd",
  "dropship",
  "afterSales",
  "guangdong",
  "sample",
  "cainiao",
  "overseas",
  "virtual",
  "exception",
  "selfOperated",
];

export const inventoryWarehouseCategoryLabels: Record<InventoryWarehouseCategory, string> = {
  jd: "京东仓",
  dropship: "代发仓",
  afterSales: "售后仓",
  guangdong: "广东仓",
  sample: "样品仓",
  cainiao: "菜鸟仓",
  overseas: "海外仓",
  virtual: "虚拟仓",
  exception: "异常仓",
  selfOperated: "自营仓",
};

export function inventoryWarehouseCategoryLabel(category: InventoryWarehouseCategory) {
  return inventoryWarehouseCategoryLabels[category];
}

export type InventoryWarehouseMappingEntry = {
  category: InventoryWarehouseCategory;
  label: string;
  includeInInventory: boolean;
};

export type InventoryWarehouseMapping = Record<string, InventoryWarehouseMappingEntry>;

const warehouseMapping = warehouseMappingData.warehouses as InventoryWarehouseMapping;

const JD_EXPLICIT_WAREHOUSE = /京东|rdc|dc仓|配送中心/i;
const JD_PLATFORM_WAREHOUSE = /(?:平台仓|中件(?:消费品)?)[^\r\n]*-chn$/i;

/**
 * Recognizes both explicit JD/RDC labels and the regional JD logistics names
 * exported by Jikexyun, for example "上海常温C平台仓9号库-CHN".
 */
export function isJdInboundWarehouseName(warehouse: string) {
  const normalized = warehouse.trim();
  return JD_EXPLICIT_WAREHOUSE.test(normalized) || JD_PLATFORM_WAREHOUSE.test(normalized);
}

export function inferInventoryWarehouseType(warehouse: string): ClassifiedInventoryWarehouseType {
  return classifyInventoryWarehouse(warehouse).warehouseType;
}

export function classifyInventoryWarehouse(warehouse: string, mapping: InventoryWarehouseMapping = warehouseMapping): {
  warehouseType: ClassifiedInventoryWarehouseType;
  warehouseCategory: InventoryWarehouseCategory;
  includeInInventory: boolean;
  mappingLabel: string;
  mappingSource: "configured" | "inferred";
} {
  const normalized = warehouse.trim();
  const configured = mapping[normalized];
  if (configured) {
    const warehouseType: ClassifiedInventoryWarehouseType = configured.category === "jd"
      ? "jd_rdc"
      : ["guangdong", "afterSales", "sample"].includes(configured.category)
        ? "owned"
        : "other";
    return {
      warehouseType,
      warehouseCategory: configured.category,
      includeInInventory: configured.includeInInventory,
      mappingLabel: configured.label,
      mappingSource: "configured",
    };
  }

  if (isJdInboundWarehouseName(normalized)) {
    return { warehouseType: "jd_rdc", warehouseCategory: "jd", includeInInventory: true, mappingLabel: "京东仓", mappingSource: "inferred" };
  }
  if (/代发/.test(normalized)) {
    return { warehouseType: "other", warehouseCategory: "dropship", includeInInventory: true, mappingLabel: "代发仓", mappingSource: "inferred" };
  }
  if (/菜鸟/.test(normalized)) {
    return { warehouseType: "other", warehouseCategory: "cainiao", includeInInventory: true, mappingLabel: "菜鸟仓", mappingSource: "inferred" };
  }
  if (/售后/.test(normalized)) {
    return { warehouseType: "owned", warehouseCategory: "afterSales", includeInInventory: true, mappingLabel: "售后仓", mappingSource: "inferred" };
  }
  if (/广东/.test(normalized)) {
    return { warehouseType: "owned", warehouseCategory: "guangdong", includeInInventory: true, mappingLabel: "广东仓", mappingSource: "inferred" };
  }
  if (/样品/.test(normalized)) {
    return { warehouseType: "owned", warehouseCategory: "sample", includeInInventory: true, mappingLabel: "样品仓", mappingSource: "inferred" };
  }
  return {
    warehouseType: /仓|库/.test(normalized) ? "owned" : "other",
    warehouseCategory: "selfOperated",
    includeInInventory: normalized !== "刷刷仓",
    mappingLabel: "自营仓",
    mappingSource: "inferred",
  };
}

/** SQL counterpart used to interpret old snapshots without rewriting facts. */
export function jdInboundWarehousePredicateSql(warehouseColumn: string, warehouseTypeColumn?: string) {
  const normalized = `LOWER(TRIM(${warehouseColumn}))`;
  const namePredicate = `(${normalized} LIKE '%京东%' OR ${normalized} LIKE '%rdc%' OR ${normalized} LIKE '%dc仓%' OR ${normalized} LIKE '%配送中心%' OR ((${normalized} LIKE '%平台仓%' OR ${normalized} LIKE '%中件%') AND ${normalized} LIKE '%-chn'))`;
  return warehouseTypeColumn
    ? `(LOWER(TRIM(${warehouseTypeColumn})) = 'jd_rdc' OR ${namePredicate})`
    : namePredicate;
}

export function resolvedWarehouseTypeSql(warehouseColumn: string, warehouseTypeColumn: string) {
  return `CASE
    WHEN ${jdInboundWarehousePredicateSql(warehouseColumn, warehouseTypeColumn)} THEN 'jd_rdc'
    WHEN LOWER(TRIM(${warehouseTypeColumn})) = 'owned' THEN 'owned'
    ELSE 'other'
  END`;
}

export function resolvedGroupedWarehouseTypeSql(warehouseColumn: string, warehouseTypeColumn: string) {
  return `CASE
    WHEN MAX(CASE WHEN ${jdInboundWarehousePredicateSql(warehouseColumn, warehouseTypeColumn)} THEN 1 ELSE 0 END) = 1 THEN 'jd_rdc'
    WHEN MAX(CASE WHEN LOWER(TRIM(${warehouseTypeColumn})) = 'owned' THEN 1 ELSE 0 END) = 1 THEN 'owned'
    ELSE 'other'
  END`;
}
