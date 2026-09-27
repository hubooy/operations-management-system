// Isolated historical audit/test entry. Never imported by runtime launch code.
import { lstat } from "node:fs/promises";
import path from "node:path";
import { canonicalProofJson as canonicalJson } from "./d1-retirement-proof.mjs";
function fail(message) { throw new Error(message); }
export async function inspectD1RetirementState(sourceD1Path) {
  if (typeof sourceD1Path !== "string" || !path.win32.isAbsolute(sourceD1Path)) fail("D1 tombstone 检查路径无效");
  const info = await lstat(sourceD1Path);
  if (!info.isFile() || info.isSymbolicLink()) fail("D1 tombstone 检查必须使用普通文件");
  const { DatabaseSync } = await import("node:sqlite");
  const database = new DatabaseSync(sourceD1Path, { readOnly: true });
  try {
    const receiptObject = database.prepare("SELECT type FROM sqlite_master WHERE name = ? LIMIT 1").get("domain_retirement_receipts");
    const retiredNames = [
      "sales_import_upload_chunks", "sales_import_uploads", "sales_order_lines", "sales_import_batches",
      "sales_overview_response_cache", "sales_overview_cache_state", "sales_projection_outbox",
      "sales_projection_source_state", "sales_write_authority",
    ];
    const placeholders = retiredNames.map(() => "?").join(",");
    const rows = database.prepare(
      `SELECT type, name, sql FROM sqlite_master WHERE name IN (${placeholders}) ORDER BY name`,
    ).all(...retiredNames);
    const viewRows = rows.filter((row) => row.type === "view");
    if (viewRows.length > 0 && (rows.length !== retiredNames.length || viewRows.length !== retiredNames.length)) {
      fail("D1 sales retirement tombstone views 部分存在或与旧表混合");
    }
    const exactViewsPresent = viewRows.length === retiredNames.length;
    if (exactViewsPresent) {
      const expectedNames = [...retiredNames].sort();
      if (canonicalJson(rows.map((row) => row.name)) !== canonicalJson(expectedNames)) fail("D1 sales retirement tombstone view 名称集无效");
      const trigger = database.prepare(
        `SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN (${placeholders}) LIMIT 1`,
      ).get(...retiredNames);
      if (trigger) fail("D1 sales retirement tombstone view 不得携带 trigger");
      for (const row of rows) {
        const normalizedSql = typeof row.sql === "string" ? row.sql.trim().replace(/\s+/g, " ") : "";
        const expectedSql = `CREATE VIEW \`${row.name}\` AS SELECT 'sales-domain-retired-v1' AS \`retirement_tombstone\` WHERE 0`;
        if (normalizedSql !== expectedSql) fail(`D1 sales retirement tombstone SQL 无效：${row.name}`);
        const count = database.prepare(`SELECT COUNT(*) AS count FROM "${row.name}"`).get();
        if (count?.count !== 0) fail(`D1 sales retirement tombstone 必须为空：${row.name}`);
      }
    }

    const sharedTargets = [
      ["fingerprints", "import_content_fingerprints"],
      ["attempts", "import_content_attempts"],
      ["scope_heads", "import_scope_heads"],
    ];
    const expectedGuards = sharedTargets.flatMap(([shortName, tableName]) => ["insert", "update", "delete"].map((operation) => ({
      name: `sales_retired_${shortName}_${operation}_guard`, tableName, operation,
    })));
    const guardNames = expectedGuards.map((item) => item.name);
    const guardPlaceholders = guardNames.map(() => "?").join(",");
    const guardRows = database.prepare(
      `SELECT type, name, tbl_name AS tableName, sql FROM sqlite_master WHERE name IN (${guardPlaceholders}) ORDER BY name`,
    ).all(...guardNames);
    if (guardRows.length > 0 && guardRows.length !== expectedGuards.length) fail("D1 shared sales retirement guards 部分存在");
    const exactSharedGuardsPresent = guardRows.length === expectedGuards.length;
    if (exactSharedGuardsPresent) {
      for (const expected of expectedGuards) {
        const row = guardRows.find((item) => item.name === expected.name);
        if (!row || row.type !== "trigger" || row.tableName !== expected.tableName) fail(`D1 shared retirement guard 身份无效：${expected.name}`);
        const predicate = expected.operation === "update"
          ? "OLD.`domain` = 'sales' OR NEW.`domain` = 'sales'"
          : `${expected.operation === "insert" ? "NEW" : "OLD"}.\`domain\` = 'sales'`;
        const expectedSql = `CREATE TRIGGER \`${expected.name}\` BEFORE ${expected.operation.toUpperCase()} ON \`${expected.tableName}\` WHEN ${predicate} BEGIN SELECT RAISE(ABORT, 'sales_domain_retired'); END`;
        const normalizedSql = typeof row.sql === "string" ? row.sql.trim().replace(/\s+/g, " ") : "";
        if (normalizedSql !== expectedSql) fail(`D1 shared retirement guard SQL 无效：${expected.name}`);
      }
    }

    let completedReceiptPresent = false;
    if (receiptObject?.type === "table") {
      try {
        const receipt = database.prepare(
          "SELECT version, status FROM domain_retirement_receipts WHERE domain = 'sales' LIMIT 2",
        ).all();
        completedReceiptPresent = receipt.length === 1
          && receipt[0].version === "sales-domain-retirement-receipt-v1" && receipt[0].status === "completed";
      } catch {
        completedReceiptPresent = false;
      }
    }
    return {
      detected: Boolean(receiptObject) || viewRows.length > 0 || guardRows.length > 0,
      exactViewsPresent,
      exactSharedGuardsPresent,
      completedReceiptPresent,
      completed: exactViewsPresent && exactSharedGuardsPresent && completedReceiptPresent,
    };
  } finally {
    database.close();
  }
}

export async function d1ContainsRetirementTombstone(sourceD1Path) {
  return (await inspectD1RetirementState(sourceD1Path)).detected;
}
