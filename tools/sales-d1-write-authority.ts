import path from "node:path";
import { pathToFileURL } from "node:url";
import { DatabaseSync } from "node:sqlite";

export type SalesD1AuthorityOwner = "d1" | "pending" | "postgresql";
export type SalesD1AuthorityTargetOwner = "pending" | "postgresql";

export type SalesD1AuthoritySnapshot = {
  owner: SalesD1AuthorityOwner;
  epoch: number;
  cutoverId: string;
  updatedAt: string;
  blockers: {
    processingBatches: number;
    activeUploads: number;
    invalidUploadExpiries: number;
    uploadChunks: number;
    processingFingerprints: number;
    processingScopeHeads: number;
    processingAttempts: number;
  };
};

type CountRow = { count: number };
type AuthorityRow = {
  owner: string;
  epoch: number;
  cutover_id: string;
  updated_at: string;
};

function count(database: DatabaseSync, sql: string): number {
  const row = database.prepare(sql).get() as CountRow | undefined;
  const value = Number(row?.count ?? Number.NaN);
  if (!Number.isSafeInteger(value) || value < 0) throw new Error("D1 销售写入门禁检查返回无效计数");
  return value;
}

export function inspectSalesD1WriteAuthority(database: DatabaseSync): SalesD1AuthoritySnapshot {
  const row = database.prepare(
    `SELECT owner, epoch, cutover_id, updated_at
     FROM sales_write_authority WHERE id = 1 LIMIT 1`,
  ).get() as AuthorityRow | undefined;
  if (!row || !["d1", "pending", "postgresql"].includes(row.owner)) {
    throw new Error("D1 缺少有效的销售写入 authority 单例");
  }
  const epoch = Number(row.epoch);
  if (!Number.isSafeInteger(epoch) || epoch < 1) throw new Error("D1 销售写入 authority epoch 无效");
  return {
    owner: row.owner as SalesD1AuthorityOwner,
    epoch,
    cutoverId: String(row.cutover_id ?? ""),
    updatedAt: String(row.updated_at ?? ""),
    blockers: {
      processingBatches: count(database, "SELECT COUNT(*) AS count FROM sales_import_batches WHERE status = 'processing'"),
      // D1 stores ISO text, but lexical comparison accepts malformed values and
      // timezone variants.  SQLite datetime() normalizes valid timestamps; a
      // NULL parse is a separate fail-closed blocker instead of being treated
      // as expired.
      activeUploads: count(database, "SELECT COUNT(*) AS count FROM sales_import_uploads WHERE status IN ('uploading', 'ready', 'processing') AND datetime(expires_at) > datetime('now')"),
      invalidUploadExpiries: count(database, "SELECT COUNT(*) AS count FROM sales_import_uploads WHERE status IN ('uploading', 'ready', 'processing') AND datetime(expires_at) IS NULL"),
      // Every chunk row is the durable manifest for one R2 object.  Even an
      // expired session must be cleaned by the exact-key cutover tool before
      // D1 is allowed to leave the write-owner state.
      uploadChunks: count(database, "SELECT COUNT(*) AS count FROM sales_import_upload_chunks"),
      processingFingerprints: count(database, "SELECT COUNT(*) AS count FROM import_content_fingerprints WHERE domain = 'sales' AND status = 'processing'"),
      processingScopeHeads: count(database, "SELECT COUNT(*) AS count FROM import_scope_heads WHERE domain = 'sales' AND status = 'processing'"),
      processingAttempts: count(database, "SELECT COUNT(*) AS count FROM import_content_attempts WHERE domain = 'sales' AND outcome = 'processing'"),
    },
  };
}

function blockerTotal(snapshot: SalesD1AuthoritySnapshot): number {
  return Object.values(snapshot.blockers).reduce((total, value) => total + value, 0);
}

export function transitionSalesD1WriteAuthority(
  database: DatabaseSync,
  input: {
    expectedOwner: SalesD1AuthorityOwner;
    expectedEpoch: number;
    targetOwner: SalesD1AuthorityTargetOwner;
    cutoverId: string;
  },
): SalesD1AuthoritySnapshot {
  database.exec("BEGIN IMMEDIATE");
  try {
    const after = transitionSalesD1WriteAuthorityInOpenTransaction(database, input);
    database.exec("COMMIT");
    return after;
  } catch (error) {
    database.exec("ROLLBACK");
    throw error;
  }
}

