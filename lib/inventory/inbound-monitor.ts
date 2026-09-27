import {
  findLatestInventoryImportBatch,
  type InventoryDatabase,
} from "@/lib/inventory/database";
import { normalizeInventoryPagination } from "@/lib/inventory/query-contract";
import { jdInboundWarehousePredicateSql } from "@/lib/inventory/warehouse-classification";
import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoSalesConsumerReader,
  type SalesConsumerReader,
  type SalesConsumerResponseMap,
} from "@/lib/django/sales-consumer-reader";
import { PublicApiError } from "@/lib/http/api-error";

export type InventoryInboundMonitorOptions = {
  page?: number;
  pageSize?: number;
  query?: string;
  warehouses?: string[];
  brands?: string[];
  categories?: string[];
  suppliers?: string[];
  signal?: AbortSignal;
};

type InboundRow = {
  product_code: string;
  product_name: string;
  brand: string;
  category: string;
  supplier: string;
  warehouse: string;
  available_quantity: number;
  in_transit_quantity: number;
  inventory_age_days: number | null;
  known_stock_value_cents: number;
  priced_quantity: number;
  sales_7d_quantity: number | null;
  sales_30d_quantity: number | null;
  sales_90d_quantity: number | null;
};

type MetricsRow = {
  item_count: number;
  warehouse_count: number;
  available_quantity: number;
  positive_available_quantity: number;
  in_transit_quantity: number;
  known_stock_value_cents: number;
  priced_quantity: number;
  matched_sales_count: number;
  sales_30d_quantity: number;
  stale_item_count: number;
  stale_value_cents: number;
  missing_supplier_count: number;
};

type RegionRow = {
  warehouse: string;
  item_count: number;
  available_quantity: number;
  in_transit_quantity: number;
  known_stock_value_cents: number;
  sales_30d_quantity: number;
  matched_sales_count: number;
};

function normalizedWarehouseSql(column: string) {
  return `REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(LOWER(TRIM(${column})), '配送中心', ''), '仓库', ''), '库房', ''), '仓', ''), ' ', ''), '（', ''), '）', ''), '(', ''), ')', ''), '-', '')`;
}

function normalizedWarehouseKey(value: string) {
  let normalized = value.trim().toLowerCase();
  for (const token of ["配送中心", "仓库", "库房", "仓", " ", "（", "）", "(", ")", "-"]) {
    normalized = normalized.split(token).join("");
  }
  return normalized;
}

function uniqueStrings(values: readonly string[] | undefined, max = 20) {
  return [...new Set((values ?? []).map((value) => value.trim()).filter(Boolean))].slice(0, max);
}

function buildFilter(options: InventoryInboundMonitorOptions) {
  const clauses: string[] = [];
  const values: string[] = [];
  const query = options.query?.trim().slice(0, 100);
  if (query) {
    const keywords = uniqueStrings(query.split(/[\s,，;；]+/), 8).map((value) => value.toLowerCase());
    if (keywords.length > 0) {
      clauses.push(`(${keywords.map(() => "(INSTR(LOWER(product_code), ?) > 0 OR INSTR(LOWER(product_name), ?) > 0 OR INSTR(LOWER(brand), ?) > 0 OR INSTR(LOWER(category), ?) > 0 OR INSTR(LOWER(supplier), ?) > 0 OR INSTR(LOWER(warehouse), ?) > 0)").join(" OR ")})`);
      for (const keyword of keywords) values.push(keyword, keyword, keyword, keyword, keyword, keyword);
    }
  }
  const warehouses = uniqueStrings(options.warehouses, 10);
  if (warehouses.length > 0) {
    clauses.push("warehouse IN (SELECT value FROM json_each(?))");
    values.push(JSON.stringify(warehouses));
  }
  const brands = uniqueStrings(options.brands, 20);
  if (brands.length > 0) {
    clauses.push("brand IN (SELECT value FROM json_each(?))");
    values.push(JSON.stringify(brands));
  }
  const categories = uniqueStrings(options.categories, 20);
  if (categories.length > 0) {
    clauses.push("category IN (SELECT value FROM json_each(?))");
    values.push(JSON.stringify(categories));
  }
  const suppliers = uniqueStrings(options.suppliers, 20);
  if (suppliers.length > 0) {
    clauses.push("supplier IN (SELECT value FROM json_each(?))");
    values.push(JSON.stringify(suppliers));
  }
  return { sql: clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "", values };
}

