import {
  findLatestInventoryImportBatch,
  type InventoryDatabase,
} from "@/lib/inventory/database";
import {
  classifyInventoryAge,
  inventoryAgeBuckets,
  normalizeInventoryPagination,
  type InventoryAgeBucketKey,
  type InventoryAgeStatus,
} from "@/lib/inventory/query-contract";
import { findLatestCompletedErpReferenceBatch } from "@/lib/erp-reference/database";
import {
  classifyInventoryWarehouse,
  resolvedGroupedWarehouseTypeSql,
  resolvedWarehouseTypeSql,
} from "@/lib/inventory/warehouse-classification";

export type { InventoryAgeStatus } from "@/lib/inventory/query-contract";

export type InventoryAgeItem = {
  key: string;
  productCode: string;
  productName: string;
  brand: string;
  specification: string;
  category: string;
  warehouse: string;
  warehouseType: "owned" | "jd_rdc" | "other";
  warehouseCategory: ReturnType<typeof classifyInventoryWarehouse>["warehouseCategory"];
  warehouseLabel: string;
  includedInInventory: boolean;
  availableQuantity: number;
  stockValueCents: number | null;
  inventoryAgeDays: number | null;
  ageBucketKey: InventoryAgeBucketKey | null;
  ageBucketLabel: string;
  sales7dQuantity: number | null;
  sales30dQuantity: number | null;
  status: InventoryAgeStatus;
  statusLabel: string;
  recommendation: string;
};

export type InventoryAgeAnalysisOptions = {
  page?: number;
  pageSize?: number;
  query?: string;
  warehouses?: string[];
  brands?: string[];
  categories?: string[];
  statuses?: InventoryAgeStatus[];
  ageBuckets?: InventoryAgeBucketKey[];
  exactKey?: string;
};

type AgeRow = {
  product_code: string;
  product_name: string | null;
  brand: string | null;
  specification: string | null;
  category: string | null;
  warehouse: string;
  warehouse_type: string;
  available_quantity: number | null;
  stock_value_cents: number | null;
  inventory_age_days: number | null;
  sales_7d_quantity: number | null;
  sales_30d_quantity: number | null;
  status: InventoryAgeStatus;
};

type MetricsRow = {
  total: number;
  stock_value_complete_count: number;
  aged_90_count: number;
  aged_90_value_cents: number;
  stagnant_count: number;
  stagnant_value_cents: number;
  zero_sales_count: number;
  cleanup_count: number;
  aged_quantity: number;
  aged_value_cents: number;
  unaged_stock_count: number;
  unaged_quantity: number;
} & Record<string, number>;

type AgePageRow = AgeRow & MetricsRow;

type AgeFacetRow = {
  kind: "warehouse" | "brand" | "category";
  value: string;
};

function normalizeWarehouseType(value: string): InventoryAgeItem["warehouseType"] {
  return value === "owned" || value === "jd_rdc" ? value : "other";
}

function hasAgeSales(totals: unknown) {
  if (!totals || typeof totals !== "object") return false;
  const coverage = (totals as { coverage?: { hasSales30dQuantity?: unknown } }).coverage;
  return coverage?.hasSales30dQuantity === true;
}

function statusCopy(status: InventoryAgeStatus) {
  if (status === "no_stock") return { label: "无可用库存", recommendation: "无需纳入滞销清理，等待下一次库存快照确认。" };
  if (status === "stagnant") return { label: "滞销清理", recommendation: "停止补货，优先评估促销、渠道调拨或清退。" };
  if (status === "slow") return { label: "低动销", recommendation: "控制补货，结合价格和渠道方案提升动销。" };
  if (status === "aged") return { label: "高库龄", recommendation: "库龄超过 90 天，建议核查动销并制定处理计划。" };
  return { label: "库龄健康", recommendation: "持续观察库存周转与近 30 日销量。" };
}

function uniqueStrings(values: readonly string[] | undefined, max = 20) {
  return [...new Set((values ?? []).map((value) => value.trim()).filter(Boolean))].slice(0, max);
}

