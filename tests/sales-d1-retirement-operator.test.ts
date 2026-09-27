import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  access,
  appendFile,
  copyFile,
  mkdtemp,
  mkdir,
  readFile,
  realpath,
  rename,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { DatabaseSync } from "node:sqlite";

import {
  executeSalesD1RetirementForTest as executeSalesD1Retirement,
  executeSalesD1RetirementWithDjangoPreflight,
  parseSalesD1RetirementArguments,
  planSalesD1Retirement,
  RETIRED_SALES_TABLES,
  RETIREMENT_TOMBSTONE_VIEWS,
  SALES_CUTOVER_ATTESTATION_VERSION,
  SHARED_IMPORT_RETIREMENT_GUARDS,
  type SalesD1RetirementInput,
  type SalesPostgresqlRetirementPreflight,
} from "../tools/sales-d1-retirement";
import { salesD1CoreEvidence } from "../tools/sales-legacy-r2-cleanup";
import { transitionSalesD1WriteAuthority } from "../tools/sales-d1-write-authority";

const REPOSITORY_ROOT = fileURLToPath(new URL("..", import.meta.url));
const CUTOVER_ID = "retirement-operator-test";

type Fixture = {
  root: string;
  source: string;
  auditOutput: string;
  attestationPath: string;
  attestationSha256: string;
  input: SalesD1RetirementInput;
};

const NOW = new Date("2026-08-28T15:32:00.000Z");
const SMOKE_CHECKED_AT = "2026-08-28T15:30:00Z";
const SMOKE_EXPIRES_AT = "2026-08-28T15:35:00Z";
const REQUIRED_CHECKS = [
  "writer_readiness",
  "sales_summary",
  "sales_category_analysis",
  "sales_category_analysis_detail",
  "sales_write_transaction_rollback_probe",
] as const;

