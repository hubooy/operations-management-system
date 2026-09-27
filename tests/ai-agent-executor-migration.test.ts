import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { registerHooks } from "node:module";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";

import type { D1Database as SalesDatabase } from "../lib/database/d1";
import { installDjangoAccessControlFixture } from "./access-control-service-fixture";

const testEnvironment: { DB?: unknown } = {};
(globalThis as typeof globalThis & { __aiAgentExecutorMigrationTestEnv?: typeof testEnvironment })
  .__aiAgentExecutorMigrationTestEnv = testEnvironment;

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "cloudflare:workers") {
      return {
        url: "data:text/javascript,export const env=globalThis.__aiAgentExecutorMigrationTestEnv;",
        shortCircuit: true,
      };
    }
    return nextResolve(specifier, context);
  },
});

const { ensureAiAgentExecutorSchema } = await import("./legacy/ai/agent-executor-schema");
const { selectNextWorkflowExecutorAdmission } = await import("./legacy/ai/agent-executor-admission");
const { ensureAuthorizationSchema } = await import("./legacy/auth/authorization");

test("0087 and runtime compatibility upgrades are safe in either rollout order", async () => {
  const workflowMigration = await readFile(new URL("../drizzle/0086_ai_agent_workflows.sql", import.meta.url), "utf8");
  const executorMigration = await readFile(new URL("../drizzle/0087_ai_agent_executor.sql", import.meta.url), "utf8");
  const snapshots: unknown[] = [];

  for (const order of ["migration-first", "runtime-first"] as const) {
    const sqlite = new DatabaseSync(":memory:");
    sqlite.exec("PRAGMA foreign_keys = ON");
    const db = sqliteAdapter(sqlite);
    testEnvironment.DB = db;
    try {
      if (order === "migration-first") {
        applyMigration(sqlite, workflowMigration);
        applyMigration(sqlite, executorMigration);
        await ensureAiAgentExecutorSchema(db);
      } else {
        await ensureAiAgentExecutorSchema(db);
        applyMigration(sqlite, workflowMigration);
        applyMigration(sqlite, executorMigration);
      }
      snapshots.push(schemaSnapshot(sqlite));
    } finally {
      delete testEnvironment.DB;
      sqlite.close();
    }
  }

  assert.deepEqual(snapshots[0], snapshots[1]);
});

test("a malformed oldest workflow admission fails closed without starving the next run", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec("PRAGMA foreign_keys = ON");
  const db = sqliteAdapter(sqlite);
  testEnvironment.DB = db;
  try {
    installDjangoAccessControlFixture(sqlite);
    await ensureAiAgentExecutorSchema(db);
    await ensureAuthorizationSchema(db);
    sqlite.prepare(`INSERT INTO app_users (email, display_name, role, status, scope_json)
      VALUES ('owner@example.com', 'Owner', 'operator', 'active', NULL)`).run();
    sqlite.prepare(`INSERT INTO ai_workflow_runs (
        id, owner_email, client_request_id, request_digest, scope_json, name,
        graph_json, graph_digest, model_id, model_version, allowed_tools_json,
        tool_policy_digest, dry_run, status
      ) VALUES (?, 'owner@example.com', ?, ?, 'null', ?, ?, ?, 'model-1', 1, '[]', ?, 0, 'queued')`)
      .run(
        "000-invalid-workflow",
        "invalid-workflow-request",
        "invalid-workflow-digest",
        "Invalid admission",
        JSON.stringify({ nodes: [{ key: "agent", type: "agent", dependsOn: [], instruction: "test" }] }),
        "invalid-graph-digest",
        "not-a-sha256",
      );
    sqlite.prepare(`INSERT INTO ai_agent_jobs (
        id, owner_email, client_request_id, request_digest, scope_json, task,
        workflow_run_id, workflow_node_key, model_id, model_version,
        allowed_tools_json, tool_policy_digest, status
      ) VALUES ('invalid-child', 'owner@example.com', 'invalid-child-request', 'invalid-child-digest',
        'null', 'child', '000-invalid-workflow', 'agent', 'model-1', 1, '[]', ?, 'queued')`)
      .run("b".repeat(64));
    sqlite.prepare(`INSERT INTO ai_workflow_node_runs (
        id, run_id, node_key, position, node_type, depends_on_json, instruction,
        status, agent_job_id
      ) VALUES ('invalid-node', '000-invalid-workflow', 'agent', 0, 'agent', '[]', 'test',
        'pending', 'invalid-child')`).run();
    sqlite.prepare(`INSERT INTO ai_workflow_runs (
        id, owner_email, client_request_id, request_digest, scope_json, name,
        graph_json, graph_digest, model_id, model_version, allowed_tools_json,
        tool_policy_digest, dry_run, status
      ) VALUES (?, 'owner@example.com', ?, ?, 'null', ?, ?, ?, 'model-1', 1, '[]', ?, 0, 'queued')`)
      .run(
        "999-valid-workflow",
        "valid-workflow-request",
        "valid-workflow-digest",
        "Valid admission",
        JSON.stringify({ nodes: [{ key: "agent", type: "agent", dependsOn: [], instruction: "test" }] }),
        "valid-graph-digest",
        "a".repeat(64),
      );

    assert.equal(await selectNextWorkflowExecutorAdmission(db), null);
    assert.deepEqual(
      { ...sqlite.prepare("SELECT status, error_code, cancel_requested FROM ai_workflow_runs WHERE id = '000-invalid-workflow'").get() },
      { status: "failed", error_code: "executor_admission_invalid", cancel_requested: 1 },
    );
    assert.deepEqual(
      { ...sqlite.prepare("SELECT status, error_code FROM ai_workflow_node_runs WHERE id = 'invalid-node'").get() },
      { status: "failed", error_code: "executor_admission_invalid" },
    );
    assert.deepEqual(
      { ...sqlite.prepare("SELECT status, error_code, cancel_requested FROM ai_agent_jobs WHERE id = 'invalid-child'").get() },
      { status: "failed", error_code: "executor_admission_invalid", cancel_requested: 1 },
    );
    const nextAdmission = await selectNextWorkflowExecutorAdmission(db);
    assert.ok(nextAdmission);
    assert.equal(nextAdmission.modelId, "model-1");
    assert.equal(nextAdmission.modelVersion, 1);
  } finally {
    delete testEnvironment.DB;
    sqlite.close();
  }
});

