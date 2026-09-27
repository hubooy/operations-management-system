// Offline migration/rehearsal implementation. Production callers use normalized-import.
import { auditRejectedImportResult, buildImportAttemptHash, buildImportContentFingerprint, ensureImportFingerprintSchema, failImportFingerprint, nextImportScopeStateToken, readImportScopeStateToken, recordImportFingerprint, renewImportFingerprintReservation, reserveImportFingerprint } from "@/lib/imports/content-fingerprint";
import type { NetshopRowInput } from "@/lib/netshop/database";
import { netshopBatchId, netshopMasterRowKey } from "@/lib/netshop/batch-identity";
import { dailyDateCoverage, dailyRowKey } from "@/lib/netshop/daily-contract";
import { extractTmallProductAssetImages, persistTmallProductAssetImages, type TmallProductAssetImage } from "@/lib/netshop/product-image-assets";
import { resolveEnabledTmallShop } from "@/lib/netshop/tmall-store-catalog";
import { DEFAULT_PLATFORM, DEFAULT_SHOP_NAME, TMALL_PLATFORM, MAX_TABULAR_ROWS, type NetshopImportExecution, type ParsedTable, type ParsedHeader, toHex, sha256, safeFileName, normalizeText, parseFile, objectFromRow, findTmallHeader, tmallRowObject, tmallMetrics, isoDateFromValue, fileDate, findValue, firstDate, isDailyAggregateRow, prepareTmallPromotionRows, prepareTmallProductAssetRows, detectDataset, warehouseType, usesSnapshotDate, isTmallSource, tmallMasterRowKey, tmallProductAssetRowKey, metricsFromRow, hashText, validateRows, netshopBusinessContentRows, type NetshopImportInput, type NetshopImportDatabaseDependencies, type NetshopImportFingerprintDependencies, type NetshopProductAssetDependencies } from "./normalized-import";
export * from "./normalized-import";

