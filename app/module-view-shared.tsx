"use client";

import { useEffect, useState } from "react";
import type React from "react";
import type { ShellPeriodState } from "./shell/navigation-contract";
import type { ImportSourceKey } from "./shell/navigation-catalog";
import type { AppCurrentUser } from "./shell/view-contract";
export type CurrentUser = AppCurrentUser;

export function canManageFinanceTargets(
  currentUser: Pick<CurrentUser, "role"> | null | undefined,
): boolean {
  return currentUser?.role === "admin";
}

export function validateFinanceTargetDeletionReason(value: string | null):
  | { status: "accepted"; reason: string }
  | { status: "cancelled" }
  | { status: "invalid" } {
  if (value === null) return { status: "cancelled" };
  const reason = value.trim();
  if (!reason || reason.length > 200) return { status: "invalid" };
  return { status: "accepted", reason };
}

export type SalesRangeLabel = "今日" | "昨天" | "近7天" | "近15天" | "近30天" | "本月" | "月度" | "去年同期" | "自定义";
export type SalesRange = "today" | "yesterday" | "last7" | "last15" | "last30" | "month" | "quarter" | "custom";

export type SalesStats = {
  grossSalesCents: number;
  netSalesCents: number;
  costAmountCents: number;
  grossProfitCents: number;
  refundAmountCents: number;
  orderCount: number;
  lineCount: number;
  netQuantity: number;
  averageOrderValueCents: number;
  grossMarginRate: number;
  refundRate: number;
};

export type SalesChannel = {
  groupKey: string;
  name: string;
  platform: string;
  grossSalesCents: number;
  netSalesCents: number;
  costAmountCents: number;
  grossProfitCents: number;
  refundAmountCents: number;
  grossMarginRate: number;
  refundRate: number;
  shareRate: number;
  orderCount: number;
  lineCount: number;
  netQuantity: number;
  averageOrderValueCents: number;
  yearAgoNetSalesCents: number;
  salesYearOverYearRate: number | null;
};

export type SalesSummaryResponse = {
  range: SalesRange;
  startDate: string;
  endDate: string;
  requestedStartDate?: string;
  requestedEndDate?: string;
  dataCutoffDate?: string | null;
  periodAdjustedToDataCutoff?: boolean;
  comparisonDayCount?: number;
  previousStartDate?: string;
  previousEndDate?: string;
  yearAgoStartDate?: string;
  yearAgoEndDate?: string;
  current: SalesStats;
  previous?: SalesStats;
  yearAgo?: SalesStats;
  channels: SalesChannel[];
  outlets?: SalesChannel[];
  shops?: SalesChannel[];
  platforms?: SalesChannel[];
  groupPagination?: {
    outlets: { total: number; returned: number; truncated: boolean };
    shops: { total: number; returned: number; truncated: boolean };
    platforms: { total: number; returned: number; truncated: boolean };
  };
  latestBatch?: {
    id: string;
    fileName: string;
    completedAt?: string | null;
  } | null;
  daily?: Array<{ date: string } & SalesStats>;
  previousDaily?: Array<{ date: string } & SalesStats>;
  yearAgoDaily?: Array<{ date: string } & SalesStats>;
  trendReturned?: number;
  trendTruncated?: boolean;
  filters?: { productCodes: string[]; platform?: string | null; platforms?: string[]; shop?: string | null; outlets?: Array<{ platform: string; shop: string }>; categories?: string[] };
  filterOptions?: {
    platforms: string[];
    shops: Array<{ key: string; name: string; platform: string }>;
    categories: string[];
  };
};

export type SalesDashboardResponse = Pick<SalesSummaryResponse,
  "range" | "startDate" | "endDate" | "requestedStartDate" | "requestedEndDate" |
  "dataCutoffDate" | "periodAdjustedToDataCutoff" | "comparisonDayCount" |
  "previousStartDate" | "previousEndDate" | "yearAgoStartDate" | "yearAgoEndDate" |
  "current" | "previous" | "yearAgo" | "outlets" | "daily" | "latestBatch"
> & { projection: "dashboard" };

export type ProductSummaryItem = {
  productCode: string;
  productName: string;
  brand: string;
  supplierName: string;
  specification: string;
  category: string;
  outlets: Array<{ platform: string; shop: string }>;
  netQuantity: number;
  grossSalesCents: number;
  refundAmountCents: number;
  netSalesCents: number;
  costCents: number;
  feeCents: number;
  grossProfitCents: number;
  grossMarginRate: number | null;
  refundRate: number;
  shippingRate: number | null;
  averageSalePriceCents: number | null;
  averageCostCents: number | null;
  observedFeeRate: number | null;
  availableQuantity: number | null;
  stockValueCents: number | null;
  knownStockValueCents: number | null;
  costCoverageRate: number | null;
};

export type ProductShopFilterOption = {
  key: string;
  platform: string;
  shop: string;
};

export type ProductSummaryPagination = { page: number; pageSize: number; total: number; returned: number; totalPages: number; truncated: boolean };
export type ProductSummarySortState = {
  by: "netSalesCents" | "grossProfitCents" | "grossMarginRate" | "refundRate" | "stockValueCents" | "netQuantity";
  direction: "asc" | "desc";
};

export const snapshotTokenPattern = /^[a-f0-9]{64}$/;
export const snapshotRestartHistoryLimit = 8;

