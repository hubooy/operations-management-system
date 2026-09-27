import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const workflowPath = new URL("../automation/n8n/jd-multi-store-daily.workflow.json", import.meta.url);
const jdMarketMainWorkflowPath = new URL("../automation/n8n/jd-market-ranking-daily.workflow.json", import.meta.url);
const jdMarketSilentWorkflowPath = new URL("../automation/n8n/jd-market-ranking-daily.chromium-silent-copy.workflow.json", import.meta.url);
const silentWorkflowPaths = [
  new URL("../automation/n8n/jd-multi-store-daily.chromium-silent-copy.workflow.json", import.meta.url),
  jdMarketSilentWorkflowPath,
];

test("JD n8n copy is inactive, uses 10:00 Shanghai loopback A/B/C stages, and contains no credentials", async () => {
  const raw = await readFile(workflowPath, "utf8");
  const workflow = JSON.parse(raw) as {
    active: boolean; settings: { timezone?: string }; nodes: Array<{ name: string; type: string; parameters?: { url?: string; options?: { timeout?: number }; rule?: { interval?: Array<{ expression?: string }> }; sendHeaders?: boolean; headerParameters?: { parameters?: Array<{ name?: string; value?: string }> } } }>;
  };
  assert.equal(workflow.active, false);
  assert.equal(workflow.settings.timezone, "Asia/Shanghai");
  assert.equal(workflow.nodes.some((node) => node.type === "n8n-nodes-base.executeCommand"), false);
  const schedule = workflow.nodes.find((node) => node.type === "n8n-nodes-base.scheduleTrigger");
  assert.equal(schedule?.parameters?.rule?.interval?.[0]?.expression, "0 10 * * *");
  const coordination = workflow.nodes.find((node) => node.name === "领取共享 helper");
  assert.equal(coordination?.parameters?.url, "http://127.0.0.1:5791/coordination/claim");
  assert.deepEqual(coordination?.parameters?.headerParameters?.parameters, [
    { name: "X-TERUISI-N8N-EXECUTION-ID", value: "={{ $execution.id }}" },
    { name: "X-TERUISI-COORDINATION-ATTEMPT", value: "={{ $runIndex }}" },
    { name: "X-TERUISI-WORKFLOW-KEY", value: "jd" },
  ]);
  const requests = workflow.nodes.filter((node) => node.type === "n8n-nodes-base.httpRequest" && node.name !== "领取共享 helper");
  assert.deepEqual(requests.map((node) => node.parameters?.url), [
    "http://127.0.0.1:5791/jd/plan", "http://127.0.0.1:5791/jd/run", "http://127.0.0.1:5791/jd/verify",
  ]);
  for (const request of requests) {
    assert.equal(request.parameters?.sendHeaders, true);
    assert.deepEqual(request.parameters?.headerParameters?.parameters, [{ name: "X-TERUISI-N8N-EXECUTION-ID", value: "={{ $execution.id }}" }]);
  }
  assert.equal(requests[1]?.parameters?.options?.timeout, 21_600_000);
  assert.match(raw, /昨天所在月 1 日至昨天/);
  assert.match(raw, /旧执行、跨执行接管、乱序和并发/);
  assert.doesNotMatch(raw, /(?:password|cookie|token|session|profileDir)\s*[:=]/i);
});

