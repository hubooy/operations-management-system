import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { isApprovedN8nMetadataReader, metadataReaderImportRoots, n8nMetadataReaderPath } from "../tools/n8n-metadata-read-boundary.mjs";

test("only the reviewed fixed n8n metadata reader is allowed, exclusively through the helper", async () => {
  const source = await readFile(new URL("../lib/jackyun/n8n-preflight-evidence.ts", import.meta.url), "utf8");
  const roots = ["tools/tmall-sycm-cookie-pipeline.ts"];
  assert.equal(isApprovedN8nMetadataReader(n8nMetadataReaderPath, source, roots), true);
  for (const [before, after] of [["readOnly: true", "readOnly: false"], ["query_only=ON", "query_only=OFF"],
    ['".n8n", "database.sqlite"', '".wrangler", "business.sqlite"'], ["SELECT data FROM execution_data", "SELECT data FROM credentials_entity"],
    ["length(data)<=1048576", "length(data)<=999999999"], ["info.nlink !== 1", "info.nlink < 0"]]) {
    const changed = source.replace(before, after); assert.notEqual(changed, source);
    assert.equal(isApprovedN8nMetadataReader(n8nMetadataReaderPath, changed, roots), false, before);
  }
  assert.equal(isApprovedN8nMetadataReader("lib/other-sqlite-reader.ts", source, roots), false);
  for (const incoming of [[], ["app/api/status/route.ts"], ["worker/index.ts"], [...roots, "app/api/status/route.ts"], ["tools/other-runner.ts"]]) {
    assert.equal(isApprovedN8nMetadataReader(n8nMetadataReaderPath, source, incoming), false);
  }
});

test("a later indirect public import cannot hide behind the helper's first parent", () => {
  const roots = ["app/route.ts", "tools/helper.ts", "worker/index.ts"];
  const edges = new Map([["app/route.ts", ["lib/a.ts"]], ["lib/a.ts", ["lib/b.ts"]], ["lib/b.ts", ["lib/metadata.ts", "lib/a.ts"]],
    ["tools/helper.ts", ["lib/metadata.ts"]], ["worker/index.ts", ["lib/other.ts"]]]);
  assert.deepEqual(metadataReaderImportRoots(roots, edges, "lib/metadata.ts"), ["app/route.ts", "tools/helper.ts"]);
});