export function claimSnapshotRestart(restartedTokens: Set<string>, snapshotToken: string) {
  if (restartedTokens.has(snapshotToken)) return false;
  if (restartedTokens.size >= snapshotRestartHistoryLimit) {
    const oldestToken = restartedTokens.values().next().value;
    if (oldestToken) restartedTokens.delete(oldestToken);
  }
  restartedTokens.add(snapshotToken);
  return true;
}

export function effectivePageForScope(page: number, scopeKey: string, committedScopeKey: string) {
  if (scopeKey !== committedScopeKey) return 1;
  return Number.isSafeInteger(page) && page > 0 ? page : 1;
}

export type ProductSummaryResponse = {
  projection: "full";
  snapshotToken: string;
  hasSales: boolean;
  range: "last30" | "last90" | "halfYear" | "custom";
  sync: {
    salesThrough: string | null;
    salesWindowStart: string | null;
    dataStartDate: string | null;
    dataCutoffDate: string | null;
    inventoryAsOf: string | null;
    latestSalesFile: string | null;
  };
  filters: { platforms: string[]; shops: ProductShopFilterOption[]; categories: string[] };
  filtersApplied: { platforms: string[]; shops: ProductShopFilterOption[]; query: string; categories: string[]; marginBands: string[] };
  sort: ProductSummarySortState;
  pagination: ProductSummaryPagination;
  metrics: {
    skuCount: number;
    grossSalesCents: number;
    netSalesCents: number;
    grossProfitCents: number;
    grossMarginRate: number | null;
    lossSkuCount: number;
    stockedSkuCount: number;
    marginBuckets: {
      below35Count: number;
      between35And40Count: number;
      between40And45Count: number;
      atLeast45Count: number;
    };
  };
  items: ProductSummaryItem[];
};

export type ProductSummaryPageResponse = {
  projection: "page";
  snapshotToken: string;
  sort: ProductSummarySortState;
  pagination: ProductSummaryPagination;
  items: ProductSummaryItem[];
};

export type InventoryHealthStatus = "no_stock" | "urgent" | "warning" | "stale" | "slow" | "healthy";
export type InventoryAgeStatus = "healthy" | "aged" | "slow" | "stagnant" | "no_stock";

export type InventoryOverviewItem = {
  key: string;
  productCode: string;
  productName: string;
  brand: string;
  specification: string;
  category: string;
  supplier: string;
  supplierSource: "jikexyun_inventory" | "erp_fallback" | "missing";
  warehouse: string;
  warehouseType: "owned" | "jd_rdc" | "other";
  warehouseCategory: "jd" | "dropship" | "afterSales" | "guangdong" | "sample" | "cainiao" | "overseas" | "virtual" | "exception" | "selfOperated";
  includedInInventory: boolean;
  onHandQuantity: number;
  availableQuantity: number;
  lockedQuantity: number;
  sourceInTransitQuantity: number;
  plannedInTransitQuantity: number;
  totalInTransitQuantity: number;
  unitCostCents: number;
  inventoryAgeDays: number | null;
  stockValueCents: number | null;
  knownStockValueCents: number;
  costCoverageRate: number;
  sales30d: number | null;
  productSales30d: number | null;
  averageDailySales: number | null;
  coverageDays: number | null;
  suggestedQuantity: number | null;
  status: InventoryHealthStatus;
  statusLabel: string;
  reason: string;
  inDraftPlan: boolean;
};

export type ReplenishmentPlanItem = {
  id: string;
  sourceBatchId: string;
  productCode: string;
  productName: string;
  brand: string;
  category: string;
  supplier: string;
  warehouse: string;
  buyer: string;
  operatorName: string;
  department: string;
  planType: string;
  orderDate: string | null;
  expectedArrivalDate: string | null;
  requiresInspection: boolean;
  currentStockQuantity: number;
  sales30dQuantity: number | null;
  suggestedQuantity: number;
  plannedQuantity: number;
  coverageDays: number | null;
  reason: string;
  notes: string;
  status: "draft" | "confirmed" | "completed" | "cancelled";
  dingTalkSync: {
    status: "not_synced" | "syncing" | "synced" | "failed";
    syncedAt: string | null;
    error: string;
  };
  createdAt: string;
  updatedAt: string;
};