function sha256(value: string | Uint8Array): string {
  return createHash("sha256").update(value).digest("hex");
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") {
    const encoded = JSON.stringify(value);
    if (encoded === undefined) throw new Error("undefined is not canonical JSON");
    return encoded;
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map(
    (key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`,
  ).join(",")}}`;
}

async function migration(name: string): Promise<string> {
  return (await readFile(path.join(REPOSITORY_ROOT, "drizzle", name), "utf8"))
    .replaceAll("--> statement-breakpoint", "");
}

function tableExists(database: DatabaseSync, name: string): boolean {
  return Boolean(database.prepare(
    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
  ).get(name));
}

function viewExists(database: DatabaseSync, name: string): boolean {
  return Boolean(database.prepare(
    "SELECT 1 FROM sqlite_master WHERE type='view' AND name=? LIMIT 1",
  ).get(name));
}

function triggerExists(database: DatabaseSync, name: string): boolean {
  return Boolean(database.prepare(
    "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=? LIMIT 1",
  ).get(name));
}

function createSourceSchema(database: DatabaseSync): void {
  database.exec(`
    CREATE TABLE sales_order_lines (id INTEGER PRIMARY KEY, product_code TEXT NOT NULL);
    CREATE TABLE sales_import_batches (id TEXT PRIMARY KEY, status TEXT NOT NULL);
    CREATE TABLE sales_import_uploads (
      id TEXT PRIMARY KEY, status TEXT NOT NULL, expires_at TEXT NOT NULL
    );
    CREATE TABLE sales_import_upload_chunks (
      upload_id TEXT NOT NULL, chunk_index INTEGER NOT NULL, object_key TEXT NOT NULL,
      PRIMARY KEY (upload_id, chunk_index)
    );
    CREATE TABLE sales_overview_cache_state (
      id INTEGER PRIMARY KEY, sales_revision INTEGER NOT NULL, erp_product_revision INTEGER NOT NULL
    );
    CREATE TABLE sales_overview_response_cache (
      cache_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL
    );
    CREATE TABLE sales_projection_source_state (
      id INTEGER PRIMARY KEY, source_epoch TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE sales_projection_outbox (
      event_sequence INTEGER PRIMARY KEY, domain TEXT NOT NULL
    );
    CREATE TABLE import_content_fingerprints (
      sequence INTEGER PRIMARY KEY, domain TEXT NOT NULL, status TEXT NOT NULL
    );
    CREATE TABLE import_content_attempts (
      sequence INTEGER PRIMARY KEY, domain TEXT NOT NULL, outcome TEXT NOT NULL
    );
    CREATE TABLE import_scope_heads (
      domain TEXT NOT NULL, scope_key TEXT NOT NULL, status TEXT NOT NULL,
      PRIMARY KEY (domain, scope_key)
    );

    CREATE TABLE market_monthly_summary_cache_state (
      id INTEGER PRIMARY KEY, source_revision INTEGER NOT NULL, status TEXT NOT NULL
    );
    CREATE TABLE market_monthly_summary_dirty_products (
      product_code TEXT PRIMARY KEY, dirty_revision INTEGER NOT NULL
    );
    CREATE TRIGGER market_monthly_summary_sales_insert
      AFTER INSERT ON sales_order_lines BEGIN SELECT 1; END;
    CREATE TRIGGER market_monthly_summary_sales_update
      AFTER UPDATE ON sales_order_lines BEGIN SELECT 1; END;
    CREATE TRIGGER market_monthly_summary_sales_delete
      AFTER DELETE ON sales_order_lines BEGIN SELECT 1; END;

    CREATE TABLE erp_product_master (
      product_code TEXT PRIMARY KEY, product_name TEXT NOT NULL
    );
    CREATE TABLE erp_reference_import_batches (
      id TEXT PRIMARY KEY,
      source_key TEXT NOT NULL,
      status TEXT NOT NULL,
      row_count INTEGER NOT NULL,
      totals_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      completed_at TEXT
    );
    CREATE TABLE inventory_stock_lines (
      id INTEGER PRIMARY KEY, product_code TEXT NOT NULL, quantity INTEGER NOT NULL
    );
    CREATE INDEX inventory_stock_lines_product_code_idx
      ON inventory_stock_lines(product_code);

    INSERT INTO sales_order_lines VALUES (1, 'SALE-1');
    INSERT INTO sales_import_batches VALUES ('sales-completed', 'completed');
    INSERT INTO sales_import_uploads
      VALUES ('sales-expired', 'ready', '2000-01-01T00:00:00Z');
    INSERT INTO sales_overview_cache_state VALUES (1, 8, 5);
    INSERT INTO sales_overview_response_cache VALUES ('sales-cache', '{"ok":true}');
    INSERT INTO sales_projection_source_state VALUES (1, 'sales-source', CURRENT_TIMESTAMP);
    INSERT INTO sales_projection_outbox VALUES (1, 'sales');

    INSERT INTO import_content_fingerprints VALUES
      (1, 'sales', 'completed'), (2, 'inventory', 'completed');
    INSERT INTO import_content_attempts VALUES
      (1, 'sales', 'completed'), (2, 'inventory', 'completed');
    INSERT INTO import_scope_heads VALUES
      ('sales', 'sales-scope', 'ready'), ('inventory', 'inventory-scope', 'ready');

    INSERT INTO market_monthly_summary_cache_state VALUES (1, 8, 'ready');
    INSERT INTO market_monthly_summary_dirty_products VALUES ('SALE-1', 8);
    INSERT INTO erp_product_master VALUES ('ERP-1', 'ERP preserved');
    INSERT INTO erp_reference_import_batches VALUES (
      'erp-products-completed', 'products', 'completed', 1,
      '{"contentHash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
      '2026-08-28T01:00:00Z', '2026-08-28T01:01:00Z'
    );
    INSERT INTO inventory_stock_lines VALUES (1, 'ERP-1', 7);
  `);
}

async function makeAttestation(
  source: string,
  attestationPath: string,
  claimedOwner: "postgresql" = "postgresql",
): Promise<string> {
  const resolvedSource = await realpath(source);
  const details = await stat(resolvedSource, { bigint: true });
  const database = new DatabaseSync(resolvedSource, { readOnly: true });
  let authority: { epoch: number; updated_at: string };
  let authoritySchemaSha256: string;
  let coreEvidenceSha256: string;
  try {
    authority = database.prepare(
      "SELECT epoch, updated_at FROM sales_write_authority WHERE id=1",
    ).get() as typeof authority;
    const triggers = (database.prepare(
      `SELECT name, sql FROM sqlite_master
       WHERE type='trigger' AND name LIKE 'sales_authority_%'
       ORDER BY name COLLATE BINARY`,
    ).all() as Array<{ name: string; sql: string | null }>).map(
      (row) => [row.name, row.sql ?? ""],
    );
    authoritySchemaSha256 = sha256(JSON.stringify(triggers));
    coreEvidenceSha256 = sha256(canonicalJson(salesD1CoreEvidence(database)));
  } finally {
    database.close();
  }
  const payload = {
    schemaVersion: SALES_CUTOVER_ATTESTATION_VERSION,
    cutoverId: CUTOVER_ID,
    observedAt: "2026-08-28T15:30:00.000Z",
    d1Authority: {
      owner: claimedOwner,
      epoch: Number(authority.epoch),
      updatedAt: new Date(`${authority.updated_at.replace(" ", "T")}Z`).toISOString(),
    },
    d1Blockers: {
      processingBatches: 0,
      activeUploads: 0,
      uploadChunks: 0,
      processingFingerprints: 0,
      processingAttempts: 0,
      processingScopeHeads: 0,
    },
    source: {
      pathSha256: sha256(resolvedSource),
      // This deliberately does not use Node's dev/ino representation.  The
      // retirement boundary must accept the Python attestation identity as an
      // opaque audit field and bind cross-runtime state through path/core.
      fileIdentitySha256: "f".repeat(64),
      sizeBytes: Number(details.size),
      authoritySchemaSha256,
    },
    postgresqlMigration: {
      applyRunId: "a".repeat(64),
      verifyRunId: "b".repeat(64),
      canonicalFormatVersion: "sales-projection-v4",
      sourceRevision: "8:5",
      targetCounts: {
        sales_import_batches: 1,
        erp_product_master: 1,
        sales_order_lines: 1,
        sales_query_projection: 1,
        import_content_fingerprints: 1,
        import_content_attempts: 1,
        import_scope_heads: 1,
        sales_import_uploads: 1,
        sales_import_upload_chunks: 0,
      },
      targetDigests: {
        sales_import_batches: "1".repeat(64),
        erp_product_master: "2".repeat(64),
        sales_order_lines: "3".repeat(64),
        sales_query_projection: "4".repeat(64),
        import_content_fingerprints: "5".repeat(64),
        import_content_attempts: "6".repeat(64),
        import_scope_heads: "7".repeat(64),
        sales_import_uploads: "8".repeat(64),
        sales_import_upload_chunks: "9".repeat(64),
      },
    },
    legacyCleanup: {
      manifestId: "c".repeat(64),
      manifestSha256: "d".repeat(64),
      sessionCount: 1,
      objectCount: 0,
      coreEvidenceSha256,
      lockedVerifyRunId: "b".repeat(64),
      completedAt: "2026-08-28T15:25:00.000Z",
    },
  };
  const payloadSha256 = sha256(canonicalJson(payload));
  await writeFile(attestationPath, `${JSON.stringify({
    schemaVersion: SALES_CUTOVER_ATTESTATION_VERSION,
    payload,
    payloadSha256,
  }, null, 2)}\n`, "utf8");
  return payloadSha256;
}

async function fixture(options: { terminal?: boolean } = {}): Promise<Fixture> {
  const root = await mkdtemp(path.join(tmpdir(), "sales-d1-retirement-"));
  const drizzle = path.join(root, "drizzle");
  await mkdir(drizzle);
  await copyFile(
    path.join(REPOSITORY_ROOT, "drizzle", "0092_sales_domain_retirement.sql"),
    path.join(drizzle, "0092_sales_domain_retirement.sql"),
  );
  const source = path.join(root, "source.sqlite");
  const database = new DatabaseSync(source);
  try {
    createSourceSchema(database);
    database.exec(await migration("0090_sales_write_authority.sql"));
    database.exec(await migration("0091_erp_reference_projection.sql"));
    database.exec(`
      INSERT INTO erp_reference_projection_outbox (
        event_id, source_epoch, domain, operation, scope_json,
        source_batch_id, erp_revision, row_count, content_hash,
        canonical_format_version, created_at
      )
      SELECT
        source.source_epoch || ':erp:' || state.source_batch_id,
        source.source_epoch, 'erp', 'replace_all', '{"source":"products"}',
        state.source_batch_id, state.erp_revision, state.row_count,
        state.content_hash, 'erp-reference-projection-v1', CURRENT_TIMESTAMP
      FROM erp_reference_projection_source_state source
      CROSS JOIN erp_product_projection_state state
      WHERE source.id=1 AND state.id=1;
    `);
    if (options.terminal !== false) {
      transitionSalesD1WriteAuthority(database, {
        expectedOwner: "d1",
        expectedEpoch: 1,
        targetOwner: "pending",
        cutoverId: CUTOVER_ID,
      });
      transitionSalesD1WriteAuthority(database, {
        expectedOwner: "pending",
        expectedEpoch: 2,
        targetOwner: "postgresql",
        cutoverId: CUTOVER_ID,
      });
    }
  } finally {
    database.close();
  }
  const attestationPath = path.join(root, "terminal-attestation.json");
  const attestationSha256 = await makeAttestation(source, attestationPath);
  const auditOutput = path.join(root, "retirement-audit.json");
  return {
    root,
    source,
    auditOutput,
    attestationPath,
    attestationSha256,
    input: {
      source,
      cutoverId: CUTOVER_ID,
      attestationPath,
      attestationSha256,
      auditOutput,
      repositoryRoot: root,
    },
  };
}

async function absent(filePath: string): Promise<boolean> {
  return access(filePath).then(() => false, () => true);
}

async function makeSmokeReceipt(item: Fixture, planId: string): Promise<{
  path: string;
  sha256: string;
}> {
  const results = Object.fromEntries(REQUIRED_CHECKS.map((name, index) => [name, {
    status: "passed",
    evidenceSha256: String(index + 1).repeat(64),
  }]));
  const payload = {
    version: "sales-postgresql-smoke-receipt-v1",
    planId,
    cutoverId: CUTOVER_ID,
    attestationPayloadSha256: item.attestationSha256,
    checkedAt: SMOKE_CHECKED_AT,
    expiresAt: SMOKE_EXPIRES_AT,
    requiredChecks: [...REQUIRED_CHECKS],
    results,
  };
  const receiptPath = path.join(item.root, "receipt.json");
  const bytes = `${canonicalJson({ payload, payloadSha256: sha256(canonicalJson(payload)) })}\n`;
  await writeFile(receiptPath, bytes, "utf8");
  return { path: receiptPath, sha256: sha256(bytes) };
}

function preflightEvidence(input: {
  planId: string;
  smokeReceiptSha256: string;
  attestationSha256: string;
}, overrides: Record<string, unknown> = {}): SalesPostgresqlRetirementPreflight & Record<string, unknown> {
  const core: Record<string, unknown> = {
    status: "verified",
    version: "sales-retirement-preflight-v1",
    planId: input.planId,
    cutoverId: CUTOVER_ID,
    attestationPayloadSha256: input.attestationSha256,
    pgAuthorityStatus: "active",
    pgAuthorityEpoch: "11111111-1111-4111-8111-111111111111",
    migrationVerifyRunId: "b".repeat(64),
    requiredChecks: [...REQUIRED_CHECKS],
    checkedAt: SMOKE_CHECKED_AT,
    expiresAt: SMOKE_EXPIRES_AT,
    smokeReceiptSha256: input.smokeReceiptSha256,
    ...overrides,
  };
  return {
    ...core,
    evidenceSha256: sha256(canonicalJson(core)),
  } as SalesPostgresqlRetirementPreflight & Record<string, unknown>;
}

async function execution(item: Fixture, options: {
  preflightOverrides?: Record<string, unknown>;
  afterStatement?: (index: number, statement: string, database: DatabaseSync) => void;
  includePreflight?: boolean;
} = {}) {
  const plan = await planSalesD1Retirement(item.input);
  assert.equal(plan.status, "planned");
  assert.match(plan.planId ?? "", /^[0-9a-f]{64}$/);
  const planId = plan.planId!;
  const smoke = await makeSmokeReceipt(item, planId);
  const requested: Array<Record<string, unknown>> = [];
  return {
    plan,
    input: {
      ...item.input,
      execute: true as const,
      approvedPlanId: planId,
      smokeReceiptPath: smoke.path,
      smokeReceiptSha256: smoke.sha256,
    },
    dependencies: {
      now: () => new Date(NOW),
      ...(options.includePreflight === false ? {} : {
        verifyPostgresqlPreflight: async (value: Record<string, unknown>) => {
          requested.push(value);
          return preflightEvidence({
            planId,
            smokeReceiptSha256: smoke.sha256,
            attestationSha256: item.attestationSha256,
          }, options.preflightOverrides);
        },
      }),
      ...(options.afterStatement ? { afterStatement: options.afterStatement } : {}),
    },
    requested,
    smoke,
  };
}

test("default retirement operation is a read-only evidence plan", async (t) => {
  const item = await fixture();
  t.after(() => rm(item.root, { recursive: true, force: true }));
  const before = await stat(item.source, { bigint: true });
  const parsed = parseSalesD1RetirementArguments([
    "--source", item.source,
    "--cutover-id", CUTOVER_ID,
    "--attestation", item.attestationPath,
    "--attestation-sha256", item.attestationSha256,
    "--audit-output", item.auditOutput,
  ]);
  assert.equal(parsed.mode, "plan");

  const result = await planSalesD1Retirement(item.input);

  const after = await stat(item.source, { bigint: true });
  assert.equal(result.status, "planned");
  assert.match(result.planId ?? "", /^[0-9a-f]{64}$/);
  assert.equal(result.authorityEpoch, 3);
  assert.deepEqual(Object.values(result.blockers ?? {}), [0, 0, 0, 0, 0, 0, 0]);
  assert.equal(result.salesTableCounts?.sales_order_lines, 1);
  assert.equal(before.mtimeNs, after.mtimeNs);
  assert.equal(await absent(item.auditOutput), true);
  assert.equal(await absent(`${item.auditOutput}.prepared`), true);
});

test("CLI execution is disabled and execute requires approved plan plus controlled live preflight", async (t) => {
  const item = await fixture();
  t.after(() => rm(item.root, { recursive: true, force: true }));

  assert.throws(
    () => parseSalesD1RetirementArguments([
      "--source", item.source,
      "--cutover-id", CUTOVER_ID,
      "--attestation", item.attestationPath,
      "--attestation-sha256", item.attestationSha256,
      "--audit-output", item.auditOutput,
      "--execute",
    ]),
    /CLI 只允许生成 plan/,
  );

  const planned = await execution(item, { includePreflight: false });
  await assert.rejects(
    executeSalesD1Retirement({
      ...item.input,
      execute: true,
      approvedPlanId: "0".repeat(64),
      smokeReceiptPath: planned.smoke.path,
      smokeReceiptSha256: planned.smoke.sha256,
    }),
    /approvedPlanId 与当前不可变 D1 退役计划不一致/,
  );

  const before = await stat(item.source, { bigint: true });
  await assert.rejects(
    executeSalesD1Retirement(planned.input, planned.dependencies),
    /缺少受控 PostgreSQL retirement preflight capability/,
  );
  const after = await stat(item.source, { bigint: true });
  assert.equal(after.mtimeNs, before.mtimeNs);
  const database = new DatabaseSync(item.source, { readOnly: true });
  try {
    assert.equal(tableExists(database, "sales_write_authority"), true);
    assert.equal(tableExists(database, "sales_order_lines"), true);
    assert.equal(tableExists(database, "domain_retirement_receipts"), false);
  } finally {
    database.close();
  }
  assert.equal(await absent(item.auditOutput), true);
});

test("the production execute export rejects a caller-controlled runtime before any D1 write", async (t) => {
  const item = await fixture();
  t.after(() => rm(item.root, { recursive: true, force: true }));
  const planned = await execution(item);
  const before = await stat(item.source, { bigint: true });
  await assert.rejects(
    executeSalesD1RetirementWithDjangoPreflight(planned.input, { runtimeRoot: item.root }),
    /固定 Windows 本机 runtime|runtime root 不符合固定部署契约/,
  );
  const after = await stat(item.source, { bigint: true });
  assert.equal(after.mtimeNs, before.mtimeNs);
  const database = new DatabaseSync(item.source, { readOnly: true });
  try {
    assert.equal(tableExists(database, "domain_retirement_receipts"), false);
    assert.equal(tableExists(database, "sales_order_lines"), true);
  } finally {
    database.close();
  }
});

test("retirement fails closed when D1 is not in the matching PostgreSQL terminal", async (t) => {
  const item = await fixture({ terminal: false });
  t.after(() => rm(item.root, { recursive: true, force: true }));

  await assert.rejects(planSalesD1Retirement(item.input), /PostgreSQL terminal/);
});

test("retirement rejects an attestation file or caller digest mismatch", async (t) => {
  const item = await fixture();
  t.after(() => rm(item.root, { recursive: true, force: true }));

  await assert.rejects(
    planSalesD1Retirement({ ...item.input, attestationSha256: "0".repeat(64) }),
    /attestation 文件、摘要或 cutoverId 不匹配/,
  );
  const envelope = JSON.parse(await readFile(item.attestationPath, "utf8")) as {
    payload: { legacyCleanup: { objectCount: number } };
  };
  envelope.payload.legacyCleanup.objectCount += 1;
  await writeFile(item.attestationPath, `${JSON.stringify(envelope, null, 2)}\n`, "utf8");
  await assert.rejects(
    planSalesD1Retirement(item.input),
    /attestation 文件、摘要或 cutoverId 不匹配/,
  );
});

test("retirement binds the current cleanup-v1 core digest instead of cross-runtime inode identity", async (t) => {
  const item = await fixture();
  t.after(() => rm(item.root, { recursive: true, force: true }));
  const envelope = JSON.parse(await readFile(item.attestationPath, "utf8")) as {
    schemaVersion: string;
    payload: { legacyCleanup: { coreEvidenceSha256: string } };
    payloadSha256: string;
  };
  assert.equal((envelope as unknown as { payload: { source: { fileIdentitySha256: string } } })
    .payload.source.fileIdentitySha256, "f".repeat(64));
  envelope.payload.legacyCleanup.coreEvidenceSha256 = "0".repeat(64);
  envelope.payloadSha256 = sha256(canonicalJson(envelope.payload));
  await writeFile(item.attestationPath, `${JSON.stringify(envelope, null, 2)}\n`, "utf8");

  await assert.rejects(
    planSalesD1Retirement({ ...item.input, attestationSha256: envelope.payloadSha256 }),
    /销售核心事实\/控制摘要与 attestation cleanup 证明不一致/,
  );
});

test("retirement rejects a new D1 blocker even after terminal attestation", async (t) => {
  const item = await fixture();
  t.after(() => rm(item.root, { recursive: true, force: true }));
  const database = new DatabaseSync(item.source);
  try {
    database.exec("DROP TRIGGER sales_authority_batches_insert");
    database.exec("INSERT INTO sales_import_batches VALUES ('late-processing', 'processing')");
  } finally {
    database.close();
  }

  await assert.rejects(planSalesD1Retirement(item.input), /blockers 非零/);
});

test("retirement rejects any trigger outside the exact shared-import authority boundary", async (t) => {
  const item = await fixture();
  t.after(() => rm(item.root, { recursive: true, force: true }));
  const database = new DatabaseSync(item.source);
  try {
    database.exec(`
      CREATE TRIGGER unexpected_shared_import_delete
      AFTER DELETE ON import_content_attempts
      BEGIN SELECT 1; END
    `);
  } finally {
    database.close();
  }

  await assert.rejects(
    planSalesD1Retirement(item.input),
    /shared-import trigger 集合超出或缺少 0090 既定 authority 边界/,
  );
});

test("retirement rejects inbound foreign keys that can cascade across its DROP or shared DELETE boundary", async (t) => {
  const cases = [
    {
      name: "retired sales table",
      sql: `CREATE TABLE cross_domain_sales_fk (
        source_id INTEGER REFERENCES sales_order_lines(id) ON DELETE CASCADE
      )`,
      target: "sales_order_lines",
    },
    {
      name: "shared import table",
      sql: `CREATE TABLE cross_domain_shared_fk (
        domain TEXT NOT NULL,
        scope_key TEXT NOT NULL,
        FOREIGN KEY(domain, scope_key)
          REFERENCES import_scope_heads(domain, scope_key) ON DELETE CASCADE
      )`,
      target: "import_scope_heads",
    },
  ] as const;
  for (const itemCase of cases) {
    await t.test(itemCase.name, async (subtest) => {
      const item = await fixture();
      subtest.after(() => rm(item.root, { recursive: true, force: true }));
      const database = new DatabaseSync(item.source);
      try {
        database.exec(itemCase.sql);
      } finally {
        database.close();
      }
      await assert.rejects(
        planSalesD1Retirement(item.input),
        new RegExp(`指向退役或 shared-delete 表 ${itemCase.target}`),
      );
    });
  }
});

test("retirement rejects boundary-external views and triggers that reference a retired table", async (t) => {
  const cases = [
    {
      name: "view",
      sql: "CREATE VIEW cross_domain_sales_view AS SELECT id FROM sales_order_lines",
    },
    {
      name: "trigger",
      sql: `CREATE TRIGGER cross_domain_sales_reference
        AFTER INSERT ON inventory_stock_lines
        BEGIN SELECT COUNT(*) FROM sales_order_lines; END`,
    },
  ] as const;
  for (const itemCase of cases) {
    await t.test(itemCase.name, async (subtest) => {
      const item = await fixture();
      subtest.after(() => rm(item.root, { recursive: true, force: true }));
      const database = new DatabaseSync(item.source);
      try {
        database.exec(itemCase.sql);
      } finally {
        database.close();
      }
      await assert.rejects(
        planSalesD1Retirement(item.input),
        /边界外 (?:view|trigger) .* 引用退役销售表 sales_order_lines/,
      );
    });
  }
});

test("retirement rejects any byte-level tampering of immutable 0092", async (t) => {
  const item = await fixture();
  t.after(() => rm(item.root, { recursive: true, force: true }));
  await appendFile(
    path.join(item.root, "drizzle", "0092_sales_domain_retirement.sql"),
    "\n-- tampered\n",
    "utf8",
  );

  await assert.rejects(planSalesD1Retirement(item.input), /0092 文件身份或内容摘要不匹配/);
});

test("preflight exact keys, identity and freshness fail before the first D1 write", async (t) => {
  const item = await fixture();
  t.after(() => rm(item.root, { recursive: true, force: true }));
  const before = await stat(item.source, { bigint: true });
  const extra = await execution(item, { preflightOverrides: { injectedApproval: true } });
  await assert.rejects(
    executeSalesD1Retirement(extra.input, extra.dependencies),
    /preflight 字段集合无效/,
  );
  const expired = await execution(item, {
    preflightOverrides: { checkedAt: "2026-08-28T15:20:00Z", expiresAt: "2026-08-28T15:25:00Z" },
  });
  await assert.rejects(
    executeSalesD1Retirement(expired.input, expired.dependencies),
    /preflight 已过期或来自未来/,
  );
  const after = await stat(item.source, { bigint: true });
  assert.equal(after.mtimeNs, before.mtimeNs);
  const database = new DatabaseSync(item.source, { readOnly: true });
  try {
    assert.equal(tableExists(database, "domain_retirement_receipts"), false);
    assert.equal(tableExists(database, "sales_order_lines"), true);
  } finally {
    database.close();
  }
});

test("a mid-migration SQL failure rolls back every D1 retirement mutation", async (t) => {
  const item = await fixture();
  t.after(() => rm(item.root, { recursive: true, force: true }));
  const planned = await execution(item, {
    afterStatement(index, _statement, database) {
      if (index === 12) database.exec("SELECT missing_retirement_sql_function()");
    },
  });

  await assert.rejects(
    executeSalesD1Retirement(planned.input, planned.dependencies),
    /missing_retirement_sql_function|no such function/,
  );

  const database = new DatabaseSync(item.source, { readOnly: true });
  try {
    for (const table of RETIRED_SALES_TABLES) assert.equal(tableExists(database, table), true, table);
    assert.equal(triggerExists(database, "sales_authority_order_lines_insert"), true);
    assert.equal(triggerExists(database, "market_monthly_summary_sales_insert"), true);
    assert.equal(
      database.prepare("SELECT owner FROM sales_write_authority WHERE id=1").get()?.owner,
      "postgresql",
    );
    assert.equal(
      database.prepare("SELECT COUNT(*) count FROM import_content_fingerprints WHERE domain='sales'").get()?.count,
      1,
    );
    assert.equal(database.prepare("SELECT COUNT(*) count FROM erp_product_master").get()?.count, 1);
    assert.equal(tableExists(database, "domain_retirement_receipts"), false);
  } finally {
    database.close();
  }
  assert.equal(await absent(item.auditOutput), true);
  assert.equal(await absent(`${item.auditOutput}.prepared`), false);
});

test("successful retirement preserves ERP and non-sales state and is evidence-idempotent", async (t) => {
  const item = await fixture();
  t.after(() => rm(item.root, { recursive: true, force: true }));
  const planned = await execution(item);

  const first = await executeSalesD1Retirement(planned.input, planned.dependencies);
  assert.equal(first.status, "completed");
  assert.equal(planned.requested.length, 1);
  assert.deepEqual(planned.requested[0], {
    planId: planned.plan.planId,
    cutoverId: CUTOVER_ID,
    attestationPayloadSha256: item.attestationSha256,
    migrationVerifyRunId: "b".repeat(64),
    smokeReceiptPath: await realpath(planned.smoke.path),
    smokeReceiptSha256: planned.smoke.sha256,
  });
  assert.equal(await absent(item.auditOutput), false);
  assert.equal(await absent(`${item.auditOutput}.prepared`), true);

  const database = new DatabaseSync(item.source, { readOnly: true });
  try {
    for (const table of RETIRED_SALES_TABLES) {
      assert.equal(tableExists(database, table), false, table);
      assert.equal(viewExists(database, table), true, table);
      assert.equal(database.prepare(`SELECT COUNT(*) count FROM "${table}"`).get()?.count, 0, table);
    }
    for (const table of [
      "erp_product_master",
      "erp_reference_projection_source_state",
      "erp_product_projection_state",
      "erp_reference_projection_outbox",
      "erp_reference_import_batches",
      "inventory_stock_lines",
    ]) assert.equal(tableExists(database, table), true, table);
    for (const table of [
      "erp_reference_projection_source_state",
      "erp_product_projection_state",
      "erp_reference_projection_outbox",
    ]) assert.equal(database.prepare(`SELECT COUNT(*) count FROM ${table}`).get()?.count, 1, table);
    assert.deepEqual(
      { ...database.prepare("SELECT * FROM erp_product_master").get() },
      { product_code: "ERP-1", product_name: "ERP preserved" },
    );
    assert.deepEqual(first.audit.result.retirementTombstoneViewsPresent, RETIREMENT_TOMBSTONE_VIEWS);
    assert.deepEqual(
      first.audit.result.sharedImportRetirementGuardsPresent,
      SHARED_IMPORT_RETIREMENT_GUARDS,
    );
    assert.deepEqual(
      { ...database.prepare(
        "SELECT id, source_key, status, row_count, totals_json FROM erp_reference_import_batches",
      ).get() },
      {
        id: "erp-products-completed",
        source_key: "products",
        status: "completed",
        row_count: 1,
        totals_json: "{\"contentHash\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}",
      },
    );
    assert.deepEqual(
      { ...database.prepare("SELECT product_code, quantity FROM inventory_stock_lines").get() },
      { product_code: "ERP-1", quantity: 7 },
    );
    for (const table of [
      "import_content_fingerprints",
      "import_content_attempts",
      "import_scope_heads",
    ]) {
      assert.equal(database.prepare(`SELECT COUNT(*) count FROM ${table} WHERE domain='sales'`).get()?.count, 0);
      assert.equal(database.prepare(`SELECT COUNT(*) count FROM ${table} WHERE domain='inventory'`).get()?.count, 1);
    }
    for (const objectName of [
      "erp_reference_projection_outbox_event_id_uq",
      "erp_reference_projection_source_no_update",
      "erp_product_projection_state_guard",
      "erp_product_import_requires_projection_event",
      "inventory_stock_lines_product_code_idx",
    ]) {
      assert.ok(database.prepare("SELECT 1 FROM sqlite_master WHERE name=?").get(objectName), objectName);
    }
    assert.deepEqual(
      { ...database.prepare(
        `SELECT domain, version, status, cutover_id cutoverId, plan_id planId,
                attestation_sha256 attestationSha256, smoke_receipt_sha256 smokeReceiptSha256,
                preflight_evidence_sha256 preflightEvidenceSha256, migration_sha256 migrationSha256,
                audit_id auditId, completed_at completedAt
         FROM domain_retirement_receipts WHERE domain='sales'`,
      ).get() },
      {
        domain: "sales",
        version: "sales-domain-retirement-receipt-v1",
        status: "completed",
        cutoverId: CUTOVER_ID,
        planId: planned.plan.planId,
        attestationSha256: item.attestationSha256,
        smokeReceiptSha256: planned.smoke.sha256,
        preflightEvidenceSha256: first.audit.postgresqlPreflight.evidenceSha256,
        migrationSha256: first.audit.migration.fileSha256,
        auditId: first.audit.auditId,
        completedAt: NOW.toISOString(),
      },
    );
  } finally {
    database.close();
  }

  const mutable = new DatabaseSync(item.source);
  try {
    mutable.exec(`
      INSERT INTO erp_reference_import_batches VALUES (
        'erp-products-new-processing', 'products', 'processing', 0,
        '{"contentHash":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}',
        '2026-08-28T15:40:00Z', NULL
      );
      INSERT INTO inventory_stock_lines VALUES (2, 'ERP-NEW', 3);
    `);
  } finally {
    mutable.close();
  }

  const second = await executeSalesD1Retirement(planned.input);
  assert.equal(second.status, "already_completed");
  assert.equal(second.audit.auditId, first.audit.auditId);
});

test("an approved plan remains valid across legitimate ERP and non-sales writes before execute", async (t) => {
  const item = await fixture();
  t.after(() => rm(item.root, { recursive: true, force: true }));
  const approved = await planSalesD1Retirement(item.input);
  assert.equal(approved.status, "planned");

  const database = new DatabaseSync(item.source);
  try {
    database.exec(`
      INSERT INTO erp_reference_import_batches VALUES (
        'erp-before-retirement', 'products', 'processing', 0,
        '{"contentHash":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"}',
        '2026-08-28T15:31:00Z', NULL
      );
      INSERT INTO inventory_stock_lines VALUES (4, 'ERP-BEFORE-RETIREMENT', 9);
    `);
  } finally {
    database.close();
  }

  const planned = await execution(item);
  assert.equal(planned.plan.planId, approved.planId);
  const completed = await executeSalesD1Retirement({
    ...planned.input,
    approvedPlanId: approved.planId!,
  }, planned.dependencies);
  assert.equal(completed.status, "completed");

  const preserved = new DatabaseSync(item.source, { readOnly: true });
  try {
    assert.equal(
      preserved.prepare("SELECT COUNT(*) count FROM erp_reference_import_batches").get()?.count,
      2,
    );
    assert.equal(
      preserved.prepare("SELECT quantity FROM inventory_stock_lines WHERE product_code='ERP-BEFORE-RETIREMENT'").get()?.quantity,
      9,
    );
  } finally {
    preserved.close();
  }
});

test("a committed retirement recovers a lost final response from receipt plus prepared audit after ERP changes", async (t) => {
  const item = await fixture();
  t.after(() => rm(item.root, { recursive: true, force: true }));
  const planned = await execution(item);
  const first = await executeSalesD1Retirement(planned.input, planned.dependencies);
  await rename(item.auditOutput, `${item.auditOutput}.prepared`);

  const database = new DatabaseSync(item.source);
  try {
    database.exec(`
      INSERT INTO erp_reference_import_batches VALUES (
        'erp-after-commit', 'products', 'processing', 0,
        '{"contentHash":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"}',
        '2026-08-28T15:45:00Z', NULL
      );
      INSERT INTO inventory_stock_lines VALUES (3, 'ERP-AFTER-COMMIT', 4);
    `);
  } finally {
    database.close();
  }

  const recovered = await executeSalesD1Retirement(planned.input);
  assert.equal(recovered.status, "already_completed");
  assert.equal(recovered.audit.auditId, first.audit.auditId);
  assert.equal(await absent(item.auditOutput), false);
  assert.equal(await absent(`${item.auditOutput}.prepared`), true);
});

test("completed replay rejects a writable trigger attached to a retirement tombstone", async (t) => {
  const item = await fixture();
  t.after(() => rm(item.root, { recursive: true, force: true }));
  const planned = await execution(item);
  await executeSalesD1Retirement(planned.input, planned.dependencies);

  const database = new DatabaseSync(item.source);
  try {
    database.exec(`
      CREATE TRIGGER sales_order_lines_tombstone_write
      INSTEAD OF INSERT ON sales_order_lines
      BEGIN SELECT 1; END
    `);
  } finally {
    database.close();
  }

  await assert.rejects(
    executeSalesD1Retirement(planned.input),
    /tombstone view 存在可写 trigger/,
  );
});

test("completed replay rejects a missing or forged permanent shared-import guard", async (t) => {
  const item = await fixture();
  t.after(() => rm(item.root, { recursive: true, force: true }));
  const planned = await execution(item);
  await executeSalesD1Retirement(planned.input, planned.dependencies);

  const database = new DatabaseSync(item.source);
  try {
    database.exec("DROP TRIGGER sales_retired_attempts_insert_guard");
    database.exec(`
      CREATE TRIGGER sales_retired_attempts_insert_guard
      BEFORE INSERT ON import_content_attempts
      WHEN NEW.domain = 'inventory'
      BEGIN SELECT RAISE(ABORT, 'forged_guard'); END
    `);
  } finally {
    database.close();
  }

  await assert.rejects(
    executeSalesD1Retirement(planned.input),
    /shared-import guard 不完整或语义不一致/,
  );
});

test("prepared-audit recovery rejects a forged retirement tombstone", async (t) => {
  const item = await fixture();
  t.after(() => rm(item.root, { recursive: true, force: true }));
  const planned = await execution(item);
  await executeSalesD1Retirement(planned.input, planned.dependencies);
  await rename(item.auditOutput, `${item.auditOutput}.prepared`);

  const database = new DatabaseSync(item.source);
  try {
    database.exec(`
      DROP VIEW sales_order_lines;
      CREATE VIEW sales_order_lines AS
        SELECT 'forged' AS retirement_tombstone
        WHERE 0
    `);
  } finally {
    database.close();
  }

  await assert.rejects(
    executeSalesD1Retirement(planned.input),
    /tombstone view 不完整或语义不一致/,
  );
  assert.equal(await absent(item.auditOutput), true);
  assert.equal(await absent(`${item.auditOutput}.prepared`), false);
});

test("strict audit keys and immutable D1 receipt cannot be forged during recovery", async (t) => {
  const item = await fixture();
  t.after(() => rm(item.root, { recursive: true, force: true }));
  const planned = await execution(item);
  await executeSalesD1Retirement(planned.input, planned.dependencies);

  const database = new DatabaseSync(item.source);
  try {
    assert.throws(
      () => database.exec("UPDATE domain_retirement_receipts SET plan_id='" + "0".repeat(64) + "' WHERE domain='sales'"),
      /domain_retirement_receipt_update_forbidden/,
    );
    assert.throws(
      () => database.exec("DELETE FROM domain_retirement_receipts WHERE domain='sales'"),
      /domain_retirement_receipt_delete_forbidden/,
    );
    assert.throws(
      () => database.exec(`
        INSERT OR REPLACE INTO domain_retirement_receipts (
          domain, version, status, cutover_id, plan_id, attestation_sha256,
          smoke_receipt_sha256, preflight_evidence_sha256, migration_sha256,
          audit_id, preserved_evidence_sha256, created_at, completed_at
        ) SELECT
          domain, version, 'approved', cutover_id, plan_id, attestation_sha256,
          smoke_receipt_sha256, preflight_evidence_sha256, migration_sha256,
          audit_id, preserved_evidence_sha256, created_at, NULL
        FROM domain_retirement_receipts WHERE domain='sales'
      `),
      /domain_retirement_receipt_insert_forbidden/,
    );
  } finally {
    database.close();
  }

  const audit = JSON.parse(await readFile(item.auditOutput, "utf8")) as Record<string, unknown>;
  audit.untrustedApproval = true;
  await writeFile(item.auditOutput, `${JSON.stringify(audit, null, 2)}\n`, "utf8");
  await assert.rejects(
    executeSalesD1Retirement(planned.input),
    /retirement audit 字段集合无效/,
  );
});

test("an already-retired D1 without immutable audit evidence is never guessed complete", async (t) => {
  const item = await fixture();
  t.after(() => rm(item.root, { recursive: true, force: true }));
  const planned = await execution(item);
  await executeSalesD1Retirement(planned.input, planned.dependencies);
  await rm(item.auditOutput);

  await assert.rejects(
    executeSalesD1Retirement(planned.input),
    /缺少同一不可变审计证据/,
  );
});