export function transitionSalesD1WriteAuthorityInOpenTransaction(
  database: DatabaseSync,
  input: {
    expectedOwner: SalesD1AuthorityOwner;
    expectedEpoch: number;
    targetOwner: SalesD1AuthorityTargetOwner;
    cutoverId: string;
  },
): SalesD1AuthoritySnapshot {
  if (!Number.isSafeInteger(input.expectedEpoch) || input.expectedEpoch < 1) {
    throw new Error("expectedEpoch 必须是正整数");
  }
  if (!/^[A-Za-z0-9._:-]{8,128}$/.test(input.cutoverId)) {
    throw new Error("cutoverId 必须是 8 到 128 位安全标识");
  }
  const allowed = input.expectedOwner === "d1" && input.targetOwner === "pending"
    || input.expectedOwner === "pending" && input.targetOwner === "postgresql";
  if (!allowed) {
    throw new Error("销售写入 authority 只允许 d1→pending、pending→postgresql；pending 是不可回退点，必须由同一 cutoverId 前向恢复");
  }

  const before = inspectSalesD1WriteAuthority(database);
  if (before.owner !== input.expectedOwner || before.epoch !== input.expectedEpoch) {
    throw new Error("销售写入 authority 已变化，拒绝过期 CAS 切换");
  }
  if (blockerTotal(before) !== 0) {
    throw new Error(`销售写入尚未静默，拒绝切换：${JSON.stringify(before.blockers)}`);
  }
  if (input.expectedOwner === "pending" && before.cutoverId !== input.cutoverId) {
    throw new Error("pending authority 不属于本次 cutoverId");
  }
  const result = database.prepare(
    `UPDATE sales_write_authority
     SET owner = ?, epoch = epoch + 1, cutover_id = ?, updated_at = CURRENT_TIMESTAMP
     WHERE id = 1 AND owner = ? AND epoch = ?`,
  ).run(input.targetOwner, input.cutoverId, input.expectedOwner, input.expectedEpoch);
  if (Number(result.changes) !== 1) throw new Error("销售写入 authority CAS 切换失败");
  return inspectSalesD1WriteAuthority(database);
}

export function parseSalesD1WriteAuthorityArguments(argv: readonly string[]) {
  const values = new Map<string, string>();
  const names = new Set([
    "--source", "--inspect", "--expected-owner", "--expected-epoch",
    "--target-owner", "--cutover-id",
  ]);
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key || !names.has(key)) throw new Error(`未知参数：${key ?? ""}`);
    if (values.has(key)) throw new Error(`参数重复：${key}`);
    if (key === "--inspect") {
      values.set(key, "true");
      continue;
    }
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) throw new Error(`${key} 缺少参数值`);
    values.set(key, value);
    index += 1;
  }
  const source = values.get("--source") ?? "";
  if (!source || !path.isAbsolute(source) || path.extname(source).toLowerCase() !== ".sqlite") {
    throw new Error("--source 必须是精确的绝对 .sqlite 路径");
  }
  if (values.has("--inspect")) {
    if (values.size !== 2) throw new Error("--inspect 只接受 --source");
    return { source, inspect: true as const };
  }
  for (const name of ["--expected-owner", "--expected-epoch", "--target-owner", "--cutover-id"]) {
    if (!values.has(name)) throw new Error(`切换缺少参数：${name}`);
  }
  if (values.size !== 5) throw new Error("切换参数集合无效");
  const expectedOwner = values.get("--expected-owner") as SalesD1AuthorityOwner | undefined;
  const targetOwner = values.get("--target-owner") as SalesD1AuthorityTargetOwner | undefined;
  if (!expectedOwner || !targetOwner || !["d1", "pending", "postgresql"].includes(expectedOwner)
    || !["pending", "postgresql"].includes(targetOwner)) {
    throw new Error("切换必须提供有效的 --expected-owner；--target-owner 只允许 pending 或 postgresql");
  }
  return {
    source,
    inspect: false as const,
    expectedOwner,
    expectedEpoch: Number(values.get("--expected-epoch")),
    targetOwner,
    cutoverId: values.get("--cutover-id") ?? "",
  };
}

async function main() {
  const options = parseSalesD1WriteAuthorityArguments(process.argv.slice(2));
  const database = new DatabaseSync(options.source, { readOnly: options.inspect });
  try {
    const result = options.inspect
      ? inspectSalesD1WriteAuthority(database)
      : transitionSalesD1WriteAuthority(database, options);
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } finally {
    database.close();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error: unknown) => {
    process.stderr.write(`${error instanceof Error ? error.message : "销售写入 authority 工具失败"}\n`);
    process.exitCode = 1;
  });
}
