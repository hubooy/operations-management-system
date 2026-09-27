import { createHash, randomUUID } from "node:crypto";

import { saveMarketImportCore, type MarketEntryForImport } from "@/lib/market/import-core";
import { parseMarketRows } from "@/lib/market/parser";
import { ensureMarketSchemaCached, type MarketSchemaDatabase } from "@/lib/market/schema-core";
import { refreshMarketSkuGmvTotals } from "@/lib/market/gmv-total";
import { refreshMarketMasterIdentities } from "@/lib/market/master-identity";

type DownloadTask = {
  id: string; category: string; scope: string; month: string; ranking_dimension: string; status: string;
  attempt_count: number; file_hash: string; import_batch_id: string; staging_batch_id: string;
  execution_token: string; last_attempt_at: string | null;
};

export type MarketDownloadExecutorActor = { email: string; role: string };
export type MarketDownloadExecutorDeps = {
  download?: (task: DownloadTask) => Promise<{ bytes: Uint8Array; fileName: string; jdTaskId?: string }>;
  cacheImages?: (input: { db: MarketSchemaDatabase; task: DownloadTask; batchId: string; rows: MarketEntryForImport[] }) => Promise<{ queued?: number; cached?: number }>;
  createPriceTasks?: (input: { db: MarketSchemaDatabase; task: DownloadTask; batchId: string; rows: MarketEntryForImport[] }) => Promise<{ created?: number }>;
};

function monthEnd(month: string) {
  const [year, value] = month.split("-").map(Number);
  return new Date(Date.UTC(year, value, 0)).toISOString().slice(0, 10);
}

async function audit(db: MarketSchemaDatabase, actor: MarketDownloadExecutorActor, action: string, taskId: string, before: unknown, after: unknown) {
  await db.prepare(`INSERT INTO market_master_audit_logs
    (id, actor_email, actor_role, action, entity_type, entity_id, before_json, after_json)
    VALUES (?, ?, ?, ?, 'market_download_task', ?, ?, ?)`)
    .bind(`market-audit-${randomUUID()}`, actor.email, actor.role, action, taskId, JSON.stringify(before ?? null), JSON.stringify(after ?? null)).run();
}

async function failTask(db: MarketSchemaDatabase, task: DownloadTask, actor: MarketDownloadExecutorActor, executionToken: string, code: string, message: string, status: "failed" | "waiting_login") {
  const attempt = Number(task.attempt_count ?? 0) + (status === "failed" ? 1 : 0);
  const terminal = status === "failed" && attempt >= 3;
  const failed = await db.prepare(`UPDATE market_download_tasks SET status=?, attempt_count=?, execution_token='',
    error_code=?, error_message=?, next_retry_at=?, last_attempt_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
    WHERE id=? AND execution_token=? AND status NOT IN ('imported','published')`)
    .bind(terminal ? "failed" : status, attempt, code, message.slice(0, 800), status === "failed" && !terminal ? new Date(Date.now() + 15 * 60_000).toISOString() : null, task.id, executionToken)
    .run() as { meta?: { changes?: number } };
  const after = await db.prepare("SELECT * FROM market_download_tasks WHERE id=?").bind(task.id).first<Record<string, unknown>>();
  if (Number(failed.meta?.changes ?? 0) === 1) await audit(db, actor, "execute_download_task", task.id, task, after);
  return after;
}

async function reconcileCompletedDownloadTask(db: MarketSchemaDatabase, task: DownloadTask, actor: MarketDownloadExecutorActor) {
  if (!task.import_batch_id) return null;
  const batch = await db.prepare("SELECT id,status,row_count FROM market_import_batches WHERE id=? LIMIT 1")
    .bind(task.import_batch_id).first<{ id: string; status: string; row_count: number }>();
  if (batch?.status !== "completed") return null;
  try {
    const reconciled = await db.prepare(`UPDATE market_download_tasks SET status='imported', execution_token='',
      error_code='', error_message='', next_retry_at=NULL,
      completed_at=COALESCE(completed_at,CURRENT_TIMESTAMP), updated_at=CURRENT_TIMESTAMP
      WHERE id=? AND import_batch_id=? AND status IN ('downloading','staged')`)
      .bind(task.id, batch.id).run() as { meta?: { changes?: number } };
    if (Number(reconciled.meta?.changes ?? 0) !== 1) return null;
    const after = await db.prepare("SELECT * FROM market_download_tasks WHERE id=?").bind(task.id).first<Record<string, unknown>>();
    await audit(db, actor, "reconcile_completed_download_task", task.id, task, after).catch(() => undefined);
    return { status: "imported", duplicate: true, reconciled: true, taskId: task.id, batchId: batch.id, rowCount: Number(batch.row_count ?? 0) };
  } catch {
    return { status: task.status, busy: true, reconciliationPending: true, taskId: task.id, batchId: batch.id };
  }
}