export type InventoryOverviewResponse = {
  projection: "overview" | "plan";
  hasInventory: boolean;
  sync: {
    latestInventoryBatchId: string | null;
    inventoryAsOf: string | null;
    inventorySyncedAt: string | null;
    salesThrough: string | null;
    salesWindowStart: string | null;
    latestInventoryFile: string | null;
    inventoryStale: boolean;
  };
  settings: {
    targetDays: number;
    criticalDays: number;
    replenishDays: number;
    slowDays: number;
    stagnantDays: number;
    salesWindowDays: number;
    salesCoverageDays: number;
    autoReplenishment: boolean;
    inventoryAlert: boolean;
  };
  metrics: {
    skuWarehouseCount: number;
    totalAvailableQuantity: number;
    totalStockValueCents: number;
    knownStockValueCents: number;
    stockValueComplete: boolean;
    costCoverageRate: number;
    salesDemandMatchRate: number;
    averageCoverageDays: number | null;
    urgentCount: number;
    replenishCount: number;
    slowMovingValueCents: number;
    noSalesCount: number;
    recommendationCount: number;
    inventoryAlertsEnabled: boolean;
    recommendationsSuppressed: boolean;
    qualityIssues: Array<{
      code: string;
      severity: "warning" | "blocking";
      message: string;
      affectedCount: number;
    }>;
  };
  health: {
    noStock: number;
    urgent: number;
    warning: number;
    stale: number;
    slow: number;
    healthy: number;
  };
  controls: {
    autoReplenishmentEnabled: boolean;
    alertsEnabled: boolean;
  };
  quality: {
    status: "reliable" | "degraded" | "blocked";
    salesMatchThreshold: number;
    salesDemandMatchRate: number;
    recommendationsSuppressed: boolean;
    issues: Array<{
      code: string;
      severity: "warning" | "blocking";
      message: string;
      affectedCount: number;
    }>;
  };
  sources: Array<{
    key: string;
    label: string;
    status: "ready" | "missing" | "stale";
    asOfDate: string | null;
  }>;
  filters: { warehouses: string[]; brands: string[]; categories: string[]; statuses: InventoryHealthStatus[] };
  mapping: {
    matchedCount: number;
    unmatchedCount: number;
    samples: Array<{
      key: string;
      productCode: string;
      productName: string;
      brand: string;
      category: string;
      supplier: string;
      warehouses: Record<"jd" | "dropship" | "afterSales" | "guangdong" | "sample" | "cainiao" | "overseas" | "virtual" | "exception" | "selfOperated", {
        inventoryQuantity: number;
        salesQuantity: number | null;
        turnoverDays: number | null;
        inTransitQuantity: number;
      }>;
      warehouseOptions: Array<{
        key: string;
        warehouse: string;
        availableQuantity: number;
        salesQuantity: number | null;
        coverageDays: number | null;
        suggestedQuantity: number | null;
        inDraftPlan: boolean;
      }>;
      totalInventoryQuantity: number;
      totalStockValueCents: number;
      totalInTransitQuantity: number;
      totalSalesQuantity: number | null;
      totalTurnoverDays: number | null;
      suggestedQuantity: number | null;
      alertStatus: InventoryHealthStatus;
      alertLabel: string;
      alertReason: string;
      unmatchedWarehouseCount: number;
    }>;
  };
  pagination: { page: number; pageSize: number; limit: number; total: number; returned: number; totalPages: number; truncated: boolean };
  recommendations: InventoryOverviewItem[];
  items: InventoryOverviewItem[];
  plans: ReplenishmentPlanItem[];
  plansPagination: { page: number; pageSize: number; total: number; returned: number; totalPages: number; truncated: boolean };
  planSummary: {
    draftCount: number;
    confirmedCount: number;
    completedCount: number;
    cancelledCount: number;
    activeQuantity: number;
  };
};

export type InventoryDashboardResponse = Pick<InventoryOverviewResponse, "hasInventory" | "sync" | "metrics" | "health">;

export type BiDashboardResponse = {
  projection: "dashboard";
  contractVersion: "bi-dashboard-read-model-v1";
  generatedAt: string;
  revision: string;
  sourceRevisions: { salesErp: string; inventory: string };
  sales: SalesDashboardResponse;
  inventory: InventoryDashboardResponse;
  inventoryHealthScore: number | null;
};

export type InventoryAgeItem = {
  key: string;
  productCode: string;
  productName: string;
  brand: string;
  specification: string;
  category: string;
  warehouse: string;
  warehouseType: "owned" | "jd_rdc" | "other";
  warehouseCategory: "jd" | "dropship" | "afterSales" | "guangdong" | "sample" | "cainiao" | "overseas" | "virtual" | "exception" | "selfOperated";
  warehouseLabel: string;
  includedInInventory: boolean;
  availableQuantity: number;
  stockValueCents: number | null;
  inventoryAgeDays: number | null;
  ageBucketKey: string | null;
  ageBucketLabel: string;
  sales7dQuantity: number | null;
  sales30dQuantity: number | null;
  status: InventoryAgeStatus;
  statusLabel: string;
  recommendation: string;
};

export type InventoryAgeAnalysisResponse = {
  hasInventory: boolean;
  sync: { inventoryAsOf: string | null; latestInventoryBatchId: string | null; sourceKey?: "inventory_age" | "inventory"; hasAgeSales: boolean };
  metrics: { skuWarehouseCount: number; stockValueComplete: boolean; aged90Count: number; aged90ValueCents: number; stagnantCount: number; stagnantValueCents: number; zeroSalesCount: number; cleanupCount: number };
  coverage: { unagedStockCount: number; unagedQuantity: number };
  distribution: Array<{ key: string; label: string; count: number; valueCents: number }>;
  fineDistribution: Array<{ key: string; label: string; count: number; quantity: number; valueCents: number; quantityShare: number; valueShare: number }>;
  filters: { warehouses: string[]; brands: string[]; categories: string[]; statuses: InventoryAgeStatus[]; ageBuckets: Array<{ value: string; label: string }> };
  pagination: { page: number; pageSize: number; limit: number; total: number; returned: number; totalPages: number; truncated: boolean };
  items: InventoryAgeItem[];
};

export type InventoryInboundMonitorItem = {
  key: string;
  productCode: string;
  productName: string;
  brand: string;
  category: string;
  supplier: string;
  warehouse: string;
  availableQuantity: number;
  inTransitQuantity: number;
  inventoryAgeDays: number | null;
  knownStockValueCents: number;
  costCoverageRate: number;
  unitCostCents: number | null;
  outbound7dQuantity: number | null;
  outbound30dQuantity: number | null;
  outbound90dQuantity: number | null;
  turnoverDays: number | null;
  risk: "normal" | "stale" | "unknown" | "no_stock";
};

