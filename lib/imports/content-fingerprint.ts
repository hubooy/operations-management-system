import type { env } from "cloudflare:workers";

import { PublicApiError } from "../http/api-error";

export type ImportFingerprintDatabase = NonNullable<typeof env.DB>;

export type ImportContentFingerprint = {
  domain: string;
  scopeKey: string;
  scopeJson: string;
  contentHash: string;
  rowCount: number;
};

export type StoredImportFingerprint = ImportContentFingerprint & {
  batchId: string;
  importHash: string;
  rawFileHash: string;
  status: "processing" | "completed";
  createdAt: string;
};

export type ImportAttemptMetadata = {
  fileName?: string;
  fileSizeBytes?: number;
  actor?: string;
  warnings?: readonly unknown[];
  warningTotalCount?: number;
  warningsTruncated?: boolean;
};

export type ImportReservationFence = {
  domain: string;
  scopeKey: string;
  batchId: string;
  attemptId: string;
};

type FingerprintRow = {
  sequence: number;
  domain: string;
  batch_id: string;
  scope_key: string;
  scope_json: string;
  import_hash: string;
  raw_file_hash: string;
  content_hash: string;
  row_count: number;
  status: string;
  publication_sequence: number | null;
  created_at: string;
};

type RecoveryAttemptRow = {
  attempt_id: string;
  state_token: string;
};

// Parsing and file validation happen before a reservation is acquired. The
// remaining database publication is bounded, so a processing owner that has
// not moved for thirty minutes is treated as an abandoned Worker invocation.
// A takeover is allowed only while the live ownership token still matches the
// state seen by the abandoned owner; partial or completed publication is never
// reclaimed through this path.
const STALE_RESERVATION_SQL = "updated_at <= datetime('now', '-30 minutes')";

const schemaStatements = [
  `CREATE TABLE IF NOT EXISTS import_content_fingerprints (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    import_hash TEXT NOT NULL,
    raw_file_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    publication_sequence INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (domain, batch_id),
    UNIQUE (domain, scope_key, import_hash)
  )`,
  `CREATE INDEX IF NOT EXISTS import_content_fingerprints_scope_idx
    ON import_content_fingerprints (domain, scope_key, publication_sequence DESC)`,
  `CREATE INDEX IF NOT EXISTS import_content_fingerprints_raw_idx
    ON import_content_fingerprints (domain, raw_file_hash)`,
  `CREATE TABLE IF NOT EXISTS import_content_attempts (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id TEXT NOT NULL UNIQUE,
    domain TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    import_hash TEXT NOT NULL,
    raw_file_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    file_name TEXT NOT NULL DEFAULT '',
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    actor TEXT NOT NULL DEFAULT '',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    outcome TEXT NOT NULL,
    error_code TEXT NOT NULL DEFAULT '',
    recovered_from_attempt_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS import_content_attempts_scope_idx
    ON import_content_attempts (domain, scope_key, sequence DESC)`,
  `CREATE INDEX IF NOT EXISTS import_content_attempts_raw_idx
    ON import_content_attempts (domain, raw_file_hash, sequence DESC)`,
  `CREATE TABLE IF NOT EXISTS import_scope_heads (
    domain TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    state_token TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready',
    owner_token TEXT,
    current_batch_id TEXT,
    generation INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (domain, scope_key)
  )`,
] as const;

const schemaReadyByDatabase = new WeakMap<object, Promise<void>>();

export async function ensureImportFingerprintSchema(db: ImportFingerprintDatabase) {
  const key = db as unknown as object;
  const existing = schemaReadyByDatabase.get(key);
  if (existing) return existing;
  const setup = db.batch(schemaStatements.map((statement) => db.prepare(statement)))
    .then(() => undefined)
    .catch((error: unknown) => {
      schemaReadyByDatabase.delete(key);
      throw error;
    });
  schemaReadyByDatabase.set(key, setup);
  return setup;
}

