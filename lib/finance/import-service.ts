// Offline migration/rehearsal implementation. Production callers use normalized-import.
import { ensureFinanceSchema, findFinanceImportBatchById, getFinanceDatabase, saveFinanceImport } from "./database";
import { auditRejectedImportResult, buildImportAttemptHash, buildImportContentFingerprint, ensureImportFingerprintSchema, failImportFingerprint, findImportFingerprintByBatch, nextImportScopeStateToken, readImportScopeStateToken, recordImportFingerprint, renewImportFingerprintReservation, reserveImportFingerprint } from "@/lib/imports/content-fingerprint";
import { parseFinanceWorkbook } from "./parser";
import { toHex, sha256, safeFinanceFileName, isSupportedFinanceSignature, validateParsedWorkbook, type FinanceImportExecution } from "./normalized-import";
export * from "./normalized-import";

export async function importFinanceReportBytes(input: {
  bytes: Uint8Array;
  fileName: string;
  fileSizeBytes: number;
}): Promise<FinanceImportExecution> {
  const rawFileHash = toHex(await sha256(input.bytes));
  const db = getFinanceDatabase();
  await ensureFinanceSchema(db);
  await ensureImportFingerprintSchema(db);
  const reject = (result: FinanceImportExecution) => auditRejectedImportResult(db, {
    domain: "finance",
    rawFileHash,
    scopeHint: { source: "monthly-finance-report" },
    metadata: { fileName: input.fileName, fileSizeBytes: input.fileSizeBytes },
  }, result);
  if (!isSupportedFinanceSignature(input.bytes)) {
    const issue = { code: "INVALID_EXCEL_SIGNATURE", message: "文件签名不是有效的 .xls 或 .xlsx 格式" };
    return reject({ ok: false, status: "rejected", message: issue.message, warnings: [], errors: [issue], errorCount: 1 });
  }

  let parsed: ReturnType<typeof parseFinanceWorkbook>;
  try {
    parsed = parseFinanceWorkbook(input.bytes);
  } catch {
    const message = "月度财报解析失败，请确认文件格式和模板";
    return reject({ ok: false, status: "rejected", message, warnings: [], errors: [{ code: "FINANCE_PARSE_ERROR", message }], errorCount: 1 });
  }

  const errors = validateParsedWorkbook(parsed);
  if (errors.length > 0) {
    return reject({
      ok: false,
      status: "rejected",
      message: "财报结构校验未通过，未写入任何月份数据",
      warnings: parsed.warnings,
      errors,
      errorCount: errors.length,
    });
  }

  const months = parsed.months.map((month) => month.month).sort();
  const fingerprintRows = parsed.months.flatMap((month) => month.lines.map((line) => ({
    businessName: month.businessName,
    ...line,
  })));
  const fingerprint = await buildImportContentFingerprint({
    domain: "finance",
    scope: { source: "monthly-finance-report", months },
    lockScope: { source: "monthly-finance-report" },
    rows: fingerprintRows,
    ignoredTopLevelKeys: ["rawValue", "sourceRowCount", "sortOrder"],
  });
  const readScopeOwnership = async () => {
    const placeholders = months.map(() => "?").join(",");
    const current = await db.prepare(
      `SELECT batch_id, COUNT(*) AS month_count
       FROM finance_months
       WHERE month IN (${placeholders}) AND status = 'completed'
       GROUP BY batch_id
       ORDER BY batch_id`,
    ).bind(...months).all<{ batch_id: string; month_count: number }>();
    return current.results.map((row) => ({ batchId: row.batch_id, monthCount: Number(row.month_count) }));
  };
  const scopeOwnership = await readScopeOwnership();
  const currentStateToken = await readImportScopeStateToken(db, fingerprint);
  const currentBatchId = scopeOwnership.length === 1 && scopeOwnership[0]?.monthCount === months.length
    ? scopeOwnership[0].batchId
    : null;
  const currentBatch = currentBatchId ? await findFinanceImportBatchById(db, currentBatchId) : null;
  const currentFingerprint = currentBatchId
    ? await findImportFingerprintByBatch(db, { domain: "finance", batchId: currentBatchId })
    : null;
  if (currentBatch?.status === "completed" && currentFingerprint?.contentHash === fingerprint.contentHash
    && currentFingerprint.scopeKey === fingerprint.scopeKey) {
    await recordImportFingerprint(db, {
      ...fingerprint,
      batchId: currentBatch.id,
      importHash: currentFingerprint.importHash,
      rawFileHash,
      publishedStateToken: currentStateToken,
      metadata: { fileName: input.fileName, fileSizeBytes: input.fileSizeBytes, warnings: currentBatch.warnings },
      outcome: "duplicate",
    });
    return {
      ok: true,
      status: "duplicate",
      message: "全部标准化财务资料与当前月份一致，无需重复导入",
      batch: currentBatch,
      importedMonths: [],
      skippedMonths: months,
      warnings: currentBatch.warnings,
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
    metadata: { fileName: input.fileName, fileSizeBytes: input.fileSizeBytes, warnings: parsed.warnings },
  });
  if (!reservation.claimed) {
    return { ok: false, status: "rejected", message: "同一财务月份已被更新，请重新提交最新文件", warnings: parsed.warnings, errors: [{ code: "IMPORT_SCOPE_CHANGED", message: "导入开始前当前月份版本已变化" }], errorCount: 1 };
  }
  await renewImportFingerprintReservation(db, { ...fingerprint, batchId: fileHash, attemptId: reservation.attemptId });

  try {
  const result = await saveFinanceImport(db, {
    fileHash,
    fileName: safeFinanceFileName(input.fileName),
    fileSizeBytes: input.fileSizeBytes,
    parsed,
    reservationFence: {
      domain: fingerprint.domain,
      scopeKey: fingerprint.scopeKey,
      batchId: fileHash,
      attemptId: reservation.attemptId,
    },
  });
  const postOwnership = await readScopeOwnership();
  if (postOwnership.length !== 1 || postOwnership[0]?.batchId !== result.batch.id
    || postOwnership[0].monthCount !== months.length) {
    throw new Error("财务导入期间目标月份已被其他批次更新");
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
    metadata: { fileName: input.fileName, fileSizeBytes: input.fileSizeBytes, warnings: parsed.warnings },
    outcome: result.created ? "imported" : "duplicate",
  });
  const importedLabel = result.importedMonths.join("、");
  const skippedLabel = result.skippedMonths.join("、");
  const message = result.importedMonths.length === 0
    ? `全部标准化财务资料与当前月份一致（${skippedLabel}），未重复写入`
    : result.skippedMonths.length > 0
      ? `已导入 ${importedLabel}；已存在的 ${skippedLabel} 自动排除`
      : `月度财报导入成功：${importedLabel}`;
  return {
    ok: true,
    status: result.importedMonths.length > 0 ? "imported" : "duplicate",
    message,
    batch: result.batch,
    importedMonths: result.importedMonths,
    skippedMonths: result.skippedMonths,
    warnings: parsed.warnings,
  };
  } catch (error) {
    await failImportFingerprint(db, { ...fingerprint, batchId: fileHash, importHash: fileHash, rawFileHash, attemptId: reservation.attemptId, metadata: { fileName: input.fileName, fileSizeBytes: input.fileSizeBytes, warnings: parsed.warnings }, errorCode: "FINANCE_IMPORT_FAILED" }).catch(() => undefined);
    throw error;
  }
}