export type InventoryInboundMonitorResponse = {
  hasInventory: boolean;
  sync: { inventoryAsOf: string | null; salesThrough: string | null; latestInventoryBatchId: string | null };
  scope: { warehouseType: "jd_rdc"; valuationBasis: "fixed_cost"; supplyPriceAvailable: boolean; nativeComparisonAvailable: boolean };
  metrics: { itemCount: number; warehouseCount: number; availableQuantity: number; inTransitQuantity: number; knownStockValueCents: number; costCoverageRate: number; salesMatchRate: number; outbound30dQuantity: number; turnoverDays: number | null; staleItemCount: number; staleValueCents: number; missingSupplierCount: number };
  filters: { warehouses: string[]; brands: string[]; categories: string[]; suppliers: string[] };
  pagination: { page: number; pageSize: number; total: number; returned: number; totalPages: number; truncated: boolean };
  regions: Array<{ warehouse: string; itemCount: number; availableQuantity: number; inTransitQuantity: number; knownStockValueCents: number; outbound30dQuantity: number; turnoverDays: number | null; salesMatchRate: number }>;
  items: InventoryInboundMonitorItem[];
  disclosures: string[];
};

export type InventoryImportResponse = {
  ok: boolean;
  status: "imported" | "duplicate" | "rejected";
  message?: string;
  batch?: {
    fileName: string;
    snapshotDate: string;
    rowCount: number;
    insertedCount: number;
  } | null;
  warnings?: ImportIssue[];
  errors?: ImportIssue[];
};

export type InventoryChunkUploadResponse = Partial<InventoryImportResponse> & {
  ok: boolean;
  status?: string;
  message?: string;
  upload?: {
    id: string;
    receivedChunkIndexes: number[];
    receivedBytes: number;
    chunkCount: number;
  };
};

export type ImportIssue = {
  code?: string;
  message: string;
  row?: number;
  sourceRowNumber?: number;
};

export type SalesImportBatch = {
  id: string;
  source: string;
  fileName: string;
  fileSizeBytes: number;
  sheetName?: string | null;
  status: "imported" | "duplicate" | "rejected" | string;
  rowCount: number;
  insertedCount: number;
  duplicateCount: number;
  warningCount: number;
  warnings?: ImportIssue[];
  createdAt: string;
  completedAt?: string | null;
};

export type ImportHistoryResponse = {
  items: SalesImportBatch[];
};

export type InventoryImportHistoryItem = Pick<SalesImportBatch, "id" | "fileName" | "status" | "rowCount" | "insertedCount" | "warningCount" | "createdAt" | "completedAt"> & { snapshotDate: string };

export type ErpReferenceImportBatch = {
  id: string;
  sourceKey: "products" | "inventory_age" | "combos";
  sourceLabel: string;
  fileName: string;
  fileSizeBytes: number;
  sheetName?: string | null;
  snapshotDate?: string | null;
  status: string;
  rowCount: number;
  insertedCount: number;
  updatedCount: number;
  excludedCount: number;
  warningCount: number;
  warnings?: ImportIssue[];
  createdAt: string;
  completedAt?: string | null;
};

export type ProductShippingRateImportBatch = {
  id: string;
  source: string;
  fileName: string;
  fileSizeBytes: number;
  sheetName: string;
  status: string;
  sourceRowCount: number;
  rowCount: number;
  insertedCount: number;
  updatedCount: number;
  duplicateCount: number;
  warningCount: number;
  createdAt: string;
  completedAt?: string | null;
};

export type UnifiedImportBatch = {
  id?: string;
  dataset?: string;
  fileName?: string;
  fileSizeBytes?: number;
  sheetName?: string | null;
  snapshotDate?: string | null;
  rowCount?: number;
  insertedCount?: number;
  updatedCount?: number;
  duplicateCount?: number;
  excludedCount?: number;
  warningCount?: number;
  warnings?: ImportIssue[];
};

export type UnifiedImportResponse = {
  ok: boolean;
  status?: string;
  message?: string;
  batch?: UnifiedImportBatch | null;
  warnings?: ImportIssue[];
  errors?: ImportIssue[];
  verification?: { verified: boolean; parsedRowCount: number; readbackRowCount: number; dateMin: string | null; dateMax: string | null; unmatchedProductCount: number; imageCount?: number; verifiedImageCount?: number };
  upload?: {
    id: string;
    receivedChunkIndexes: number[];
    receivedBytes: number;
    chunkCount: number;
  };
};

export type UnifiedHistoryItem = {
  shopName?: string;
  warnings?: ImportIssue[];
  errors?: ImportIssue[];
  id: string;
  sourceKey: ImportSourceKey | "jd_promotion";
  sourceLabel: string;
  fileName: string;
  fileSizeBytes?: number;
  sheetName?: string | null;
  snapshotDate?: string | null;
  status: string;
  rowCount: number;
  insertedCount: number;
  updatedCount?: number;
  duplicateCount?: number;
  excludedCount?: number;
  warningCount: number;
  createdAt: string;
  completedAt?: string | null;
};

export type CustomerServiceImportHistoryItem = {
  id: string; shopName: string; sessionFileName: string; chatFileName: string; status: string; conversationCount: number; matchedCount: number; warnings: string[]; createdAt: string; completedAt?: string | null;
};

