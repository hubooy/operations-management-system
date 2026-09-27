import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";

import type { AppPrincipal } from "../lib/auth/authorization";
import {
  ensureAiDataKnowledgeSchema,
  retrieveKnowledgeForPrompt,
  searchAiKnowledge,
  systemKnowledgeSeeds,
} from "./legacy/ai/data-knowledge";
import {
  AI_ARTIFACT_LIMITS,
  boundAiTableArtifactCandidates,
  extractAiTableArtifactCandidates,
  getAiArtifactDownload,
  isAiArtifactId,
  listAiArtifactsForConversation,
  persistAiTableArtifacts,
  recordAiArtifactDelivery,
  toSafeCsv,
} from "./legacy/ai/artifacts";
import type { D1Database } from "../lib/database/d1";

const analyst: AppPrincipal = {
  email: "analyst@example.com",
  displayName: "Analyst",
  role: "analyst",
  scope: null,
};
const viewer: AppPrincipal = { ...analyst, email: "viewer@example.com", role: "viewer" };
const admin: AppPrincipal = { ...analyst, email: "admin@example.com", role: "admin" };

test("0042 creates the bounded knowledge, artifact, and delivery contracts", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(await readFile(new URL("../drizzle/0042_ai_knowledge_and_artifacts.sql", import.meta.url), "utf8"));
  const tables = sqlite.prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").all()
    .map((row) => String((row as { name: string }).name));
  assert.deepEqual(tables, ["ai_artifact_deliveries", "ai_artifacts", "ai_knowledge_entries"]);
  const deliveryColumns = sqlite.prepare("PRAGMA table_info(ai_artifact_deliveries)").all()
    .map((row) => String((row as { name: string }).name));
  assert.ok(deliveryColumns.includes("request_id"));
  assert.ok(deliveryColumns.includes("actor_role"));
  sqlite.close();
});

test("system knowledge is seeded idempotently, role-filtered, and source-bounded", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  await ensureAiDataKnowledgeSchema(db);
  await ensureAiDataKnowledgeSchema(db);
  const count = sqlite.prepare("SELECT COUNT(*) count FROM ai_knowledge_entries").get() as { count: number };
  assert.equal(count.count, systemKnowledgeSeeds.length);

  const market = await searchAiKnowledge({ query: "市场定位价", limit: 8 }, analyst, db);
  assert.equal(market.items[0]?.id, "knowledge-market-price-semantics");
  assert.equal(market.filtersApplied.role, "analyst");
  assert.equal(market.matchMode, "deterministic_lexical");

  const viewerResult = await searchAiKnowledge({ query: "市场定位价", limit: 8 }, viewer, db);
  assert.equal(viewerResult.items.some((item) => item.id === "knowledge-market-price-semantics"), false);
  await assert.rejects(searchAiKnowledge({ query: "单", limit: 4 }, analyst, db), /2—80/);

  sqlite.prepare("UPDATE ai_knowledge_entries SET status='disabled' WHERE id='knowledge-market-price-semantics'").run();
  const disabled = await searchAiKnowledge({ query: "市场定位价", limit: 8 }, analyst, db);
  assert.equal(disabled.items.some((item) => item.id === "knowledge-market-price-semantics"), false);

  const promptContext = await retrieveKnowledgeForPrompt(`${"金额单位与人民币分".repeat(20)}尾部`, analyst, db);
  assert.ok(promptContext.context.length <= 3_000);
  assert.match(promptContext.context, /<knowledge source=/);
  assert.ok(promptContext.sourceIds.length <= 4);
  sqlite.close();
});

test("table artifacts enforce row, column, cell, payload, and sensitive-field limits", () => {
  const items = Array.from({ length: 60 }, (_, index) => ({
    sku: `SKU-${index}`,
    name: index === 0 ? "=WEBSERVICE(\"https://example.invalid\")" : `商品 ${index}`,
    amountCents: index * 100,
    active: index % 2 === 0,
    note: "长".repeat(300),
    secretToken: "must-not-leak",
    rawContent: "must-not-leak",
    nested: { ignored: true },
  }));
  const candidates = extractAiTableArtifactCandidates({
    toolName: "get_product_performance",
    toolTitle: "商品表现查询",
    data: { items, totalMatched: 80, truncated: false },
  });
  assert.equal(candidates.length, 1);
  const candidate = candidates[0];
  assert.equal(candidate.rows.length, AI_ARTIFACT_LIMITS.rowsPerTable);
  assert.equal(candidate.rowCount, 80);
  assert.equal(candidate.truncated, true);
  assert.ok(candidate.columns.length <= AI_ARTIFACT_LIMITS.columnsPerTable);
  assert.equal(candidate.columns.includes("secretToken"), false);
  assert.equal(candidate.columns.includes("rawContent"), false);
  assert.equal(candidate.columns.includes("nested"), false);
  assert.ok(Array.from(String(candidate.rows[0][candidate.columns.indexOf("note")])).length <= AI_ARTIFACT_LIMITS.cellCharacters);

  const messageCandidates = boundAiTableArtifactCandidates([candidate, candidate, candidate]);
  const messagePayloadBytes = messageCandidates.reduce(
    (total, item) => total + new TextEncoder().encode(JSON.stringify(item)).byteLength,
    0,
  );
  assert.ok(messagePayloadBytes <= AI_ARTIFACT_LIMITS.payloadBytes);
  assert.ok(messageCandidates.reduce((total, item) => total + item.rows.length, 0) < 3 * AI_ARTIFACT_LIMITS.rowsPerTable);
});

