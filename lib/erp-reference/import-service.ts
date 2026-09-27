import { isXlsxSignature } from "@/lib/sales/import-service";
import {
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
} from "@/lib/imports/content-fingerprint";
import {
  ERP_REFERENCE_SOURCE_LABELS,
  isErpReferenceSourceKey,
  parseErpReferenceXlsx,
  type ComboItemImportRow,
  type ErpReferenceIssue,
  type ErpReferenceSourceKey,
  type InventoryAgeImportRow,
  type ProductMasterRow,
} from "@/lib/imports/erp-reference";
import {
  ensureErpReferenceSchema,
  findErpReferenceBatchById,
  getErpReferenceDatabase,
  saveComboImport,
  saveInventoryAgeImport,
  saveProductMasterImport,
  type ErpReferenceImportBatch,
} from "./database";

function toHex(buffer: ArrayBuffer) {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function sha256(bytes: Uint8Array) {
  const input = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  return crypto.subtle.digest("SHA-256", input);
}

function safeFileName(name: string) {
  const baseName = name.split(/[\\/]/).pop() ?? "erp-reference.xlsx";
  return baseName.replace(/[\u0000-\u001f\u007f]/g, "").slice(0, 255);
}

function isIsoDate(value: string | undefined): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

export type ErpReferenceImportExecution = {
  ok: boolean;
  status: "imported" | "duplicate" | "rejected";
  message: string;
  batch?: ErpReferenceImportBatch | null;
  warnings: ErpReferenceIssue[];
  errors?: ErpReferenceIssue[];
  errorCount?: number;
};

export async function importErpReferenceBytes(input: {
  source: ErpReferenceSourceKey;
  bytes: Uint8Array;
  fileName: string;
  fileSizeBytes: number;
  snapshotDate?: string;
}): Promise<ErpReferenceImportExecution> {
  const db = getErpReferenceDatabase();
  await ensureErpReferenceSchema(db);
  await ensureImportFingerprintSchema(db);
  const rawFileHash = toHex(await sha256(input.bytes));
  const reject = (result: ErpReferenceImportExecution) => auditRejectedImportResult(db, {
    domain: "erp-reference",
    rawFileHash,
    scopeHint: { source: input.source, snapshotDate: input.snapshotDate?.trim() || null },
    metadata: { fileName: input.fileName, fileSizeBytes: input.fileSizeBytes },
  }, result);
  if (!isErpReferenceSourceKey(input.source)) {
    return reject({
      ok: false,
      status: "rejected",
      message: "不支持的数据来源",
      warnings: [],
      errors: [{ code: "INVALID_SOURCE", message: "source 必须为 products、inventory_age 或 combos" }],
      errorCount: 1,
    });
  }
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

  const snapshotDate = input.snapshotDate?.trim();
  if (input.source === "inventory_age" && !isIsoDate(snapshotDate)) {
    return reject({
      ok: false,
      status: "rejected",
      message: "库龄报表必须提供有效快照日期",
      warnings: [],
      errors: [{ code: "INVALID_SNAPSHOT_DATE", message: "快照日期必须为 YYYY-MM-DD" }],
      errorCount: 1,
    });
  }

  let parsed: ReturnType<typeof parseErpReferenceXlsx>;
  try {
    parsed = parseErpReferenceXlsx(input.source, input.bytes);
  } catch {
    const message = `${ERP_REFERENCE_SOURCE_LABELS[input.source]}解析失败，请确认文件格式和模板`;
    return reject({
      ok: false,
      status: "rejected",
      message,
      warnings: [],
      errors: [{ code: "XLSX_PARSE_ERROR", message }],
      errorCount: 1,
    });
  }
  if (parsed.errors.length > 0 || parsed.rows.length === 0) {
    const errors = parsed.errors.length > 0
      ? parsed.errors.slice(0, 200)
      : [{ code: "NO_DATA_ROWS", message: "工作表中没有可导入的数据行" }];
    return reject({
      ok: false,
      status: "rejected",
      message: "文件校验未通过，未写入任何数据",
      warnings: parsed.warnings,
      errors,
      errorCount: parsed.errors.length || 1,
    });
  }

  const fileName = safeFileName(input.fileName);
  const businessRows = input.source === "inventory_age"
    ? (parsed.rows as InventoryAgeImportRow[]).filter((row) => row.warehouse.trim() !== "刷刷仓")
    : parsed.rows;
  if (businessRows.length === 0) {
    return reject({
      ok: false,
      status: "rejected",
      message: "应用业务排除规则后没有可导入的数据",
      warnings: parsed.warnings,
      errors: [{ code: "NO_DATA_ROWS_AFTER_FILTER", message: "没有符合当前经营口径的业务资料" }],
      errorCount: 1,
    });
  }
  const fingerprint = await buildImportContentFingerprint({
    domain: "erp-reference",
    scope: {
      source: input.source,
      snapshotDate: input.source === "inventory_age" ? snapshotDate : null,
    },
    lockScope: { source: input.source },
    rows: businessRows,
    ignoredTopLevelKeys: ["sourceRowNumber"],
  });
  const readScopeOwnership = async () => input.source === "inventory_age"
    ? db.prepare(
      `SELECT last_import_batch_id AS batch_id, COUNT(*) AS row_count
       FROM erp_inventory_age_lines
       WHERE snapshot_date = ?
       GROUP BY last_import_batch_id
       ORDER BY last_import_batch_id`,
    ).bind(snapshotDate).all<{ batch_id: string; row_count: number }>()
    : input.source === "products"
      ? db.prepare(
        `SELECT last_import_batch_id AS batch_id, COUNT(*) AS row_count
         FROM erp_product_master
         GROUP BY last_import_batch_id
         ORDER BY last_import_batch_id`,
      ).all<{ batch_id: string; row_count: number }>()
      : db.prepare(
        `SELECT last_import_batch_id AS batch_id, COUNT(*) AS row_count
         FROM erp_combo_items
         GROUP BY last_import_batch_id
        ORDER BY last_import_batch_id`,
      ).all<{ batch_id: string; row_count: number }>();
  const scopeOwnership = await readScopeOwnership();
  const ownership = scopeOwnership.results.map((row) => ({
    batchId: row.batch_id,
    rowCount: Number(row.row_count),
  }));
  const currentStateToken = await readImportScopeStateToken(db, fingerprint);
  const currentBatchId = ownership.length === 1 && ownership[0]?.rowCount === businessRows.length
    ? ownership[0].batchId
    : null;
  const currentBatch = currentBatchId ? await findErpReferenceBatchById(db, currentBatchId) : null;
  const currentTotals = currentBatch?.totals as { contentHash?: unknown; rawFileHash?: unknown } | null;
  if (currentBatch?.status === "completed" && currentTotals?.contentHash === fingerprint.contentHash) {
    await recordImportFingerprint(db, {
      ...fingerprint,
      batchId: currentBatch.id,
      importHash: currentBatch.fileHash,
      rawFileHash,
      publishedStateToken: currentStateToken,
      metadata: { fileName, fileSizeBytes: input.fileSizeBytes, warnings: currentBatch.warnings },
      outcome: "duplicate",
    });
    return {
      ok: true,
      status: "duplicate",
      message: "全部标准化业务资料与当前数据一致，无需重复导入",
      batch: currentBatch,
      warnings: currentBatch.warnings,
    };
  }
  const fileHash = await buildImportAttemptHash({
    fingerprint,
    currentStateToken,
  });
  const id = `${input.source}:${fileHash}`;
  const reservation = await reserveImportFingerprint(db, {
    ...fingerprint,
    batchId: id,
    importHash: fileHash,
    rawFileHash,
    currentStateToken,
    metadata: { fileName, fileSizeBytes: input.fileSizeBytes, warnings: parsed.warnings },
  });
  if (!reservation.claimed) {
    return { ok: false, status: "rejected", message: "同一 ERP 业务范围已被更新，请重新提交最新文件", warnings: parsed.warnings, errors: [{ code: "IMPORT_SCOPE_CHANGED", message: "导入开始前当前范围版本已变化" }], errorCount: 1 };
  }
  await renewImportFingerprintReservation(db, { ...fingerprint, batchId: id, attemptId: reservation.attemptId });
  try {
  const assertPublishedScope = async (batchId: string) => {
    const result = await readScopeOwnership();
    const ownership = result.results.map((row) => ({ batchId: row.batch_id, rowCount: Number(row.row_count) }));
    if (ownership.length !== 1 || ownership[0]?.batchId !== batchId || ownership[0].rowCount !== businessRows.length) {
      throw new Error("ERP 导入期间同一业务范围已被其他批次更新");
    }
  };
  const publishedStateToken = (batchId: string) => nextImportScopeStateToken({
    previousStateToken: currentStateToken,
    batchId,
    contentHash: fingerprint.contentHash,
    rowCount: fingerprint.rowCount,
  });
  if (input.source === "products") {
    const rows = parsed.rows as ProductMasterRow[];
    const missingNames = rows.filter((row) => !row.productName).length;
    const warnings = [
      ...parsed.warnings,
      ...(missingNames > 0 ? [{ code: "MISSING_PRODUCT_NAME", message: `${missingNames} 行缺少货品名称，已保留货品编号` }] : []),
    ];
    const result = await saveProductMasterImport(db, {
      id,
      fileName,
      fileSizeBytes: input.fileSizeBytes,
      fileHash,
      sheetName: parsed.sheetName,
      rows,
      warnings,
      contentHash: fingerprint.contentHash,
      reservationFence: { domain: fingerprint.domain, scopeKey: fingerprint.scopeKey, batchId: id, attemptId: reservation.attemptId },
      totals: { ...parsed.totals, rawFileHash, contentHash: fingerprint.contentHash },
    });
    const { batch } = result;
    if (batch.status !== "completed") throw new Error("货品主数据导入批次未完成，不能记录内容指纹");
    await assertPublishedScope(batch.id);
    await recordImportFingerprint(db, { ...fingerprint, batchId: batch.id, importHash: fileHash, rawFileHash, attemptId: reservation.attemptId, publishedStateToken: await publishedStateToken(batch.id), metadata: { fileName, fileSizeBytes: input.fileSizeBytes, warnings }, outcome: result.created ? "imported" : "duplicate" });
    return { ok: true, status: result.created ? "imported" : "duplicate", message: result.created ? "货品主数据导入成功" : "全部标准化货品资料与当前数据一致，无需重复导入", batch, warnings };
  }

  if (input.source === "inventory_age") {
    const allRows = parsed.rows as InventoryAgeImportRow[];
    const excludedRows = allRows.filter((row) => row.warehouse.trim() === "刷刷仓");
    const rows = allRows.filter((row) => row.warehouse.trim() !== "刷刷仓");
    const warnings = [
      ...parsed.warnings,
      ...(excludedRows.length > 0
        ? [{ code: "EXCLUDED_BRUSH_WAREHOUSE", message: `已从库龄分析中排除刷刷仓 ${excludedRows.length} 行，原文件批次仍保留审计记录` }]
        : []),
    ];
    const result = await saveInventoryAgeImport(db, {
      id,
      fileName,
      fileSizeBytes: input.fileSizeBytes,
      fileHash,
      sheetName: parsed.sheetName,
      snapshotDate: snapshotDate!,
      rows,
      excludedCount: excludedRows.length,
      warnings,
      reservationFence: { domain: fingerprint.domain, scopeKey: fingerprint.scopeKey, batchId: id, attemptId: reservation.attemptId },
      totals: { ...parsed.totals, rawFileHash, contentHash: fingerprint.contentHash, excludedBrushWarehouseRows: excludedRows.length },
    });
    const { batch } = result;
    if (batch.status !== "completed") throw new Error("库龄导入批次未完成，不能记录内容指纹");
    await assertPublishedScope(batch.id);
    await recordImportFingerprint(db, { ...fingerprint, batchId: batch.id, importHash: fileHash, rawFileHash, attemptId: reservation.attemptId, publishedStateToken: await publishedStateToken(batch.id), metadata: { fileName, fileSizeBytes: input.fileSizeBytes, warnings }, outcome: result.created ? "imported" : "duplicate" });
    return { ok: true, status: result.created ? "imported" : "duplicate", message: result.created ? "库龄快照导入成功" : "全部标准化库龄资料与当前快照一致，无需重复导入", batch, warnings };
  }

  const rows = parsed.rows as ComboItemImportRow[];
  const result = await saveComboImport(db, {
    id,
    fileName,
    fileSizeBytes: input.fileSizeBytes,
    fileHash,
    sheetName: parsed.sheetName,
    rows,
    warnings: parsed.warnings,
    reservationFence: { domain: fingerprint.domain, scopeKey: fingerprint.scopeKey, batchId: id, attemptId: reservation.attemptId },
    totals: { ...parsed.totals, rawFileHash, contentHash: fingerprint.contentHash },
  });
  const { batch } = result;
  if (batch.status !== "completed") throw new Error("组合装导入批次未完成，不能记录内容指纹");
  await assertPublishedScope(batch.id);
  await recordImportFingerprint(db, { ...fingerprint, batchId: batch.id, importHash: fileHash, rawFileHash, attemptId: reservation.attemptId, publishedStateToken: await publishedStateToken(batch.id), metadata: { fileName, fileSizeBytes: input.fileSizeBytes, warnings: parsed.warnings }, outcome: result.created ? "imported" : "duplicate" });
  return { ok: true, status: result.created ? "imported" : "duplicate", message: result.created ? "组合装及子件导入成功" : "全部标准化组合装资料与当前数据一致，无需重复导入", batch, warnings: parsed.warnings };
  } catch (error) {
    await failImportFingerprint(db, { ...fingerprint, batchId: id, importHash: fileHash, rawFileHash, attemptId: reservation.attemptId, metadata: { fileName, fileSizeBytes: input.fileSizeBytes, warnings: parsed.warnings }, errorCode: "ERP_IMPORT_FAILED" }).catch(() => undefined);
    throw error;
  }
}