export type NetshopImportHistoryItem = {
  id: string;
  source: string;
  dataset: string;
  shopName: string;
  fileName: string;
  fileSizeBytes: number;
  sheetName?: string | null;
  snapshotDate?: string | null;
  status: string;
  rowCount: number;
  insertedCount: number;
  duplicateCount: number;
  warningCount: number;
  warnings?: ImportIssue[];
  createdAt: string;
  completedAt?: string | null;
};

export type JdSkuCatalogItem = {
  platform: string;
  shopName: string;
  spuId: string;
  skuId: string;
  productCode: string;
  productName: string;
  imageUrl: string;
  saleAttribute: string;
  category: string;
  brand: string;
  price: number | null;
  priceCents: number | null;
  totalInventory: number | null;
  availableInventory: number | null;
  status: string;
  productUrl: string;
  createdAt: string;
  snapshotDate: string | null;
  costPriceCents: number | null;
  netSalesCents: number | null;
  grossMarginRate: number | null;
  refundRate: number | null;
  salesMatched: boolean;
};

export type JdSkuCatalogResponse = {
  snapshotToken: string;
  batch: Pick<NetshopImportHistoryItem, "fileName" | "snapshotDate" | "rowCount" | "completedAt"> | null;
  summary: { totalSkus: number; onSaleSkus: number; totalInventory: number; availableInventory: number };
  shops: Array<{ shopName: string; platform: string; snapshotDate: string | null; completedAt: string | null }>;
  sales: { periodStart: string | null; periodEnd: string | null; dataCutoffDate: string | null; platform: string };
  items: JdSkuCatalogItem[];
  pagination: { page: number; pageSize: number; total: number; returned?: number; truncated?: boolean };
};

export type JdSkuCatalogPageResponse = {
  snapshotToken: string;
  items: JdSkuCatalogItem[];
  pagination: JdSkuCatalogResponse["pagination"];
};

export type NetshopProductPerformanceDimension = "sku" | "spu";

export type NetshopProductPerformanceItem = {
  id: string;
  platform: string;
  skuId: string;
  spuId: string;
  productCode: string;
  productName: string;
  imageUrl: string;
  productUrl: string;
  category: string;
  shopNames: string[];
  dateMin: string | null;
  dateMax: string | null;
  dataDays: number;
  pageViews: number;
  visitors: number;
  searchImpressions: number;
  searchClicks: number;
  searchClickRate: number | null;
  addCartCustomers: number;
  addCartQuantity: number;
  orderCustomers: number;
  orderQuantity: number;
  orderAmount: number;
  orderAmountCents: number;
  transactionOrders: number;
  transactionAmount: number;
  transactionAmountCents: number;
  transactionQuantity: number;
  transactionCustomers: number;
  favorites: number;
  refundAmountCents: number;
  searchVisitors: number;
  searchTransactionCustomers: number;
  uvValue: number | null;
  conversionRate: number | null;
};

export type NetshopProductPerformanceResponse = {
  snapshotToken: string;
  dimension: NetshopProductPerformanceDimension;
  dataset: "sku_daily" | "spu_daily";
  requestedPeriod: { startDate: string | null; endDate: string | null };
  dateMin: string | null;
  dataCutoffDate: string | null;
  monetaryUnit: "cents";
  visitorAggregation: "product_day_sum";
  coverage: {
    actualDates: string[];
    missingDates: string[];
    availableDateMin: string | null;
    availableDateMax: string | null;
    total: number;
    returned: number;
    truncated: boolean;
  };
  platforms: string[];
  shops: Array<{ shopName: string; platform: string; productCount: number }>;
  summary: {
    productCount: number;
    pageViews: number;
    visitors: number;
    searchImpressions: number;
    searchClicks: number;
    searchClickRate: number | null;
    addCartCustomers: number;
    addCartQuantity: number;
    orderCustomers: number;
    orderQuantity: number;
    orderAmount: number;
    orderAmountCents: number;
    transactionOrders: number;
    transactionAmount: number;
    transactionAmountCents: number;
    transactionQuantity: number;
    transactionCustomers: number;
    favorites: number;
    refundAmountCents: number;
    searchVisitors: number;
    searchTransactionCustomers: number;
    uvValue: number | null;
    conversionRate: number | null;
  };
  daily: Array<{
    date: string;
    pageViews: number;
    visitors: number;
    transactionCustomers: number;
    transactionQuantity: number;
    transactionAmountCents: number;
    refundAmountCents: number;
    favorites: number;
    addCartCustomers: number;
    addCartQuantity: number;
  }>;
  dailyPagination: { total: number; returned: number; truncated: boolean };
  items: NetshopProductPerformanceItem[];
  pagination: { page: number; pageSize: number; total: number; returned?: number; truncated?: boolean };
};

export type NetshopProductPerformanceSummaryResponse = Pick<
  NetshopProductPerformanceResponse,
  "snapshotToken" | "dimension" | "dataset" | "requestedPeriod" | "dateMin" | "dataCutoffDate" | "monetaryUnit" | "visitorAggregation" | "summary"
>;

export type NetshopProductPerformancePageResponse = Pick<
  NetshopProductPerformanceResponse,
  "snapshotToken" | "items" | "pagination"
>;