function canonicalValue(value: unknown, ignoredTopLevelKeys: ReadonlySet<string>, topLevel: boolean): unknown {
  if (value === null || value === undefined) return null;
  if (typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("导入内容包含非有限数字，无法生成稳定指纹");
    return Object.is(value, -0) ? 0 : value;
  }
  if (typeof value === "bigint") return value.toString();
  if (value instanceof Date) return value.toISOString();
  if (Array.isArray(value)) {
    return value.map((item) => canonicalValue(item, ignoredTopLevelKeys, false));
  }
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    return Object.fromEntries(
      Object.keys(record)
        .filter((key) => !(topLevel && ignoredTopLevelKeys.has(key)))
        .sort()
        .map((key) => [key, canonicalValue(record[key], ignoredTopLevelKeys, false)]),
    );
  }
  throw new Error(`导入内容包含不支持的字段类型：${typeof value}`);
}

function canonicalJson(value: unknown, ignoredTopLevelKeys: ReadonlySet<string> = new Set()) {
  return JSON.stringify(canonicalValue(value, ignoredTopLevelKeys, true));
}

function encodePart(value: string) {
  return `${new TextEncoder().encode(value).length}:${value}`;
}

async function sha256Text(value: string) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/**
 * Builds an order-insensitive fingerprint from normalized business rows.
 * Only explicitly named top-level technical fields are ignored; nested raw
 * business data remains part of the comparison.
 */
export async function buildImportContentFingerprint(input: {
  domain: string;
  scope: unknown;
  lockScope?: unknown;
  rows: readonly unknown[];
  ignoredTopLevelKeys?: readonly string[];
}): Promise<ImportContentFingerprint> {
  const domain = input.domain.trim();
  if (!domain) throw new Error("导入内容指纹缺少领域名称");
  const ignored = new Set(input.ignoredTopLevelKeys ?? []);
  const scopeJson = canonicalJson(input.scope);
  const lockScopeJson = canonicalJson(input.lockScope ?? input.scope);
  const scopeKey = await sha256Text(`import-lock-scope-v1\n${encodePart(domain)}${encodePart(lockScopeJson)}`);
  const rows: string[] = [];
  const digestBatchSize = 32;
  for (let offset = 0; offset < input.rows.length; offset += digestBatchSize) {
    const digests = await Promise.all(input.rows
      .slice(offset, offset + digestBatchSize)
      .map((row) => sha256Text(canonicalJson(row, ignored))));
    rows.push(...digests);
  }
  rows.sort();
  const payload = `import-content-v3\n${encodePart(scopeJson)}${rows.length}\n${rows.join("")}`;
  const contentHash = await sha256Text(payload);
  return { domain, scopeKey, scopeJson, contentHash, rowCount: rows.length };
}

/**
 * Persists a pre-validation rejection without creating a business fingerprint
 * or touching the scope head. Invalid or incomplete files have no trustworthy
 * business scope/content, so they must be auditable but must never take a write
 * lock or participate in duplicate decisions.
 */
export async function recordRejectedImportAttempt(
  db: ImportFingerprintDatabase,
  input: {
    domain: string;
    rawFileHash: string;
    scopeHint?: unknown;
    errorCode?: string;
    issues?: readonly unknown[];
    metadata?: ImportAttemptMetadata;
  },
) {
  await ensureImportFingerprintSchema(db);
  const domain = input.domain.trim();
  if (!domain || !/^[a-f0-9]{64}$/.test(input.rawFileHash)) {
    throw new Error("拒绝审计缺少有效领域或原文件哈希");
  }
  const scopeJson = canonicalJson({ stage: "prevalidation", hint: input.scopeHint ?? null });
  const scopeKey = await sha256Text(`import-rejected-scope-v1\n${encodePart(domain)}${encodePart(scopeJson)}`);
  const errorCode = String(input.errorCode || "IMPORT_REJECTED").trim().slice(0, 100) || "IMPORT_REJECTED";
  const contentHash = await sha256Text(`import-rejected-content-v1\n${encodePart(scopeJson)}${encodePart(input.rawFileHash)}`);
  const importHash = await sha256Text(`import-rejected-attempt-v1\n${encodePart(domain)}${encodePart(scopeKey)}${encodePart(input.rawFileHash)}${encodePart(errorCode)}`);
  const metadata = normalizeAttemptMetadata({
    ...input.metadata,
    warnings: input.issues ?? input.metadata?.warnings,
  });
  const attemptId = crypto.randomUUID();
  await db.prepare(
    `INSERT INTO import_content_attempts (
      attempt_id, domain, batch_id, scope_key, scope_json, import_hash, raw_file_hash,
      content_hash, row_count, file_name, file_size_bytes, actor, warnings_json,
      outcome, error_code
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 'rejected', ?)`,
  ).bind(
    attemptId,
    domain,
    importHash,
    scopeKey,
    scopeJson,
    importHash,
    input.rawFileHash,
    contentHash,
    metadata.fileName,
    metadata.fileSizeBytes,
    metadata.actor,
    metadata.warningsJson,
    errorCode,
  ).run();
  return { attemptId, importHash, scopeKey };
}

