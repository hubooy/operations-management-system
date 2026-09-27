import { createHash } from "node:crypto";
import {
  buildImportAttemptHash,
  buildImportContentFingerprint,
  ensureImportFingerprintSchema,
  failImportFingerprint,
  findImportFingerprintByBatch,
  nextImportScopeStateToken,
  readImportScopeStateToken,
  recordImportFingerprint,
  recordRejectedImportAttempt,
  renewImportFingerprintReservation,
  reserveImportFingerprint,
} from "@/lib/imports/content-fingerprint";
import {
  ensureMarketSchema,
  findMarketBatchByHash,
  findMarketBatchById,
  getMarketDatabase,
  saveMarketImport,
} from "@/lib/market/database";
import { parseMarketRows } from "@/lib/market/parser";
import { createOrResumeMarketImageCacheJob } from "@/lib/market/image-cache-job";
import { matchImportedMarketBrands, refreshSystemMarketBrandSeeds } from "@/lib/market/brand-seeds";
import { refreshMarketSkuGmvTotals } from "@/lib/market/gmv-total";
import { refreshMarketMasterIdentities } from "@/lib/market/master-identity";
import { marketImportRangeKey } from "@/lib/market/import-identity";

export { parseMarketRows } from "@/lib/market/parser";

async function queueImagesAfterImport(db: ReturnType<typeof getMarketDatabase>, batchId: string, requestedBy?: string) {
  try {
    return await createOrResumeMarketImageCacheJob(db, { batchId, requestedBy: requestedBy || "market-import" });
  } catch {
    return {
      id: "", status: "failed" as const, total: 0, discoveredCount: 0, discoveryComplete: false,
      cached: 0, failed: 0, pending: 0, propagationPending: 0, processedCount: 0,
      runCount: 0, errorMessage: "", maintenanceFailed: true,
    };
  }
}

async function refreshBrandSeedsAfterImport(
  db: ReturnType<typeof getMarketDatabase>,
  actorEmail: string,
  systemSeedSnapshot: Awaited<ReturnType<typeof matchImportedMarketBrands>>["systemSeedSnapshot"],
) {
  try {
    return await refreshSystemMarketBrandSeeds(db, actorEmail, { systemSeedSnapshot });
  } catch {
    return { discovered: 0, inserted: 0, refreshed: 0, disabled: 0, manualPreserved: 0, maintenanceFailed: true };
  }
}

async function repairLegacyDerivedCaches(db: ReturnType<typeof getMarketDatabase>) {
  await Promise.allSettled([refreshMarketSkuGmvTotals(db), refreshMarketMasterIdentities(db)]);
}