export type NetshopPromotionPerformanceResponse = {
  monetaryUnit: "cents";
  requestedPeriod: { startDate: string | null; endDate: string | null };
  dateMin: string | null;
  dataCutoffDate: string | null;
  coverage: { promotionDates: string[]; productDailyDates: string[]; intersectionDates: string[]; missingProductDailyDates: string[]; missingPromotionDates: string[] };
  summary: {
    productCount: number; spendCents: number; netTransactionAmountCents: number; grossTransactionAmountCents: number;
    platformPaymentAmountCents: number; impressions: number; clicks: number; netOrders: number; favorites: number; cartQuantity: number;
    clickThroughRate: number | null; averageClickCostCents: number | null; roas: number | null; spendRate: number | null; promotionTransactionShare: number | null;
  };
  daily: Array<{ date: string; spendCents: number; netTransactionAmountCents: number; platformPaymentAmountCents: number | null; impressions: number; clicks: number; netOrders: number; roas: number | null; spendRate: number | null; promotionTransactionShare: number | null }>;
  filterOptions: { shops: Array<{ platform: string; shopName: string }>; pagination: { total: number; returned: number; truncated: boolean } };
  items: Array<{ id: string; platform: string; productName: string; shopName: string; dateMin: string | null; dateMax: string | null; dates?: string[]; dataDays: number; spendCents: number; netTransactionAmountCents: number; grossTransactionAmountCents: number; impressions: number; clicks: number; netOrders: number; favorites: number; cartQuantity: number; clickThroughRate: number | null; averageClickCostCents: number | null; roas: number | null }>;
  pagination: { page: number; pageSize: number; total: number; returned: number; truncated: boolean };
};

export type NetshopPromotionItemsResponse = Pick<NetshopPromotionPerformanceResponse,
  "monetaryUnit" | "requestedPeriod" | "dataCutoffDate" | "items" | "pagination"
> & { snapshotToken: string };

export type NetshopPromotionOverviewResponse = Pick<NetshopPromotionPerformanceResponse,
  "monetaryUnit" | "requestedPeriod" | "dataCutoffDate" | "summary" | "coverage" | "daily" | "filterOptions"
> & {
  snapshotToken: string;
  dailyPagination: { total: number; returned: number; truncated: boolean };
};

export const netshopOutletFilterKey = (platform: string, shopName: string) => `${platform}\u001f${shopName}`;

export type FinanceActualMetrics = {
  grossSalesCents: number;
  returnAmountCents: number;
  netSalesCents: number;
  netCostCents: number;
  grossProfitCents: number;
  grossMarginBps: number;
  returnRateBps: number;
  sellingExpenseCents: number;
  smallProfitCents: number;
  smallMarginBps: number;
  otherExpenseCents: number;
  profitCents: number;
  profitMarginBps: number;
  promotionExpenseCents: number;
  promotionFeeRatioBps: number;
};

export type FinanceTargetTotals = {
  salesTargetCents: number;
  profitTargetCents: number;
  smallMarginBps: number;
  inventoryCleanupTargetCents: number;
  promotionFeeRatioBps: number;
  stagnantInventoryTargetCents: number;
  targetCount: number;
};

export type FinanceProgress = {
  sales: number | null;
  profit: number | null;
  smallMarginGapBps: number | null;
  promotionFeeGapBps: number | null;
};

export type FinanceTarget = {
  id: string;
  periodType: "month" | "year" | "project";
  periodKey: string;
  platform: string;
  shopName: string;
  category: string;
  manager: string;
  salesTargetCents: number;
  profitTargetCents: number;
  grossMarginBps: number;
  smallMarginBps: number;
  inventoryCleanupTargetCents: number;
  promotionFeeRatioBps: number;
  stagnantInventoryTargetCents: number;
  version: number;
  createdAt: string;
  updatedAt: string;
};

export type FinanceAnalysisResponse = {
  hasData: boolean;
  selectedMonth: string | null;
  selectedMonths?: string[];
  periodLabel?: string;
  previousMonth?: string | null;
  previousMonths?: string[];
  yearAgoMonth?: string | null;
  yearAgoMonths?: string[];
  months: Array<{ month: string; fileName: string; importedAt: string; shopCount: number; subjectCount: number }>;
  current?: FinanceActualMetrics;
  previous?: FinanceActualMetrics | null;
  yearAgo?: FinanceActualMetrics | null;
  yearToDate?: FinanceActualMetrics;
  timeline: Array<{ month: string } & FinanceActualMetrics>;
  targets?: { month: FinanceTargetTotals; year: FinanceTargetTotals; projects: FinanceTarget[]; legacyCompatibility?: { excluded: number; reason: string } };
  progress?: { month: FinanceProgress; year: FinanceProgress };
  expenses: Array<{ name: string; current: number; previous: number | null; yearAgo: number | null; feeRateBps: number; yearAgoFeeRateBps: number | null; momRate: number | null; yoyRate: number | null; abnormal: boolean }>;
  expensePagination?: { total: number; returned: number; truncated: boolean };
  shops: Array<{ key: string; name: string; groupName: string; manager: string; actual: FinanceActualMetrics; target: FinanceTargetTotals; progress: FinanceProgress }>;
  shopPagination?: { total: number; returned: number; truncated: boolean };
  anomalies: Array<{ level: "critical" | "warning" | "info"; title: string; detail: string }>;
  filters?: { platforms: string[]; shops: Array<{ key: string; name: string; platform: string }> };
  selection?: {
    allMonths: boolean;
    months: string[];
    requestedMonths?: string[];
    fallbackApplied?: boolean;
    platforms: string[];
    shops: string[];
    truncated: boolean;
    availableMonthCount: number;
  };
  sync?: { dataCutoffMonth: string; sourceFileName: string; importedAt: string };
  error?: string;
};