function schemaSnapshot(sqlite: DatabaseSync) {
  const columnNames = (table: string) => (sqlite.prepare(`PRAGMA table_info(${table})`).all() as Array<{ name: string }>)
    .map((column) => column.name)
    .sort();
  const indexNames = (table: string) => (sqlite.prepare(`PRAGMA index_list(${table})`).all() as Array<{ name: string }>)
    .map((index) => index.name)
    .sort();

  assert.equal(columnNames("ai_models").includes("version"), true);
  for (const table of ["ai_agent_jobs", "ai_workflow_runs"]) {
    const columns = columnNames(table);
    for (const required of [
      "model_id",
      "model_version",
      "allowed_tools_json",
      "tool_policy_digest",
      "provider_round_count",
      "tool_call_count",
      "provider_dispatch_started_at",
    ]) assert.equal(columns.includes(required), true, `${table}.${required}`);
  }

  return {
    modelColumns: columnNames("ai_models"),
    jobColumns: columnNames("ai_agent_jobs"),
    workflowColumns: columnNames("ai_workflow_runs"),
    providerDispatchColumns: columnNames("ai_agent_provider_dispatches"),
    providerDispatchIndexes: indexNames("ai_agent_provider_dispatches"),
    providerResultColumns: columnNames("ai_agent_provider_results"),
    toolDispatchColumns: columnNames("ai_agent_tool_dispatches"),
    toolDispatchIndexes: indexNames("ai_agent_tool_dispatches"),
    toolResultColumns: columnNames("ai_agent_tool_results"),
  };
}

function applyMigration(sqlite: DatabaseSync, source: string) {
  const statements = source.split("--> statement-breakpoint")
    .map((statement) => statement.trim())
    .filter(Boolean);
  assert.ok(statements.length > 0);
  for (const statement of statements) sqlite.prepare(statement).run();
}

function sqliteAdapter(sqlite: DatabaseSync): SalesDatabase {
  return {
    prepare(sql: string) {
      let values: Array<string | number | bigint | Uint8Array | null> = [];
      return {
        bind(...nextValues: unknown[]) { values = nextValues as typeof values; return this; },
        async first<T>() { return (sqlite.prepare(sql).get(...values) ?? null) as T | null; },
        async all<T>() { return { results: sqlite.prepare(sql).all(...values) as T[] }; },
        async run() {
          const result = sqlite.prepare(sql).run(...values);
          return { meta: { changes: Number(result.changes) } };
        },
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
  } as unknown as SalesDatabase;
}
