import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { jackyunWebSessionActions, jackyunDirectActions } from "../tools/jackyun-export-first-pipeline";

for (const variant of ["daily", "http"]) test(`${variant} manual n8n graph requires all five exports and validation before import`, async () => {
  const raw = await readFile(new URL(`../automation/n8n/jackyun-five-dataset-${variant}.workflow.json`, import.meta.url), "utf8");
  const workflow = JSON.parse(raw);
  assert.equal(workflow.id, "J8kY2mQ5vR7sT4pN");
  assert.equal(workflow.name, "吉客云导入系统");
  assert.equal(workflow.active, false);
  assert.equal(workflow.settings.timezone, "Asia/Shanghai");
  assert.equal(workflow.nodes.some((node: { type: string }) => /scheduleTrigger|executeCommand/.test(node.type)), false);
  const requests = workflow.nodes.filter((node: { type: string }) => node.type === "n8n-nodes-base.httpRequest");
  assert.equal(requests[0].parameters.url, "http://127.0.0.1:5791/coordination/claim");
  assert.deepEqual(requests.slice(1).map((node: { parameters: { url: string } }) => node.parameters.url),
    (variant === "http" ? jackyunDirectActions : jackyunWebSessionActions).map(action => `http://127.0.0.1:5791/jackyun/export-first/${action}`));
  for (const node of requests) {
    assert.ok(node.parameters.headerParameters.parameters.some((header: { name: string; value: string }) =>
      header.name === "X-TERUISI-N8N-EXECUTION-ID" && header.value === "={{ $execution.id }}"));
    assert.equal(Boolean(node.continueOnFail), false);
    if (node !== requests[0]) assert.equal(Boolean(node.retryOnFail), false);
  }
  const steps = requests.slice(1);
  for (let index = 0; index < steps.length - 1; index++) {
    assert.deepEqual(workflow.connections[steps[index].name].main, [[{ node: steps[index + 1].name, type: "main", index: 0 }]]);
  }
  assert.equal(workflow.connections["手动运行"].main[0][0].node, requests[0].name);
  assert.equal(workflow.connections["helper 领取成功？"].main[0][0].node, steps[0].name);
  assert.equal(workflow.connections["等待前序流程释放 helper"].main[0][0].node, requests[0].name);
  assert.doesNotMatch(raw, /--(?:username|password|cookie|token)\b|JACKYUN_(?:USERNAME|PASSWORD)/i);
});
