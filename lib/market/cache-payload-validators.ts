type UnknownRecord = Record<string, unknown>;
type ValueValidator = (value: unknown) => boolean;

const stringValue: ValueValidator = (value) => typeof value === "string";
const booleanValue: ValueValidator = (value) => typeof value === "boolean";
const numberValue: ValueValidator = (value) => typeof value === "number" && Number.isFinite(value);
const nullableString: ValueValidator = (value) => value === null || stringValue(value);
const nullableNumber: ValueValidator = (value) => value === null || numberValue(value);

function record(value: unknown): value is UnknownRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function fields(value: unknown, shape: Record<string, ValueValidator>, exact = false): value is UnknownRecord {
  if (!record(value)) return false;
  if (exact) {
    const expected = Object.keys(shape).sort();
    const actual = Object.keys(value).sort();
    if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) return false;
  }
  return Object.entries(shape).every(([key, validate]) => Object.hasOwn(value, key) && validate(value[key]));
}

function arrayOf(validate: ValueValidator): ValueValidator {
  return (value) => Array.isArray(value) && value.every(validate);
}

const stringArray = arrayOf(stringValue);

const filterOption: ValueValidator = (value) => fields(value, {
  value: stringValue,
  count: numberValue,
}, true);

const marketItem: ValueValidator = (value) => fields(value, {
  id: numberValue,
  periodStart: stringValue,
  periodEnd: stringValue,
  category: stringValue,
  scope: stringValue,
  rankingDimension: (item) => item === "SKU" || item === "SPU",
  operationMode: (item) => item === "POP" || item === "自营" || item === "未知",
  subcategory: stringValue,
  rank: nullableNumber,
  previousRank: nullableNumber,
  rankChange: nullableNumber,
  skuCode: stringValue,
  productName: stringValue,
  brand: stringValue,
  priceCents: nullableNumber,
  marketPriceCents: nullableNumber,
  candidatePriceCents: nullableNumber,
  marketPriceSource: stringValue,
  candidatePriceSource: stringValue,
  averageTransactionPriceCents: nullableNumber,
  discountBps: nullableNumber,
  discountReference: booleanValue,
  gmvCents: numberValue,
  quantity: numberValue,
  pageViews: numberValue,
  visitors: numberValue,
  conversionBps: nullableNumber,
  cartCustomers: numberValue,
  searchClicks: numberValue,
  imageUrl: stringValue,
  sourceImageUrl: stringValue,
  imageCacheStatus: stringValue,
  productUrl: stringValue,
  periodCount: numberValue,
  isOwn: booleanValue,
  ownSalesCents: numberValue,
  gmvOutOfBand: booleanValue,
});

const trendPoint: ValueValidator = (value) => fields(value, {
  period: stringValue,
  gmv_cents: numberValue,
  quantity: numberValue,
  visitors: numberValue,
  product_count: numberValue,
  brand_count: numberValue,
  pop_gmv_cents: numberValue,
  self_gmv_cents: numberValue,
  average_transaction_price_cents: nullableNumber,
  weighted_market_price_cents: nullableNumber,
});

const priceBandSummary: ValueValidator = (value) => fields(value, {
  priceBand: stringValue,
  gmvCents: numberValue,
  quantity: numberValue,
  skuCount: numberValue,
  popGmvCents: numberValue,
  selfGmvCents: numberValue,
  gmvShareBps: numberValue,
  selfOperatedShareBps: nullableNumber,
  mainBrands: stringArray,
});

const priceBandTrend: ValueValidator = (value) => fields(value, {
  period: stringValue,
  priceBand: stringValue,
  gmvCents: numberValue,
  quantity: numberValue,
  gmvShareBps: numberValue,
});

const growthFields = {
  latestPeriod: nullableString,
  monthOverMonthBps: nullableNumber,
  yearOverYearBps: nullableNumber,
} satisfies Record<string, ValueValidator>;

const brandItem: ValueValidator = (value) => fields(value, {
  brand: stringValue,
  gmvCents: numberValue,
  quantity: numberValue,
  skuCount: numberValue,
  bestRank: nullableNumber,
  gmvShareBps: numberValue,
  heroSkuGmvCents: numberValue,
  heroSkuShareBps: numberValue,
  priceBands: stringArray,
  subcategories: stringArray,
  ...growthFields,
});

const subcategoryItem: ValueValidator = (value) => fields(value, {
  subcategory: stringValue,
  skuCount: numberValue,
  gmvCents: numberValue,
  gmvShareBps: numberValue,
  quantity: numberValue,
  averageTransactionPriceCents: nullableNumber,
  selfOperatedShareBps: nullableNumber,
  pendingSkuCount: numberValue,
  mainBrands: stringArray,
  mainPriceBands: stringArray,
  ...growthFields,
});