export async function auditRejectedImportResult<
  TResult extends { warnings?: readonly unknown[]; errors?: readonly unknown[] },
>(
  db: ImportFingerprintDatabase,
  input: {
    domain: string;
    rawFileHash: string;
    scopeHint?: unknown;
    metadata?: Omit<ImportAttemptMetadata, "warnings">;
  },
  result: TResult,
): Promise<TResult> {
  const firstError = result.errors?.[0] as { code?: unknown } | undefined;
  await recordRejectedImportAttempt(db, {
    ...input,
    errorCode: typeof firstError?.code === "string" ? firstError.code : "IMPORT_REJECTED",
    issues: [...(result.warnings ?? []), ...(result.errors ?? [])],
  });
  return result;
}

/**
 * The attempt hash remains deterministic for concurrent requests against the
 * same current state, while allowing an older content version to be restored
 * after a different version has been published.
 */
export async function buildImportAttemptHash(input: {
  fingerprint: ImportContentFingerprint;
  currentStateToken?: string | null;
}) {
  return sha256Text([
    "import-attempt-v1",
    input.fingerprint.domain,
    input.fingerprint.scopeKey,
    input.fingerprint.contentHash,
    input.currentStateToken?.trim() || "initial",
  ].map(encodePart).join(""));
}

export async function importScopeStateToken(rows: readonly { batchId: string; rowCount: number }[]) {
  if (!rows.length) return "initial";
  const canonicalOwnership = JSON.stringify([...rows]
    .map((row) => ({ batchId: row.batchId, rowCount: Number(row.rowCount) }))
    .sort((left, right) => left.batchId.localeCompare(right.batchId)));
  return sha256Text(`import-scope-state-v1\n${canonicalOwnership}`);
}

export async function readImportScopeStateToken(
  db: ImportFingerprintDatabase,
  input: Pick<ImportContentFingerprint, "domain" | "scopeKey">,
) {
  await ensureImportFingerprintSchema(db);
  const row = await db.prepare(
    `SELECT state_token
     FROM import_scope_heads
     WHERE domain = ? AND scope_key = ?
     LIMIT 1`,
  ).bind(input.domain, input.scopeKey).first<{ state_token: string }>();
  return row?.state_token?.trim() || "initial";
}

export async function nextImportScopeStateToken(input: {
  previousStateToken?: string | null;
  batchId: string;
  contentHash: string;
  rowCount: number;
}) {
  return sha256Text([
    "import-scope-state-v2",
    input.previousStateToken?.trim() || "initial",
    input.batchId,
    input.contentHash,
    String(input.rowCount),
  ].map(encodePart).join(""));
}

function mapStoredFingerprint(row: FingerprintRow): StoredImportFingerprint {
  return {
    domain: row.domain,
    batchId: row.batch_id,
    scopeKey: row.scope_key,
    scopeJson: row.scope_json,
    importHash: row.import_hash,
    rawFileHash: row.raw_file_hash,
    contentHash: row.content_hash,
    rowCount: Number(row.row_count),
    status: row.status === "completed" ? "completed" : "processing",
    createdAt: row.created_at,
  };
}

export async function findLatestImportFingerprint(
  db: ImportFingerprintDatabase,
  input: Pick<ImportContentFingerprint, "domain" | "scopeKey">,
) {
  await ensureImportFingerprintSchema(db);
  const row = await db.prepare(
    `SELECT sequence, domain, batch_id, scope_key, scope_json, import_hash, raw_file_hash,
            content_hash, row_count, status, publication_sequence, created_at
     FROM import_content_fingerprints
     WHERE domain = ? AND scope_key = ? AND status = 'completed'
     ORDER BY publication_sequence DESC, sequence DESC
     LIMIT 1`,
  ).bind(input.domain, input.scopeKey).first<FingerprintRow>();
  return row ? mapStoredFingerprint(row) : null;
}