test("JD Chromium silent-download copies stay inactive and preserve loopback orchestration", async () => {
  for (const copyPath of silentWorkflowPaths) {
    const raw = await readFile(copyPath, "utf8");
    const workflow = JSON.parse(raw) as {
      name: string;
      active: boolean;
      settings: { timezone?: string };
      nodes: Array<{ type: string; parameters?: { url?: string; headerParameters?: { parameters?: Array<{ name?: string; value?: string }> } } }>;
    };
    assert.match(workflow.name, /Chromium静默下载副本/);
    assert.equal(workflow.active, false);
    assert.equal(workflow.settings.timezone, "Asia/Shanghai");
    assert.equal(workflow.nodes.some((node) => node.type === "n8n-nodes-base.executeCommand"), false);
    const businessRequests = workflow.nodes.filter((node) => node.type === "n8n-nodes-base.httpRequest" && /^http:\/\/127\.0\.0\.1:5791\/jd(?:-market)?\//.test(node.parameters?.url ?? ""));
    const urls = businessRequests.map((node) => node.parameters?.url);
    assert.equal(urls.length, 3);
    assert.equal(urls.every((url) => /^http:\/\/127\.0\.0\.1:5791\/jd(?:-market)?\/(?:plan|run|verify)$/.test(url ?? "")), true);
    for (const request of businessRequests) {
      assert.deepEqual(request.parameters?.headerParameters?.parameters, [
        { name: "X-TERUISI-N8N-EXECUTION-ID", value: "={{ $execution.id }}" },
        { name: "X-TERUISI-JD-SILENT-NO-WINDOW", value: "1" },
      ]);
    }
    const coordination = workflow.nodes.find((node) => node.parameters?.url === "http://127.0.0.1:5791/coordination/claim");
    assert.ok(coordination);
    assert.deepEqual(coordination?.parameters?.headerParameters?.parameters?.at(-2), {
      name: "X-TERUISI-COORDINATION-ATTEMPT",
      value: "={{ $runIndex }}",
    });
    assert.deepEqual(coordination?.parameters?.headerParameters?.parameters?.at(-1), {
      name: "X-TERUISI-WORKFLOW-KEY",
      value: workflow.name.includes("市场") ? "jd-market" : "jd",
    });
    assert.match(raw, /窗口在后台隐藏并最小化/);
    assert.match(raw, /不弹系统保存窗口/);
    assert.match(raw, /不打开可见窗口/);
    assert.doesNotMatch(raw, /(?:password|cookie|token|session|profileDir)\s*[:=]/i);
  }
});

test("JD multi-store templates route scheduled and manual entries through the same atomic claim loop", async () => {
  for (const copyPath of [workflowPath, silentWorkflowPaths[0]!]) {
    const workflow = JSON.parse(await readFile(copyPath, "utf8")) as {
      nodes: Array<{ name: string; retryOnFail?: boolean; maxTries?: number; waitBetweenTries?: number; parameters?: { url?: string; resume?: string; amount?: number; unit?: string; headerParameters?: { parameters?: Array<{ name?: string; value?: string }> } } }>;
      connections: Record<string, { main: Array<Array<{ node: string }>> }>;
    };
    const claim = workflow.nodes.find((node) => node.name === "领取共享 helper");
    const wait = workflow.nodes.find((node) => node.name === "等待前序流程释放 helper");
    assert.equal(claim?.parameters?.url, "http://127.0.0.1:5791/coordination/claim");
    assert.deepEqual(claim?.parameters?.headerParameters?.parameters?.at(-2), { name: "X-TERUISI-COORDINATION-ATTEMPT", value: "={{ $runIndex }}" });
    assert.deepEqual(claim?.parameters?.headerParameters?.parameters?.at(-1), { name: "X-TERUISI-WORKFLOW-KEY", value: "jd" });
    assert.equal(claim?.retryOnFail, true);
    assert.equal(claim?.maxTries, 5);
    assert.equal(claim?.waitBetweenTries, 5000);
    assert.deepEqual(wait?.parameters, { resume: "timeInterval", amount: 5, unit: "minutes" });
    assert.equal(workflow.connections["手动运行"]?.main[0]?.[0]?.node, "领取共享 helper");
    assert.equal(workflow.connections["每天 10:00 执行"]?.main[0]?.[0]?.node, "领取共享 helper");
    assert.equal(workflow.connections["helper 领取成功？"]?.main[1]?.[0]?.node, "等待前序流程释放 helper");
    assert.equal(workflow.connections["等待前序流程释放 helper"]?.main[0]?.[0]?.node, "领取共享 helper");
  }
});

test("JD market main and silent workflows preserve the latest seven-category A/B/C contract", async () => {
  const [mainRaw, silentRaw, view] = await Promise.all([
    readFile(jdMarketMainWorkflowPath, "utf8"),
    readFile(jdMarketSilentWorkflowPath, "utf8"),
    readFile(new URL("../app/n8n-workflow-view.tsx", import.meta.url), "utf8"),
  ]);
  for (const raw of [mainRaw, silentRaw]) {
    const workflow = JSON.parse(raw) as {
      name: string;
      active: boolean;
      settings: { timezone?: string };
      nodes: Array<{
        type: string;
        parameters?: {
          url?: string;
          options?: { timeout?: number };
          rule?: { interval?: Array<{ expression?: string }> };
          headerParameters?: { parameters?: Array<{ name?: string; value?: string }> };
        };
      }>;
    };
    assert.equal(workflow.active, false);
    assert.equal(workflow.settings.timezone, "Asia/Shanghai");
    assert.doesNotMatch(workflow.name, /5类目/);
    const schedule = workflow.nodes.find((node) => node.type === "n8n-nodes-base.scheduleTrigger");
    assert.equal(schedule?.parameters?.rule?.interval?.[0]?.expression, "30 10 * * *");
    const requests = workflow.nodes.filter((node) => node.type === "n8n-nodes-base.httpRequest" && /^http:\/\/127\.0\.0\.1:5791\/jd-market\//.test(node.parameters?.url ?? ""));
    assert.deepEqual(requests.map((node) => node.parameters?.url), [
      "http://127.0.0.1:5791/jd-market/plan",
      "http://127.0.0.1:5791/jd-market/run",
      "http://127.0.0.1:5791/jd-market/verify",
    ]);
    assert.deepEqual(requests.map((node) => node.parameters?.options?.timeout), [900_000, 21_600_000, 900_000]);
    for (const request of requests) {
      assert.deepEqual(request.parameters?.headerParameters?.parameters, [
        { name: "X-TERUISI-N8N-EXECUTION-ID", value: "={{ $execution.id }}" },
        { name: "X-TERUISI-JD-SILENT-NO-WINDOW", value: "1" },
      ]);
    }
    assert.match(raw, /7 (?:个)?类目|7 类目/);
    assert.match(raw, /一个自然日一个京东原生 XLSX|每个缺失自然日独立选择和下载一个原生 XLSX/);
    assert.match(raw, /industry-top\.html/);
    assert.match(raw, /行业榜单/);
    assert.match(raw, /completed proof/);
    assert.match(raw, /不得同时启用/);
  }
  assert.match(view, /jd-market-ranking-daily\.chromium-silent-copy\.workflow\.json/);
  assert.match(view, /jd_market:[\s\S]*?scheduleMetric: "10:30"/);
  assert.match(view, /jd_promotion:[\s\S]*?scheduleMetric: "10:40"/);
  assert.match(view, /jd_promotion_cut_meat:[\s\S]*?scheduleMetric: "10:50"/);
  assert.match(view, /Profile 3 隐藏 Chromium/);
});

test("JD market scheduled and manual runs atomically claim the shared helper before A", async () => {
  const raw = await readFile(jdMarketSilentWorkflowPath, "utf8");
  const workflow = JSON.parse(raw) as {
    nodes: Array<{ name: string; type: string; retryOnFail?: boolean; maxTries?: number; waitBetweenTries?: number; parameters?: { url?: string; resume?: string; amount?: number; unit?: string; conditions?: { conditions?: Array<{ leftValue?: string; rightValue?: string }> } } }>;
    connections: Record<string, { main: Array<Array<{ node: string }>> }>;
  };
  const initialWait = workflow.nodes.find((node) => node.name === "定时分支先让四店领取 helper");
  const claim = workflow.nodes.find((node) => node.name === "领取共享 helper");
  const gate = workflow.nodes.find((node) => node.name === "helper 领取成功？");
  const retryWait = workflow.nodes.find((node) => node.name === "等待前序流程释放 helper");
  assert.deepEqual(initialWait?.parameters, { resume: "timeInterval", amount: 1, unit: "minutes" });
  assert.equal(claim?.parameters?.url, "http://127.0.0.1:5791/coordination/claim");
  assert.equal(claim?.retryOnFail, true);
  assert.equal(claim?.maxTries, 5);
  assert.equal(claim?.waitBetweenTries, 5000);
  assert.equal(gate?.parameters?.conditions?.conditions?.[0]?.leftValue, "={{ $json.coordinationStatus }}");
  assert.equal(gate?.parameters?.conditions?.conditions?.[0]?.rightValue, "granted");
  assert.deepEqual(retryWait?.parameters, { resume: "timeInterval", amount: 5, unit: "minutes" });
  assert.equal(workflow.connections["手动运行"]?.main[0]?.[0]?.node, "领取共享 helper");
  assert.equal(workflow.connections["每天 10:30 补缺"]?.main[0]?.[0]?.node, "定时分支先让四店领取 helper");
  assert.equal(workflow.connections["定时分支先让四店领取 helper"]?.main[0]?.[0]?.node, "领取共享 helper");
  assert.equal(workflow.connections["helper 领取成功？"]?.main[0]?.[0]?.node, "A·计算运营系统缺失日期");
  assert.equal(workflow.connections["helper 领取成功？"]?.main[1]?.[0]?.node, "等待前序流程释放 helper");
  assert.equal(workflow.connections["等待前序流程释放 helper"]?.main[0]?.[0]?.node, "领取共享 helper");
});