const lifecyclePoint: ValueValidator = (value) => fields(value, {
  period: stringValue,
  entryCount: nullableNumber,
  exitCount: nullableNumber,
});

const operationModePoint: ValueValidator = (value) => fields(value, {
  operationMode: stringValue,
  gmvCents: numberValue,
  quantity: numberValue,
  skuCount: numberValue,
  visitors: numberValue,
  conversionBps: nullableNumber,
  brandCount: numberValue,
  gmvShareBps: numberValue,
  averageTransactionPriceCents: nullableNumber,
  gmvPerSkuCents: numberValue,
});

const brandConcentrationPoint: ValueValidator = (value) => fields(value, {
  period: stringValue,
  gmvCents: numberValue,
  brandCount: numberValue,
  cr3Bps: numberValue,
  cr5Bps: numberValue,
});

const trafficExample: ValueValidator = (value) => fields(value, {
  skuCode: stringValue,
  productName: stringValue,
  gmvCents: numberValue,
});

const trafficQuadrant: ValueValidator = (value) => fields(value, {
  quadrant: (item) => [
    "high_traffic_high_conversion",
    "high_traffic_low_conversion",
    "low_traffic_high_conversion",
    "low_traffic_low_conversion",
  ].includes(String(item)),
  productCount: numberValue,
  gmvCents: numberValue,
  quantity: numberValue,
  visitors: numberValue,
  conversionBps: nullableNumber,
  visitorThreshold: numberValue,
  conversionThresholdBps: numberValue,
  examples: arrayOf(trafficExample),
});

const productSignal: ValueValidator = (value) => fields(value, {
  group: stringValue,
  label: stringValue,
  count: numberValue,
  shareBps: numberValue,
  examples: stringArray,
});

const opportunity: ValueValidator = (value) => fields(value, {
  subcategory: stringValue,
  priceBand: stringValue,
  scenario: stringValue,
  gmvCents: numberValue,
  quantity: numberValue,
  skuCount: numberValue,
  visitors: numberValue,
  conversionBps: nullableNumber,
  brandCount: numberValue,
  gmvShareBps: numberValue,
  growthBps: nullableNumber,
  selfOperatedShareBps: numberValue,
  pendingPriceShareBps: numberValue,
  score: numberValue,
  recommendation: (item) => item === "建议进入" || item === "持续观察" || item === "谨慎回避",
  reasons: stringArray,
  decisionReady: booleanValue,
});

const externalDataGap: ValueValidator = (value) => fields(value, {
  key: stringValue,
  label: stringValue,
  status: stringValue,
  note: stringValue,
});

const industryReport: ValueValidator = (value) => fields(value, {
  definition: (item) => fields(item, {
    title: stringValue,
    metricScope: stringValue,
    profile: (profile) => fields(profile, {
      category: stringValue,
      coreSubcategories: stringArray,
      adjacentSubcategories: stringArray,
      adjacentCategories: stringArray,
    }),
    selectedCategories: stringArray,
    selectedScopes: stringArray,
    selectedRankingDimensions: stringArray,
  }),
  period: (item) => fields(item, {
    coverageMonths: numberValue,
    latestPeriod: nullableString,
    latestGmvCents: numberValue,
    monthOverMonthBps: nullableNumber,
    yearOverYearBps: nullableNumber,
    peak: (point) => point === null || fields(point, { period: stringValue, gmvCents: numberValue }),
    trough: (point) => point === null || fields(point, { period: stringValue, gmvCents: numberValue }),
    latestEntryCount: nullableNumber,
    latestExitCount: nullableNumber,
    latestExitPeriod: nullableString,
  }),
  lifecycle: arrayOf(lifecyclePoint),
  operationModes: arrayOf(operationModePoint),
  brandConcentrationTrend: arrayOf(brandConcentrationPoint),
  trafficQuadrants: arrayOf(trafficQuadrant),
  productSignals: (item) => fields(item, {
    sampleSize: numberValue,
    source: stringValue,
    signals: arrayOf(productSignal),
  }),
  opportunities: arrayOf(opportunity),
  dataQuality: (item) => fields(item, {
    categoryCount: numberValue,
    scopeCount: numberValue,
    rankingDimensionCount: numberValue,
    operationModeCount: numberValue,
    unknownBrandSkuCount: numberValue,
    unclassifiedSkuCount: numberValue,
    pendingPriceSkuCount: numberValue,
    identityReady: booleanValue,
    coverageReady: booleanValue,
    comparisonReady: booleanValue,
    warnings: stringArray,
  }),
  externalDataGaps: arrayOf(externalDataGap),
});