export async function findImportFingerprintByBatch(
  db: ImportFingerprintDatabase,
  input: { domain: string; batchId: string },
) {
  await ensureImportFingerprintSchema(db);
  const row = await db.prepare(
    `SELECT sequence, domain, batch_id, scope_key, scope_json, import_hash, raw_file_hash,
            content_hash, row_count, status, publication_sequence, created_at
     FROM import_content_fingerprints
     WHERE domain = ? AND batch_id = ?
     LIMIT 1`,
  ).bind(input.domain, input.batchId).first<FingerprintRow>();
  return row ? mapStoredFingerprint(row) : null;
}

export async function recordImportFingerprint(
  db: ImportFingerprintDatabase,
  input: ImportContentFingerprint & {
    batchId: string;
    importHash: string;
    rawFileHash: string;
    outcome?: "imported" | "duplicate";
    attemptId?: string;
    publishedStateToken?: string;
    metadata?: ImportAttemptMetadata;
  },
) {
  await ensureImportFingerprintSchema(db);
  if (!/^[a-f0-9]{64}$/.test(input.importHash) || !/^[a-f0-9]{64}$/.test(input.rawFileHash)
    || !/^[a-f0-9]{64}$/.test(input.contentHash)) {
    throw new Error("导入指纹或文件哈希格式无效");
  }
  const recoveryAttempt = !input.attemptId && input.publishedStateToken
    ? await db.prepare(
      `SELECT attempt.attempt_id, head.state_token
       FROM import_scope_heads head
       JOIN import_content_attempts attempt ON attempt.attempt_id = head.owner_token
       WHERE head.domain = ? AND head.scope_key = ? AND head.status = 'processing'
         AND attempt.batch_id = ? AND attempt.content_hash = ? AND attempt.outcome = 'processing'
       LIMIT 1`,
    ).bind(input.domain, input.scopeKey, input.batchId, input.contentHash).first<RecoveryAttemptRow>()
    : null;
  const ownerAttemptId = input.attemptId ?? recoveryAttempt?.attempt_id ?? null;
  const attemptId = recoveryAttempt ? crypto.randomUUID() : ownerAttemptId ?? crypto.randomUUID();
  const requiresOwner = Boolean(ownerAttemptId);
  const metadata = normalizeAttemptMetadata(input.metadata);
  const readyDuplicateRepairToken = !requiresOwner
    && input.outcome === "duplicate"
    && input.publishedStateToken
    ? await nextImportScopeStateToken({
      previousStateToken: input.publishedStateToken,
      batchId: input.batchId,
      contentHash: input.contentHash,
      rowCount: input.rowCount,
    })
    : null;
  const finalPublishedStateToken = recoveryAttempt
    ? await nextImportScopeStateToken({
      previousStateToken: recoveryAttempt.state_token,
      batchId: input.batchId,
      contentHash: input.contentHash,
      rowCount: input.rowCount,
    })
    : readyDuplicateRepairToken
      ?? input.publishedStateToken
      ?? await importScopeStateToken([{ batchId: input.batchId, rowCount: input.rowCount }]);
  const completionOwnerGuard = requiresOwner
    ? ` WHERE EXISTS (
        SELECT 1 FROM import_scope_heads
        WHERE domain = ? AND scope_key = ? AND status = 'processing' AND owner_token = ?
      )`
    : "";
  const fingerprintStatement = db.prepare(
    `INSERT INTO import_content_fingerprints (
      domain, batch_id, scope_key, scope_json, import_hash, raw_file_hash,
      content_hash, row_count, status, publication_sequence
    ) SELECT ?, ?, ?, ?, ?, ?, ?, ?, 'completed',
      COALESCE((SELECT MAX(publication_sequence) + 1 FROM import_content_fingerprints), 1)
      ${completionOwnerGuard}
    ON CONFLICT(domain, batch_id) DO UPDATE SET
      scope_key = excluded.scope_key,
      scope_json = excluded.scope_json,
      import_hash = excluded.import_hash,
      content_hash = excluded.content_hash,
      row_count = excluded.row_count,
      status = 'completed',
      publication_sequence = COALESCE((SELECT MAX(publication_sequence) + 1 FROM import_content_fingerprints), 1)`,
  ).bind(
    input.domain,
    input.batchId,
    input.scopeKey,
    input.scopeJson,
    input.importHash,
    input.rawFileHash,
    input.contentHash,
    input.rowCount,
    ...(requiresOwner ? [input.domain, input.scopeKey, ownerAttemptId] : []),
  );
  const attemptValues = [
    attemptId,
    input.domain,
    input.batchId,
    input.scopeKey,
    input.scopeJson,
    input.importHash,
    input.rawFileHash,
    input.contentHash,
    input.rowCount,
    metadata.fileName,
    metadata.fileSizeBytes,
    metadata.actor,
    metadata.warningsJson,
    input.outcome ?? "imported",
    recoveryAttempt?.attempt_id ?? "",
  ] as const;
  const attemptStatement = requiresOwner
    ? db.prepare(
      `INSERT INTO import_content_attempts (
        attempt_id, domain, batch_id, scope_key, scope_json, import_hash, raw_file_hash,
        content_hash, row_count, file_name, file_size_bytes, actor, warnings_json, outcome,
        error_code, recovered_from_attempt_id
      ) SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?
        WHERE EXISTS (
          SELECT 1 FROM import_scope_heads
          WHERE domain = ? AND scope_key = ? AND status = 'processing' AND owner_token = ?
        )
      ON CONFLICT(attempt_id) DO UPDATE SET
        batch_id = excluded.batch_id,
        file_name = excluded.file_name,
        file_size_bytes = excluded.file_size_bytes,
        actor = excluded.actor,
        warnings_json = excluded.warnings_json,
        outcome = excluded.outcome,
        error_code = '',
        updated_at = CURRENT_TIMESTAMP
      WHERE import_content_attempts.outcome = 'processing'
        AND EXISTS (
          SELECT 1 FROM import_scope_heads
          WHERE domain = ? AND scope_key = ? AND status = 'processing' AND owner_token = ?
        )`,
    ).bind(
      ...attemptValues,
      input.domain,
      input.scopeKey,
      ownerAttemptId,
      input.domain,
      input.scopeKey,
      ownerAttemptId,
    )
    : db.prepare(
      `INSERT INTO import_content_attempts (
        attempt_id, domain, batch_id, scope_key, scope_json, import_hash, raw_file_hash,
        content_hash, row_count, file_name, file_size_bytes, actor, warnings_json, outcome,
        error_code, recovered_from_attempt_id
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)
      ON CONFLICT(attempt_id) DO NOTHING`,
    ).bind(...attemptValues);
  const statements = [fingerprintStatement];
  if (recoveryAttempt) {
    statements.push(db.prepare(
      `UPDATE import_content_attempts
       SET outcome = 'imported', error_code = '', updated_at = CURRENT_TIMESTAMP
       WHERE attempt_id = ? AND outcome = 'processing'
         AND EXISTS (
           SELECT 1 FROM import_scope_heads
           WHERE domain = ? AND scope_key = ? AND status = 'processing' AND owner_token = ?
         )`,
    ).bind(recoveryAttempt.attempt_id, input.domain, input.scopeKey, recoveryAttempt.attempt_id));
  }
  statements.push(attemptStatement);
  if (requiresOwner) {
    statements.push(db.prepare(
      `UPDATE import_scope_heads
       SET state_token = ?, status = 'ready', owner_token = NULL,
           current_batch_id = ?, generation = generation + 1, updated_at = CURRENT_TIMESTAMP
       WHERE domain = ? AND scope_key = ?
         AND status = 'processing' AND owner_token = ?`,
    ).bind(
      finalPublishedStateToken,
      input.batchId,
      input.domain,
      input.scopeKey,
      ownerAttemptId,
    ));
  } else {
    statements.push(db.prepare(
      `INSERT INTO import_scope_heads (
        domain, scope_key, state_token, status, current_batch_id
      ) VALUES (?, ?, ?, 'ready', ?)
      ON CONFLICT(domain, scope_key) DO NOTHING`,
    ).bind(
      input.domain,
      input.scopeKey,
      finalPublishedStateToken,
      input.batchId,
    ));
    if (readyDuplicateRepairToken && input.publishedStateToken) {
      statements.push(db.prepare(
        `UPDATE import_scope_heads
         SET state_token = ?, current_batch_id = ?, generation = generation + 1,
             updated_at = CURRENT_TIMESTAMP
         WHERE domain = ? AND scope_key = ? AND status = 'ready'
           AND state_token = ?
           AND (current_batch_id IS NULL OR current_batch_id <> ?)`,
      ).bind(
        readyDuplicateRepairToken,
        input.batchId,
        input.domain,
        input.scopeKey,
        input.publishedStateToken,
        input.batchId,
      ));
    }
  }
  const results = await db.batch(statements);
  if (requiresOwner && Number(results.at(-1)?.meta?.changes ?? 0) !== 1) {
    throw new PublicApiError(
      409,
      "conflict",
      "导入范围已被其他任务接管，请刷新后重试。",
    );
  }
  return {
    attemptId,
    recovered: Boolean(recoveryAttempt),
    recoveredFromAttemptId: recoveryAttempt?.attempt_id ?? null,
  };
}