export type FinanceTargetOptions = { shops: Array<{ key: string; platform: string; name: string }>; categories: string[]; projects: string[]; pagination?: { shops: { total: number; returned: number; truncated: boolean } } };

export type ImportFeedback = {
  tone: "success" | "warning" | "error" | "duplicate";
  title: string;
  message: string;
  details: string[];
};

export const salesRangeMap: Record<SalesRangeLabel, SalesRange> = {
  今日: "today",
  昨天: "yesterday",
  近7天: "last7",
  近15天: "last15",
  近30天: "last30",
  本月: "month",
  月度: "custom",
  去年同期: "custom",
  自定义: "custom",
};

export const channelTones = ["blue", "purple", "green", "orange"] as const;
export const channelColors = ["#4776e6", "#8167d9", "#27a978", "#e99436"];
export const DIRECT_IMPORT_FILE_SIZE = 2 * 1024 * 1024;
export const MAX_IMPORT_FILE_SIZE = 128 * 1024 * 1024;
export const SALES_UPLOAD_CHUNK_SIZE = 2 * 1024 * 1024;
export const MAX_INVENTORY_FILE_SIZE = 20 * 1024 * 1024;
export const DIRECT_INVENTORY_FILE_SIZE = 1024 * 1024;
export const INVENTORY_UPLOAD_CHUNK_SIZE = 1024 * 1024;
export const MAX_FINANCE_FILE_SIZE = 8 * 1024 * 1024;
export const MAX_JD_SKU_FILE_SIZE = 25 * 1024 * 1024;
export const MAX_TMALL_PRODUCT_ASSET_FILE_SIZE = 64 * 1024 * 1024;
export const TMALL_PRODUCT_ASSET_CHUNK_SIZE = 2 * 1024 * 1024;

export const formatCurrency = (value: number) =>
  new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    maximumFractionDigits: 0,
  }).format(value);

export const formatCurrencyFromCents = (value = 0) => formatCurrency(value / 100);
export const formatExactCurrencyFromCents = (value = 0) => new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "CNY",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
}).format(value / 100);
export const formatMerchantCurrency = (value?: number | null) => value === null || value === undefined
  ? "—"
  : `¥${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value)}`;
export const formatOptionalCurrencyFromCents = (value?: number | null) => value === null || value === undefined ? "—" : formatCurrencyFromCents(value);
export const formatOptionalRate = (value?: number | null) => value === null || value === undefined ? "—" : formatRate(value);
export const formatCount = (value = 0) => new Intl.NumberFormat("zh-CN").format(value);
export const rateAsPercent = (value = 0) => value * 100;
export const formatRate = (value = 0) => `${rateAsPercent(value).toFixed(1)}%`;
export const formatYearOverYear = (value?: number | null) => value === null || value === undefined
  ? "—"
  : `${value >= 0 ? "+" : ""}${rateAsPercent(value).toFixed(1)}%`;
export const formatNetSalesYearOverYear = (value?: number | null) => value === null || value === undefined
  ? "无同期数据"
  : formatYearOverYear(value);
export const netSalesYearOverYearTone = (value?: number | null) => value === null || value === undefined
  ? "muted-text"
  : value < 0 ? "orange-text" : "green-text";
export const formatFileSize = (bytes = 0) => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

export const DAY_MS = 86_400_000;
export const addIsoDays = (value: string, days: number) => {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
};
export const isoDayDifference = (start: string, end: string) => Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / DAY_MS);
export const shanghaiIsoToday = () => {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date());
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return `${value("year")}-${value("month")}-${value("day")}`;
};
export const selectedMonthPeriod = (month: string) => {
  const [year, monthNumber] = month.split("-").map(Number);
  const lastDay = new Date(Date.UTC(year, monthNumber, 0)).getUTCDate();
  return { startDate: `${month}-01`, endDate: `${month}-${String(lastDay).padStart(2, "0")}` };
};
export const skuSalesPeriod = (range: SalesRangeLabel, customStartDate: string, customEndDate: string) => {
  const today = shanghaiIsoToday();
  if (range === "今日") return { startDate: today, endDate: today };
  if (range === "昨天") {
    const yesterday = addIsoDays(today, -1);
    return { startDate: yesterday, endDate: yesterday };
  }
  if (range === "近7天") return { startDate: addIsoDays(today, -6), endDate: today };
  if (range === "近15天") return { startDate: addIsoDays(today, -14), endDate: today };
  if (range === "近30天") return { startDate: addIsoDays(today, -29), endDate: today };
  if (range === "月度" || range === "自定义" || range === "去年同期") return { startDate: customStartDate, endDate: customEndDate };
  return { startDate: `${today.slice(0, 7)}-01`, endDate: today };
};

export const shellPeriodForRange = (
  range: SalesRangeLabel,
  selectedMonth: string,
  customStartDate: string,
  customEndDate: string,
): ShellPeriodState => {
  if (range === "今日") return { kind: "today" };
  if (range === "昨天") return { kind: "yesterday" };
  if (range === "近7天") return { kind: "last7" };
  if (range === "近15天") return { kind: "last15" };
  if (range === "近30天") return { kind: "last30" };
  if (range === "月度") return { kind: "calendar_month", month: selectedMonth };
  if (range === "自定义") return { kind: "custom", from: customStartDate, to: customEndDate };
  if (range === "去年同期") return { kind: "previous_year", from: customStartDate, to: customEndDate };
  return { kind: "current_month" };
};

