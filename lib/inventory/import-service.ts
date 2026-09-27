import {
  parseInventoryStockXlsx,
  type InventoryStockRow,
  type InventoryStockIssue,
} from "@/lib/imports/inventory-stock";
import {
  buildImportAttemptHash,
  buildImportContentFingerprint,
  auditRejectedImportResult,
  ensureImportFingerprintSchema,
  failImportFingerprint,
  nextImportScopeStateToken,
  readImportScopeStateToken,
  recordImportFingerprint,
  renewImportFingerprintReservation,
  reserveImportFingerprint,
} from "@/lib/imports/content-fingerprint";
import { isXlsxSignature } from "@/lib/sales/import-service";
import {
  ensureInventorySchema,
  findInventoryImportBatchByHash,
  findLatestInventoryImportBatchForSnapshot,
  getInventoryDatabase,
  saveInventoryImport,
  type InventoryImportIssue,
} from "@/lib/inventory/database";
import { validateInventoryImportRows } from "@/lib/inventory/data-quality";
import { readOperatingSettings } from "@/lib/settings/service";

function toHex(buffer: ArrayBuffer) {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function sha256(bytes: Uint8Array) {
  const input = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  return crypto.subtle.digest("SHA-256", input);
}

function safeFileName(name: string) {
  const baseName = name.split(/[\\/]/).pop() ?? "inventory-stock.xlsx";
  return baseName.replace(/[\u0000-\u001f\u007f]/g, "").slice(0, 255);
}

function dateFromFileName(fileName: string): string | null {
  const compact = /(?:^|\D)(20\d{2})(\d{2})(\d{2})(?:\D|$)/.exec(fileName);
  const separated = /(?:^|\D)(20\d{2})[-_.年](\d{1,2})[-_.月](\d{1,2})日?(?:\D|$)/.exec(fileName);
  const match = compact ?? separated;
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return null;
  return `${year.toString().padStart(4, "0")}-${month.toString().padStart(2, "0")}-${day.toString().padStart(2, "0")}`;
}

function isIsoDate(value: string | undefined): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

function mapIssue(issue: InventoryStockIssue): InventoryImportIssue {
  return {
    row: issue.sourceRowNumber,
    field: issue.field,
    code: issue.code,
    message: issue.message,
  };
}

function summarizeInventoryRows(rows: InventoryStockRow[], sourceRowCount: number) {
  const warehouses = new Set<string>();
  const products = new Set<string>();
  const result = {
    sourceRowCount,
    rowCount: 0,
    warehouseCount: 0,
    productCount: 0,
    onHandQuantity: 0,
    availableQuantity: 0,
    lockedQuantity: 0,
    inTransitQuantity: 0,
    stockValueCents: 0,
    sales7dQuantity: 0,
    sales30dQuantity: 0,
  };
  for (const row of rows) {
    warehouses.add(row.warehouse);
    products.add(row.productCode);
    result.rowCount += 1;
    result.onHandQuantity += row.onHandQuantity;
    result.availableQuantity += row.availableQuantity;
    result.lockedQuantity += row.lockedQuantity;
    result.inTransitQuantity += row.inTransitQuantity;
    result.stockValueCents += Math.max(0, row.availableQuantity) * row.unitCostCents;
    result.sales7dQuantity += row.sales7dQuantity;
    result.sales30dQuantity += row.sales30dQuantity;
  }
  result.warehouseCount = warehouses.size;
  result.productCount = products.size;
  return result;
}

export type InventoryImportExecution = {
  ok: boolean;
  status: "imported" | "duplicate" | "rejected";
  message: string;
  batch?: Awaited<ReturnType<typeof findInventoryImportBatchByHash>>;
  warnings: InventoryImportIssue[];
  errors?: InventoryImportIssue[];
  errorCount?: number;
};

export async function importInventoryStockBytes(input: {
  bytes: Uint8Array;
  fileName: string;
  fileSizeBytes: number;
  snapshotDateOverride?: string;
}): Promise<InventoryImportExecution> {
  const rawFileHash = toHex(await sha256(input.bytes));
  const db = getInventoryDatabase();
  await ensureInventorySchema(db);
  await ensureImportFingerprintSchema(db);
  const reject = (result: InventoryImportExecution) => auditRejectedImportResult(db, {
    domain: "inventory-stock",
    rawFileHash,
    scopeHint: { source: "inventory_stock", snapshotDate: input.snapshotDateOverride?.trim() || null },
    metadata: { fileName: input.fileName, fileSizeBytes: input.fileSizeBytes },
  }, result);
  if (!isXlsxSignature(input.bytes)) {
    return reject({
      ok: false,
      status: "rejected",
      message: "文件签名不是有效的 .xlsx（ZIP）格式",
      warnings: [],
      errors: [{ code: "INVALID_XLSX_SIGNATURE", message: "文件签名无效" }],
      errorCount: 1,
    });
  }
  let parsed: ReturnType<typeof parseInventoryStockXlsx>;
  try {
    parsed = parseInventoryStockXlsx(input.bytes);
  } catch {
    const message = "库存 Excel 文件解析失败，请确认文件格式和模板";
    return reject({
      ok: false,
      status: "rejected",
      message,
      warnings: [],
      errors: [{ code: "XLSX_PARSE_ERROR", message }],
      errorCount: 1,
    });
  }

  const errors = parsed.errors.map(mapIssue).slice(0, 200);
  if (parsed.rows.length === 0 && errors.length === 0) {
    errors.push({ code: "NO_DATA_ROWS", message: "工作表中没有可导入的库存明细行" });
  }
  if (errors.length > 0) {
    return reject({
      ok: false,
      status: "rejected",
      message: "文件校验未通过，未写入任何库存数据",
      warnings: [],
      errors,
      errorCount: parsed.errors.length,
    });
  }

  let excludedBrushWarehouseRows = 0;
  let retainedZeroCostRows = 0;
  const importRows: InventoryStockRow[] = [];
  const qualityCandidateRows: InventoryStockRow[] = [];
  for (const row of parsed.rows) {
    if (row.warehouse.trim() === "刷刷仓") excludedBrushWarehouseRows += 1;
    else {
      qualityCandidateRows.push(row);
      if (row.unitCostCents === 0) retainedZeroCostRows += 1;
      importRows.push(row);
    }
  }
  const operatingSettings = await readOperatingSettings(db);
  const qualityErrors = validateInventoryImportRows(qualityCandidateRows, {
    allowNegativeInventory: operatingSettings.allowNegativeInventory,
  });
  if (qualityErrors.length > 0) {
    return reject({
      ok: false,
      status: "rejected",
      message: "库存数据质量门禁未通过，未写入任何库存数据",
      warnings: [],
      errors: qualityErrors.slice(0, 200),
      errorCount: qualityErrors.length,
    });
  }
  if (importRows.length === 0) {
    return reject({
      ok: false,
      status: "rejected",
      message: "剔除刷刷仓后没有可导入的库存数据",
      warnings: excludedBrushWarehouseRows > 0
        ? [{ code: "EXCLUDED_BRUSH_WAREHOUSE", message: `已识别刷刷仓 ${excludedBrushWarehouseRows} 行` }]
        : [],
      errors: [{ code: "NO_DATA_ROWS_AFTER_FILTER", message: "没有符合经营分析口径的库存明细行" }],
      errorCount: 1,
    });
  }
  const seenBusinessKeys = new Set<string>();
  const duplicateBusinessKeys = new Set<string>();
  for (const row of importRows) {
    if (seenBusinessKeys.has(row.rowKey)) duplicateBusinessKeys.add(row.rowKey);
    else seenBusinessKeys.add(row.rowKey);
  }
  if (duplicateBusinessKeys.size > 0) {
    return reject({
      ok: false,
      status: "rejected",
      message: "库存报表包含重复的仓库与货品组合，未写入任何数据",
      warnings: [],
      errors: [{ code: "DUPLICATE_INVENTORY_IDENTITY", message: `检测到 ${duplicateBusinessKeys.size} 个重复的仓库与货品组合，请合并或修正后重试` }],
      errorCount: duplicateBusinessKeys.size,
    });
  }

  const rowDates = [...new Set(importRows.map((row) => row.snapshotDate).filter((value): value is string => Boolean(value)))];
  if (rowDates.length > 1) {
    return reject({
      ok: false,
      status: "rejected",
      message: "库存报表包含多个快照日期，不能合并为同一库存批次",
      warnings: [],
      errors: [{ code: "MIXED_SNAPSHOT_DATES", message: `检测到 ${rowDates.length} 个不同的库存日期，请按日期拆分后重新上传` }],
      errorCount: 1,
    });
  }

  const suppliedSnapshotDate = input.snapshotDateOverride?.trim();
  if (suppliedSnapshotDate && !isIsoDate(suppliedSnapshotDate)) {
    return reject({
      ok: false,
      status: "rejected",
      message: "手工填写的快照日期无效",
      warnings: [],
      errors: [{ code: "INVALID_SNAPSHOT_DATE", message: "快照日期必须为 YYYY-MM-DD" }],
      errorCount: 1,
    });
  }
  const fileNameDate = dateFromFileName(input.fileName);
  const snapshotDate = rowDates[0] ?? fileNameDate ?? suppliedSnapshotDate;
  if (!snapshotDate) {
    return reject({
      ok: false,
      status: "rejected",
      message: "无法确定库存快照日期",
      warnings: [],
      errors: [{ code: "MISSING_SNAPSHOT_DATE", message: "报表没有库存日期，请在同步时填写快照日期，或在文件名中加入日期，例如“分仓库存2026.07.11.xlsx”" }],
      errorCount: 1,
    });
  }

  const missingNameRows = importRows.filter((row) => !row.productName).length;
  const warnings: InventoryImportIssue[] = [
    ...(retainedZeroCostRows > 0
      ? [{ code: "RETAINED_ZERO_UNIT_COST", message: `${retainedZeroCostRows} 行明确零成本库存已保留，库存金额按 0 计并参与补货计算` }]
      : []),
    ...(missingNameRows > 0
      ? [{ code: "MISSING_PRODUCT_NAME", message: `${missingNameRows} 行缺少货品名称，页面将使用销售明细中的名称补全` }]
      : []),
    ...(!parsed.coverage.hasAvailableQuantity
      ? [{ code: "DERIVED_AVAILABLE_QUANTITY", message: "报表未提供可用库存，系统已按“实盘数量 - 锁定数量”推导" }]
      : []),
    ...(!parsed.coverage.hasLockedQuantity
      ? [{ code: "MISSING_LOCKED_QUANTITY", message: "报表未提供锁定数量，本批次暂按 0 处理" }]
      : []),
    ...(!parsed.coverage.hasInTransitQuantity
      ? [{ code: "MISSING_IN_TRANSIT_QUANTITY", message: "报表未提供采购在途，本批次暂按 0 处理；备货草稿与已确认计划仍会单独计入在途" }]
      : []),
    ...(rowDates[0] && fileNameDate && rowDates[0] !== fileNameDate
      ? [{ code: "SNAPSHOT_DATE_FILENAME_MISMATCH", message: `报表日期 ${rowDates[0]} 与文件名日期 ${fileNameDate} 不一致，已采用报表日期` }]
      : []),
    ...(!rowDates[0] && !fileNameDate && suppliedSnapshotDate
      ? [{ code: "MANUAL_SNAPSHOT_DATE", message: `报表未提供库存日期，已采用手工填写的快照日期 ${suppliedSnapshotDate}` }]
      : []),
    ...(!parsed.coverage.hasSales30dQuantity
      ? [{ code: "MISSING_AGE_SALES", message: "报表未提供前30天销量，滞销清理将只显示库龄风险，不生成零销量判定" }]
      : []),
    ...(excludedBrushWarehouseRows > 0
      ? [{ code: "EXCLUDED_BRUSH_WAREHOUSE", message: `已剔除刷刷仓 ${excludedBrushWarehouseRows} 行，不写入经营分析数据` }]
      : []),
  ];

  const fingerprint = await buildImportContentFingerprint({
    domain: "inventory-stock",
    scope: { source: "inventory_stock", snapshotDate },
    lockScope: { source: "inventory_stock" },
    rows: importRows,
    ignoredTopLevelKeys: ["sourceRowNumber", "sourceRowHash", "rowKey"],
  });
  const currentStateToken = await readImportScopeStateToken(db, fingerprint);
  const latestScopeBatch = await findLatestInventoryImportBatchForSnapshot(db, snapshotDate);
  const currentTotals = latestScopeBatch?.totals as { contentHash?: unknown; rawFileHash?: unknown } | null;
  if (latestScopeBatch?.status === "completed" && latestScopeBatch.rowCount === importRows.length
    && currentTotals?.contentHash === fingerprint.contentHash) {
    await recordImportFingerprint(db, {
      ...fingerprint,
      batchId: latestScopeBatch.id,
      importHash: latestScopeBatch.fileHash,
      rawFileHash,
      publishedStateToken: currentStateToken,
      metadata: { fileName: input.fileName, fileSizeBytes: input.fileSizeBytes, warnings: latestScopeBatch.warnings },
      outcome: "duplicate",
    });
    return {
      ok: true,
      status: "duplicate",
      message: "全部标准化库存资料与当前快照一致，无需重复导入",
      batch: latestScopeBatch,
      warnings: latestScopeBatch.warnings,
    };
  }
  const fileHash = await buildImportAttemptHash({
    fingerprint,
    currentStateToken,
  });
  const reservation = await reserveImportFingerprint(db, {
    ...fingerprint,
    batchId: fileHash,
    importHash: fileHash,
    rawFileHash,
    currentStateToken,
    metadata: { fileName: input.fileName, fileSizeBytes: input.fileSizeBytes, warnings },
  });
  if (!reservation.claimed) {
    return { ok: false, status: "rejected", message: "同一库存快照已被更新，请重新提交最新文件", warnings, errors: [{ code: "IMPORT_SCOPE_CHANGED", message: "导入开始前当前快照版本已变化" }], errorCount: 1 };
  }
  await renewImportFingerprintReservation(db, { ...fingerprint, batchId: fileHash, attemptId: reservation.attemptId });

  try {
  const totals = summarizeInventoryRows(importRows, parsed.totals.sourceRowCount);
  const result = await saveInventoryImport(db, {
    fileHash,
    fileName: safeFileName(input.fileName),
    fileSizeBytes: input.fileSizeBytes,
    sheetName: parsed.sheetName,
    snapshotDate,
    rows: importRows,
    warnings,
    reservationFence: {
      domain: fingerprint.domain,
      scopeKey: fingerprint.scopeKey,
      batchId: fileHash,
      attemptId: reservation.attemptId,
    },
    totals: {
      ...parsed.totals,
      ...totals,
      coverage: parsed.coverage,
      rawFileHash,
      contentHash: fingerprint.contentHash,
      excludedBrushWarehouseRows,
      excludedZeroCostRows: 0,
      retainedZeroCostRows,
    },
  });

  const latestAfterSave = await findLatestInventoryImportBatchForSnapshot(db, snapshotDate);
  if (result.batch.status !== "completed" || result.batch.insertedCount !== importRows.length
    || latestAfterSave?.id !== result.batch.id) {
    throw new Error("库存导入批次未完成或落库行数与解析结果不一致");
  }

  await recordImportFingerprint(db, {
    ...fingerprint,
    batchId: result.batch.id,
    importHash: fileHash,
    rawFileHash,
    attemptId: reservation.attemptId,
    publishedStateToken: await nextImportScopeStateToken({
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
    message: result.created ? "分仓库存快照同步成功" : "全部标准化库存资料与当前快照一致，无需重复导入",
    batch: result.batch,
    warnings,
  };
  } catch (error) {
    await failImportFingerprint(db, { ...fingerprint, batchId: fileHash, importHash: fileHash, rawFileHash, attemptId: reservation.attemptId, metadata: { fileName: input.fileName, fileSizeBytes: input.fileSizeBytes, warnings }, errorCode: "INVENTORY_IMPORT_FAILED" }).catch(() => undefined);
    throw error;
  }
}
