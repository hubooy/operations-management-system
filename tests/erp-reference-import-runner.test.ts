import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("ERP 库龄分片上传从初始化阶段绑定精确快照日期", () => {
  const runner = readFileSync(new URL("../tools/erp-reference-import-runner.mjs", import.meta.url), "utf8");
  assert.match(runner, /action: "init",[\s\S]*?source,[\s\S]*?source === "inventory_age" \? \{ snapshotDate \}/);
  assert.match(runner, /action: "complete", source, uploadId: initBody\.upload\.id, snapshotDate/);
});
