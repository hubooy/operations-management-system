import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  buildTmallN8nWorkflow,
  buildTmallYijiuDirectPmCandidateWorkflow,
  tmallN8nWorkflowDefinitions,
  tmallYijiuDirectPmCandidateFileName,
} from "../tools/generate-tmall-n8n-workflows";
import {
  TMALL_YIJIU_DIRECT_PM_PROTOCOL,
  tmallDirectProductMasterRoute,
  tmallDirectPromotionRoute,
} from "../tools/tmall-yijiu-direct-pm-contract";

const workflowPath = new URL("../automation/n8n/tmall-yijiu-sycm-cookie-daily.workflow.json", import.meta.url);

test("Cookie 直连 n8n 副本保持商品日和推广前置、货品收尾五段式、上海时区和凭证隔离", async () => {
  const raw = await readFile(workflowPath, "utf8");
  const workflow = JSON.parse(raw) as {
    id: string;
    name: string;
    active: boolean;
    settings: { timezone?: string };
    nodes: Array<{
      name: string;
      type: string;
      parameters?: {
        command?: string;
        url?: string;
        rule?: { interval?: Array<{ expression?: string }> };
        sendHeaders?: boolean;
        headerParameters?: { parameters?: Array<{ name?: string; value?: string }> };
        options?: { timeout?: number };
      };
    }>;
    connections: Record<string, { main?: Array<Array<{ node?: string }>> }>;
  };
  assert.equal(workflow.id, "M4xY8kQ2vR6sT9pC");
  assert.equal(workflow.name, "天猫店铺数据导入");
  assert.equal(workflow.active, false);
  assert.equal(workflow.settings.timezone, "Asia/Shanghai");
  assert.equal(workflow.nodes.find((node) => node.type === "n8n-nodes-base.manualTrigger")?.name, "手动完整运行（强制 M）");
  const scheduleNode = workflow.nodes.find((node) => node.type === "n8n-nodes-base.scheduleTrigger");
  assert.equal(scheduleNode?.name, "每天 11:00 运行");
  assert.equal(scheduleNode?.parameters?.rule?.interval?.[0]?.expression, "0 11 * * *");
  const coordination = workflow.nodes.find((node) => node.name === "领取共享 helper");
  assert.equal(coordination?.parameters?.url, "http://127.0.0.1:5791/coordination/claim");
  assert.deepEqual(coordination?.parameters?.headerParameters?.parameters, [
    { name: "X-TERUISI-N8N-EXECUTION-ID", value: "={{ $execution.id }}" },
    { name: "X-TERUISI-COORDINATION-ATTEMPT", value: "={{ $runIndex }}" },
    { name: "X-TERUISI-WORKFLOW-KEY", value: "tmall" },
    { name: "X-TERUISI-TMALL-STORE-KEY", value: "tmall-yijiu" },
  ]);
  const requestNodes = workflow.nodes.filter((node) => node.type === "n8n-nodes-base.httpRequest" && node.name !== "领取共享 helper");
  assert.deepEqual(requestNodes.map((node) => node.parameters?.url), [
    "http://127.0.0.1:5791/product-master",
    "http://127.0.0.1:5791/plan-backfill",
    "http://127.0.0.1:5791/fetch",
    "http://127.0.0.1:5791/import",
    "http://127.0.0.1:5791/promotion",
    "http://127.0.0.1:5791/next-day",
  ]);
  for (const node of requestNodes) {
    assert.equal(node.parameters?.sendHeaders, true);
    const expectedHeaders = [
      { name: "X-TERUISI-N8N-EXECUTION-ID", value: "={{ $execution.id }}" },
      { name: "X-TERUISI-TMALL-STORE-KEY", value: "tmall-yijiu" },
      ...(node.parameters?.url?.endsWith("/plan-backfill")
        ? [
            {
              name: "X-TERUISI-TMALL-PLAN-START-DATE",
              value: "={{ $mode === 'cli' ? ($env.TERUISI_TMALL_PLAN_START_DATE || '') : '' }}",
            },
            {
              name: "X-TERUISI-TMALL-PLAN-END-DATE",
              value: "={{ $mode === 'cli' ? ($env.TERUISI_TMALL_PLAN_END_DATE || '') : '' }}",
            },
          ]
        : []),
      ...(node.parameters?.url?.endsWith("/product-master")
        ? [{
            name: "X-TERUISI-TMALL-FORCE-PRODUCT-MASTER",
            value: "={{ $mode === 'manual' && $('手动完整运行（强制 M）').isExecuted ? '1' : '0' }}",
          }]
        : []),
    ];
    if (node.parameters?.url?.endsWith("/next-day")) expectedHeaders.push({ name: "X-TERUISI-TMALL-BACKFILL-CYCLE", value: "={{ $runIndex }}" });
    assert.deepEqual(node.parameters?.headerParameters?.parameters, expectedHeaders);
  }
  assert.equal(workflow.nodes.some((node) => node.type === "n8n-nodes-base.executeCommand"), false);
  assert.match(raw, /商品 > 我的商品 > 出售中/);
  assert.match(raw, /excel商品批量导出/);
  assert.match(raw, /最后一页才点击“前往下载”/);
  assert.match(raw, /报表 > 商品报表/);
  assert.match(raw, /货品全站推广、关键词推广、人群推广、店铺直达/);
  assert.match(raw, /商品、计划/);
  assert.match(raw, /全部数据指标/);
  assert.match(raw, /同日起止日期/);
  assert.match(raw, /计划维度多行先按“日期 \+ 商品”完整汇总/);
  assert.match(raw, /拒绝旧全站推任务/);
  assert.match(raw, /生成成功/);
  assert.doesNotMatch(raw, /从左下角打开“商品管家”/);
  assert.doesNotMatch(raw, /批量导出表格/);
  assert.equal(workflow.connections["手动完整运行（强制 M）"]?.main?.[0]?.[0]?.node, "领取共享 helper");
  assert.equal(workflow.connections["手动运行"], undefined);
  assert.equal(workflow.connections["每天 11:00 运行"]?.main?.[0]?.[0]?.node, "领取共享 helper");
  assert.equal(workflow.connections["领取共享 helper"]?.main?.[0]?.[0]?.node, "helper 领取成功？");
  assert.equal(workflow.connections["helper 领取成功？"]?.main?.[0]?.[0]?.node, "A·计划目标日期");
  assert.equal(workflow.connections["helper 领取成功？"]?.main?.[1]?.[0]?.node, "等待前序流程释放 helper");
  assert.equal(workflow.connections["等待前序流程释放 helper"]?.main?.[0]?.[0]?.node, "领取共享 helper");
  assert.equal(workflow.connections["A·计划目标日期"]?.main?.[0]?.[0]?.node, "B·逐日下载并验证 XLS");
  assert.equal(workflow.connections["B·逐日下载并验证 XLS"]?.main?.[0]?.[0]?.node, "C·签收、导入并覆盖回查");
  assert.equal(workflow.connections["C·签收、导入并覆盖回查"]?.main?.[0]?.[0]?.node, "P·商品报表逐日下载、汇总导入并回查");
  assert.equal(workflow.connections["P·商品报表逐日下载、汇总导入并回查"]?.main?.[0]?.[0]?.node, "N·复查缺口并计划下一日");
  assert.equal(workflow.connections["M·出售中逐页导出、合并校验并导入"]?.main?.[0]?.[0]?.node, "全部缺失日已补齐？");
  assert.match(raw, /A→B→C→P→M/);
  assert.match(raw, /每 1 天到期一次/);
  assert.match(raw, /not_due/);
  assert.match(raw, /到期失败不推进下次日期/);
  assert.match(raw, /Windows 用户.*DPAPI/);
  assert.match(raw, /验证码、安全验证/);
  assert.equal(requestNodes[0]?.parameters?.options?.timeout, 1_800_000);
  assert.equal(requestNodes.find(node => node.name.startsWith("P·"))?.parameters?.options?.timeout, 21_600_000);
  assert.doesNotMatch(raw, /--(?:username|password|cookie)\b|TMALL_(?:USERNAME|PASSWORD)\b|Cookie:\s*[^`\n]/i);
  assert.doesNotMatch(raw, /localhost:8000|teruisi123|_tb_token_=|cookie2=/i);
});

test("六店 n8n 模板固定绑定独立店铺键、上午错峰调度且仓库模板默认未激活", async () => {
  const pagewiseStoreKeys = new Set(["tmall-yijiu", "tmall-tuofeng", "tmall-cuizhiwang", "tmall-masitu"]);
  const registry = JSON.parse(await readFile(new URL("../config/tmall-store-accounts.json", import.meta.url), "utf8")) as {
    stores: Array<{
      storeKey: string;
      shopName: string;
      enabled: boolean;
      loginMode?: string;
      productMasterExportMode?: string;
      productMasterCadence?: { intervalDays: number; initialDueDate: string };
      browser: { userDataDir?: string; profileDir: string; debugPort: number; downloadDir: string };
    }>;
  };
  const selectedStores = tmallN8nWorkflowDefinitions.map((definition) => {
    const store = registry.stores.find((candidate) => candidate.storeKey === definition.storeKey);
    assert.ok(store, `${definition.storeKey} 缺少注册项`);
    return store;
  });
  const enabledStoreKeys = registry.stores.filter((store) => store.enabled).map((store) => store.storeKey).sort();
  assert.deepEqual(tmallN8nWorkflowDefinitions.map((definition) => definition.storeKey).sort(), enabledStoreKeys);
  assert.equal(new Set(tmallN8nWorkflowDefinitions.map((definition) => definition.workflowId)).size, tmallN8nWorkflowDefinitions.length);
  assert.equal(new Set(tmallN8nWorkflowDefinitions.map((definition) => definition.fileName)).size, tmallN8nWorkflowDefinitions.length);
  assert.deepEqual(tmallN8nWorkflowDefinitions.map((definition) => definition.cronExpression), [
    "0 11 * * *",
    "10 11 * * *",
    "20 11 * * *",
    "30 11 * * *",
    "40 11 * * *",
    "50 11 * * *",
  ]);
  assert.equal(new Set(selectedStores.map((store) => store.browser.userDataDir)).size, selectedStores.length);
  assert.equal(new Set(selectedStores.map((store) => store.browser.profileDir)).size, selectedStores.length);
  assert.equal(new Set(selectedStores.map((store) => store.browser.debugPort)).size, selectedStores.length);
  assert.equal(new Set(selectedStores.map((store) => store.browser.downloadDir)).size, selectedStores.length);

  for (const [index, definition] of tmallN8nWorkflowDefinitions.entries()) {
    const raw = await readFile(new URL(`../automation/n8n/${definition.fileName}`, import.meta.url), "utf8");
    const workflow = JSON.parse(raw) as {
      id: string;
      name: string;
      active: boolean;
      settings: { timezone?: string };
      nodes: Array<{
        name: string;
        type: string;
        parameters?: {
          url?: string;
          rule?: { interval?: Array<{ expression?: string }> };
          headerParameters?: { parameters?: Array<{ name?: string; value?: string }> };
        };
      }>;
      connections: Record<string, { main?: Array<Array<{ node?: string }>> }>;
    };
    assert.equal(workflow.id, definition.workflowId);
    assert.equal(workflow.name, definition.workflowName);
    assert.equal(workflow.active, false);
    assert.equal(workflow.settings.timezone, "Asia/Shanghai");
    const schedule = workflow.nodes.find((node) => node.type === "n8n-nodes-base.scheduleTrigger");
    assert.equal(schedule?.name, definition.scheduleName);
    assert.equal(schedule?.parameters?.rule?.interval?.[0]?.expression, definition.cronExpression);
    assert.equal(workflow.connections[definition.scheduleName]?.main?.[0]?.[0]?.node, "领取共享 helper");
    assert.equal(workflow.connections["手动完整运行（强制 M）"]?.main?.[0]?.[0]?.node, "领取共享 helper");

    const requestNodes = workflow.nodes.filter((node) => node.type === "n8n-nodes-base.httpRequest");
    assert.equal(requestNodes.length, 7);
    for (const node of requestNodes) {
      const headers = node.parameters?.headerParameters?.parameters ?? [];
      assert.deepEqual(headers.filter((header) => header.name === "X-TERUISI-TMALL-STORE-KEY"), [
        { name: "X-TERUISI-TMALL-STORE-KEY", value: definition.storeKey },
      ]);
      assert.deepEqual(headers.filter((header) => header.name === "X-TERUISI-N8N-EXECUTION-ID"), [
        { name: "X-TERUISI-N8N-EXECUTION-ID", value: "={{ $execution.id }}" },
      ]);
      assert.deepEqual(headers.filter((header) => header.name === "X-TERUISI-TMALL-FORCE-PRODUCT-MASTER"),
        /\/product-master(?:-direct-v1)?$/.test(node.parameters?.url ?? "")
          ? [{
              name: "X-TERUISI-TMALL-FORCE-PRODUCT-MASTER",
              value: "={{ $mode === 'manual' && $('手动完整运行（强制 M）').isExecuted ? '1' : '0' }}",
            }]
          : []);
      assert.deepEqual(headers.filter((header) => header.name === "X-TERUISI-TMALL-PLAN-START-DATE"),
        node.parameters?.url?.endsWith("/plan-backfill")
          ? [{
              name: "X-TERUISI-TMALL-PLAN-START-DATE",
              value: "={{ $mode === 'cli' ? ($env.TERUISI_TMALL_PLAN_START_DATE || '') : '' }}",
            }]
          : []);
      assert.deepEqual(headers.filter((header) => header.name === "X-TERUISI-TMALL-PLAN-END-DATE"),
        node.parameters?.url?.endsWith("/plan-backfill")
          ? [{
              name: "X-TERUISI-TMALL-PLAN-END-DATE",
              value: "={{ $mode === 'cli' ? ($env.TERUISI_TMALL_PLAN_END_DATE || '') : '' }}",
            }]
          : []);
    }
    const coordination = requestNodes.find((node) => node.parameters?.url?.endsWith("/coordination/claim"));
    assert.equal(coordination?.parameters?.headerParameters?.parameters?.some(
      (header) => header.name === "X-TERUISI-WORKFLOW-KEY" && header.value === "tmall",
    ), true);
    assert.match(raw, new RegExp(definition.storeKey));
    assert.match(raw, new RegExp(definition.shopName));
    assert.doesNotMatch(raw, /--(?:username|password|cookie)\b|TMALL_(?:USERNAME|PASSWORD)\b|_tb_token_=|cookie2=/i);

    const store = selectedStores[index]!;
    assert.equal(store.shopName, definition.shopName);
    assert.equal(store.loginMode, "windows_dpapi_credentials");
    assert.equal(store.enabled, true);
    assert.deepEqual(store.productMasterCadence, definition.productMasterCadence);
    assert.match(raw, new RegExp(`初始到期日为 .${definition.productMasterCadence.initialDueDate}.`));
    if (pagewiseStoreKeys.has(definition.storeKey)) {
      assert.equal(store.productMasterExportMode, "on_sale_pagewise_excel");
      assert.match(raw, /M·出售中逐页导出、合并校验并导入/);
      assert.match(raw, /商品 > 我的商品 > 出售中/);
      assert.match(raw, /excel商品批量导出/);
      assert.match(raw, /最后一页才点击“前往下载”/);
      assert.match(raw, /先合并成一个无跨页重复/);
      assert.match(raw, /禁止逐页导入互相覆盖/);
      assert.equal(
        workflow.connections["还有缺口且预算充足？"]?.main?.[1]?.[0]?.node,
        "M·出售中逐页导出、合并校验并导入",
      );
      assert.equal(workflow.nodes.some((node) => node.name === "M·商品管家批量导出、校验并导入"), false);
    } else {
      assert.equal(store.productMasterExportMode, undefined);
      assert.equal(
        workflow.connections["还有缺口且预算充足？"]?.main?.[1]?.[0]?.node,
        definition.storeKey === "tmall-lili" ? "M·MTOP 分批导出、合并校验并导入" : "M·商品管家批量导出、校验并导入",
      );
    }
  }
  assert.deepEqual(
    tmallN8nWorkflowDefinitions.filter((definition) => definition.productMasterExportMode === "on_sale_pagewise_excel")
      .map((definition) => definition.storeKey),
    ["tmall-yijiu", "tmall-tuofeng", "tmall-cuizhiwang", "tmall-masitu"],
  );
  assert.deepEqual(
    tmallN8nWorkflowDefinitions.map((definition) => [definition.storeKey, definition.productMasterCadence.initialDueDate]),
    [
      ["tmall-yijiu", "2026-08-27"],
      ["tmall-lili", "2026-08-27"],
      ["tmall-tuofeng", "2026-08-25"],
      ["tmall-cuizhiwang", "2026-08-25"],
      ["tmall-masitu", "2026-08-26"],
      ["tmall-yiyong", "2026-08-26"],
    ],
  );
});

test("逐页基础模板生成丽力直连 M，马思图仍为逐页 M", async () => {
  const source = JSON.parse(await readFile(workflowPath, "utf8"));
  const lili = tmallN8nWorkflowDefinitions.find((definition) => definition.storeKey === "tmall-lili");
  const masitu = tmallN8nWorkflowDefinitions.find((definition) => definition.storeKey === "tmall-masitu");
  assert.ok(lili);
  assert.ok(masitu);
  const productManagerWorkflow = buildTmallN8nWorkflow(source, lili);
  assert.equal(productManagerWorkflow.nodes.some((node) => node.name === "M·MTOP 分批导出、合并校验并导入"), true);
  assert.equal(
    (productManagerWorkflow.connections["还有缺口且预算充足？"] as { main?: Array<Array<{ node?: string }>> })
      ?.main?.[1]?.[0]?.node,
    "M·MTOP 分批导出、合并校验并导入",
  );
  const pagewiseWorkflow = buildTmallN8nWorkflow(source, masitu);
  assert.equal(pagewiseWorkflow.nodes.some((node) => node.name === "M·出售中逐页导出、合并校验并导入"), true);
});

test("亿玖 P/M 直连现行模板保持同一工作流 ID、仓库默认停用，并只替换两个终端业务节点", async () => {
  const [baselineRaw, candidateRaw] = await Promise.all([
    readFile(workflowPath, "utf8"),
    readFile(new URL(`../automation/n8n/${tmallYijiuDirectPmCandidateFileName}`, import.meta.url), "utf8"),
  ]);
  const baseline = JSON.parse(baselineRaw) as {
    id: string;
    versionId: string;
    nodes: Array<{ name: string; parameters?: unknown }>;
  };
  const candidate = JSON.parse(candidateRaw) as {
    id: string;
    name: string;
    active: boolean;
    versionId: string;
    meta?: Record<string, unknown>;
    settings?: { timezone?: string };
    nodes: Array<{
      name: string;
      type: string;
      parameters?: {
        url?: string;
        options?: { timeout?: number };
        headerParameters?: { parameters?: Array<{ name?: string; value?: string }> };
      };
    }>;
    connections: Record<string, { main?: Array<Array<{ node?: string }>> }>;
  };
  assert.equal(candidate.id, baseline.id);
  assert.equal(candidate.id, "M4xY8kQ2vR6sT9pC");
  assert.notEqual(candidate.versionId, baseline.versionId);
  assert.equal(candidate.name, "天猫店铺数据导入（亿玖 P/M 直连·每日 M）");
  assert.equal(candidate.active, false);
  assert.equal(candidate.settings?.timezone, "Asia/Shanghai");
  const { hourlyRetryPolicy, ...candidateMeta } = candidate.meta ?? {};
  assert.deepEqual(candidateMeta, {
    templateCredsSetupCompleted: true,
    currentForYijiu: true,
    candidateProtocol: TMALL_YIJIU_DIRECT_PM_PROTOCOL,
    replacesWorkflowId: "M4xY8kQ2vR6sT9pC",
  });
  assert.deepEqual(hourlyRetryPolicy, {
    version: "2026-09-10.safe-hourly-retry.1",
    delayMinutes: 60,
    errorWorkflowId: "TeruisiHourlyRetry2026",
    webhookPath: "teruisi-hourly-retry-46892fbc3a30a11922bfa53523d5af695645e453",
  });

  for (const name of ["领取共享 helper", "A·计划目标日期", "B·逐日下载并验证 XLS", "C·签收、导入并覆盖回查"]) {
    assert.deepEqual(
      candidate.nodes.find((node) => node.name === name)?.parameters,
      baseline.nodes.find((node) => node.name === name)?.parameters,
      `${name} 不应因直连 P/M 改造而变化`,
    );
  }
  const promotion = candidate.nodes.find((node) => node.name === "P·直连创建商品报表、下载、汇总导入并回查");
  const productMaster = candidate.nodes.find((node) => node.name === "M·MTOP 分批导出、合并校验并导入");
  assert.equal(promotion?.parameters?.url, `http://127.0.0.1:5791${tmallDirectPromotionRoute}`);
  assert.equal(productMaster?.parameters?.url, `http://127.0.0.1:5791${tmallDirectProductMasterRoute}`);
  assert.equal(promotion?.parameters?.options?.timeout, 21_600_000);
  assert.equal(productMaster?.parameters?.options?.timeout, 1_800_000);
  for (const node of [promotion, productMaster]) {
    assert.deepEqual(node?.parameters?.headerParameters?.parameters?.filter(
      (header) => header.name === "X-TERUISI-TMALL-CANDIDATE-PROTOCOL",
    ), [{ name: "X-TERUISI-TMALL-CANDIDATE-PROTOCOL", value: TMALL_YIJIU_DIRECT_PM_PROTOCOL }]);
  }
  assert.deepEqual(productMaster?.parameters?.headerParameters?.parameters?.filter(
    (header) => header.name === "X-TERUISI-TMALL-FORCE-PRODUCT-MASTER",
  ), [{
    name: "X-TERUISI-TMALL-FORCE-PRODUCT-MASTER",
    value: "={{ $mode === 'manual' && $('手动完整运行（强制 M）').isExecuted ? '1' : '0' }}",
  }]);
  assert.equal(
    candidate.connections["C·签收、导入并覆盖回查"]?.main?.[0]?.[0]?.node,
    "P·直连创建商品报表、下载、汇总导入并回查",
  );
  assert.equal(
    candidate.connections["还有缺口且预算充足？"]?.main?.[1]?.[0]?.node,
    "M·MTOP 分批导出、合并校验并导入",
  );
  assert.equal(candidate.connections["M·MTOP 分批导出、合并校验并导入"]?.main?.[0]?.[0]?.node, "全部缺失日已补齐？");
  assert.match(candidateRaw, /同一工作流 ID 的现行替换版本/);
  assert.match(candidateRaw, /亿玖 M 按上海日期每天到期一次/);
  assert.match(candidateRaw, /旧版本只保留历史审计，不得用于新的定时或恢复 execution/);
  assert.match(candidateRaw, /响应未决时禁止自动重提/);
  assert.match(candidateRaw, /唯一写类路径逐字固定/);
  assert.match(candidateRaw, /全部批文件校验后合并成一个权威 XLSX，只导入一次并回查/);
  assert.doesNotMatch(candidateRaw, /--(?:username|password|cookie)\b|_tb_token_=|cookie2=/i);

  const rebuilt = buildTmallYijiuDirectPmCandidateWorkflow(JSON.parse(baselineRaw));
  assert.equal(JSON.stringify(rebuilt), JSON.stringify(candidate));
});

test("运营系统在左侧自动化中心受控嵌入天猫 n8n 画布", async () => {
  const [page, navigationCatalog, view] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/shell/navigation-catalog.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/n8n-workflow-view.tsx", import.meta.url), "utf8"),
  ]);

  const workflowNavigation = navigationCatalog.indexOf('{ key: "n8n_workflows", label: "自动化中心"');
  const dashboardNavigation = navigationCatalog.indexOf('{ key: "dashboard", label: "BI 看板"');
  assert.ok(dashboardNavigation >= 0 && workflowNavigation >= 0);
  assert.match(page, /n8n_workflows: \(\{ currentUser, moduleView, onModuleViewChange \}\) => <N8nWorkflowView currentUser=\{currentUser\} moduleView=/);
  assert.match(view, /tmall-yijiu-direct-pm-candidate\.workflow\.json/);
  assert.match(view, /jackyun-five-dataset-api\.workflow\.json/);
  assert.match(view, /A → B → C → D → E/);
  assert.match(view, /五表全部下载并通过校验后才开始导入/);
  assert.match(view, /jd-multi-store-daily\.workflow\.json/);
  assert.match(view, /http:\/\/localhost:5678\/workflow\//);
  assert.match(view, /canManageN8nWorkflow\(currentUser\?\.role\)/);
  assert.match(view, /workflowEditorReady \? <div className="n8n-frame-shell">/);
  assert.match(view, /iframeTitle: "天猫店铺数据导入 n8n 工作流"/);
  assert.match(view, /iframeTitle: "吉客云导入系统 n8n 工作流"/);
  assert.match(view, /iframeTitle: "京东多店铺商品数据统一下载与导入 n8n 工作流"/);
  assert.match(view, /sandbox="allow-downloads[^"]+allow-scripts"/);
  assert.match(view, /账号、密码、Cookie、Token 和 Session 均不进入运营系统/);
  assert.match(view, /http:\/\/127\.0\.0\.1:5791\/health/);
  assert.match(view, /data-helper-status=\{helperStatus\.kind\}/);
  assert.match(view, /业务范围与规范化后的完整业务内容都一致时返回 duplicate/);
  assert.match(view, /A → B → C → P → M/);
  assert.match(view, /商品推广报表/);
  assert.match(view, /scheduleMetric: "11:00"/);
  assert.match(view, /jackyun:[\s\S]*?scheduleMetric: "00:10"/);
  assert.match(view, /scheduleTriggerLabel: "每天"/);
  assert.match(view, /scheduleMetric: "10:00"/);
  assert.match(view, /\{config\.scheduleMetric\} \{config\.scheduleTriggerLabel\}/);
  assert.match(view, /辅助服务已就绪/);
  assert.match(view, /payload\.tmallProfile !== "ready"/);
  assert.doesNotMatch(view, /key === "tmall" && payload\.cookieSource !== "ready"/);
  assert.match(view, /实际发布状态以 n8n 画布为准/);
  assert.match(view, /document\.visibilityState === "hidden"/);
  assert.match(view, /document\.addEventListener\("visibilitychange", onVisibilityChange\)/);
  assert.match(view, /stableChecks >= 2 \? 15_000 : 5_000/);
  assert.doesNotMatch(view, /setInterval\(\(\) => void check\(\), 5_000\)/);
});

test("n8n 工作流视图只在操作角色且 helper ready 时挂载可执行入口", async () => {
  const [{ createElement }, { renderToStaticMarkup }, workflowView] = await Promise.all([
    import("react"),
    import("react-dom/server"),
    import("../app/n8n-workflow-view"),
  ]);
  const {
    default: N8nWorkflowView,
    canManageN8nWorkflow,
    shouldMountN8nWorkflowEditor,
  } = workflowView;

  const viewerHtml = renderToStaticMarkup(createElement(N8nWorkflowView, { currentUser: { role: "viewer" }, moduleView: "jackyun", onModuleViewChange: () => undefined }));
  const operatorHtml = renderToStaticMarkup(createElement(N8nWorkflowView, { currentUser: { role: "operator" }, moduleView: "jackyun", onModuleViewChange: () => undefined }));

  assert.match(viewerHtml, /吉客云导入系统/);
  assert.match(viewerHtml, /天猫店铺数据导入/);
  assert.match(viewerHtml, /京东多店铺商品数据统一下载与导入/);
  assert.match(viewerHtml, /需要操作员或管理员权限/);
  assert.doesNotMatch(viewerHtml, /<iframe/);
  assert.equal(canManageN8nWorkflow("viewer"), false);
  assert.equal(canManageN8nWorkflow("analyst"), false);
  assert.equal(canManageN8nWorkflow("operator"), true);
  assert.equal(canManageN8nWorkflow("admin"), true);
  for (const helperKind of ["checking", "running", "cookie-missing", "offline"] as const) {
    assert.equal(shouldMountN8nWorkflowEditor("operator", helperKind), false);
    assert.equal(shouldMountN8nWorkflowEditor("admin", helperKind), false);
  }
  assert.equal(shouldMountN8nWorkflowEditor("viewer", "ready"), false);
  assert.equal(shouldMountN8nWorkflowEditor("analyst", "ready"), false);
  assert.equal(shouldMountN8nWorkflowEditor("operator", "ready"), true);
  assert.equal(shouldMountN8nWorkflowEditor("admin", "ready"), true);
  assert.doesNotMatch(operatorHtml, /<iframe/);
  assert.doesNotMatch(operatorHtml, /href="http:\/\/localhost:5678\/workflow\//);
  assert.doesNotMatch(operatorHtml, /刷新画布|在 n8n 中打开/);
  assert.match(operatorHtml, /data-helper-status="checking"/);
  assert.match(operatorHtml, /执行门禁/);
});