export async function importNetshopBytes(
  input: NetshopImportInput,
  databaseDependencies?: NetshopImportDatabaseDependencies,
  fingerprintDependencies: NetshopImportFingerprintDependencies = {
    auditRejectedImportResult,
    buildImportAttemptHash,
    buildImportContentFingerprint,
    ensureImportFingerprintSchema,
    failImportFingerprint,
    nextImportScopeStateToken,
    readImportScopeStateToken,
    recordImportFingerprint,
    renewImportFingerprintReservation,
    reserveImportFingerprint,
  },
  productAssetDependencies: NetshopProductAssetDependencies = {
    persistTmallProductAssetImages,
  },
): Promise<NetshopImportExecution> {
  const {
    ensureNetshopSchema,
    findNetshopImportBatchById,
    getNetshopDatabase,
    reconcileNetshopMasterProducts,
    sanitizeNetshopIssues,
    saveNetshopImport,
    readNetshopScopeOwnership,
    readNetshopScopeRows,
    verifyNetshopImportBatch,
  } = databaseDependencies ?? await import("@/lib/netshop/database");
  const rawFileHash = toHex(await sha256(input.bytes));
  const db = getNetshopDatabase();
  await ensureNetshopSchema(db);
  await fingerprintDependencies.ensureImportFingerprintSchema(db);
  const reject = (result: NetshopImportExecution) => fingerprintDependencies.auditRejectedImportResult(db, {
    domain: "netshop",
    rawFileHash,
    scopeHint: {
      source: input.source,
      platform: input.platform?.trim() || null,
      shopName: input.shopName?.trim() || null,
      snapshotDate: input.snapshotDate?.trim() || null,
      startDate: input.expectedStartDate?.trim() || null,
      endDate: input.expectedEndDate?.trim() || null,
    },
    metadata: { fileName: input.fileName, fileSizeBytes: input.fileSizeBytes },
  }, result);

  let parsed: ParsedTable;
  let assetImages: ReadonlyMap<number, TmallProductAssetImage> | undefined;
  try {
    if (input.source === "tmall_product_master" && !/\.xlsx$/i.test(input.fileName)) throw new Error("天猫货品主数据只接受 .xlsx 文件");
    if (input.source === "tmall_product_assets" && !/\.xlsx$/i.test(input.fileName)) throw new Error("天猫 SPU 商品图只接受 .xlsx 文件");
    if (input.source === "tmall_product_daily" && !/\.xls$/i.test(input.fileName)) throw new Error("生意参谋商品日数据只接受二进制 .xls 文件");
    if (input.source === "tmall_promotion" && !/\.zip$/i.test(input.fileName)) throw new Error("天猫推广数据只接受包含单个 CSV 的 .zip 文件");
    parsed = parseFile(input.bytes, input.fileName, input.source);
    if (input.source === "tmall_product_assets") assetImages = await extractTmallProductAssetImages(input.bytes);
  } catch (error) {
    const message = error instanceof Error ? error.message : "文件解析失败";
    return reject({ ok: false, status: "rejected", message, warnings: [], errors: [{ code: "PARSE_ERROR", message }], errorCount: 1 });
  }

  let header: ParsedHeader;
  try {
    header = findTmallHeader(input.source, parsed, input.expectedStartDate, input.expectedEndDate);
  } catch (error) {
    const message = error instanceof Error ? error.message : "表头识别失败";
    return reject({ ok: false, status: "rejected", message, warnings: [], errors: [{ code: "HEADER_NOT_FOUND", message }], errorCount: 1 });
  }

  let dataset: string;
  try {
    dataset = detectDataset(input.source, input.fileName, header.headers);
  } catch (error) {
    const message = error instanceof Error ? error.message : "数据集识别失败";
    return reject({ ok: false, status: "rejected", message, warnings: [], errors: [{ code: "DATASET_HEADER_MISMATCH", message }], errorCount: 1 });
  }
  if (input.source === "jd_sku_daily" && input.expectedDataset && input.expectedDataset !== dataset) {
    const message = `上传文件数据集为 ${dataset}，与预期 ${input.expectedDataset} 不一致`;
    return reject({ ok: false, status: "rejected", message, warnings: [], errors: [{ code: "EXPECTED_DATASET_MISMATCH", message }], errorCount: 1 });
  }
  let platform: string;
  let shopName: string;
  try {
    platform = isTmallSource(input.source) ? TMALL_PLATFORM : normalizeText(input.platform) || DEFAULT_PLATFORM;
    shopName = isTmallSource(input.source)
      ? resolveEnabledTmallShop(input.shopName).shopName
      : normalizeText(input.shopName) || DEFAULT_SHOP_NAME;
  } catch (error) {
    const message = error instanceof Error ? error.message : "天猫店铺身份无效";
    return reject({ ok: false, status: "rejected", message, warnings: [], errors: [{ code: "TMALL_SHOP_NOT_ALLOWED", message }], errorCount: 1 });
  }
  const snapshotDate = input.source === "tmall_product_master" || input.source === "tmall_product_assets"
    ? isoDateFromValue(input.snapshotDate)
    : isoDateFromValue(input.snapshotDate) || fileDate(input.fileName) || "";
  const snapshotSource = usesSnapshotDate(input.source);
  if (snapshotSource && !snapshotDate) {
    const message = `${input.source} 快照导入必须提供有效 snapshot_date=YYYY-MM-DD`;
    return reject({ ok: false, status: "rejected", message, warnings: [], errors: [{ code: "MISSING_SNAPSHOT_DATE", message }], errorCount: 1 });
  }
  const parsedRawRows = parsed.rows.slice(header.index + 1)
    .map((row) => ({
      rowNumber: row.rowNumber,
      raw: isTmallSource(input.source)
        ? tmallRowObject(input.source, header.headers, row.values, header.businessDateFallback)
        : objectFromRow(header.headers, row.values),
    }))
    .filter((row) => Object.values(row.raw).some((value) => normalizeText(value)))
    .filter((row) => !isDailyAggregateRow(input.source, row.raw));
  const rawRows = input.source === "tmall_promotion" ? prepareTmallPromotionRows(parsedRawRows) : parsedRawRows;
  if (rawRows.length > MAX_TABULAR_ROWS) {
    const message = `单次最多导入 ${MAX_TABULAR_ROWS} 行`;
    return reject({ ok: false, status: "rejected", message, warnings: [], errors: [{ code: "ROW_LIMIT_EXCEEDED", message }], errorCount: 1 });
  }
  const sourceErrors = input.source === "tmall_product_assets"
    ? prepareTmallProductAssetRows(rawRows, assetImages, shopName)
    : [];

  const rows: NetshopRowInput[] = [];
  for (const row of rawRows) {
    // 商品 SKU 导出中的“创建时间”属于商品主数据，不是经营发生日期。
    // 将它当作业务日期会把商品目录错误地混入日度经营口径。
    const isProductMaster = input.source === "jd_product_master" || input.source === "tmall_product_master" || input.source === "tmall_product_assets";
    const businessDate = isProductMaster ? "" : firstDate(row.raw);
    const tmallSpuId = input.source === "tmall_product_master" || input.source === "tmall_product_assets"
      ? normalizeText(row.raw["商品ID"])
      : input.source === "tmall_product_daily"
        ? normalizeText(row.raw["商品ID"])
        : input.source === "tmall_promotion"
          ? normalizeText(row.raw["主体ID"])
          : "";
    const productCode = input.source === "tmall_product_assets"
      ? ""
      : input.source === "tmall_product_master"
      ? normalizeText(row.raw["SKU商家编码"] ?? row.raw["商品商家编码"])
      : input.source === "tmall_product_daily"
        ? normalizeText(row.raw["货号"])
      : input.source === "jd_product_master"
      ? normalizeText(row.raw["商品编码"])
      : normalizeText(findValue(row.raw, [/货品编号/, /商品编码/, /商品编号/, /商品ID/i, /^sku$/i, /skuid/i]));
    const skuId = input.source === "tmall_product_master"
      ? normalizeText(row.raw["SKUID"])
      : normalizeText(findValue(row.raw, [/^sku$/i, /sku.?id/i, /skuid/i, /SKU编码/i]));
    const spuId = tmallSpuId || (input.source === "jd_product_master"
      ? productCode
      : normalizeText(findValue(row.raw, [/^spu$/i, /spu.?id/i, /spuid/i, /SPU编码/i])));
    const productName = input.source === "tmall_product_assets"
      ? normalizeText(row.raw["商品名称"])
      : input.source === "tmall_product_master"
      ? normalizeText(row.raw["商品标题"])
      : input.source === "tmall_promotion"
        ? normalizeText(row.raw["主体名称"])
        : normalizeText(findValue(row.raw, [/商品名称/, /货品名称/, /产品名称/, /名称/]));
    const rawJson = JSON.stringify(row.raw);
    const rowHash = await hashText(rawJson);
    const sourceRowKey = input.source === "tmall_product_assets"
      ? tmallProductAssetRowKey({ dataset, platform, shopName, snapshotDate, spuId })
      : input.source === "tmall_product_master"
      ? tmallMasterRowKey({
          dataset,
          platform,
          shopName,
          snapshotDate,
          spuId,
          skuId,
          saleAttribute: normalizeText(row.raw["销售属性"]),
          rowNumber: row.rowNumber,
        })
      : dataset === "sku_daily" || dataset === "spu_daily" || dataset === "promotion_daily"
        ? dailyRowKey(dataset, platform, shopName, businessDate, dataset === "sku_daily" ? skuId : spuId)
      : netshopMasterRowKey({ source: input.source, platform, shopName, fileHash: rawFileHash, rowNumber: row.rowNumber, rowHash });
    rows.push({
      sourceRowNumber: row.rowNumber,
      sourceRowKey,
      sourceRowHash: rowHash,
      source: input.source,
      dataset,
      platform,
      shopName,
      businessDate,
      snapshotDate: snapshotSource ? snapshotDate : "",
      productCode,
      productName,
      skuId,
      spuId,
      warehouseType: input.source === "inv_selfop" ? warehouseType(input.fileName, row.raw) : "",
      metrics: isTmallSource(input.source) ? tmallMetrics(input.source, row.raw) : metricsFromRow(row.raw),
      raw: row.raw,
    });
  }

  const errors = sourceErrors.length > 0 ? sourceErrors : validateRows(rows);
  if (input.source === "tmall_promotion") {
    for (const row of rawRows) {
      if (normalizeText(row.raw["主体类型"]) !== "商品") {
        errors.push({ row: row.rowNumber, field: "主体类型", code: "UNSUPPORTED_SUBJECT_TYPE", message: "推广导入当前只接受主体类型为“商品”的数据" });
      }
    }
  }
  if (input.source === "jd_sku_daily" || input.source === "tmall_product_daily" || input.source === "tmall_promotion") {
    const coverage = dailyDateCoverage(input.expectedStartDate, input.expectedEndDate, rows.map((row) => row.businessDate));
    if (!coverage.validRange) {
      errors.push({ code: "MISSING_EXPECTED_DATE_RANGE", message: "分天导入必须提供有效的目标起止日期" });
    } else {
      if (coverage.missingDates.length) errors.push({ code: "MISSING_EXPECTED_DATES", message: `目标区间缺少日期：${coverage.missingDates.join(", ")}` });
      if (coverage.outOfRangeDates.length) errors.push({ code: "OUT_OF_RANGE_DATES", message: `文件包含目标区间外日期：${coverage.outOfRangeDates.join(", ")}` });
    }
  }
  if (errors.length > 0) {
    return reject({ ok: false, status: "rejected", message: "文件校验未通过，未写入数据", warnings: [], errors, errorCount: errors.length });
  }

  const dateValues = rows.map((row) => row.businessDate).filter(Boolean);
  const sortedDates = [...dateValues].sort();
  const expectedVerification = {
    rowCount: rows.length,
    dataset,
    platform,
    shopName,
    dateMin: sortedDates[0] ?? null,
    dateMax: sortedDates[sortedDates.length - 1] ?? null,
  };
  const fingerprintScope = {
    source: input.source,
    dataset,
    platform,
    shopName,
    snapshotDate: snapshotSource ? snapshotDate : null,
    startDate: input.expectedStartDate ?? sortedDates[0] ?? null,
    endDate: input.expectedEndDate ?? sortedDates[sortedDates.length - 1] ?? null,
  };
  const fingerprintLockScope = { source: input.source, dataset, platform, shopName };
  const fingerprint = await fingerprintDependencies.buildImportContentFingerprint({
    domain: "netshop",
    scope: fingerprintScope,
    lockScope: fingerprintLockScope,
    rows: netshopBusinessContentRows(rows),
  });
  const allRowsHaveBusinessDate = !snapshotSource && rows.every((row) => Boolean(row.businessDate));
  const replaceStartDate = allRowsHaveBusinessDate ? input.expectedStartDate ?? sortedDates[0] ?? null : null;
  const replaceEndDate = allRowsHaveBusinessDate ? input.expectedEndDate ?? sortedDates[sortedDates.length - 1] ?? null : null;
  const replaceFullScope = !snapshotSource && (!replaceStartDate || !replaceEndDate);
  const scopeOwnership = await readNetshopScopeOwnership(db, {
      source: input.source,
      dataset,
      platform,
      shopName,
      startDate: replaceStartDate,
      endDate: replaceEndDate,
      snapshotDate: snapshotSource ? snapshotDate : null,
      fullScope: replaceFullScope,
    });
  const currentStateToken = await fingerprintDependencies.readImportScopeStateToken(db, fingerprint);
  const currentBatchId = scopeOwnership.length === 1 && scopeOwnership[0]?.rowCount === rows.length
    ? scopeOwnership[0].batchId
    : null;
  const currentBatch = currentBatchId ? await findNetshopImportBatchById(db, currentBatchId) : null;
  const currentTotals = currentBatch?.totals as { contentHash?: unknown; rawFileHash?: unknown } | null;
  if (currentBatch?.status === "completed" && currentTotals?.contentHash === fingerprint.contentHash) {
    const currentRows = await readNetshopScopeRows(db, {
      source: input.source,
      dataset,
      platform,
      shopName,
      startDate: replaceStartDate,
      endDate: replaceEndDate,
      snapshotDate: snapshotSource ? snapshotDate : null,
      fullScope: replaceFullScope,
    });
    const currentFingerprint = await fingerprintDependencies.buildImportContentFingerprint({
      domain: "netshop",
      scope: fingerprintScope,
      lockScope: fingerprintLockScope,
      rows: netshopBusinessContentRows(currentRows),
    });
    const verification = currentFingerprint.contentHash === fingerprint.contentHash
      ? await verifyNetshopImportBatch(db, currentBatch, expectedVerification)
      : null;
    if (verification?.verified) {
      let imagePersistence: Awaited<ReturnType<typeof persistTmallProductAssetImages>> | null = null;
      if (input.source === "tmall_product_assets") {
        try {
          imagePersistence = await productAssetDependencies.persistTmallProductAssetImages([...(assetImages?.values() ?? [])]);
        } catch (error) {
          const message = error instanceof Error ? error.message : "商品图片存储回查失败";
          return reject({ ok: false, status: "rejected", message, warnings: [], errors: [{ code: "PRODUCT_IMAGE_PERSIST_FAILED", message }], errorCount: 1 });
        }
      }
      const duplicateStateToken = await fingerprintDependencies.readImportScopeStateToken(db, fingerprint);
      const duplicateOwnership = await readNetshopScopeOwnership(db, {
        source: input.source,
        dataset,
        platform,
        shopName,
        startDate: replaceStartDate,
        endDate: replaceEndDate,
        snapshotDate: snapshotSource ? snapshotDate : null,
        fullScope: replaceFullScope,
      });
      if (duplicateStateToken !== currentStateToken
        || duplicateOwnership.length !== 1
        || duplicateOwnership[0]?.batchId !== currentBatch.id
        || duplicateOwnership[0]?.rowCount !== rows.length) {
        return reject({
          ok: false,
          status: "rejected",
          message: "商品图片回查期间同一网店业务范围已更新，请重新提交最新文件",
          warnings: [],
          errors: [{ code: "IMPORT_SCOPE_CHANGED", message: "重复导入确认前当前业务范围版本已变化" }],
          errorCount: 1,
        });
      }
      await fingerprintDependencies.recordImportFingerprint(db, {
        ...fingerprint,
        batchId: currentBatch.id,
        importHash: currentBatch.fileHash,
        rawFileHash,
        publishedStateToken: currentStateToken,
        metadata: { fileName: input.fileName, fileSizeBytes: input.fileSizeBytes, warnings: currentBatch.warnings },
        outcome: "duplicate",
      });
      const duplicateRepairStateToken = await fingerprintDependencies.nextImportScopeStateToken({
        previousStateToken: currentStateToken,
        batchId: currentBatch.id,
        contentHash: fingerprint.contentHash,
        rowCount: rows.length,
      });
      const recordedStateToken = await fingerprintDependencies.readImportScopeStateToken(db, fingerprint);
      const recordedOwnership = await readNetshopScopeOwnership(db, {
        source: input.source,
        dataset,
        platform,
        shopName,
        startDate: replaceStartDate,
        endDate: replaceEndDate,
        snapshotDate: snapshotSource ? snapshotDate : null,
        fullScope: replaceFullScope,
      });
      if (![currentStateToken, duplicateRepairStateToken].includes(recordedStateToken)
        || recordedOwnership.length !== 1
        || recordedOwnership[0]?.batchId !== currentBatch.id
        || recordedOwnership[0]?.rowCount !== rows.length) {
        return reject({
          ok: false,
          status: "rejected",
          message: "重复导入确认期间同一网店业务范围已更新，请重新提交最新文件",
          warnings: [],
          errors: [{ code: "IMPORT_SCOPE_CHANGED", message: "重复导入审计完成前当前业务范围版本已变化" }],
          errorCount: 1,
        });
      }
      const unmatchedProductCount = Number((currentBatch.totals as { unmatchedProductCount?: number } | null)?.unmatchedProductCount ?? 0);
      return {
        ok: true,
        status: "duplicate",
        message: "全部标准化业务资料与当前数据一致，无需重复导入",
        batch: currentBatch,
        warnings: currentBatch.warnings,
        verification: {
          ...verification,
          unmatchedProductCount,
          ...(imagePersistence ? { imageCount: imagePersistence.total, verifiedImageCount: imagePersistence.verified } : {}),
        },
      };
    }
  }
  const fileHash = await fingerprintDependencies.buildImportAttemptHash({
    fingerprint,
    currentStateToken,
  });
  const reservedBatchId = netshopBatchId({ source: input.source, platform, shopName, fileHash });
  const missingDateRows = rows.filter((row) => !row.businessDate && !snapshotSource).length;
  const missingSnapshotRows = rows.filter((row) => snapshotSource && !row.snapshotDate).length;
  const missingSkuRows = input.source === "tmall_product_master" ? rows.filter((row) => !row.skuId).length : 0;
  const missingSkuMerchantCodeRows = input.source === "tmall_product_master"
    ? rows.filter((row) => !normalizeText(row.raw["SKU商家编码"])).length
    : 0;
  const merchantCodeCounts = new Map<string, number>();
  if (input.source === "tmall_product_master") {
    for (const row of rows) {
      const merchantSkuCode = normalizeText(row.raw["SKU商家编码"]);
      if (merchantSkuCode) merchantCodeCounts.set(merchantSkuCode, (merchantCodeCounts.get(merchantSkuCode) ?? 0) + 1);
    }
  }
  const duplicateMerchantCodeRows = [...merchantCodeCounts.values()].filter((count) => count > 1).reduce((sum, count) => sum + count, 0);
  const reconciliationProductIds = [...new Set(rows.map((row) => row.spuId.trim()).filter(Boolean))];
  const reconciliation = input.source === "tmall_product_daily" || input.source === "tmall_promotion"
    ? await reconcileNetshopMasterProducts(db, { platform, shopName, productIds: reconciliationProductIds })
    : { masterAvailable: true, unmatchedCount: 0, unmatchedSample: [] as string[] };
  if ((input.source === "tmall_product_daily" || input.source === "tmall_promotion")
    && reconciliation.masterAvailable
    && reconciliationProductIds.length >= 5
    && reconciliation.unmatchedCount === reconciliationProductIds.length) {
    return reject({
      ok: false,
      status: "rejected",
      message: "报表商品与该店铺最新货品主数据零交集，疑似账号或店铺上下文不一致，已阻止导入",
      warnings: [],
      errors: [{
        code: "MASTER_IDENTITY_MISMATCH",
        message: `${reconciliationProductIds.length} 个商品ID均未匹配该店铺最新货品主数据`,
      }],
      errorCount: 1,
    });
  }
  const warnings = sanitizeNetshopIssues([
    ...(missingDateRows > 0 ? [{ code: "MISSING_BUSINESS_DATE", message: `${missingDateRows} 行未识别到业务日期，overview 不会把这些行计入 date_max` }] : []),
    ...(missingSnapshotRows > 0 ? [{ code: "MISSING_SNAPSHOT_DATE", message: `${missingSnapshotRows} 行未识别到快照日期，请在上传时传 snapshot_date=YYYY-MM-DD` }] : []),
    ...(missingSkuRows > 0 ? [{ code: "MISSING_SKU_ID", message: `${missingSkuRows} 行缺少 SKU ID，已使用商品ID、销售属性和源行号构造隔离键` }] : []),
    ...(missingSkuMerchantCodeRows > 0 ? [{ code: "MISSING_MERCHANT_SKU_CODE", message: `${missingSkuMerchantCodeRows} 行缺少 SKU 商家编码；该字段仅保留作业务映射，不参与唯一键` }] : []),
    ...(duplicateMerchantCodeRows > 0 ? [{ code: "DUPLICATE_MERCHANT_CODE", message: `${duplicateMerchantCodeRows} 行使用重复 SKU 商家编码；商家编码仅作映射，不作为主键` }] : []),
    ...(!reconciliation.masterAvailable ? [{ code: "MASTER_DATA_UNAVAILABLE", message: "尚无该店铺货品主数据，商品日数据已保留，暂无法核验商品匹配" }] : []),
    ...(reconciliation.masterAvailable && reconciliation.unmatchedCount > 0 ? [{ code: "UNMATCHED_MASTER_PRODUCTS", message: `${reconciliation.unmatchedCount} 个商品ID未匹配最新货品主数据；样例：${reconciliation.unmatchedSample.join("、")}` }] : []),
  ]);

  const reservation = await fingerprintDependencies.reserveImportFingerprint(db, {
    ...fingerprint,
    batchId: reservedBatchId,
    importHash: fileHash,
    rawFileHash,
    currentStateToken,
    metadata: { fileName: input.fileName, fileSizeBytes: input.fileSizeBytes, warnings },
  });
  if (!reservation.claimed) {
    return { ok: false, status: "rejected", message: "同一网店业务范围已被更新，请重新提交最新文件", warnings, errors: [{ code: "IMPORT_SCOPE_CHANGED", message: "导入开始前当前业务范围版本已变化" }], errorCount: 1 };
  }
  await fingerprintDependencies.renewImportFingerprintReservation(db, {
    ...fingerprint,
    batchId: reservedBatchId,
    attemptId: reservation.attemptId,
  });

  try {
  const imagePersistence = input.source === "tmall_product_assets"
    ? await productAssetDependencies.persistTmallProductAssetImages([...(assetImages?.values() ?? [])], {
        onBatchPersisted: () => fingerprintDependencies.renewImportFingerprintReservation(db, {
          ...fingerprint,
          batchId: reservedBatchId,
          attemptId: reservation.attemptId,
        }),
      })
    : null;
  const sumMetric = (key: string) => rows.reduce((sum, row) => sum + Number(row.metrics[key] ?? 0), 0);
  const result = await saveNetshopImport(db, {
    source: input.source,
    dataset,
    platform,
    shopName,
    fileHash,
    fileName: safeFileName(input.fileName),
    fileSizeBytes: input.fileSizeBytes,
    sheetName: parsed.sheetName,
    rows,
    warnings,
    totals: {
      sourceRowCount: rawRows.length,
      rowCount: rows.length,
      rawFileHash,
      contentHash: fingerprint.contentHash,
      dataset,
      dateMin: sortedDates[0] ?? null,
      dateMax: sortedDates[sortedDates.length - 1] ?? null,
      unmatchedProductCount: reconciliation.unmatchedCount,
      uniqueProductCount: new Set(rows.map((row) => row.spuId).filter(Boolean)).size,
      uniqueSkuCount: new Set(rows.map((row) => row.skuId).filter(Boolean)).size,
      imageCount: imagePersistence?.total ?? 0,
      uniqueImageCount: imagePersistence?.unique ?? 0,
      verifiedImageCount: imagePersistence?.verified ?? 0,
      inventoryQuantity: sumMetric("inventoryQuantity"),
      transactionAmountCents: sumMetric("transactionAmountCents"),
      refundAmountCents: sumMetric("refundAmountCents"),
      spendCents: sumMetric("spendCents"),
      netTransactionAmountCents: sumMetric("netTransactionAmountCents"),
      impressions: sumMetric("impressions"),
      clicks: sumMetric("clicks"),
      netOrders: sumMetric("netOrders"),
    },
    note: normalizeText(input.note),
    reservationFence: {
      domain: fingerprint.domain,
      scopeKey: fingerprint.scopeKey,
      batchId: reservedBatchId,
      attemptId: reservation.attemptId,
    },
    replaceScope: replaceStartDate && replaceEndDate
      ? { startDate: replaceStartDate, endDate: replaceEndDate }
      : snapshotSource
        ? { snapshotDate }
        : { fullScope: true },
  });

  const postOwnership = await readNetshopScopeOwnership(db, {
      source: input.source,
      dataset,
      platform,
      shopName,
      startDate: replaceStartDate,
      endDate: replaceEndDate,
      snapshotDate: snapshotSource ? snapshotDate : null,
      fullScope: replaceFullScope,
    });
  if (postOwnership.length !== 1
    || postOwnership[0]?.batchId !== result.batch.id
    || postOwnership[0].rowCount !== rows.length) {
    throw new Error("当前业务范围的事实归属与本批次不一致");
  }
  const verification = await verifyNetshopImportBatch(db, result.batch, {
    rowCount: rows.length,
    dataset,
    platform,
    shopName,
    dateMin: sortedDates[0] ?? null,
    dateMax: sortedDates[sortedDates.length - 1] ?? null,
  });
  if (!verification.verified) {
    throw new Error("批次、行数、店铺、数据集或日期覆盖回查不一致");
  }

  await fingerprintDependencies.recordImportFingerprint(db, {
    ...fingerprint,
    batchId: result.batch.id,
    importHash: fileHash,
    rawFileHash,
    attemptId: reservation.attemptId,
    publishedStateToken: await fingerprintDependencies.nextImportScopeStateToken({
      previousStateToken: currentStateToken,
      batchId: result.batch.id,
      contentHash: fingerprint.contentHash,
      rowCount: fingerprint.rowCount,
    }),
    metadata: { fileName: input.fileName, fileSizeBytes: input.fileSizeBytes, warnings },
    outcome: result.created ? "imported" : "duplicate",
  });

  return {
    ok: true,
    status: result.created ? "imported" : "duplicate",
    message: result.created ? `${platform}网店数据导入成功` : "全部标准化业务资料与当前数据一致，无需重复导入",
    batch: result.batch,
    warnings,
    verification: {
      ...verification,
      unmatchedProductCount: reconciliation.unmatchedCount,
      ...(imagePersistence ? { imageCount: imagePersistence.total, verifiedImageCount: imagePersistence.verified } : {}),
    },
  };
  } catch (error) {
    await fingerprintDependencies.failImportFingerprint(db, { ...fingerprint, batchId: reservedBatchId, importHash: fileHash, rawFileHash, attemptId: reservation.attemptId, metadata: { fileName: input.fileName, fileSizeBytes: input.fileSizeBytes, warnings }, errorCode: "NETSHOP_IMPORT_FAILED" }).catch(() => undefined);
    return { ok: false, status: "rejected", message: error instanceof Error ? error.message : "网店数据导入失败", warnings, errors: [{ code: "NETSHOP_IMPORT_FAILED", message: error instanceof Error ? error.message : "网店数据导入失败" }], errorCount: 1 };
  }
}