function buildInboundCte(input: {
  batchId: string;
  salesRows: SalesConsumerResponseMap["inventory_inbound_windows"]["rows"];
  hasSales: boolean;
}) {
  return {
    sql: `WITH stock AS (
      SELECT
        product_code,
        COALESCE(MAX(NULLIF(product_name, '')), product_code) AS product_name,
        COALESCE(MAX(NULLIF(brand, '')), '') AS brand,
        COALESCE(MAX(NULLIF(category, '')), '未分类') AS category,
        warehouse,
        ${normalizedWarehouseSql("warehouse")} AS warehouse_key,
        COALESCE(SUM(available_quantity), 0) AS available_quantity,
        COALESCE(SUM(in_transit_quantity), 0) AS in_transit_quantity,
        MAX(inventory_age_days) AS inventory_age_days,
        COALESCE(SUM(CASE WHEN unit_cost_cents >= 0 THEN MAX(available_quantity, 0) * unit_cost_cents ELSE 0 END), 0) AS known_stock_value_cents,
        COALESCE(SUM(CASE WHEN unit_cost_cents >= 0 THEN MAX(available_quantity, 0) ELSE 0 END), 0) AS priced_quantity
      FROM inventory_stock_lines
      WHERE batch_id = ? AND ${jdInboundWarehousePredicateSql("warehouse", "warehouse_type")} AND TRIM(warehouse) <> '刷刷仓'
      GROUP BY product_code, warehouse
    ), outbound AS (
      SELECT
        CAST(json_extract(value, '$.productCode') AS TEXT) AS product_code,
        CAST(json_extract(value, '$.warehouseKey') AS TEXT) AS warehouse_key,
        CAST(json_extract(value, '$.sales7dQuantity') AS REAL) AS sales_7d_quantity,
        CAST(json_extract(value, '$.sales30dQuantity') AS REAL) AS sales_30d_quantity,
        CAST(json_extract(value, '$.sales90dQuantity') AS REAL) AS sales_90d_quantity
      FROM json_each(?)
    ), base AS (
      SELECT
        st.product_code,
        st.product_name,
        st.brand,
        st.category,
        COALESCE(NULLIF(pm.supplier, ''), '未映射供应商') AS supplier,
        st.warehouse,
        st.available_quantity,
        st.in_transit_quantity,
        st.inventory_age_days,
        st.known_stock_value_cents,
        st.priced_quantity,
        CASE WHEN ? = 1 AND ob.product_code IS NOT NULL THEN COALESCE(ob.sales_7d_quantity, 0) ELSE NULL END AS sales_7d_quantity,
        CASE WHEN ? = 1 AND ob.product_code IS NOT NULL THEN COALESCE(ob.sales_30d_quantity, 0) ELSE NULL END AS sales_30d_quantity,
        CASE WHEN ? = 1 AND ob.product_code IS NOT NULL THEN COALESCE(ob.sales_90d_quantity, 0) ELSE NULL END AS sales_90d_quantity
      FROM stock st
      LEFT JOIN outbound ob ON ob.product_code = st.product_code AND ob.warehouse_key = st.warehouse_key
      LEFT JOIN erp_product_master pm ON pm.product_code = st.product_code
    )`,
    values: [
      input.batchId,
      JSON.stringify(input.salesRows),
      input.hasSales ? 1 : 0,
      input.hasSales ? 1 : 0,
      input.hasSales ? 1 : 0,
    ],
  };
}

function numberValue(value: unknown) {
  const number = Number(value ?? 0);
  return Number.isFinite(number) ? number : 0;
}

const MAX_INBOUND_PRODUCT_CODES = 5_000;

function inboundConsumerUnavailable() {
  return new PublicApiError(503, "service_unavailable", "Django 销售读取服务返回的数据不完整，请稍后重试。");
}

function isIsoDateOrNull(value: unknown): value is string | null {
  if (value === null) return true;
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  return new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
}