const marketBatchWarning: ValueValidator = (value) => {
  if (!record(value) || typeof value.message !== "string") return false;
  return (value.row === undefined || numberValue(value.row))
    && (value.field === undefined || stringValue(value.field));
};

const marketBatch: ValueValidator = (value) => fields(value, {
  id: stringValue,
  sourceType: stringValue,
  fileName: stringValue,
  fileSizeBytes: numberValue,
  fileHash: stringValue,
  sheetName: stringValue,
  status: stringValue,
  rowCount: numberValue,
  insertedCount: numberValue,
  updatedCount: numberValue,
  warningCount: numberValue,
  periodStart: nullableString,
  periodEnd: nullableString,
  warnings: arrayOf(marketBatchWarning),
  createdAt: stringValue,
  completedAt: nullableString,
});

export function validateMarketOverviewCachePayload(value: unknown, expectedView: "ranking" | "full") {
  const topLevel = {
    view: (item: unknown) => item === expectedView,
    salesRevision: stringValue,
    summary: (item: unknown) => fields(item, {
      productCount: numberValue,
      categoryCount: numberValue,
      brandCount: numberValue,
      gmvCents: numberValue,
      quantity: numberValue,
      pageViews: numberValue,
      visitors: numberValue,
      ownProductCount: numberValue,
      activeSkuCount: numberValue,
      pendingAiCount: numberValue,
      selfOperatedGmvCents: numberValue,
      selfOperatedShareBps: nullableNumber,
      medianMarketPriceCents: nullableNumber,
      weightedMarketPriceCents: nullableNumber,
      averageTransactionPriceCents: nullableNumber,
    }),
    items: arrayOf(marketItem),
    pagination: (item: unknown) => fields(item, {
      page: numberValue,
      pageSize: numberValue,
      total: numberValue,
      pageCount: numberValue,
    }),
    trend: arrayOf(trendPoint),
    trendTotal: numberValue,
    trendTruncated: booleanValue,
    priceBands: arrayOf(filterOption),
    priceBandSummary: arrayOf(priceBandSummary),
    priceBandTrend: arrayOf(priceBandTrend),
    brandAnalysis: (item: unknown) => fields(item, {
      items: arrayOf(brandItem),
      cr3Bps: numberValue,
      cr5Bps: numberValue,
      concentration: stringValue,
    }),
    subcategorySummary: arrayOf(subcategoryItem),
    industryReport,
    filters: (item: unknown) => fields(item, {
      categories: arrayOf(filterOption),
      scopes: arrayOf(filterOption),
      brands: arrayOf(filterOption),
      rankingDimensions: arrayOf(filterOption),
      operationModes: arrayOf(filterOption),
      subcategories: arrayOf(filterOption),
      priceBands: arrayOf(filterOption),
    }),
    dataRange: (item: unknown) => fields(item, {
      startDate: nullableString,
      endDate: nullableString,
    }),
    batches: arrayOf(marketBatch),
    imageCache: (item: unknown) => fields(item, {
      total: numberValue,
      cached: numberValue,
      failed: numberValue,
      pending: numberValue,
    }),
  } satisfies Record<string, ValueValidator>;
  return fields(value, topLevel, true);
}

const filterOptionsShape = {
  categories_json: stringValue,
  scopes_json: stringValue,
  brands_json: stringValue,
  dimensions_json: stringValue,
  modes_json: stringValue,
  subcategories_json: stringValue,
} satisfies Record<string, ValueValidator>;

export function validateMarketFilterOptionsCachePayload(value: unknown) {
  if (!fields(value, filterOptionsShape, true)) return false;
  return Object.keys(filterOptionsShape).every((key) => {
    try {
      return arrayOf(filterOption)(JSON.parse(String(value[key])));
    } catch {
      return false;
    }
  });
}

const systemKpiShape = {
  marketIdentityTotal: numberValue,
  pendingPriceCount: numberValue,
  pendingAiCount: numberValue,
  completedAiCount: numberValue,
  sameImageReuseCount: numberValue,
  priceOnlyRecognitionCount: numberValue,
  fullRecognitionCount: numberValue,
  blockedRecognitionCount: numberValue,
} satisfies Record<string, ValueValidator>;

export function validateMarketSystemKpiCachePayload(value: unknown) {
  return fields(value, systemKpiShape, true);
}