test("CSV output quotes fields and neutralizes spreadsheet formulas", () => {
  const csv = toSafeCsv(
    ["name", "note"],
    [["=1+1", "a,b"], ["  +SUM(A1:A2)", "line\n\"quoted\""]],
  );
  assert.match(csv, /^name,note\r\n'=1\+1,"a,b"/);
  assert.match(csv, /'  \+SUM\(A1:A2\)/);
  assert.match(csv, /"line\n""quoted"""/);
});

test("artifact persistence and download recheck owner and record bounded receipts", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const db = sqliteAdapter(sqlite);
  sqlite.exec(`CREATE TABLE ai_conversations (
    id TEXT PRIMARY KEY NOT NULL,
    created_by TEXT NOT NULL
  );
  CREATE TABLE ai_conversation_messages (
    id TEXT PRIMARY KEY NOT NULL,
    conversation_id TEXT NOT NULL
  );`);
  sqlite.prepare("INSERT INTO ai_conversations (id, created_by) VALUES ('conversation-1', ?)")
    .run(analyst.email);
  sqlite.prepare("INSERT INTO ai_conversation_messages (id, conversation_id) VALUES ('message-1', 'conversation-1')")
    .run();
  const [candidate] = extractAiTableArtifactCandidates({
    toolName: "get_sales_summary",
    toolTitle: "销售汇总",
    data: { items: [{ shop: "旗舰店", netSalesCents: 12_345 }, { shop: "专营店", netSalesCents: -250 }], totalMatched: 2 },
  });
  const [artifact] = await persistAiTableArtifacts({
    conversationId: "conversation-1",
    messageId: "message-1",
    principal: analyst,
    candidates: [candidate],
    database: db,
  });
  assert.match(artifact.id, /^ai-artifact-[a-f0-9-]{36}$/);
  assert.equal(isAiArtifactId(artifact.id), true);
  assert.equal(isAiArtifactId("ai-artifact-------------------------------------"), false);

  const owned = await listAiArtifactsForConversation("conversation-1", analyst, db);
  assert.equal(owned.get("message-1")?.length, 1);
  const denied = await listAiArtifactsForConversation("conversation-1", viewer, db);
  assert.equal(denied.size, 0);
  const adminVisible = await listAiArtifactsForConversation("conversation-1", admin, db);
  assert.equal(adminVisible.get("message-1")?.[0].id, artifact.id);

  assert.equal(await getAiArtifactDownload(artifact.id, viewer, db), null);
  const download = await getAiArtifactDownload(artifact.id, analyst, db);
  assert.ok(download);
  assert.equal(download.bytes[0], 0xef);
  assert.equal(download.bytes[1], 0xbb);
  assert.equal(download.bytes[2], 0xbf);
  await recordAiArtifactDelivery({
    artifactId: artifact.id,
    requestId: "request-download-1",
    principal: analyst,
    status: "succeeded",
    byteSize: download.bytes.byteLength,
    contentDigest: download.contentDigest,
    database: db,
  });
  const receipt = sqlite.prepare(`SELECT request_id requestId, actor_email actorEmail,
    actor_role actorRole, surface, status, byte_size byteSize, error_code errorCode
    FROM ai_artifact_deliveries`).get() as Record<string, unknown>;
  assert.deepEqual({ ...receipt }, {
    requestId: "request-download-1",
    actorEmail: analyst.email,
    actorRole: "analyst",
    surface: "web_download",
    status: "succeeded",
    byteSize: download.bytes.byteLength,
    errorCode: null,
  });
  sqlite.close();
});

function sqliteAdapter(sqlite: DatabaseSync): D1Database {
  return {
    prepare(sql: string) {
      let values: Array<string | number | bigint | Uint8Array | null> = [];
      return {
        bind(...nextValues: unknown[]) { values = nextValues as typeof values; return this; },
        async first<T>() { return (sqlite.prepare(sql).get(...values) ?? null) as T | null; },
        async all<T>() { return { results: sqlite.prepare(sql).all(...values) as T[] }; },
        async run() { const result = sqlite.prepare(sql).run(...values); return { meta: { changes: Number(result.changes) } }; },
      };
    },
    async batch(statements: Array<{ run(): Promise<unknown> }>) {
      sqlite.exec("BEGIN");
      try {
        const results = [];
        for (const statement of statements) results.push(await statement.run());
        sqlite.exec("COMMIT");
        return results;
      } catch (error) {
        sqlite.exec("ROLLBACK");
        throw error;
      }
    },
  } as unknown as D1Database;
}