export async function reserveImportFingerprint(
  db: ImportFingerprintDatabase,
  input: ImportContentFingerprint & {
    batchId: string;
    importHash: string;
    rawFileHash: string;
    currentStateToken?: string | null;
    metadata?: ImportAttemptMetadata;
  },
) {
  await ensureImportFingerprintSchema(db);
  if (!/^[a-f0-9]{64}$/.test(input.importHash) || !/^[a-f0-9]{64}$/.test(input.rawFileHash)
    || !/^[a-f0-9]{64}$/.test(input.contentHash)) {
    throw new Error("导入指纹或文件哈希格式无效");
  }
  const attemptId = crypto.randomUUID();
  const expectedStateToken = input.currentStateToken?.trim() || "initial";
  const metadata = normalizeAttemptMetadata(input.metadata);
  const results = await db.batch([db.prepare(
    `INSERT INTO import_scope_heads (domain, scope_key, state_token, status)
     VALUES (?, ?, ?, 'ready')
     ON CONFLICT(domain, scope_key) DO NOTHING`,
  ).bind(input.domain, input.scopeKey, expectedStateToken), db.prepare(
    `UPDATE import_content_attempts
     SET outcome = 'failed', error_code = 'IMPORT_RESERVATION_EXPIRED',
         updated_at = CURRENT_TIMESTAMP
     WHERE attempt_id = (
       SELECT owner_token FROM import_scope_heads
       WHERE domain = ? AND scope_key = ? AND status = 'processing'
         AND state_token = ? AND ${STALE_RESERVATION_SQL}
     ) AND outcome = 'processing'`,
  ).bind(input.domain, input.scopeKey, expectedStateToken), db.prepare(
    `DELETE FROM import_content_fingerprints
     WHERE domain = ? AND scope_key = ? AND status = 'processing'
       AND EXISTS (
         SELECT 1 FROM import_scope_heads
         WHERE domain = ? AND scope_key = ? AND status = 'processing'
           AND state_token = ? AND ${STALE_RESERVATION_SQL}
       )`,
  ).bind(
    input.domain,
    input.scopeKey,
    input.domain,
    input.scopeKey,
    expectedStateToken,
  ), db.prepare(
    `UPDATE import_scope_heads
     SET status = 'ready', owner_token = NULL, current_batch_id = NULL,
         generation = generation + 1, updated_at = CURRENT_TIMESTAMP
     WHERE domain = ? AND scope_key = ? AND status = 'processing'
       AND state_token = ? AND ${STALE_RESERVATION_SQL}`,
  ).bind(input.domain, input.scopeKey, expectedStateToken), db.prepare(
    `UPDATE import_scope_heads
     SET status = 'processing', owner_token = ?, current_batch_id = ?,
         updated_at = CURRENT_TIMESTAMP
     WHERE domain = ? AND scope_key = ?
       AND status = 'ready' AND state_token = ?`,
  ).bind(attemptId, input.batchId, input.domain, input.scopeKey, expectedStateToken), db.prepare(
    `INSERT INTO import_content_fingerprints (
      domain, batch_id, scope_key, scope_json, import_hash, raw_file_hash,
      content_hash, row_count, status
    ) SELECT ?, ?, ?, ?, ?, ?, ?, ?, 'processing'
      WHERE EXISTS (
        SELECT 1 FROM import_scope_heads
        WHERE domain = ? AND scope_key = ? AND status = 'processing' AND owner_token = ?
      )
    ON CONFLICT DO NOTHING`,
  ).bind(
    input.domain,
    input.batchId,
    input.scopeKey,
    input.scopeJson,
    input.importHash,
    input.rawFileHash,
    input.contentHash,
    input.rowCount,
    input.domain,
    input.scopeKey,
    attemptId,
  ), db.prepare(
    `INSERT INTO import_content_attempts (
      attempt_id, domain, batch_id, scope_key, scope_json, import_hash, raw_file_hash,
      content_hash, row_count, file_name, file_size_bytes, actor, warnings_json, outcome, error_code
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
      CASE WHEN EXISTS (
        SELECT 1 FROM import_scope_heads
        WHERE domain = ? AND scope_key = ? AND status = 'processing' AND owner_token = ?
      ) THEN 'processing' ELSE 'superseded' END,
      CASE WHEN EXISTS (
        SELECT 1 FROM import_scope_heads
        WHERE domain = ? AND scope_key = ? AND status = 'processing' AND owner_token = ?
      ) THEN '' WHEN EXISTS (
        SELECT 1 FROM import_scope_heads
        WHERE domain = ? AND scope_key = ? AND status = 'processing'
      ) THEN 'IMPORT_SCOPE_BUSY' ELSE 'IMPORT_SCOPE_CHANGED' END)`,
  ).bind(
    attemptId,
    input.domain,
    input.batchId,
    input.scopeKey,
    input.scopeJson,
    input.importHash,
    input.rawFileHash,
    input.contentHash,
    input.rowCount,
    metadata.fileName,
    metadata.fileSizeBytes,
    metadata.actor,
    metadata.warningsJson,
    input.domain,
    input.scopeKey,
    attemptId,
    input.domain,
    input.scopeKey,
    attemptId,
    input.domain,
    input.scopeKey,
  )]);
  const recoveredStaleReservation = Number(results[3]?.meta?.changes ?? 0) === 1;
  const claimed = Number(results[4]?.meta?.changes ?? 0) === 1;
  return { attemptId, claimed, recoveredStaleReservation, resynchronizedState: false };
}