function validateInboundSalesResult(
  result: { revision: string; data: SalesConsumerResponseMap["inventory_inbound_windows"] },
  requestedProductCodes: Set<string>,
): void {
  if (!result || typeof result.revision !== "string" || !result.revision || result.revision.length > 128
    || !result.data || !Array.isArray(result.data.rows)) {
    throw inboundConsumerUnavailable();
  }
  const data = result.data;
  if (data.truncated !== false || !isIsoDateOrNull(data.asOfDate)
    || !isIsoDateOrNull(data.dataStartDate) || !isIsoDateOrNull(data.dataCutoffDate)) {
    throw inboundConsumerUnavailable();
  }
  const keys = new Set<string>();
  for (const row of data.rows) {
    if (!row || typeof row.productCode !== "string" || !requestedProductCodes.has(row.productCode)
      || typeof row.productName !== "string" || typeof row.warehouseKey !== "string"
      || row.warehouseKey !== normalizedWarehouseKey(row.warehouseKey)
      || !Number.isFinite(row.sales7dQuantity) || row.sales7dQuantity < 0
      || !Number.isFinite(row.sales30dQuantity) || row.sales30dQuantity < 0
      || !Number.isFinite(row.sales90dQuantity) || row.sales90dQuantity < 0) {
      throw inboundConsumerUnavailable();
    }
    const key = `${row.productCode}\u001f${row.warehouseKey}`;
    if (keys.has(key)) throw inboundConsumerUnavailable();
    keys.add(key);
  }
}