export const rangeForShellPeriod = (period: ShellPeriodState): SalesRangeLabel => {
  if (period.kind === "today") return "今日";
  if (period.kind === "yesterday") return "昨天";
  if (period.kind === "last7") return "近7天";
  if (period.kind === "last15") return "近15天";
  if (period.kind === "last30") return "近30天";
  if (period.kind === "previous_year") return "去年同期";
  if (period.kind === "calendar_month") return "月度";
  if (period.kind === "custom") return "自定义";
  return "本月";
};

export type ProductComparisonMode = "period" | "year";

export const moveIsoYears = (value: string, offset: number) => {
  const [year, month, day] = value.split("-").map(Number);
  const targetYear = year + offset;
  const targetLastDay = new Date(Date.UTC(targetYear, month, 0)).getUTCDate();
  return `${targetYear}-${String(month).padStart(2, "0")}-${String(Math.min(day, targetLastDay)).padStart(2, "0")}`;
};

export const previousYearPeriod = (period: { startDate: string; endDate: string }) => ({
  startDate: moveIsoYears(period.startDate, -1),
  endDate: moveIsoYears(period.endDate, -1),
});

export const productComparisonPeriod = (
  current: { startDate: string; endDate: string },
  mode: ProductComparisonMode,
) => {
  if (mode === "year") {
    return { startDate: moveIsoYears(current.startDate, -1), endDate: moveIsoYears(current.endDate, -1) };
  }
  const days = Math.max(1, isoDayDifference(current.startDate, current.endDate) + 1);
  const endDate = addIsoDays(current.startDate, -1);
  return { startDate: addIsoDays(endDate, -(days - 1)), endDate };
};

export const productComparisonRate = (value?: number | null, baseline?: number | null) => {
  if (value === null || value === undefined || baseline === null || baseline === undefined || baseline === 0) return null;
  return (value - baseline) / Math.abs(baseline);
};

export const formatProductComparison = (value?: number | null, baseline?: number | null) => {
  const rate = productComparisonRate(value, baseline);
  return rate === null ? "—" : `${rate >= 0 ? "+" : ""}${(rate * 100).toFixed(1)}%`;
};

export function useDebouncedValue<T>(value: T, delay = 260) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [delay, value]);
  return debounced;
}

export const startOfIsoMonth = (value: string) => `${value.slice(0, 7)}-01`;
export const endOfIsoMonth = (value: string) => {
  const [year, month] = value.slice(0, 7).split("-").map(Number);
  return `${value.slice(0, 7)}-${String(new Date(Date.UTC(year, month, 0)).getUTCDate()).padStart(2, "0")}`;
};
export const addIsoMonths = (value: string, months: number) => {
  const [year, month, day] = value.split("-").map(Number);
  const target = new Date(Date.UTC(year, month - 1 + months, 1));
  const lastDay = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
  return `${target.getUTCFullYear()}-${String(target.getUTCMonth() + 1).padStart(2, "0")}-${String(Math.min(day, lastDay)).padStart(2, "0")}`;
};
export const addIsoYears = (value: string, years: number) => {
  const [year, month, day] = value.split("-").map(Number);
  const targetYear = year + years;
  const lastDay = new Date(Date.UTC(targetYear, month, 0)).getUTCDate();
  return `${targetYear}-${String(month).padStart(2, "0")}-${String(Math.min(day, lastDay)).padStart(2, "0")}`;
};
export const clampIsoDate = (value: string, minDate: string, maxDate: string) => value < minDate ? minDate : value > maxDate ? maxDate : value;

export const formatDateTime = (value?: string | null) => {
  if (!value) return "—";
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value)
    ? `${value.replace(" ", "T")}Z`
    : value;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
};
export const formatChange = (current = 0, previous = 0) => {
  if (previous === 0) return current === 0 ? "0.0%" : "新增";
  return `${(((current - previous) / Math.abs(previous)) * 100).toFixed(1)}%`;
};
export const comparisonHint = (current = 0, previous = 0, yearAgo = 0) => (
  <><span>同比 {formatChange(current, yearAgo)}</span><span>环比 {formatChange(current, previous)}</span></>
);
export const issueText = (issue: ImportIssue) =>
  issue.sourceRowNumber || issue.row
    ? `第 ${issue.sourceRowNumber ?? issue.row} 行：${issue.message}`
    : issue.message;

export function Dot({ tone = "blue" }: { tone?: string }) {
  return <span className={`dot dot-${tone}`} aria-hidden="true" />;
}

export function MetricCard({
  label,
  value,
  change,
  hint,
  tone = "blue",
}: {
  label: string;
  value: string;
  change: string;
  hint: React.ReactNode;
  tone?: string;
}) {
  const down = change.startsWith("-");
  return (
    <article className="metric-card">
      <div className="metric-top">
        <span className={`metric-icon metric-icon-${tone}`}><Dot tone={tone} /></span>
        <span className={`change ${down ? "change-down" : ""}`}>{change} {down ? "↘" : "↗"}</span>
      </div>
      <p>{label}</p>
      <strong>{value}</strong>
      <small>{hint}</small>
    </article>
  );
}

export function SectionHeader({ title, note, action }: { title: string; note?: string; action?: string }) {
  return (
    <div className="section-header">
      <div>
        <h2>{title}</h2>
        {note && <p>{note}</p>}
      </div>
      {action && <button className="text-button">{action} <span>→</span></button>}
    </div>
  );
}