export async function renewImportFingerprintReservation(
  db: ImportFingerprintDatabase,
  input: Pick<ImportContentFingerprint, "domain" | "scopeKey"> & {
    batchId: string;
    attemptId: string;
  },
) {
  await ensureImportFingerprintSchema(db);
  const result = await db.prepare(
    `UPDATE import_scope_heads
     SET updated_at = CURRENT_TIMESTAMP
     WHERE domain = ? AND scope_key = ? AND status = 'processing'
       AND owner_token = ? AND current_batch_id = ?`,
  ).bind(input.domain, input.scopeKey, input.attemptId, input.batchId).run();
  if (Number(result.meta?.changes ?? 0) !== 1) {
    throw new PublicApiError(
      409,
      "conflict",
      "导入范围已被其他任务接管，请刷新后重试。",
    );
  }
}

/**
 * Appended to the same D1 batch that publishes domain facts. If ownership was
 * lost, the conditional NULL insert violates the head's NOT NULL contract and
 * rolls back the entire batch instead of allowing a late Worker to commit.
 */
export function importReservationCommitFence<
  TBound extends { run(): Promise<unknown> },
  TPrepared extends { bind(...values: unknown[]): TBound },
>(
  db: { prepare(sql: string): TPrepared },
  input: ImportReservationFence,
): TBound {
  return db.prepare(
    `INSERT INTO import_scope_heads (domain, scope_key, state_token, status)
     SELECT NULL, NULL, NULL, 'invalid'
     WHERE NOT EXISTS (
       SELECT 1 FROM import_scope_heads
       WHERE domain = ? AND scope_key = ? AND status = 'processing'
         AND owner_token = ? AND current_batch_id = ?
     )`,
  ).bind(input.domain, input.scopeKey, input.attemptId, input.batchId);
}