export async function getInventoryInboundMonitor(
  db: InventoryDatabase,
  principal: AppPrincipal,
  options: InventoryInboundMonitorOptions = {},
  salesReader: SalesConsumerReader = createDjangoSalesConsumerReader(),
) {
  const pagination = normalizeInventoryPagination(options);
  const latestBatch = await findLatestInventoryImportBatch(db);
  if (!latestBatch) {
    return {
      hasInventory: false,
      sync: { inventoryAsOf: null, salesThrough: null, latestInventoryBatchId: null, salesRevision: null },
      scope: { warehouseType: "jd_rdc" as const, valuationBasis: "fixed_cost" as const, supplyPriceAvailable: false, nativeComparisonAvailable: false },
      metrics: { itemCount: 0, warehouseCount: 0, availableQuantity: 0, inTransitQuantity: 0, knownStockValueCents: 0, costCoverageRate: 1, salesMatchRate: 0, outbound30dQuantity: 0, turnoverDays: null, staleItemCount: 0, staleValueCents: 0, missingSupplierCount: 0 },
      filters: { warehouses: [], brands: [], categories: [], suppliers: [] },
      pagination: { page: pagination.page, pageSize: pagination.pageSize, total: 0, returned: 0, totalPages: 0, truncated: false },
      regions: [],
      items: [],
      disclosures: ["当前没有库存快照。", "京东原生库存/周转指标尚未接入，暂不输出原生差异或残差结论。"],
    };
  }

  const productResult = await db.prepare(
    `SELECT product_code
     FROM inventory_stock_lines
     WHERE batch_id = ? AND ${jdInboundWarehousePredicateSql("warehouse", "warehouse_type")}
       AND TRIM(warehouse) <> '刷刷仓' AND TRIM(product_code) <> ''
     GROUP BY product_code
     ORDER BY product_code
     LIMIT ?`,
  ).bind(latestBatch.id, MAX_INBOUND_PRODUCT_CODES + 1).all<{ product_code: string }>();
  if (productResult.results.length > MAX_INBOUND_PRODUCT_CODES) throw inboundConsumerUnavailable();
  const productCodes = productResult.results.map((row) => row.product_code);
  const salesResult = productCodes.length > 0
    ? await salesReader.read(principal, {
      operation: "inventory_inbound_windows",
      asOfDate: null,
      productCodes,
      limit: 10_000,
    }, { signal: options.signal })
    : null;
  if (salesResult) validateInboundSalesResult(salesResult, new Set(productCodes));
  const salesData = salesResult?.data as SalesConsumerResponseMap["inventory_inbound_windows"] | undefined;
  const cte = buildInboundCte({
    batchId: latestBatch.id,
    salesRows: salesData?.rows ?? [],
    hasSales: salesData?.asOfDate !== null && salesData?.asOfDate !== undefined,
  });
  const filter = buildFilter(options);
  const metricsSql = `SELECT
    COUNT(*) AS item_count,
    COUNT(DISTINCT warehouse) AS warehouse_count,
    COALESCE(SUM(available_quantity), 0) AS available_quantity,
    COALESCE(SUM(MAX(available_quantity, 0)), 0) AS positive_available_quantity,
    COALESCE(SUM(in_transit_quantity), 0) AS in_transit_quantity,
    COALESCE(SUM(known_stock_value_cents), 0) AS known_stock_value_cents,
    COALESCE(SUM(priced_quantity), 0) AS priced_quantity,
    COALESCE(SUM(CASE WHEN sales_30d_quantity IS NOT NULL THEN 1 ELSE 0 END), 0) AS matched_sales_count,
    COALESCE(SUM(CASE WHEN sales_30d_quantity > 0 THEN sales_30d_quantity ELSE 0 END), 0) AS sales_30d_quantity,
    COALESCE(SUM(CASE WHEN available_quantity > 0 AND (sales_90d_quantity = 0 OR inventory_age_days >= 90) THEN 1 ELSE 0 END), 0) AS stale_item_count,
    COALESCE(SUM(CASE WHEN available_quantity > 0 AND (sales_90d_quantity = 0 OR inventory_age_days >= 90) THEN known_stock_value_cents ELSE 0 END), 0) AS stale_value_cents,
    COALESCE(SUM(CASE WHEN supplier = '未映射供应商' THEN 1 ELSE 0 END), 0) AS missing_supplier_count
    FROM filtered`;
  const [metricsRow, pageRows, regionRows, facetRows] = await Promise.all([
    db.prepare(`${cte.sql}, filtered AS (SELECT * FROM base ${filter.sql}) ${metricsSql}`)
      .bind(...cte.values, ...filter.values).first<MetricsRow>(),
    db.prepare(`${cte.sql}, filtered AS (SELECT * FROM base ${filter.sql})
      SELECT * FROM filtered
      ORDER BY CASE WHEN available_quantity > 0 AND (sales_90d_quantity = 0 OR inventory_age_days >= 90) THEN 0 ELSE 1 END,
        known_stock_value_cents DESC, product_code ASC, warehouse ASC
      LIMIT ? OFFSET ?`).bind(...cte.values, ...filter.values, pagination.pageSize, pagination.offset).all<InboundRow>(),
    db.prepare(`${cte.sql}, filtered AS (SELECT * FROM base ${filter.sql})
      SELECT warehouse, COUNT(*) AS item_count,
        COALESCE(SUM(available_quantity), 0) AS available_quantity,
        COALESCE(SUM(in_transit_quantity), 0) AS in_transit_quantity,
        COALESCE(SUM(known_stock_value_cents), 0) AS known_stock_value_cents,
        COALESCE(SUM(CASE WHEN sales_30d_quantity > 0 THEN sales_30d_quantity ELSE 0 END), 0) AS sales_30d_quantity,
        COALESCE(SUM(CASE WHEN sales_30d_quantity IS NOT NULL THEN 1 ELSE 0 END), 0) AS matched_sales_count
      FROM filtered GROUP BY warehouse ORDER BY known_stock_value_cents DESC, warehouse ASC LIMIT 100`)
      .bind(...cte.values, ...filter.values).all<RegionRow>(),
    db.prepare(`${cte.sql}
      SELECT 'warehouse' AS kind, warehouse AS value FROM base GROUP BY warehouse
      UNION ALL
      SELECT 'brand' AS kind, brand AS value FROM base WHERE TRIM(brand) <> '' GROUP BY brand
      UNION ALL
      SELECT 'category' AS kind, category AS value FROM base GROUP BY category
      UNION ALL
      SELECT 'supplier' AS kind, supplier AS value FROM base GROUP BY supplier
      ORDER BY kind, value
      LIMIT 1500`)
      .bind(...cte.values).all<{ kind: "warehouse" | "brand" | "category" | "supplier"; value: string }>(),
  ]);

  const metrics = metricsRow ?? {} as MetricsRow;
  const itemCount = numberValue(metrics.item_count);
  const availableQuantity = numberValue(metrics.available_quantity);
  const positiveAvailableQuantity = numberValue(metrics.positive_available_quantity);
  const pricedQuantity = numberValue(metrics.priced_quantity);
  const outbound30dQuantity = numberValue(metrics.sales_30d_quantity);
  const total = itemCount;
  return {
    hasInventory: true,
    sync: {
      inventoryAsOf: latestBatch.snapshotDate,
      salesThrough: salesData?.asOfDate ?? null,
      latestInventoryBatchId: latestBatch.id,
      salesRevision: salesResult?.revision ?? null,
    },
    scope: { warehouseType: "jd_rdc" as const, valuationBasis: "fixed_cost" as const, supplyPriceAvailable: false, nativeComparisonAvailable: false },
    metrics: {
      itemCount,
      warehouseCount: numberValue(metrics.warehouse_count),
      availableQuantity,
      inTransitQuantity: numberValue(metrics.in_transit_quantity),
      knownStockValueCents: numberValue(metrics.known_stock_value_cents),
      costCoverageRate: positiveAvailableQuantity > 0 ? Math.min(1, pricedQuantity / positiveAvailableQuantity) : 1,
      salesMatchRate: itemCount > 0 ? numberValue(metrics.matched_sales_count) / itemCount : 0,
      outbound30dQuantity,
      turnoverDays: outbound30dQuantity > 0 ? Math.max(0, availableQuantity) / (outbound30dQuantity / 30) : null,
      staleItemCount: numberValue(metrics.stale_item_count),
      staleValueCents: numberValue(metrics.stale_value_cents),
      missingSupplierCount: numberValue(metrics.missing_supplier_count),
    },
    filters: {
      warehouses: facetRows.results.filter((row) => row.kind === "warehouse").map((row) => row.value),
      brands: facetRows.results.filter((row) => row.kind === "brand").map((row) => row.value),
      categories: facetRows.results.filter((row) => row.kind === "category").map((row) => row.value),
      suppliers: facetRows.results.filter((row) => row.kind === "supplier").map((row) => row.value),
    },
    pagination: { page: pagination.page, pageSize: pagination.pageSize, total, returned: pageRows.results.length, totalPages: total === 0 ? 0 : Math.ceil(total / pagination.pageSize), truncated: pagination.offset + pageRows.results.length < total },
    regions: regionRows.results.map((row) => {
      const sales30 = numberValue(row.sales_30d_quantity);
      const quantity = numberValue(row.available_quantity);
      return {
        warehouse: row.warehouse,
        itemCount: numberValue(row.item_count),
        availableQuantity: quantity,
        inTransitQuantity: numberValue(row.in_transit_quantity),
        knownStockValueCents: numberValue(row.known_stock_value_cents),
        outbound30dQuantity: sales30,
        turnoverDays: sales30 > 0 ? Math.max(0, quantity) / (sales30 / 30) : null,
        salesMatchRate: numberValue(row.item_count) > 0 ? numberValue(row.matched_sales_count) / numberValue(row.item_count) : 0,
      };
    }),
    items: pageRows.results.map((row) => {
      const available = numberValue(row.available_quantity);
      const sales30 = row.sales_30d_quantity === null ? null : numberValue(row.sales_30d_quantity);
      const priced = numberValue(row.priced_quantity);
      return {
        key: `${row.warehouse}\u001f${row.product_code}`,
        productCode: row.product_code,
        productName: row.product_name || row.product_code,
        brand: row.brand || "",
        category: row.category || "未分类",
        supplier: row.supplier,
        warehouse: row.warehouse,
        availableQuantity: available,
        inTransitQuantity: numberValue(row.in_transit_quantity),
        inventoryAgeDays: row.inventory_age_days === null ? null : numberValue(row.inventory_age_days),
        knownStockValueCents: numberValue(row.known_stock_value_cents),
        costCoverageRate: available > 0 ? Math.min(1, priced / Math.max(0, available)) : 1,
        unitCostCents: priced > 0 ? numberValue(row.known_stock_value_cents) / priced : null,
        outbound7dQuantity: row.sales_7d_quantity === null ? null : numberValue(row.sales_7d_quantity),
        outbound30dQuantity: sales30,
        outbound90dQuantity: row.sales_90d_quantity === null ? null : numberValue(row.sales_90d_quantity),
        turnoverDays: sales30 !== null && sales30 > 0 ? Math.max(0, available) / (sales30 / 30) : null,
        risk: available <= 0
          ? "no_stock" as const
          : numberValue(row.inventory_age_days) >= 90
            || (row.sales_90d_quantity !== null && numberValue(row.sales_90d_quantity) <= 0)
            ? "stale" as const
            : row.sales_90d_quantity === null
              ? "unknown" as const
              : "normal" as const,
      };
    }),
    disclosures: [
      "仅统计京东 RDC/DC 与可识别的京东区域平台仓；历史快照按受控仓名规则兼容识别，不改写原始事实。",
      "库存货值按系统固定成本计算；供应价/结算价尚未接入，不能替代京东结算口径。",
      "京东原生库存与原生周转指标尚未接入，因此暂不输出原生差异、残差 SKU 或一致性通过结论。",
      "7/30/90 日出库采用销售明细正向销量，退款不计为出库；刷刷仓与补差价专用行已排除。",
    ],
  };
}
