import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { adaptLiliDirectMaster } from "../tools/generate-tmall-n8n-workflows";
import { TMALL_LILI_DIRECT_M_PROTOCOL, assertTmallDirectMasterStore, assertTmallDirectPmStore, tmallDirectPmProtocolError } from "../tools/tmall-yijiu-direct-pm-contract";

test("Lili allows only its independent M protocol, never promotion or another shop", () => {
  assert.doesNotThrow(() => assertTmallDirectMasterStore("tmall-lili"));
  assert.throws(() => assertTmallDirectPmStore("tmall-lili"));
  assert.equal(tmallDirectPmProtocolError({ route: "/product-master-direct-v1", storeKey: "tmall-lili", protocol: TMALL_LILI_DIRECT_M_PROTOCOL }), null);
  assert.equal(tmallDirectPmProtocolError({ route: "/promotion-direct-v1", storeKey: "tmall-lili", protocol: TMALL_LILI_DIRECT_M_PROTOCOL })?.error, "tmall_direct_pm_store_not_allowed");
  for (const protocol of [undefined, "yijiu-direct-pm-v1", "yiyong-direct-pm-v1", [TMALL_LILI_DIRECT_M_PROTOCOL]]) {
    assert.equal(tmallDirectPmProtocolError({ route: "/product-master-direct-v1", storeKey: "tmall-lili", protocol })?.error, "missing_or_invalid_tmall_direct_pm_protocol");
  }
  for (const storeKey of ["tmall-tuofeng", "tmall-yiyong", "constructor", "__proto__"]) {
    assert.ok(tmallDirectPmProtocolError({ route: "/product-master-direct-v1", storeKey, protocol: TMALL_LILI_DIRECT_M_PROTOCOL }));
  }
});

test("single-day live shape preserves A/B/C/P, schedules, settings and retry wiring", () => {
  const originalName = "M·商品管家批量导出、校验并导入";
  const nodes = ["schedule", "retry", "claim", "A", "B", "C", "P"].map(name => ({ name, type: "synthetic", parameters: { unchanged: name } }));
  const workflow = {
    id: "TmallLiliDaily2026", name: "丽力", active: true,
    settings: { timezone: "Asia/Shanghai", errorWorkflow: "TeruisiHourlyRetry2026" },
    nodes: [...nodes, { name: originalName, type: "n8n-nodes-base.httpRequest", parameters: {
      method: "POST", url: "http://127.0.0.1:5791/product-master",
      headerParameters: { parameters: [{ name: "X-TERUISI-TMALL-STORE-KEY", value: "tmall-lili" }] },
    } }],
    connections: {
      schedule: { main: [[{ node: "claim", type: "main", index: 0 }]] },
      retry: { main: [[{ node: "claim", type: "main", index: 0 }]] },
      P: { main: [[{ node: originalName, type: "main", index: 0 }]] },
    },
  };
  const before = structuredClone(workflow);
  adaptLiliDirectMaster(workflow);
  assert.deepEqual(workflow.nodes.slice(0, -1), before.nodes.slice(0, -1));
  assert.deepEqual(workflow.settings, before.settings);
  assert.deepEqual(workflow.connections.schedule, before.connections.schedule);
  assert.deepEqual(workflow.connections.retry, before.connections.retry);
  assert.equal(workflow.connections.P.main[0]![0]!.node, "M·MTOP 分批导出、合并校验并导入");
  assert.equal(workflow.nodes.length, before.nodes.length);
  assert.equal(workflow.active, true);
  const wrong = structuredClone(before);
  const wrongParameters = wrong.nodes.at(-1)!.parameters;
  assert.ok("url" in wrongParameters);
  wrongParameters.url = "https://unexpected.example/export";
  assert.throws(() => adaptLiliDirectMaster(wrong), /原路由/);
});

test("Lili live adaptation changes M only, without adopting daily-backfill or touching P", async () => {
  const workflow = JSON.parse(await readFile(new URL("../automation/n8n/tmall-lili-sycm-cookie-daily.workflow.json", import.meta.url), "utf8"));
  const oldM = workflow.nodes.find((node: {name: string}) => node.name.startsWith("M·"));
  oldM.name = "M·商品管家批量导出、校验并导入";
  oldM.parameters.url = "http://127.0.0.1:5791/product-master";
  oldM.parameters.headerParameters.parameters = oldM.parameters.headerParameters.parameters.filter((header: {name: string}) => !header.name.includes("CANDIDATE"));
  const untouched = workflow.nodes.filter((node: {name: string}) => !node.name.startsWith("M·"));
  const before = JSON.stringify(untouched);
  adaptLiliDirectMaster(workflow);
  assert.equal(JSON.stringify(workflow.nodes.filter((node: {name: string}) => !node.name.startsWith("M·"))), before);
  assert.equal(oldM.parameters.url, "http://127.0.0.1:5791/product-master-direct-v1");
  const once = JSON.stringify(workflow);
  adaptLiliDirectMaster(workflow);
  assert.equal(JSON.stringify(workflow), once);
  assert.throws(() => adaptLiliDirectMaster({ ...workflow, id: "TmallYiyongDaily2026" }));
  const config = JSON.parse(await readFile(new URL("../config/tmall-store-accounts.json", import.meta.url), "utf8"));
  assert.equal(config.stores.find((store: {storeKey: string}) => store.storeKey === "tmall-lili").productMasterCadence.intervalDays, 1);
});
