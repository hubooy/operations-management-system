import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { createReadStream } from "node:fs";
import {
  mkdir,
  readFile,
  realpath,
  rename,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { DatabaseSync } from "node:sqlite";

const MANIFEST_VERSION = "sales-legacy-r2-cleanup-v1" as const;
export const LEGACY_SALES_R2_BUCKET = "site-creator-r2";

type LegacySession = {
  id: string;
  status: string;
  fileSizeBytes: number;
  chunkSizeBytes: number;
  chunkCount: number;
  receivedChunkCount: number;
  receivedBytes: number;
  createdAt: string;
  updatedAt: string;
  expiresAt: string;
};

type LegacyObject = {
  uploadId: string;
  chunkIndex: number;
  objectKey: string;
  sizeBytes: number;
  sha256: string;
  createdAt: string;
};

export type LegacySalesR2CleanupManifest = {
  version: typeof MANIFEST_VERSION;
  manifestId: string;
  cutoverId: string;
  sourcePathDigest: string;
  bucket: typeof LEGACY_SALES_R2_BUCKET;
  persistPathDigest: string;
  plannedAt: string;
  status: "planned" | "deleting" | "metadata_deleted" | "completed";
  sessions: LegacySession[];
  objects: LegacyObject[];
  coreEvidence: LegacySalesCoreEvidence;
  verifiedMissingObjectKeys: string[];
  lockedApplyRunId?: string;
  lockedVerifyRunId?: string;
  lockedVerifyRecordedAt?: string;
  metadataDeletedAt?: string;
  completedAt?: string;
};

export type LegacySalesCoreEvidence = {
  format: "sales-d1-core-evidence-v1";
  tables: Record<string, { rowCount: number; sha256: string }>;
};

type AuthorityRow = { owner: string; cutover_id: string };
type SessionRow = {
  id: string;
  status: string;
  file_size_bytes: number;
  chunk_size_bytes: number;
  chunk_count: number;
  received_chunk_count: number;
  received_bytes: number;
  created_at: string;
  updated_at: string;
  expires_at: string;
};
type ObjectRow = {
  upload_id: string;
  chunk_index: number;
  object_key: string;
  size_bytes: number;
  sha256: string;
  created_at: string;
};

export type LocalR2Client = {
  deleteObject(objectKey: string): Promise<void>;
  inspectObject(objectKey: string): Promise<{ sizeBytes: number; sha256: string } | null>;
};

function sha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(",")}}`;
}

function safeInteger(value: unknown, label: string, minimum = 0): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum) {
    throw new Error(`${label} 无效`);
  }
  return value;
}

function assertExactKeys(value: object, expected: readonly string[], label: string): void {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (canonicalJson(actual) !== canonicalJson(wanted)) throw new Error(`${label} 字段集合无效`);
}

function validateTimestamp(value: string, label: string): string {
  if (!value || !Number.isFinite(Date.parse(value))) throw new Error(`${label} 包含无效时间`);
  return value;
}

function validateObjectKey(uploadId: string, objectKey: string): void {
  if (!/^[A-Za-z0-9-]{1,128}$/.test(uploadId)) throw new Error("清理清单 uploadId 无效");
  const expectedPrefix = `sales-upload/${uploadId}/`;
  if (!objectKey.startsWith(expectedPrefix)
    || objectKey.length <= expectedPrefix.length
    || objectKey.length > 1024
    || objectKey.includes("\\")
    || /[\u0000-\u001f\u007f]/.test(objectKey)) {
    throw new Error("清理清单包含越界 R2 object_key");
  }
}

function validateManifestObject(item: LegacyObject): LegacyObject {
  validateObjectKey(item.uploadId, item.objectKey);
  safeInteger(item.chunkIndex, "清理清单 chunkIndex");
  safeInteger(item.sizeBytes, "清理清单 sizeBytes", 1);
  if (!/^[0-9a-f]{64}$/.test(item.sha256)) throw new Error("清理清单 sha256 无效");
  validateTimestamp(item.createdAt, "清理清单 object createdAt");
  return item;
}

export function validateLegacySalesR2CleanupManifest(input: unknown): LegacySalesR2CleanupManifest {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new Error("R2 清理清单不是对象");
  const manifest = input as LegacySalesR2CleanupManifest;
  const baseKeys = [
    "version", "manifestId", "cutoverId", "sourcePathDigest", "bucket",
    "persistPathDigest", "plannedAt", "status", "sessions", "objects",
    "coreEvidence", "verifiedMissingObjectKeys",
  ];
  const statusKeys: Record<string, string[]> = {
    planned: [],
    deleting: ["lockedApplyRunId", "lockedVerifyRunId", "lockedVerifyRecordedAt"],
    metadata_deleted: [
      "lockedApplyRunId", "lockedVerifyRunId", "lockedVerifyRecordedAt", "metadataDeletedAt",
    ],
    completed: [
      "lockedApplyRunId", "lockedVerifyRunId", "lockedVerifyRecordedAt", "metadataDeletedAt", "completedAt",
    ],
  };
  if (typeof manifest.status !== "string" || !statusKeys[manifest.status]) {
    throw new Error("R2 清理清单状态无效");
  }
  assertExactKeys(manifest, [...baseKeys, ...statusKeys[manifest.status]], "R2 清理清单");
  if (manifest.version !== MANIFEST_VERSION
    || !/^[0-9a-f]{64}$/.test(manifest.manifestId)
    || !/^[A-Za-z0-9._:-]{8,128}$/.test(manifest.cutoverId)
    || !/^[0-9a-f]{64}$/.test(manifest.sourcePathDigest)
    || manifest.bucket !== LEGACY_SALES_R2_BUCKET
    || !/^[0-9a-f]{64}$/.test(manifest.persistPathDigest)
    || !["planned", "deleting", "metadata_deleted", "completed"].includes(manifest.status)
    || !Array.isArray(manifest.sessions)
    || !Array.isArray(manifest.objects)
    || !manifest.coreEvidence
    || manifest.coreEvidence.format !== "sales-d1-core-evidence-v1"
    || typeof manifest.coreEvidence.tables !== "object"
    || !Array.isArray(manifest.verifiedMissingObjectKeys)) {
    throw new Error("R2 清理清单契约无效");
  }
  validateTimestamp(manifest.plannedAt, "R2 清理清单 plannedAt");
  const sessionIds = new Set<string>();
  for (const session of manifest.sessions) {
    if (!session || typeof session !== "object" || Array.isArray(session)) {
      throw new Error("R2 清理清单会话无效");
    }
    assertExactKeys(session, [
      "id", "status", "fileSizeBytes", "chunkSizeBytes", "chunkCount",
      "receivedChunkCount", "receivedBytes", "createdAt", "updatedAt", "expiresAt",
    ], "R2 清理清单会话");
    if (!session || !/^[A-Za-z0-9-]{1,128}$/.test(session.id) || sessionIds.has(session.id)) {
      throw new Error("R2 清理清单会话身份无效或重复");
    }
    sessionIds.add(session.id);
    safeInteger(session.fileSizeBytes, "R2 清理清单 fileSizeBytes", 1);
    safeInteger(session.chunkSizeBytes, "R2 清理清单 chunkSizeBytes", 1);
    safeInteger(session.chunkCount, "R2 清理清单 chunkCount", 1);
    safeInteger(session.receivedChunkCount, "R2 清理清单 receivedChunkCount");
    safeInteger(session.receivedBytes, "R2 清理清单 receivedBytes");
    validateTimestamp(session.createdAt, "R2 清理清单 createdAt");
    validateTimestamp(session.updatedAt, "R2 清理清单 updatedAt");
    validateTimestamp(session.expiresAt, "R2 清理清单 expiresAt");
  }
  const objectKeys = new Set<string>();
  for (const item of manifest.objects) {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      throw new Error("R2 清理清单对象无效");
    }
    assertExactKeys(item, [
      "uploadId", "chunkIndex", "objectKey", "sizeBytes", "sha256", "createdAt",
    ], "R2 清理清单对象");
    validateManifestObject(item);
    if (!sessionIds.has(item.uploadId) || objectKeys.has(item.objectKey)) {
      throw new Error("R2 清理清单 object_key 身份无效或重复");
    }
    objectKeys.add(item.objectKey);
  }
  if (manifest.verifiedMissingObjectKeys.some((key) => !objectKeys.has(key))
    || new Set(manifest.verifiedMissingObjectKeys).size !== manifest.verifiedMissingObjectKeys.length) {
    throw new Error("R2 清理清单缺失回查集合无效");
  }
  const hasLockedApply = manifest.lockedApplyRunId !== undefined;
  const hasLockedRun = manifest.lockedVerifyRunId !== undefined;
  const hasLockedTime = manifest.lockedVerifyRecordedAt !== undefined;
  if (hasLockedApply !== hasLockedRun
    || hasLockedRun !== hasLockedTime
    || (hasLockedApply && !/^[0-9a-f]{32,64}$/i.test(manifest.lockedApplyRunId ?? ""))
    || (hasLockedRun && !/^[0-9a-f]{32,64}$/i.test(manifest.lockedVerifyRunId ?? ""))
    || (hasLockedTime && !Number.isFinite(Date.parse(manifest.lockedVerifyRecordedAt ?? "")))) {
    throw new Error("R2 清理清单 locked verify 证明无效");
  }
  if (manifest.status === "completed" && (!hasLockedRun || !hasLockedTime)) {
    throw new Error("已完成 R2 清理清单缺少 locked verify 证明");
  }
  if (manifest.status === "completed"
    && canonicalJson(manifest.verifiedMissingObjectKeys) !== canonicalJson([...objectKeys].sort())) {
    throw new Error("已完成 R2 清理清单缺失回查未覆盖全部对象");
  }
  assertExactKeys(manifest.coreEvidence, ["format", "tables"], "R2 清理清单核心证据");
  const evidenceNames = Object.keys(manifest.coreEvidence.tables).sort();
  const expectedEvidenceNames = CORE_TABLES.map((item) => item.name).sort();
  if (canonicalJson(evidenceNames) !== canonicalJson(expectedEvidenceNames)) {
    throw new Error("R2 清理清单核心证据表集合无效");
  }
  for (const name of evidenceNames) {
    const evidence = manifest.coreEvidence.tables[name];
    if (!evidence || typeof evidence !== "object" || Array.isArray(evidence)) {
      throw new Error("R2 清理清单核心证据摘要无效");
    }
    assertExactKeys(evidence, ["rowCount", "sha256"], `R2 清理清单核心证据 ${name}`);
    if (!evidence || !Number.isSafeInteger(evidence.rowCount) || evidence.rowCount < 0
      || !/^[0-9a-f]{64}$/.test(evidence.sha256)) {
      throw new Error("R2 清理清单核心证据摘要无效");
    }
  }
  const expectedId = sha256(canonicalJson({
    version: manifest.version,
    cutoverId: manifest.cutoverId,
    sourcePathDigest: manifest.sourcePathDigest,
    bucket: manifest.bucket,
    persistPathDigest: manifest.persistPathDigest,
    plannedAt: manifest.plannedAt,
    sessions: manifest.sessions,
    objects: manifest.objects,
    coreEvidence: manifest.coreEvidence,
  }));
  if (manifest.manifestId !== expectedId) throw new Error("R2 清理清单摘要不匹配");
  return manifest;
}

const CORE_TABLES = [
  { name: "sales_order_lines", where: "" },
  { name: "sales_import_batches", where: "" },
  { name: "sales_overview_cache_state", where: "WHERE id = 1" },
  { name: "sales_projection_source_state", where: "WHERE id = 1" },
  { name: "sales_overview_response_cache", where: "" },
  { name: "sales_projection_outbox", where: "WHERE domain = 'sales'" },
  { name: "import_content_fingerprints", where: "WHERE domain = 'sales'" },
  { name: "import_content_attempts", where: "WHERE domain = 'sales'" },
  { name: "import_scope_heads", where: "WHERE domain = 'sales'" },
] as const;

export function salesD1CoreEvidence(database: DatabaseSync): LegacySalesCoreEvidence {
  const tables: LegacySalesCoreEvidence["tables"] = {};
  for (const spec of CORE_TABLES) {
    const columns = database.prepare(`PRAGMA table_info("${spec.name}")`).all() as Array<Record<string, unknown>>;
    if (columns.length === 0) throw new Error(`D1 缺少核心销售表 ${spec.name}`);
    const columnNames = columns.map((column) => String(column.name));
    const digest = createHash("sha256");
    digest.update(canonicalJson({
      format: "sales-d1-core-table-v1",
      table: spec.name,
      where: spec.where,
      schema: columns.map((column) => ({
        cid: Number(column.cid),
        name: String(column.name),
        type: String(column.type),
        notnull: Number(column.notnull),
        default: column.dflt_value == null ? null : String(column.dflt_value),
        pk: Number(column.pk),
      })),
    }));
    let rowCount = 0;
    const rows = database.prepare(
      `SELECT rowid AS "__teruisi_rowid", * FROM "${spec.name}" ${spec.where} ORDER BY rowid ASC`,
    ).iterate() as Iterable<Record<string, unknown>>;
    for (const row of rows) {
      const serialized = canonicalJson([
        row.__teruisi_rowid,
        ...columnNames.map((column) => row[column]),
      ]);
      digest.update(`${Buffer.byteLength(serialized, "utf8")}:`);
      digest.update(serialized, "utf8");
      rowCount += 1;
    }
    tables[spec.name] = { rowCount, sha256: digest.digest("hex") };
  }
  return { format: "sales-d1-core-evidence-v1", tables };
}

export async function writeLegacySalesR2CleanupManifest(
  filePath: string,
  value: LegacySalesR2CleanupManifest,
): Promise<void> {
  const directory = path.dirname(filePath);
  await mkdir(directory, { recursive: true });
  const temporary = path.join(directory, `.${path.basename(filePath)}.${randomUUID()}.tmp`);
  try {
    await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, { encoding: "utf8", flag: "wx" });
    await rename(temporary, filePath);
  } finally {
    await rm(temporary, { force: true }).catch(() => undefined);
  }
}

function currentAuthority(database: DatabaseSync, allowMissing = false): AuthorityRow | null {
  if (allowMissing && !database.prepare(
    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sales_write_authority' LIMIT 1",
  ).get()) return null;
  const row = database.prepare(
    "SELECT owner, cutover_id FROM sales_write_authority WHERE id = 1 LIMIT 1",
  ).get() as AuthorityRow | undefined;
  if (!row && allowMissing) return null;
  if (!row || row.owner !== "d1") {
    throw new Error("legacy R2 清理只允许在 D1 仍为销售写入所有者时执行");
  }
  return row;
}

function snapshotLegacyRows(database: DatabaseSync, nowIso: string, allowMissingAuthority = false) {
  currentAuthority(database, allowMissingAuthority);
  const invalidExpiry = safeInteger((database.prepare(
    "SELECT COUNT(*) count FROM sales_import_uploads WHERE datetime(expires_at) IS NULL",
  ).get() as { count: number }).count, "无效 expires_at 计数");
  if (invalidExpiry !== 0) throw new Error("D1 上传会话包含无效 expires_at，拒绝推断过期范围");

  const orphanChunks = safeInteger((database.prepare(
    `SELECT COUNT(*) count FROM sales_import_upload_chunks chunk
     LEFT JOIN sales_import_uploads upload ON upload.id = chunk.upload_id
     WHERE upload.id IS NULL`,
  ).get() as { count: number }).count, "孤立分片计数");
  if (orphanChunks !== 0) throw new Error("D1 存在无法绑定上传会话的孤立分片，拒绝清理");

  const unexpiredChunks = safeInteger((database.prepare(
    `SELECT COUNT(*) count FROM sales_import_upload_chunks chunk
     JOIN sales_import_uploads upload ON upload.id = chunk.upload_id
     WHERE upload.status <> 'completed' AND datetime(upload.expires_at) > datetime(?)`,
  ).get(nowIso) as { count: number }).count, "未过期分片计数");
  if (unexpiredChunks !== 0) throw new Error("D1 仍有未过期会话持有 R2 分片，拒绝清理");

  const sessions = (database.prepare(
    `SELECT id, status, file_size_bytes, chunk_size_bytes, chunk_count,
            received_chunk_count, received_bytes, created_at, updated_at, expires_at
     FROM sales_import_uploads
     WHERE status = 'completed' OR datetime(expires_at) <= datetime(?)
     ORDER BY id COLLATE BINARY`,
  ).all(nowIso) as SessionRow[]).map((row) => ({
    id: String(row.id),
    status: String(row.status),
    fileSizeBytes: safeInteger(row.file_size_bytes, "D1 file_size_bytes", 1),
    chunkSizeBytes: safeInteger(row.chunk_size_bytes, "D1 chunk_size_bytes", 1),
    chunkCount: safeInteger(row.chunk_count, "D1 chunk_count", 1),
    receivedChunkCount: safeInteger(row.received_chunk_count, "D1 received_chunk_count"),
    receivedBytes: safeInteger(row.received_bytes, "D1 received_bytes"),
    createdAt: validateTimestamp(String(row.created_at), "D1 created_at"),
    updatedAt: validateTimestamp(String(row.updated_at), "D1 updated_at"),
    expiresAt: validateTimestamp(String(row.expires_at), "D1 expires_at"),
  }));
  const objects = (database.prepare(
    `SELECT chunk.upload_id, chunk.chunk_index, chunk.object_key, chunk.size_bytes,
            chunk.sha256, chunk.created_at
     FROM sales_import_upload_chunks chunk
     JOIN sales_import_uploads upload ON upload.id = chunk.upload_id
     WHERE upload.status = 'completed' OR datetime(upload.expires_at) <= datetime(?)
     ORDER BY chunk.upload_id COLLATE BINARY, chunk.chunk_index ASC`,
  ).all(nowIso) as ObjectRow[]).map((row) => validateManifestObject({
    uploadId: String(row.upload_id),
    chunkIndex: safeInteger(row.chunk_index, "D1 chunk_index"),
    objectKey: String(row.object_key),
    sizeBytes: safeInteger(row.size_bytes, "D1 size_bytes", 1),
    sha256: String(row.sha256).toLowerCase(),
    createdAt: validateTimestamp(String(row.created_at), "D1 chunk created_at"),
  }));
  const totalChunks = safeInteger((database.prepare(
    "SELECT COUNT(*) count FROM sales_import_upload_chunks",
  ).get() as { count: number }).count, "D1 分片总数");
  const totalUploads = safeInteger((database.prepare(
    "SELECT COUNT(*) count FROM sales_import_uploads",
  ).get() as { count: number }).count, "D1 上传会话总数");
  if (objects.length !== totalChunks) throw new Error("不是全部 D1 分片都能证明属于已过期会话，拒绝清理");
  if (sessions.length !== totalUploads) {
    throw new Error("不是全部 D1 上传会话都能证明 completed 或已过期，拒绝清理");
  }
  if (new Set(objects.map((item) => item.objectKey)).size !== objects.length) {
    throw new Error("D1 分片 object_key 重复，拒绝不确定删除");
  }
  return { sessions, objects };
}

export async function validateLocalR2Target(bucket: string, persistTo: string): Promise<string> {
  if (bucket !== LEGACY_SALES_R2_BUCKET) throw new Error(`本工具只允许本机桶 ${LEGACY_SALES_R2_BUCKET}`);
  if (!path.isAbsolute(persistTo)) throw new Error("--persist-to 必须是精确的绝对路径");
  const resolved = await realpath(persistTo);
  const info = await stat(resolved);
  if (!info.isDirectory()
    || path.basename(resolved).toLowerCase() !== "state"
    || path.basename(path.dirname(resolved)).toLowerCase() !== ".wrangler") {
    throw new Error("--persist-to 必须精确指向 .wrangler/state 目录");
  }
  const r2Directory = path.join(resolved, "v3", "r2");
  if (!(await stat(r2Directory).catch(() => null))?.isDirectory()) {
    throw new Error("--persist-to 不包含本机 v3/r2 状态目录");
  }
  return resolved;
}

export function createLegacyCleanupManifest(
  database: DatabaseSync,
  input: {
    sourcePathDigest: string;
    persistPathDigest: string;
    cutoverId: string;
    plannedAt: string;
  },
): LegacySalesR2CleanupManifest {
  if (!/^[0-9a-f]{64}$/.test(input.sourcePathDigest)
    || !/^[0-9a-f]{64}$/.test(input.persistPathDigest)) {
    throw new Error("清理来源摘要无效");
  }
  if (!/^[A-Za-z0-9._:-]{8,128}$/.test(input.cutoverId)) throw new Error("cutoverId 无效");
  validateTimestamp(input.plannedAt, "plannedAt");
  const rows = snapshotLegacyRows(database, input.plannedAt, true);
  const identity = {
    version: MANIFEST_VERSION,
    cutoverId: input.cutoverId,
    sourcePathDigest: input.sourcePathDigest,
    bucket: LEGACY_SALES_R2_BUCKET as typeof LEGACY_SALES_R2_BUCKET,
    persistPathDigest: input.persistPathDigest,
    plannedAt: input.plannedAt,
    sessions: rows.sessions,
    objects: rows.objects,
    coreEvidence: salesD1CoreEvidence(database),
  };
  return {
    ...identity,
    manifestId: sha256(canonicalJson(identity)),
    status: "planned",
    verifiedMissingObjectKeys: [],
  };
}

function rowsMatchManifest(
  database: DatabaseSync,
  manifest: LegacySalesR2CleanupManifest,
  allowMissingAuthority = false,
): boolean {
  const rows = snapshotLegacyRows(database, manifest.plannedAt, allowMissingAuthority);
  return canonicalJson(rows.sessions) === canonicalJson(manifest.sessions)
    && canonicalJson(rows.objects) === canonicalJson(manifest.objects)
    && canonicalJson(salesD1CoreEvidence(database)) === canonicalJson(manifest.coreEvidence);
}

function authorityMatchesCleanupRecovery(database: DatabaseSync, manifest: LegacySalesR2CleanupManifest): boolean {
  const authorityTable = database.prepare(
    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sales_write_authority' LIMIT 1",
  ).get();
  if (!authorityTable) return false;
  const row = database.prepare(
    "SELECT owner, cutover_id FROM sales_write_authority WHERE id = 1 LIMIT 1",
  ).get() as AuthorityRow | undefined;
  return Boolean(row && (row.owner === "d1" || (
    (row.owner === "pending" || row.owner === "postgresql")
    && row.cutover_id === manifest.cutoverId
    && /^[0-9a-f]{32,64}$/i.test(manifest.lockedApplyRunId ?? "")
  )));
}

export function legacySalesMetadataAlreadyDeleted(
  database: DatabaseSync,
  manifest: LegacySalesR2CleanupManifest,
): boolean {
  if (!authorityMatchesCleanupRecovery(database, manifest)) return false;
  const chunks = safeInteger((database.prepare(
    "SELECT COUNT(*) count FROM sales_import_upload_chunks",
  ).get() as { count: number }).count, "D1 分片总数");
  if (chunks !== 0) return false;
  const uploads = safeInteger((database.prepare(
    "SELECT COUNT(*) count FROM sales_import_uploads",
  ).get() as { count: number }).count, "D1 上传会话总数");
  if (uploads !== 0) return false;
  const statement = database.prepare("SELECT COUNT(*) count FROM sales_import_uploads WHERE id = ?");
  return manifest.sessions.every((session) => Number((statement.get(session.id) as { count: number }).count) === 0);
}

function atomicCleanupAlreadyCommitted(
  database: DatabaseSync,
  manifest: LegacySalesR2CleanupManifest,
): boolean {
  const row = database.prepare(
    "SELECT owner, cutover_id FROM sales_write_authority WHERE id = 1 LIMIT 1",
  ).get() as AuthorityRow | undefined;
  return Boolean(
    row
    && (row.owner === "pending" || row.owner === "postgresql")
    && row.cutover_id === manifest.cutoverId
    && /^[0-9a-f]{32,64}$/i.test(manifest.lockedVerifyRunId ?? "")
    && manifest.lockedVerifyRecordedAt
    && Number.isFinite(Date.parse(manifest.lockedVerifyRecordedAt))
    && canonicalJson(manifest.verifiedMissingObjectKeys)
      === canonicalJson(manifest.objects.map((item) => item.objectKey).sort())
    && legacySalesMetadataAlreadyDeleted(database, manifest),
  );
}

export function assertLegacySalesCleanupSource(
  database: DatabaseSync,
  manifest: LegacySalesR2CleanupManifest,
): void {
  if (!rowsMatchManifest(database, manifest)) {
    throw new Error("D1 上传清单或非 staging 核心证据已变化，拒绝清理");
  }
}

export function deleteLegacySalesMetadataInOpenTransaction(
  database: DatabaseSync,
  manifest: LegacySalesR2CleanupManifest,
): void {
  assertLegacySalesCleanupSource(database, manifest);
  const verified = new Set(manifest.verifiedMissingObjectKeys);
  if (manifest.objects.some((item) => !verified.has(item.objectKey))) {
    throw new Error("仍有 R2 object_key 未完成缺失回查");
  }
  const deleteChunk = database.prepare(
    `DELETE FROM sales_import_upload_chunks
     WHERE upload_id = ? AND chunk_index = ? AND object_key = ? AND size_bytes = ?
       AND lower(sha256) = ? AND created_at = ?`,
  );
  for (const item of manifest.objects) {
    const result = deleteChunk.run(
      item.uploadId, item.chunkIndex, item.objectKey, item.sizeBytes, item.sha256, item.createdAt,
    );
    if (Number(result.changes) !== 1) throw new Error("D1 分片元数据 CAS 删除失败");
  }
  const deleteSession = database.prepare(
    `DELETE FROM sales_import_uploads
     WHERE id = ? AND status = ? AND file_size_bytes = ? AND chunk_size_bytes = ?
       AND chunk_count = ? AND received_chunk_count = ? AND received_bytes = ?
       AND created_at = ? AND updated_at = ? AND expires_at = ?
       AND (status = 'completed' OR datetime(expires_at) <= datetime(?))`,
  );
  for (const session of manifest.sessions) {
    const result = deleteSession.run(
      session.id, session.status, session.fileSizeBytes, session.chunkSizeBytes,
      session.chunkCount, session.receivedChunkCount, session.receivedBytes,
      session.createdAt, session.updatedAt, session.expiresAt, manifest.plannedAt,
    );
    if (Number(result.changes) !== 1) throw new Error("D1 过期上传会话 CAS 删除失败");
  }
  const remaining = safeInteger((database.prepare(
    "SELECT COUNT(*) count FROM sales_import_upload_chunks",
  ).get() as { count: number }).count, "D1 分片剩余数");
  if (remaining !== 0) throw new Error("D1 仍有上传分片，拒绝完成清理");
  if (canonicalJson(salesD1CoreEvidence(database)) !== canonicalJson(manifest.coreEvidence)) {
    throw new Error("清理改变了非 staging 销售事实、控制状态或 revision，拒绝提交");
  }
}

export async function deleteLegacySalesR2ManifestObjects(input: {
  manifest: LegacySalesR2CleanupManifest;
  manifestPath: string;
  client: LocalR2Client;
}): Promise<LegacySalesR2CleanupManifest> {
  let manifest = { ...input.manifest, status: "deleting" as const };
  await writeLegacySalesR2CleanupManifest(input.manifestPath, manifest);
  const verified = new Set(manifest.verifiedMissingObjectKeys);
  for (const item of manifest.objects) {
    const keyDigest = sha256(item.objectKey);
    let current: { sizeBytes: number; sha256: string } | null;
    try {
      current = await input.client.inspectObject(item.objectKey);
    } catch {
      throw new Error(`R2 对象删除前核验失败（keySha256=${keyDigest}）`);
    }
    if (current !== null) {
      if (current.sizeBytes !== item.sizeBytes || current.sha256.toLowerCase() !== item.sha256) {
        throw new Error(`R2 对象内容与已审批清单不一致（keySha256=${keyDigest}）`);
      }
      try {
        await input.client.deleteObject(item.objectKey);
      } catch {
        throw new Error(`R2 精确对象删除失败（keySha256=${keyDigest}）`);
      }
    }
    try {
      if (await input.client.inspectObject(item.objectKey) !== null) {
        throw new Error("still-present");
      }
    } catch {
      throw new Error(`R2 对象删除后缺失回查失败（keySha256=${keyDigest}）`);
    }
    verified.add(item.objectKey);
    manifest = { ...manifest, verifiedMissingObjectKeys: [...verified].sort() };
    await writeLegacySalesR2CleanupManifest(input.manifestPath, manifest);
  }
  return manifest;
}

async function resolveCleanupPaths(input: {
  source: string;
  bucket: string;
  persistTo: string;
  manifestPath: string;
}) {
  if (!path.isAbsolute(input.source) || path.extname(input.source).toLowerCase() !== ".sqlite") {
    throw new Error("--source 必须是精确的绝对 .sqlite 路径");
  }
  const source = await realpath(input.source);
  if (!(await stat(source)).isFile()) throw new Error("--source 不是 SQLite 文件");
  if (!path.isAbsolute(input.manifestPath) || path.extname(input.manifestPath).toLowerCase() !== ".json") {
    throw new Error("--manifest 必须是精确的绝对 .json 路径");
  }
  const persistTo = await validateLocalR2Target(input.bucket, input.persistTo);
  const sourcePathDigest = sha256(source);
  const persistPathDigest = sha256(persistTo);
  return { source, persistTo, sourcePathDigest, persistPathDigest };
}

export async function readLegacySalesR2CleanupManifest(
  manifestPath: string,
): Promise<LegacySalesR2CleanupManifest> {
  const raw = await readFile(manifestPath, "utf8");
  return validateLegacySalesR2CleanupManifest(JSON.parse(raw));
}

export async function planLegacySalesR2Cleanup(input: {
  source: string;
  cutoverId: string;
  bucket: string;
  persistTo: string;
  manifestPath: string;
  now?: Date;
}): Promise<LegacySalesR2CleanupManifest> {
  const resolved = await resolveCleanupPaths(input);
  const existing = await readFile(input.manifestPath, "utf8").then(JSON.parse).catch((error: unknown) => {
    const code = (error as NodeJS.ErrnoException)?.code;
    if (code === "ENOENT") return null;
    throw error;
  });
  const database = new DatabaseSync(resolved.source, { readOnly: true });
  try {
    const manifest = existing
      ? validateLegacySalesR2CleanupManifest(existing)
      : createLegacyCleanupManifest(database, {
        sourcePathDigest: resolved.sourcePathDigest,
        persistPathDigest: resolved.persistPathDigest,
        cutoverId: input.cutoverId,
        plannedAt: (input.now ?? new Date()).toISOString(),
      });
    if (manifest.sourcePathDigest !== resolved.sourcePathDigest
      || manifest.persistPathDigest !== resolved.persistPathDigest
      || manifest.bucket !== input.bucket
      || manifest.cutoverId !== input.cutoverId) {
      throw new Error("现有 R2 清理清单不属于本次来源或本机状态目录");
    }
    if (existing) {
      if (!legacySalesMetadataAlreadyDeleted(database, manifest)
        && !rowsMatchManifest(database, manifest, true)) {
        throw new Error("现有 R2 清理清单与 D1 当前精确行不一致");
      }
    } else {
      await writeLegacySalesR2CleanupManifest(input.manifestPath, manifest);
    }
    return manifest;
  } finally {
    database.close();
  }
}

export async function executeLegacySalesR2Cleanup(input: {
  source: string;
  cutoverId: string;
  bucket: string;
  persistTo: string;
  manifestPath: string;
  approvedManifestId: string;
  client?: LocalR2Client;
  beforeLockedCleanup: () => Promise<{ applyRunId: string; runId: string }>;
  finalizeD1: (database: DatabaseSync, manifest: LegacySalesR2CleanupManifest) => void;
}): Promise<LegacySalesR2CleanupManifest> {
  if (typeof input.beforeLockedCleanup !== "function" || typeof input.finalizeD1 !== "function") {
    throw new Error("R2 清理只能由 cutover 原子 locked-verify/finalize 流程执行");
  }
  const resolved = await resolveCleanupPaths(input);
  let manifest = await readLegacySalesR2CleanupManifest(input.manifestPath);
  if (manifest.manifestId !== input.approvedManifestId || !/^[0-9a-f]{64}$/.test(input.approvedManifestId)) {
    throw new Error("--approved-manifest-id 与已审核 R2 清理清单不一致");
  }
  if (manifest.sourcePathDigest !== resolved.sourcePathDigest
    || manifest.persistPathDigest !== resolved.persistPathDigest
    || manifest.bucket !== input.bucket
    || manifest.cutoverId !== input.cutoverId) {
    throw new Error("已审核 R2 清理清单不属于本次 cutover/来源/本机状态目录");
  }
  const database = new DatabaseSync(resolved.source);
  try {
    if (atomicCleanupAlreadyCommitted(database, manifest)) {
      manifest = {
        ...manifest,
        status: "completed",
        metadataDeletedAt: manifest.metadataDeletedAt ?? new Date().toISOString(),
        completedAt: manifest.completedAt ?? new Date().toISOString(),
      };
      await writeLegacySalesR2CleanupManifest(input.manifestPath, manifest);
      return manifest;
    }
    database.exec("BEGIN IMMEDIATE");
    try {
      assertLegacySalesCleanupSource(database, manifest);
      const lockedVerify = await input.beforeLockedCleanup();
      if (!lockedVerify || !/^[0-9a-f]{32,64}$/i.test(lockedVerify.runId)) {
        throw new Error("D1 写锁内最终 verify 未返回有效 runId");
      }
      if (!/^[0-9a-f]{32,64}$/i.test(lockedVerify.applyRunId)) {
        throw new Error("D1 写锁内最终 verify 未绑定有效 applyRunId");
      }
      manifest = {
        ...manifest,
        lockedApplyRunId: lockedVerify.applyRunId,
        lockedVerifyRunId: lockedVerify.runId,
        lockedVerifyRecordedAt: new Date().toISOString(),
      };
      await writeLegacySalesR2CleanupManifest(input.manifestPath, manifest);
      const client = input.client ?? createWranglerLocalR2Client({
        bucket: input.bucket,
        persistTo: resolved.persistTo,
        temporaryDirectory: path.dirname(input.manifestPath),
      });
      manifest = await deleteLegacySalesR2ManifestObjects({
        manifest,
        manifestPath: input.manifestPath,
        client,
      });
      deleteLegacySalesMetadataInOpenTransaction(database, manifest);
      input.finalizeD1(database, manifest);
      database.exec("COMMIT");
    } catch (error) {
      database.exec("ROLLBACK");
      throw error;
    }
    manifest = {
      ...manifest,
      status: "metadata_deleted",
      metadataDeletedAt: new Date().toISOString(),
    };
    await writeLegacySalesR2CleanupManifest(input.manifestPath, manifest);
    if (!atomicCleanupAlreadyCommitted(database, manifest)) {
      throw new Error("D1 元数据与 pending 原子提交后回查失败");
    }
    manifest = { ...manifest, status: "completed", completedAt: new Date().toISOString() };
    await writeLegacySalesR2CleanupManifest(input.manifestPath, manifest);
    return manifest;
  } finally {
    database.close();
  }
}

async function runProcess(command: string, args: string[]): Promise<{ code: number; output: string }> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { shell: false, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    let output = "";
    child.stdout.setEncoding("utf8").on("data", (chunk) => { output += chunk; });
    child.stderr.setEncoding("utf8").on("data", (chunk) => { output += chunk; });
    child.on("error", reject);
    child.on("close", (code) => resolve({ code: Number(code ?? -1), output: output.slice(-8_000) }));
  });
}

export function createWranglerLocalR2Client(input: {
  bucket: string;
  persistTo: string;
  temporaryDirectory: string;
  wranglerCliPath?: string;
  processRunner?: typeof runProcess;
}): LocalR2Client {
  if (input.bucket !== LEGACY_SALES_R2_BUCKET) throw new Error("Wrangler R2 客户端桶名无效");
  const configuredCli = String(input.wranglerCliPath ?? process.env.TERUISI_WRANGLER_CLI_JS ?? "").trim();
  if (configuredCli && (!path.isAbsolute(configuredCli) || path.basename(configuredCli) !== "cli.js")) {
    throw new Error("受控 Wrangler CLI 必须是绝对 cli.js 路径");
  }
  if (!configuredCli && process.env.TERUISI_DJANGO_ENVIRONMENT === "production") {
    throw new Error("生产 R2 清理必须显式绑定受保护的 Wrangler CLI");
  }
  const executable = configuredCli ? process.execPath : (process.platform === "win32" ? "npx.cmd" : "npx");
  const base = configuredCli ? [configuredCli, "r2", "object"] : ["wrangler", "r2", "object"];
  const processRunner = input.processRunner ?? runProcess;
  return {
    async deleteObject(objectKey) {
      validateObjectKey(objectKey.split("/")[1] ?? "", objectKey);
      const result = await processRunner(executable, [
        ...base, "delete", `${input.bucket}/${objectKey}`,
        "--local", "--persist-to", input.persistTo, "--force",
      ]);
      if (result.code !== 0) {
        throw new Error(`Wrangler 精确删除 R2 对象失败（keySha256=${sha256(objectKey)}, exitCode=${result.code}）`);
      }
    },
    async inspectObject(objectKey) {
      const temporary = path.join(input.temporaryDirectory, `.r2-probe-${randomUUID()}.bin`);
      try {
        const result = await processRunner(executable, [
          ...base, "get", `${input.bucket}/${objectKey}`,
          "--local", "--persist-to", input.persistTo, "--file", temporary,
        ]);
        if (result.code === 0) {
          const info = await stat(temporary);
          const digest = createHash("sha256");
          for await (const chunk of createReadStream(temporary)) digest.update(chunk);
          return { sizeBytes: info.size, sha256: digest.digest("hex") };
        }
        if (/not[ -]?found|no such key|10007|does not exist/i.test(result.output)) return null;
        throw new Error(`Wrangler R2 核验失败（keySha256=${sha256(objectKey)}, exitCode=${result.code}）`);
      } finally {
        await rm(temporary, { force: true }).catch(() => undefined);
      }
    },
  };
}

function parseArguments(argv: readonly string[]) {
  const values = new Map<string, string>();
  const valued = new Set(["--source", "--cutover-id", "--bucket", "--persist-to", "--manifest"]);
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key?.startsWith("--")) throw new Error(`未知参数：${key ?? ""}`);
    if (key === "--execute") {
      throw new Error("独立 R2 工具禁止 execute；真实清理只能由 sales-local-cutover 原子编排执行");
    }
    if (key === "--dry-run") {
      values.set(key, "true");
      continue;
    }
    if (!valued.has(key)) throw new Error(`未知参数：${key}`);
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) throw new Error(`${key} 缺少参数值`);
    values.set(key, value);
    index += 1;
  }
  if (!values.has("--dry-run")) throw new Error("独立 R2 工具只允许显式 --dry-run");
  return {
    mode: "dry-run" as const,
    source: values.get("--source") ?? "",
    cutoverId: values.get("--cutover-id") ?? "",
    bucket: values.get("--bucket") ?? "",
    persistTo: values.get("--persist-to") ?? "",
    manifestPath: values.get("--manifest") ?? "",
  };
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  const result = await planLegacySalesR2Cleanup(options);
  process.stdout.write(`${JSON.stringify({
    status: "dry_run_completed",
    manifestId: result.manifestId,
    sessions: result.sessions.length,
    objects: result.objects.length,
    manifestPath: path.resolve(options.manifestPath),
  })}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error: unknown) => {
    process.stderr.write(`${error instanceof Error ? error.message : "legacy R2 清理失败"}\n`);
    process.exitCode = 1;
  });
}