export async function executeMarketDownloadTask(db: MarketSchemaDatabase, input: { taskId: string }, actor: MarketDownloadExecutorActor, deps: MarketDownloadExecutorDeps = {}) {
  await ensureMarketSchemaCached(db);
  const task = await db.prepare("SELECT * FROM market_download_tasks WHERE id=? LIMIT 1").bind(input.taskId).first<DownloadTask>();
  if (!task) throw new Error("market download task not found");
  if (task.status === "imported" || task.status === "published") return { status: task.status, duplicate: true, taskId: task.id };
  if (Number(task.attempt_count ?? 0) >= 3) return { status: "failed", terminal: true, taskId: task.id };
  if (task.status === "downloading" || task.status === "staged") {
    const reconciled = await reconcileCompletedDownloadTask(db, task, actor);
    if (reconciled) return reconciled;
    const released = await db.prepare(`UPDATE market_download_tasks SET status='planned',
      execution_token='', error_code='stale_execution_recovered', error_message='Recovered an expired download execution lease',
      next_retry_at=NULL, updated_at=CURRENT_TIMESTAMP
      WHERE id=? AND status IN ('downloading','staged')
        AND (last_attempt_at IS NULL OR datetime(last_attempt_at)<=datetime('now','-30 minutes'))`)
      .bind(task.id).run() as { meta?: { changes?: number } };
    if (Number(released.meta?.changes ?? 0) !== 1) return { status: task.status, busy: true, taskId: task.id };
    task.status = "planned";
  }
  const retryAt = await db.prepare("SELECT next_retry_at FROM market_download_tasks WHERE id=?").bind(task.id).first<{ next_retry_at: string | null }>();
  if (retryAt?.next_retry_at && Date.parse(retryAt.next_retry_at) > Date.now()) {
    return { status: task.status, retryAt: retryAt.next_retry_at, taskId: task.id };
  }
  const executionToken = randomUUID();

  const claimed = await db.prepare(`UPDATE market_download_tasks SET status='downloading', execution_token=?, last_attempt_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
    WHERE id=? AND status IN ('planned','created','failed','waiting_login') AND attempt_count<3
      AND (next_retry_at IS NULL OR datetime(next_retry_at)<=CURRENT_TIMESTAMP)`).bind(executionToken, task.id).run() as { meta?: { changes?: number } };
  if (Number(claimed.meta?.changes ?? 0) !== 1) {
    const current = await db.prepare("SELECT status, next_retry_at FROM market_download_tasks WHERE id=?").bind(task.id).first<Record<string, unknown>>();
    return { status: String(current?.status ?? task.status), busy: true, retryAt: current?.next_retry_at ?? null, taskId: task.id };
  }
  if (!deps.download) return { status: String((await failTask(db, task, actor, executionToken, "waiting_login", "JD login session is not available", "waiting_login"))?.status ?? "waiting_login"), taskId: task.id, externalValidation: "waiting_login" };

  let downloaded: { bytes: Uint8Array; fileName: string; jdTaskId?: string };
  try {
    downloaded = await deps.download(task);
  } catch (error) {
    const message = error instanceof Error ? error.message : "download failed";
    return { status: String((await failTask(db, task, actor, executionToken, "download_failed", message, "failed"))?.status ?? "failed"), taskId: task.id };
  }

  if (!downloaded.bytes.byteLength || downloaded.bytes.byteLength > 25 * 1024 * 1024) {
    return { status: String((await failTask(db, task, actor, executionToken, "invalid_file_size", "download file must be between 1 byte and 25 MB", "failed"))?.status ?? "failed"), taskId: task.id };
  }
  const fileHash = createHash("sha256").update(downloaded.bytes).digest("hex");
  const importIdentityHash = createHash("sha256")
    .update(downloaded.bytes)
    .update(`\0${task.category}\0${task.scope}\0${task.ranking_dimension}\0${task.month}`)
    .digest("hex");
  let completedImport: {
    batchId: string; stagingBatchId: string; rowCount: number; duplicate: boolean; validation: Record<string, unknown>;
  } | null = null;
  try {
    const parsed = parseMarketRows({
      bytes: downloaded.bytes,
      fileName: downloaded.fileName,
      defaultStartDate: `${task.month}-01`,
      defaultEndDate: monthEnd(task.month),
      defaultCategory: task.category,
    });
    const rows = parsed.rows as MarketEntryForImport[];
    if (!rows.length || rows.length > 5_000) throw new Error("download file row count must be between 1 and 5000");
    const expectedStart = `${task.month}-01`;
    const expectedEnd = monthEnd(task.month);
    const validation = {
      headerValid: 1,
      periodValid: rows.every((row) => row.periodStart === expectedStart && row.periodEnd === expectedEnd),
      categoryValid: rows.every((row) => row.category === task.category),
      dimensionValid: rows.every((row) => row.rankingDimension === task.ranking_dimension),
      scopeValid: rows.every((row) => row.scope === task.scope),
      warningCount: parsed.warnings.length,
      rawFileHash: fileHash,
      importIdentityHash,
    };
    if (!validation.periodValid || !validation.categoryValid || !validation.dimensionValid || !validation.scopeValid) throw new Error("download file validation failed");
    const stagingBatchId = `market-staging-${task.id}-${fileHash.slice(0, 16)}`;
    for (let offset = 0; offset < rows.length; offset += 80) {
      await db.batch(rows.slice(offset, offset + 80).map((row, index) => db.prepare(`INSERT OR IGNORE INTO market_download_staging_rows
        (id, task_id, file_hash, row_number, row_json) VALUES (?, ?, ?, ?, ?)`)
        .bind(`${stagingBatchId}-${offset + index + 1}`, task.id, fileHash, row.sourceRowNumber, JSON.stringify(row))));
    }
    const existingBatch = await db.prepare("SELECT id,status,created_at FROM market_import_batches WHERE file_hash=? LIMIT 1")
      .bind(importIdentityHash).first<{ id: string; status: string; created_at: string }>();
    const batchId = existingBatch?.id ?? `market-import-${task.id}-${importIdentityHash.slice(0, 16)}`;
    const duplicate = existingBatch?.status === "completed";
    if (existingBatch?.status === "processing" && Date.now() - Date.parse(existingBatch.created_at) < 30 * 60_000) {
      throw new Error("matching market import is still processing");
    }
    const stagedMetadata = await db.prepare(`UPDATE market_download_tasks SET jd_task_id=COALESCE(NULLIF(?,''), jd_task_id),
      source_file_name=?, file_hash=?, row_count=?, header_valid=1, period_valid=1, category_valid=1, dimension_valid=1,
      staging_batch_id=?, import_batch_id=?, validation_json=?, error_code='', error_message='', next_retry_at=NULL,
      last_attempt_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
      WHERE id=? AND status='downloading' AND execution_token=?`)
      .bind(downloaded.jdTaskId ?? "", downloaded.fileName, fileHash, rows.length, stagingBatchId, batchId, JSON.stringify(validation), task.id, executionToken).run() as { meta?: { changes?: number } };
    if (Number(stagedMetadata.meta?.changes ?? 0) !== 1) throw new Error("market download task lost its execution claim");
    if (!duplicate) {
      if (existingBatch) {
        await db.batch([
          db.prepare(`UPDATE market_download_tasks SET status=CASE
            WHEN status='downloading' AND execution_token=? THEN status ELSE NULL END
            WHERE id=?`).bind(executionToken, task.id),
          db.prepare("DELETE FROM market_import_staging_rows WHERE batch_id=?").bind(batchId),
          db.prepare("DELETE FROM market_import_identity_refresh_keys_v2 WHERE batch_id=?").bind(batchId),
          db.prepare("DELETE FROM market_import_range_claims WHERE batch_id=?").bind(batchId),
          db.prepare("DELETE FROM market_import_batches WHERE id=? AND status<>'completed'").bind(batchId),
        ]);
      }
      await saveMarketImportCore({
        db,
        batchId,
        sourceType: "jd_market_download",
        fileName: downloaded.fileName,
        fileSizeBytes: downloaded.bytes.byteLength,
        fileHash: importIdentityHash,
        sheetName: parsed.sheetName,
        rows,
        warnings: parsed.warnings,
        executionFence: { taskId: task.id, token: executionToken },
      });
    }
    if (duplicate) await Promise.allSettled([refreshMarketSkuGmvTotals(db), refreshMarketMasterIdentities(db)]);
    completedImport = { batchId, stagingBatchId, rowCount: rows.length, duplicate, validation };
    const completedTask = await db.prepare(`UPDATE market_download_tasks SET status='imported', execution_token='', jd_task_id=COALESCE(NULLIF(?,''), jd_task_id),
      source_file_name=?, file_hash=?, row_count=?, header_valid=1, period_valid=1, category_valid=1, dimension_valid=1,
      staging_batch_id=?, import_batch_id=?, validation_json=?, error_code='', error_message='', next_retry_at=NULL,
      last_attempt_at=CURRENT_TIMESTAMP, completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
      WHERE id=? AND ((status='downloading' AND execution_token=?) OR (status='imported' AND import_batch_id=?))`)
      .bind(downloaded.jdTaskId ?? "", downloaded.fileName, fileHash, rows.length, stagingBatchId, batchId, JSON.stringify(validation), task.id, executionToken, batchId).run() as { meta?: { changes?: number } };
    if (Number(completedTask.meta?.changes ?? 0) !== 1) throw new Error("completed import task metadata was not persisted");
    const maintenance = await Promise.allSettled([
      deps.cacheImages ? deps.cacheImages({ db, task, batchId, rows }) : Promise.resolve(null),
      deps.createPriceTasks ? deps.createPriceTasks({ db, task, batchId, rows }) : Promise.resolve(null),
    ]);
    const maintenanceFailed = maintenance.some((result) => result.status === "rejected");
    const after = await db.prepare("SELECT * FROM market_download_tasks WHERE id=?").bind(task.id).first<Record<string, unknown>>();
    await audit(db, actor, "execute_download_task", task.id, task, after);
    return { status: "imported", duplicate, maintenanceFailed, taskId: task.id, batchId, stagingBatchId, rowCount: rows.length, validation };
  } catch (error) {
    const message = error instanceof Error ? error.message : "import execution failed";
    if (completedImport) {
      await db.prepare(`UPDATE market_download_tasks SET status='imported', execution_token='', jd_task_id=COALESCE(NULLIF(?,''), jd_task_id),
        source_file_name=?, file_hash=?, row_count=?, header_valid=1, period_valid=1, category_valid=1, dimension_valid=1,
        import_batch_id=?, staging_batch_id=?, validation_json=?,
        error_code='post_import_metadata_failed', error_message=?, next_retry_at=NULL,
        last_attempt_at=CURRENT_TIMESTAMP, completed_at=COALESCE(completed_at,CURRENT_TIMESTAMP), updated_at=CURRENT_TIMESTAMP
        WHERE id=? AND execution_token=? AND status NOT IN ('imported','published')`)
        .bind(downloaded.jdTaskId ?? "", downloaded.fileName, fileHash, completedImport.rowCount,
          completedImport.batchId, completedImport.stagingBatchId, JSON.stringify(completedImport.validation), message.slice(0, 800), task.id, executionToken)
        .run().catch(() => undefined);
      return { status: "imported", maintenanceFailed: true, reconciliationPending: true, taskId: task.id, ...completedImport };
    }
    return { status: String((await failTask(db, task, actor, executionToken, "validation_or_import_failed", message, "failed"))?.status ?? "failed"), taskId: task.id };
  }
}