export async function importMarketFile(input: {
  bytes: Uint8Array;
  fileName: string;
  fileSizeBytes: number;
  sourceType: string;
  defaultStartDate: string;
  defaultEndDate: string;
  defaultCategory?: string;
  defaultScope?: string;
  defaultPriceBandFilter?: string;
  actorEmail?: string;
}) {
  const db = getMarketDatabase();
  await ensureMarketSchema(db);
  await ensureImportFingerprintSchema(db);
  const rawFileHash = createHash("sha256").update(input.bytes).digest("hex");
  const rejectBeforeFingerprint = (errorCode: string, message: string) => recordRejectedImportAttempt(db, {
    domain: "market",
    rawFileHash,
    scopeHint: {
      sourceType: input.sourceType,
      startDate: input.defaultStartDate,
      endDate: input.defaultEndDate,
      category: input.defaultCategory?.trim() || null,
      scope: input.defaultScope?.trim() || null,
    },
    errorCode,
    issues: [{ code: errorCode, message }],
    metadata: { fileName: input.fileName, fileSizeBytes: input.fileSizeBytes, actor: input.actorEmail },
  });
  let parsed: ReturnType<typeof parseMarketRows>;
  try {
    parsed = parseMarketRows(input);
  } catch (error) {
    const message = error instanceof Error ? error.message : "市场文件解析失败";
    await rejectBeforeFingerprint("MARKET_PARSE_ERROR", message);
    throw error;
  }
  const brandMatch = await matchImportedMarketBrands(db, parsed.rows);
  const ranges = [...new Set(brandMatch.rows.map((row) => JSON.stringify({
    category: row.category,
    scope: row.scope,
    rankingDimension: row.rankingDimension,
    priceBandFilter: row.priceBandFilter,
    periodStart: row.periodStart,
    periodEnd: row.periodEnd,
  })))].sort().map((value) => JSON.parse(value) as {
    category: string;
    scope: string;
    rankingDimension: string;
    priceBandFilter: string;
    periodStart: string;
    periodEnd: string;
  });
  if (brandMatch.rows.some((row) => row.periodStart < input.defaultStartDate || row.periodEnd > input.defaultEndDate)) {
    const message = "市场文件包含表单权威周期之外的日期，请修正导入周期后重试";
    await rejectBeforeFingerprint("MARKET_PERIOD_OUT_OF_RANGE", message);
    throw new Error(message);
  }
  // The persisted claim remains a stable month-level base lock so a daily
  // backfill cannot race a monthly replacement.  The exact periods in
  // `ranges` are used for content identity and fact replacement below.
  const replaceRangeKeys = [...new Set(ranges.map((range) => marketImportRangeKey({
    category: range.category!,
    scope: range.scope!,
    rankingDimension: range.rankingDimension!,
    month: range.periodEnd.slice(0, 7),
  })))];
  const fingerprint = await buildImportContentFingerprint({
    domain: "market",
    scope: { sourceType: input.sourceType, ranges },
    lockScope: { dataset: "market_ranking_entries" },
    rows: brandMatch.rows,
    ignoredTopLevelKeys: ["sourceRowNumber", "naturalKey", "importRangeKey"],
  });
  const rangesJson = JSON.stringify(ranges);
  const importReceipt = (batchId: string) => ({
    batchId,
    rawFileSha256: rawFileHash,
    fileName: input.fileName,
    fileSizeBytes: input.fileSizeBytes,
    sourceType: input.sourceType,
    rowCount: brandMatch.rows.length,
    warningCount: parsed.warnings.length,
    ranges,
  });
  const readScopeOwnership = async () => {
    const current = await db.prepare(
      `SELECT last_import_batch_id AS batch_id, COUNT(*) AS row_count
       FROM market_ranking_entries entry
       WHERE EXISTS (
         SELECT 1 FROM json_each(?) target
         WHERE entry.category = json_extract(target.value, '$.category')
           AND entry.scope = json_extract(target.value, '$.scope')
           AND entry.ranking_dimension = json_extract(target.value, '$.rankingDimension')
           AND entry.price_band_filter = json_extract(target.value, '$.priceBandFilter')
           AND entry.period_start = json_extract(target.value, '$.periodStart')
           AND entry.period_end = json_extract(target.value, '$.periodEnd')
       )
       GROUP BY last_import_batch_id
       ORDER BY last_import_batch_id`,
    ).bind(rangesJson).all<{ batch_id: string; row_count: number }>();
    return current.results.map((row) => ({ batchId: row.batch_id, rowCount: Number(row.row_count) }));
  };
  const scopeOwnership = await readScopeOwnership();
  const currentStateToken = await readImportScopeStateToken(db, fingerprint);
  const currentBatchId = scopeOwnership.length === 1 && scopeOwnership[0]?.rowCount === brandMatch.rows.length
    ? scopeOwnership[0].batchId
    : null;
  const currentBatch = currentBatchId ? await findMarketBatchById(db, currentBatchId) : null;
  const currentFingerprint = currentBatchId
    ? await findImportFingerprintByBatch(db, { domain: fingerprint.domain, batchId: currentBatchId })
    : null;
  if (currentBatch?.status === "completed" && currentBatch.rowCount === brandMatch.rows.length
    && currentFingerprint?.scopeKey === fingerprint.scopeKey
    && currentFingerprint.contentHash === fingerprint.contentHash) {
    await recordImportFingerprint(db, {
      ...fingerprint,
      batchId: currentBatch.id,
      importHash: currentFingerprint.importHash,
      rawFileHash,
      publishedStateToken: currentStateToken,
      metadata: { fileName: input.fileName, fileSizeBytes: input.fileSizeBytes, actor: input.actorEmail, warnings: parsed.warnings },
      outcome: "duplicate",
    });
    await repairLegacyDerivedCaches(db);
    const imageCacheJob = await queueImagesAfterImport(db, currentBatch.id, input.actorEmail);
    return {
      ok: true,
      status: "duplicate" as const,
      message: "全部标准化市场资料与当前范围一致，无需重复导入；图片缓存已交给后台任务",
      batch: currentBatch,
      importReceipt: importReceipt(currentBatch.id),
      imageCacheJob,
      imageCache: {
        total: imageCacheJob.total, cached: imageCacheJob.cached, failed: imageCacheJob.failed,
        pending: imageCacheJob.pending + imageCacheJob.propagationPending,
      },
    };
  }
  const fileHash = await buildImportAttemptHash({
    fingerprint,
    currentStateToken,
  });
  const existing = await findMarketBatchByHash(db, fileHash);
  if (existing?.status === "processing" && Date.now() - Date.parse(existing.createdAt) < 30 * 60 * 1000) {
    return { ok: true, status: "processing" as const, message: "相同业务资料正在导入，请稍后刷新", batch: existing };
  }
  if (existing) {
    await db.batch([
      db.prepare("DELETE FROM market_import_staging_rows WHERE batch_id=?").bind(existing.id),
      db.prepare("DELETE FROM market_import_identity_refresh_keys_v2 WHERE batch_id=?").bind(existing.id),
      db.prepare("DELETE FROM market_import_range_claims WHERE batch_id=?").bind(existing.id),
      db.prepare("DELETE FROM market_import_batches WHERE id=? AND status<>'completed'").bind(existing.id),
    ]);
  }
  const batchId = `market-${fileHash}`;
  const reservation = await reserveImportFingerprint(db, {
    ...fingerprint,
    batchId,
    importHash: fileHash,
    rawFileHash,
    currentStateToken,
    metadata: { fileName: input.fileName, fileSizeBytes: input.fileSizeBytes, actor: input.actorEmail, warnings: parsed.warnings },
  });
  if (!reservation.claimed) {
    throw new Error("同一市场业务范围已被更新，请重新提交最新文件");
  }
  await renewImportFingerprintReservation(db, { ...fingerprint, batchId, attemptId: reservation.attemptId });
  try {
  const saved = await saveMarketImport({
    db,
    batchId,
    sourceType: input.sourceType,
    fileName: input.fileName,
    fileSizeBytes: input.fileSizeBytes,
    fileHash,
    sheetName: parsed.sheetName,
    rows: brandMatch.rows,
    warnings: parsed.warnings,
    replaceRangeKeys,
    reservationFence: {
      domain: fingerprint.domain,
      scopeKey: fingerprint.scopeKey,
      batchId,
      attemptId: reservation.attemptId,
    },
  });
  const { batch } = saved;
  const postOwnership = await readScopeOwnership();
  if (batch.status !== "completed" || postOwnership.length !== 1
    || postOwnership[0]?.batchId !== batch.id || postOwnership[0].rowCount !== brandMatch.rows.length) {
    throw new Error("市场分析导入批次未完成或当前范围的落库事实与解析结果不一致");
  }
  const created = saved.created;
  await recordImportFingerprint(db, {
    ...fingerprint,
    batchId: batch.id,
    importHash: fileHash,
    rawFileHash,
    attemptId: reservation.attemptId,
    publishedStateToken: await nextImportScopeStateToken({
      previousStateToken: currentStateToken,
      batchId: batch.id,
      contentHash: fingerprint.contentHash,
      rowCount: fingerprint.rowCount,
    }),
    metadata: { fileName: input.fileName, fileSizeBytes: input.fileSizeBytes, actor: input.actorEmail, warnings: parsed.warnings },
    outcome: created ? "imported" : "duplicate",
  });
  const [imageCacheJob, brandSeedRefresh] = await Promise.all([
    queueImagesAfterImport(db, batch.id, input.actorEmail),
    refreshBrandSeedsAfterImport(db, input.actorEmail?.trim() || "market-import", brandMatch.systemSeedSnapshot),
  ]);
  return {
    ok: true,
    status: created ? "imported" as const : "duplicate" as const,
    message: created
      ? `成功导入 ${batch.rowCount} 条市场商品数据，系统品牌种子自动匹配 ${brandMatch.summary.matched} 条`
      : "全部标准化市场资料与当前范围一致，无需重复导入；图片缓存已交给后台任务",
    batch,
    importReceipt: importReceipt(batch.id),
    imageCacheJob,
    imageCache: {
      total: imageCacheJob.total, cached: imageCacheJob.cached, failed: imageCacheJob.failed,
      pending: imageCacheJob.pending + imageCacheJob.propagationPending,
    },
    brandSeedRefresh,
    brandMatch: brandMatch.summary,
  };
  } catch (error) {
    await failImportFingerprint(db, { ...fingerprint, batchId, importHash: fileHash, rawFileHash, attemptId: reservation.attemptId, metadata: { fileName: input.fileName, fileSizeBytes: input.fileSizeBytes, actor: input.actorEmail, warnings: parsed.warnings }, errorCode: "MARKET_IMPORT_FAILED" }).catch(() => undefined);
    throw error;
  }
}