/**
 * D1 rolls a publish batch back when the commit fence loses ownership. After
 * that rollback, read the current head and translate only the proven takeover
 * case to a stable 409; unrelated constraint/SQL failures stay internal.
 */
export async function rethrowImportPublishError(
  db: ImportFingerprintDatabase,
  fence: ImportReservationFence,
  error: unknown,
): Promise<never> {
  const head = await db.prepare(`SELECT status, owner_token, current_batch_id
    FROM import_scope_heads WHERE domain = ? AND scope_key = ? LIMIT 1`)
    .bind(fence.domain, fence.scopeKey)
    .first<{ status: string; owner_token: string | null; current_batch_id: string | null }>();
  if (!head
    || head.status !== "processing"
    || head.owner_token !== fence.attemptId
    || head.current_batch_id !== fence.batchId) {
    throw new PublicApiError(
      409,
      "conflict",
      "导入范围已被其他任务接管，请刷新后重试。",
    );
  }
  throw error;
}

export async function failImportFingerprint(
  db: ImportFingerprintDatabase,
  input: ImportContentFingerprint & {
    batchId: string;
    importHash: string;
    rawFileHash: string;
    attemptId: string;
    errorCode?: string;
    outcome?: "failed" | "rejected" | "superseded";
    metadata?: ImportAttemptMetadata;
  },
) {
  await ensureImportFingerprintSchema(db);
  const metadata = normalizeAttemptMetadata(input.metadata);
  await db.batch([
    db.prepare(
      `DELETE FROM import_content_fingerprints
       WHERE domain = ? AND batch_id = ? AND status = 'processing'
         AND EXISTS (
           SELECT 1 FROM import_scope_heads
           WHERE domain = ? AND scope_key = ? AND status = 'processing'
             AND owner_token = ? AND current_batch_id = ?
         )`,
    ).bind(
      input.domain,
      input.batchId,
      input.domain,
      input.scopeKey,
      input.attemptId,
      input.batchId,
    ),
    db.prepare(
      `INSERT INTO import_content_attempts (
        attempt_id, domain, batch_id, scope_key, scope_json, import_hash, raw_file_hash,
        content_hash, row_count, file_name, file_size_bytes, actor, warnings_json, outcome, error_code
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(attempt_id) DO UPDATE SET
        file_name = excluded.file_name,
        file_size_bytes = excluded.file_size_bytes,
        actor = excluded.actor,
        warnings_json = excluded.warnings_json,
        outcome = excluded.outcome,
        error_code = excluded.error_code,
        updated_at = CURRENT_TIMESTAMP
      WHERE import_content_attempts.outcome = 'processing'`,
    ).bind(
      input.attemptId,
      input.domain,
      input.batchId,
      input.scopeKey,
      input.scopeJson,
      input.importHash,
      input.rawFileHash,
      input.contentHash,
      input.rowCount,
      metadata.fileName,
      metadata.fileSizeBytes,
      metadata.actor,
      metadata.warningsJson,
      input.outcome ?? "failed",
      (input.errorCode ?? "IMPORT_FAILED").slice(0, 100),
    ),
    db.prepare(
      `UPDATE import_scope_heads
       SET status = 'ready', owner_token = NULL, current_batch_id = NULL,
           generation = generation + 1, updated_at = CURRENT_TIMESTAMP
       WHERE domain = ? AND scope_key = ? AND status = 'processing'
         AND owner_token = ? AND current_batch_id = ?`,
    ).bind(input.domain, input.scopeKey, input.attemptId, input.batchId),
  ]);
}

function normalizeAttemptMetadata(metadata?: ImportAttemptMetadata) {
  const fileSizeBytes = Number(metadata?.fileSizeBytes ?? 0);
  const warnings = (metadata?.warnings ?? []).slice(0, 200);
  const warningTotalCount = Number(metadata?.warningTotalCount);
  const hasWarningSummary = Number.isSafeInteger(warningTotalCount) && warningTotalCount >= warnings.length;
  return {
    fileName: String(metadata?.fileName ?? "").replace(/[\u0000-\u001f\u007f]/g, "").slice(0, 255),
    fileSizeBytes: Number.isSafeInteger(fileSizeBytes) && fileSizeBytes >= 0 ? fileSizeBytes : 0,
    actor: String(metadata?.actor ?? "").trim().slice(0, 200),
    warningsJson: JSON.stringify(hasWarningSummary
      ? { items: warnings, totalCount: warningTotalCount, truncated: metadata?.warningsTruncated === true || warningTotalCount > warnings.length }
      : warnings).slice(0, 50_000),
  };
}