function filterClause(options: InventoryAgeAnalysisOptions) {
  const clauses: string[] = [];
  const values: string[] = [];
  const query = options.query?.trim().slice(0, 100);
  if (query) {
    const keywords = uniqueStrings(query.split(/[\s,，;；]+/), 8).map((value) => value.toLowerCase());
    if (keywords.length > 0) {
      clauses.push(`(${keywords.map(() => "(INSTR(LOWER(product_code), ?) > 0 OR INSTR(LOWER(product_name), ?) > 0 OR INSTR(LOWER(brand), ?) > 0 OR INSTR(LOWER(specification), ?) > 0 OR INSTR(LOWER(category), ?) > 0 OR INSTR(LOWER(warehouse), ?) > 0)").join(" OR ")})`);
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
  const statuses = uniqueStrings(options.statuses, 5)
    .filter((value) => ["healthy", "aged", "slow", "stagnant", "no_stock"].includes(value));
  if (statuses.length > 0) {
    clauses.push("status IN (SELECT value FROM json_each(?))");
    values.push(JSON.stringify(statuses));
  }
  const ageBucketKeys = new Set(inventoryAgeBuckets.map((bucket) => bucket.key));
  const selectedAgeBuckets = uniqueStrings(options.ageBuckets, inventoryAgeBuckets.length)
    .filter((value): value is InventoryAgeBucketKey => ageBucketKeys.has(value as InventoryAgeBucketKey));
  if (selectedAgeBuckets.length > 0) {
    const selected = inventoryAgeBuckets.filter((bucket) => selectedAgeBuckets.includes(bucket.key));
    clauses.push(`(${selected.map(bucketCondition).join(" OR ")})`);
  }
  if (options.exactKey) {
    clauses.push("warehouse || char(31) || product_code = ?");
    values.push(options.exactKey);
  }
  return { sql: clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "", values };
}

function ageCte(sourceSql: string) {
  return `WITH base AS (${sourceSql}), classified AS (
    SELECT *, CASE
      WHEN available_quantity <= 0 THEN 'no_stock'
      WHEN inventory_age_days IS NULL THEN 'healthy'
      WHEN sales_30d_quantity IS NOT NULL AND inventory_age_days >= 90 AND sales_30d_quantity <= 0 THEN 'stagnant'
      WHEN sales_30d_quantity IS NOT NULL AND inventory_age_days >= 60 AND sales_30d_quantity <= 3 THEN 'slow'
      WHEN inventory_age_days >= 90 THEN 'aged'
      ELSE 'healthy'
    END AS status
    FROM base
  )`;
}

function bucketCondition(bucket: (typeof inventoryAgeBuckets)[number]) {
  return bucket.maxDays === null
    ? `inventory_age_days >= ${bucket.minDays}`
    : `inventory_age_days BETWEEN ${bucket.minDays} AND ${bucket.maxDays}`;
}

function bucketAlias(key: InventoryAgeBucketKey) {
  return key.replace("-", "_").replace("+", "_plus");
}

function metricsSelectSql(windowed = false) {
  const over = windowed ? " OVER ()" : "";
  const bucketMetrics = inventoryAgeBuckets.flatMap((bucket) => {
    const condition = `${bucketCondition(bucket)} AND available_quantity > 0`;
    const alias = bucketAlias(bucket.key);
    return [
      `COALESCE(SUM(CASE WHEN ${condition} THEN 1 ELSE 0 END)${over}, 0) AS bucket_${alias}_count`,
      `COALESCE(SUM(CASE WHEN ${condition} THEN available_quantity ELSE 0 END)${over}, 0) AS bucket_${alias}_quantity`,
      `COALESCE(SUM(CASE WHEN ${condition} THEN stock_value_cents ELSE 0 END)${over}, 0) AS bucket_${alias}_value`,
    ];
  }).join(",\n    ");
  return `
    COUNT(*)${over} AS total,
    COALESCE(SUM(CASE WHEN stock_value_cents IS NOT NULL THEN 1 ELSE 0 END)${over}, 0) AS stock_value_complete_count,
    COALESCE(SUM(CASE WHEN inventory_age_days >= 90 AND available_quantity > 0 THEN 1 ELSE 0 END)${over}, 0) AS aged_90_count,
    COALESCE(SUM(CASE WHEN inventory_age_days >= 90 AND available_quantity > 0 THEN stock_value_cents ELSE 0 END)${over}, 0) AS aged_90_value_cents,
    COALESCE(SUM(CASE WHEN status = 'stagnant' THEN 1 ELSE 0 END)${over}, 0) AS stagnant_count,
    COALESCE(SUM(CASE WHEN status = 'stagnant' THEN stock_value_cents ELSE 0 END)${over}, 0) AS stagnant_value_cents,
    COALESCE(SUM(CASE WHEN sales_30d_quantity IS NOT NULL AND sales_30d_quantity <= 0 AND available_quantity > 0 THEN 1 ELSE 0 END)${over}, 0) AS zero_sales_count,
    COALESCE(SUM(CASE WHEN status IN ('stagnant', 'slow', 'aged') THEN 1 ELSE 0 END)${over}, 0) AS cleanup_count,
    COALESCE(SUM(CASE WHEN inventory_age_days IS NOT NULL AND inventory_age_days >= 0 AND available_quantity > 0 THEN available_quantity ELSE 0 END)${over}, 0) AS aged_quantity,
    COALESCE(SUM(CASE WHEN inventory_age_days IS NOT NULL AND inventory_age_days >= 0 AND available_quantity > 0 THEN stock_value_cents ELSE 0 END)${over}, 0) AS aged_value_cents,
    COALESCE(SUM(CASE WHEN inventory_age_days IS NULL AND available_quantity > 0 THEN 1 ELSE 0 END)${over}, 0) AS unaged_stock_count,
    COALESCE(SUM(CASE WHEN inventory_age_days IS NULL AND available_quantity > 0 THEN available_quantity ELSE 0 END)${over}, 0) AS unaged_quantity,
    COALESCE(SUM(CASE WHEN inventory_age_days BETWEEN 0 AND 30 THEN 1 ELSE 0 END)${over}, 0) AS bucket_0_30_count,
    COALESCE(SUM(CASE WHEN inventory_age_days BETWEEN 0 AND 30 THEN stock_value_cents ELSE 0 END)${over}, 0) AS bucket_0_30_value,
    COALESCE(SUM(CASE WHEN inventory_age_days BETWEEN 31 AND 60 THEN 1 ELSE 0 END)${over}, 0) AS bucket_31_60_count,
    COALESCE(SUM(CASE WHEN inventory_age_days BETWEEN 31 AND 60 THEN stock_value_cents ELSE 0 END)${over}, 0) AS bucket_31_60_value,
    COALESCE(SUM(CASE WHEN inventory_age_days BETWEEN 61 AND 89 THEN 1 ELSE 0 END)${over}, 0) AS bucket_61_89_count,
    COALESCE(SUM(CASE WHEN inventory_age_days BETWEEN 61 AND 89 THEN stock_value_cents ELSE 0 END)${over}, 0) AS bucket_61_89_value,
    COALESCE(SUM(CASE WHEN inventory_age_days >= 90 THEN 1 ELSE 0 END)${over}, 0) AS bucket_90_count,
    COALESCE(SUM(CASE WHEN inventory_age_days >= 90 THEN stock_value_cents ELSE 0 END)${over}, 0) AS bucket_90_value,
    ${bucketMetrics}`;
}

function mapItem(row: AgeRow): InventoryAgeItem {
  const availableQuantity = Number(row.available_quantity ?? 0);
  const inventoryAgeDays = row.inventory_age_days === null ? null : Number(row.inventory_age_days);
  const sales30dQuantity = row.sales_30d_quantity === null ? null : Number(row.sales_30d_quantity);
  const classified = classifyInventoryAge({ availableQuantity, inventoryAgeDays, sales30dQuantity });
  const copy = statusCopy(classified.status);
  const warehouseClassification = classifyInventoryWarehouse(row.warehouse);
  const ageBucket = inventoryAgeDays === null
    ? null
    : inventoryAgeBuckets.find((bucket) => inventoryAgeDays >= bucket.minDays && (bucket.maxDays === null || inventoryAgeDays <= bucket.maxDays)) ?? null;
  return {
    key: `${row.warehouse}\u001f${row.product_code}`,
    productCode: row.product_code,
    productName: row.product_name || row.product_code,
    brand: row.brand || "",
    specification: row.specification || "",
    category: row.category || "未分类",
    warehouse: row.warehouse,
    warehouseType: warehouseClassification.mappingSource === "inferred" && warehouseClassification.warehouseCategory === "selfOperated"
      ? normalizeWarehouseType(row.warehouse_type)
      : warehouseClassification.warehouseType,
    warehouseCategory: warehouseClassification.warehouseCategory,
    warehouseLabel: warehouseClassification.mappingLabel,
    includedInInventory: warehouseClassification.includeInInventory,
    availableQuantity,
    stockValueCents: row.stock_value_cents === null ? null : Number(row.stock_value_cents),
    inventoryAgeDays,
    ageBucketKey: ageBucket?.key ?? null,
    ageBucketLabel: ageBucket?.label ?? "库龄未识别",
    sales7dQuantity: row.sales_7d_quantity === null ? null : Number(row.sales_7d_quantity),
    sales30dQuantity,
    status: classified.status,
    statusLabel: copy.label,
    recommendation: inventoryAgeDays === null && classified.status === "healthy"
      ? "当前报表未提供库龄，暂不参与库龄预警。"
      : copy.recommendation,
  };
}

export async function getInventoryAgeAnalysis(db: InventoryDatabase, options: InventoryAgeAnalysisOptions = {}) {
  const pagination = normalizeInventoryPagination(options);
  const [latestBatch, latestAgeBatch] = await Promise.all([
    findLatestInventoryImportBatch(db),
    findLatestCompletedErpReferenceBatch(db, "inventory_age"),
  ]);
  const hasCompletedAgeSnapshot = Boolean(latestAgeBatch?.snapshotDate);
  if (!latestBatch && !hasCompletedAgeSnapshot) {
    return {
      hasInventory: false,
      sync: { inventoryAsOf: null, latestInventoryBatchId: null, hasAgeSales: false },
      metrics: { skuWarehouseCount: 0, stockValueComplete: true, aged90Count: 0, aged90ValueCents: 0, stagnantCount: 0, stagnantValueCents: 0, zeroSalesCount: 0, cleanupCount: 0 },
      coverage: { unagedStockCount: 0, unagedQuantity: 0 },
      distribution: [] as Array<{ key: string; label: string; count: number; valueCents: number }>,
      fineDistribution: [] as Array<{ key: string; label: string; count: number; quantity: number; valueCents: number; quantityShare: number; valueShare: number }>,
      filters: { warehouses: [], brands: [], categories: [], statuses: ["healthy", "aged", "slow", "stagnant", "no_stock"], ageBuckets: inventoryAgeBuckets.map(({ key, label }) => ({ value: key, label })) },
      pagination: { page: pagination.page, pageSize: pagination.pageSize, limit: pagination.pageSize, total: 0, returned: 0, totalPages: 0, truncated: false },
      items: [] as InventoryAgeItem[],
    };
  }

  let sourceSql: string;
  let sourceValues: Array<string | number>;
  let ageSalesAvailable: boolean;
  if (latestAgeBatch?.snapshotDate) {
    sourceSql = `SELECT
      age.product_code,
      NULLIF(age.product_name, '') AS product_name,
      COALESCE(NULLIF(master.brand, ''), '') AS brand,
      NULLIF(age.specification, '') AS specification,
      COALESCE(NULLIF(age.category, ''), '未分类') AS category,
      age.warehouse,
      ${resolvedWarehouseTypeSql("age.warehouse", "age.warehouse_type")} AS warehouse_type,
      age.available_quantity,
      CASE
        WHEN age.available_quantity > 0 AND age.unit_cost_cents < 0 THEN NULL
        ELSE MAX(age.available_quantity, 0) * age.unit_cost_cents
      END AS stock_value_cents,
      age.inventory_age_days,
      age.sales_7d_quantity,
      age.sales_30d_quantity
    FROM erp_inventory_age_lines age
    LEFT JOIN erp_product_master master ON master.product_code = age.product_code
    WHERE age.snapshot_date = ? AND age.last_import_batch_id = ? AND TRIM(age.warehouse) <> '刷刷仓'`;
    sourceValues = [latestAgeBatch.snapshotDate, latestAgeBatch.id];
    const coverage = await db.prepare(
      `SELECT COUNT(*) AS count FROM erp_inventory_age_lines
       WHERE snapshot_date = ? AND last_import_batch_id = ? AND sales_30d_quantity IS NOT NULL AND TRIM(warehouse) <> '刷刷仓'`,
    ).bind(...sourceValues).first<{ count: number }>();
    ageSalesAvailable = Number(coverage?.count ?? 0) > 0;
  } else {
    ageSalesAvailable = hasAgeSales(latestBatch!.totals);
    sourceSql = `SELECT
      s.product_code,
      MAX(NULLIF(s.product_name, '')) AS product_name,
      COALESCE(MAX(NULLIF(s.brand, '')), '') AS brand,
      MAX(NULLIF(s.specification, '')) AS specification,
      COALESCE(MAX(NULLIF(s.category, '')), '未分类') AS category,
      s.warehouse,
      ${resolvedGroupedWarehouseTypeSql("s.warehouse", "s.warehouse_type")} AS warehouse_type,
      COALESCE(SUM(s.available_quantity), 0) AS available_quantity,
      CASE
        WHEN SUM(MAX(s.available_quantity, 0)) > SUM(CASE WHEN s.unit_cost_cents >= 0 THEN MAX(s.available_quantity, 0) ELSE 0 END) THEN NULL
        ELSE COALESCE(SUM(CASE WHEN s.unit_cost_cents >= 0 THEN MAX(s.available_quantity, 0) * s.unit_cost_cents ELSE 0 END), 0)
      END AS stock_value_cents,
      MAX(s.inventory_age_days) AS inventory_age_days,
      CASE WHEN ? = 1 THEN SUM(a.sales_7d_quantity) ELSE NULL END AS sales_7d_quantity,
      CASE WHEN ? = 1 THEN SUM(a.sales_30d_quantity) ELSE NULL END AS sales_30d_quantity
    FROM inventory_stock_lines s
    LEFT JOIN inventory_age_metrics a ON a.batch_id = s.batch_id AND a.row_key = s.row_key
    WHERE s.batch_id = ? AND TRIM(s.warehouse) <> '刷刷仓'
    GROUP BY s.product_code, s.warehouse`;
    sourceValues = [ageSalesAvailable ? 1 : 0, ageSalesAvailable ? 1 : 0, latestBatch!.id];
  }

  const cte = ageCte(sourceSql);
  const filter = filterClause(options);
  const [pageResult, facetsResult] = await Promise.all([
    db.prepare(`${cte}, filtered AS (SELECT * FROM classified ${filter.sql})
      SELECT *,${metricsSelectSql(true)}
      FROM filtered
      ORDER BY CASE status WHEN 'stagnant' THEN 0 WHEN 'slow' THEN 1 WHEN 'aged' THEN 2 WHEN 'healthy' THEN 3 ELSE 4 END,
        inventory_age_days DESC, stock_value_cents DESC, product_code ASC, warehouse ASC
      LIMIT ? OFFSET ?`).bind(...sourceValues, ...filter.values, pagination.pageSize, pagination.offset).all<AgePageRow>(),
    db.prepare(`${cte}, warehouse_options AS (
        SELECT DISTINCT warehouse AS value FROM classified ORDER BY warehouse LIMIT 500
      ), brand_options AS (
        SELECT DISTINCT brand AS value FROM classified WHERE TRIM(brand) <> '' ORDER BY brand LIMIT 500
      ), category_options AS (
        SELECT DISTINCT category AS value FROM classified WHERE TRIM(category) <> '' ORDER BY category LIMIT 500
      )
      SELECT 'warehouse' AS kind, value FROM warehouse_options
      UNION ALL
      SELECT 'brand' AS kind, value FROM brand_options
      UNION ALL
      SELECT 'category' AS kind, value FROM category_options
      ORDER BY kind, value`)
      .bind(...sourceValues).all<AgeFacetRow>(),
  ]);
  // Window aggregates make the common, non-empty page one heavy query cheaper.
  // An empty page has no row to carry those aggregates, so preserve the exact
  // zero-result/out-of-range totals with one bounded fallback query only then.
  const metricRow = pageResult.results[0] ?? await db.prepare(
    `${cte}, filtered AS (SELECT * FROM classified ${filter.sql})
      SELECT${metricsSelectSql()} FROM filtered`,
  ).bind(...sourceValues, ...filter.values).first<MetricsRow>();
  const metrics: MetricsRow = metricRow ?? {
    total: 0, stock_value_complete_count: 0, aged_90_count: 0, aged_90_value_cents: 0, stagnant_count: 0, stagnant_value_cents: 0,
    zero_sales_count: 0, cleanup_count: 0, aged_quantity: 0, aged_value_cents: 0,
    unaged_stock_count: 0, unaged_quantity: 0,
  } as MetricsRow;
  const total = Number(metrics.total ?? 0);
  const agedQuantity = Number(metrics.aged_quantity ?? 0);
  const agedValueCents = Number(metrics.aged_value_cents ?? 0);
  return {
    hasInventory: true,
    sync: {
      inventoryAsOf: hasCompletedAgeSnapshot ? latestAgeBatch!.snapshotDate : latestBatch!.snapshotDate,
      latestInventoryBatchId: hasCompletedAgeSnapshot ? latestAgeBatch!.id : latestBatch!.id,
      sourceKey: hasCompletedAgeSnapshot ? "inventory_age" : "inventory",
      hasAgeSales: ageSalesAvailable,
    },
    metrics: {
      skuWarehouseCount: total,
      stockValueComplete: Number(metrics.stock_value_complete_count ?? 0) >= total,
      aged90Count: Number(metrics.aged_90_count ?? 0),
      aged90ValueCents: Number(metrics.aged_90_value_cents ?? 0),
      stagnantCount: Number(metrics.stagnant_count ?? 0),
      stagnantValueCents: Number(metrics.stagnant_value_cents ?? 0),
      zeroSalesCount: Number(metrics.zero_sales_count ?? 0),
      cleanupCount: Number(metrics.cleanup_count ?? 0),
    },
    coverage: {
      unagedStockCount: Number(metrics.unaged_stock_count ?? 0),
      unagedQuantity: Number(metrics.unaged_quantity ?? 0),
    },
    distribution: [
      { key: "0-30", label: "0–30 天", count: Number(metrics.bucket_0_30_count ?? 0), valueCents: Number(metrics.bucket_0_30_value ?? 0) },
      { key: "31-60", label: "31–60 天", count: Number(metrics.bucket_31_60_count ?? 0), valueCents: Number(metrics.bucket_31_60_value ?? 0) },
      { key: "61-90", label: "61–89 天", count: Number(metrics.bucket_61_89_count ?? 0), valueCents: Number(metrics.bucket_61_89_value ?? 0) },
      { key: "90+", label: "90 天以上", count: Number(metrics.bucket_90_count ?? 0), valueCents: Number(metrics.bucket_90_value ?? 0) },
    ],
    fineDistribution: inventoryAgeBuckets.map((bucket) => {
      const alias = bucketAlias(bucket.key);
      const count = Number(metrics[`bucket_${alias}_count`] ?? 0);
      const quantity = Number(metrics[`bucket_${alias}_quantity`] ?? 0);
      const valueCents = Number(metrics[`bucket_${alias}_value`] ?? 0);
      return {
        key: bucket.key,
        label: bucket.label,
        count,
        quantity,
        valueCents,
        quantityShare: agedQuantity > 0 ? quantity / agedQuantity : 0,
        valueShare: agedValueCents > 0 ? valueCents / agedValueCents : 0,
      };
    }),
    filters: {
      warehouses: facetsResult.results.filter((row) => row.kind === "warehouse").map((row) => row.value),
      brands: facetsResult.results.filter((row) => row.kind === "brand").map((row) => row.value),
      categories: facetsResult.results.filter((row) => row.kind === "category").map((row) => row.value),
      statuses: ["healthy", "aged", "slow", "stagnant", "no_stock"],
      ageBuckets: inventoryAgeBuckets.map(({ key, label }) => ({ value: key, label })),
    },
    pagination: { page: pagination.page, pageSize: pagination.pageSize, limit: pagination.pageSize, total, returned: pageResult.results.length, totalPages: total === 0 ? 0 : Math.ceil(total / pagination.pageSize), truncated: pagination.offset + pageResult.results.length < total },
    items: pageResult.results.map(mapItem),
  };
}
