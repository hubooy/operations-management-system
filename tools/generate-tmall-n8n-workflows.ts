import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import {
  TMALL_YIJIU_DIRECT_PM_PROTOCOL,
  TMALL_LILI_DIRECT_M_PROTOCOL,
  tmallDirectPmProtocolForStore,
  tmallDirectPmProtocolHeader,
  tmallDirectProductMasterRoute,
  tmallDirectPromotionRoute,
} from "./tmall-yijiu-direct-pm-contract";
import { attachHourlyRetryTarget } from "./n8n-hourly-retry-policy.mjs";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const workflowDirectory = path.join(projectRoot, "automation", "n8n");
const baseWorkflowFile = path.join(workflowDirectory, "tmall-yijiu-sycm-cookie-daily.workflow.json");
export const tmallYijiuDirectPmCandidateFileName = "tmall-yijiu-direct-pm-candidate.workflow.json";
export const tmallYiyongDirectPmCandidateFileName = "tmall-yiyong-direct-pm-candidate.workflow.json";

export type TmallN8nWorkflowDefinition = {
  storeKey: string;
  shopName: string;
  shortName: string;
  workflowId: string;
  workflowName: string;
  fileName: string;
  cronExpression: string;
  scheduleName: string;
  productMasterExportMode?: "on_sale_pagewise_excel";
  productMasterCadence: {
    intervalDays: number;
    initialDueDate: string;
  };
};

export const tmallN8nWorkflowDefinitions: readonly TmallN8nWorkflowDefinition[] = [
  {
    storeKey: "tmall-yijiu",
    shopName: "天猫-志高亿玖专卖店",
    shortName: "亿玖",
    workflowId: "M4xY8kQ2vR6sT9pC",
    workflowName: "天猫店铺数据导入",
    fileName: "tmall-yijiu-sycm-cookie-daily.workflow.json",
    cronExpression: "0 11 * * *",
    scheduleName: "每天 11:00 运行",
    productMasterExportMode: "on_sale_pagewise_excel",
    productMasterCadence: { intervalDays: 1, initialDueDate: "2026-08-27" },
  },
  {
    storeKey: "tmall-lili",
    shopName: "天猫-志高丽力专卖店",
    shortName: "丽力",
    workflowId: "TmallLiliDaily2026",
    workflowName: "天猫店铺数据导入-丽力",
    fileName: "tmall-lili-sycm-cookie-daily.workflow.json",
    cronExpression: "10 11 * * *",
    scheduleName: "每天 11:10 运行",
    productMasterCadence: { intervalDays: 1, initialDueDate: "2026-08-27" },
  },
  {
    storeKey: "tmall-tuofeng",
    shopName: "天猫-志高拓丰专卖店",
    shortName: "拓丰",
    workflowId: "TmallTuofengDaily2026",
    workflowName: "天猫店铺数据导入-拓丰",
    fileName: "tmall-tuofeng-sycm-cookie-daily.workflow.json",
    cronExpression: "20 11 * * *",
    scheduleName: "每天 11:20 运行",
    productMasterExportMode: "on_sale_pagewise_excel",
    productMasterCadence: { intervalDays: 3, initialDueDate: "2026-08-25" },
  },
  {
    storeKey: "tmall-cuizhiwang",
    shopName: "天猫-志高炊之王专卖店",
    shortName: "炊之王",
    workflowId: "TmallCuizhiwangDaily2026",
    workflowName: "天猫店铺数据导入-炊之王",
    fileName: "tmall-cuizhiwang-sycm-cookie-daily.workflow.json",
    cronExpression: "30 11 * * *",
    scheduleName: "每天 11:30 运行",
    productMasterExportMode: "on_sale_pagewise_excel",
    productMasterCadence: { intervalDays: 3, initialDueDate: "2026-08-25" },
  },
  {
    storeKey: "tmall-masitu",
    shopName: "天猫-志高马思图专卖店",
    shortName: "马思图",
    workflowId: "TmallMasituDaily2026",
    workflowName: "天猫店铺数据导入-马思图",
    fileName: "tmall-masitu-sycm-cookie-daily.workflow.json",
    cronExpression: "40 11 * * *",
    scheduleName: "每天 11:40 运行",
    productMasterExportMode: "on_sale_pagewise_excel",
    productMasterCadence: { intervalDays: 3, initialDueDate: "2026-08-26" },
  },
  {
    storeKey: "tmall-yiyong",
    shopName: "天猫-志高亿用专卖店",
    shortName: "亿用",
    workflowId: "TmallYiyongDaily2026",
    workflowName: "天猫店铺数据导入-亿用",
    fileName: "tmall-yiyong-sycm-cookie-daily.workflow.json",
    cronExpression: "50 11 * * *",
    scheduleName: "每天 11:50 运行",
    productMasterCadence: { intervalDays: 1, initialDueDate: "2026-08-26" },
  },
] as const;

type WorkflowNode = {
  id?: string;
  typeVersion?: number;
  position?: number[];
  name: string;
  type: string;
  parameters?: {
    content?: string;
    sendHeaders?: boolean;
    headerParameters?: { parameters?: Array<{ name?: string; value?: string }> };
    rule?: { interval: Array<{ field: "cronExpression"; expression: string }> };
    [key: string]: unknown;
  };
};

type WorkflowTemplate = {
  id: string;
  name: string;
  active: boolean;
  versionId?: string;
  nodes: WorkflowNode[];
  connections: Record<string, unknown>;
  settings?: Record<string, unknown>;
  meta?: Record<string, unknown>;
};

const bindingEndMarker = "<!-- tmall-store-binding:end -->";

function stableUuid(seed: string) {
  const bytes = Buffer.from(createHash("sha256").update(seed).digest("hex").slice(0, 32), "hex");
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = bytes.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function replaceStoreSpecificText(content: string, definition: TmallN8nWorkflowDefinition) {
  const withoutBinding = content.includes(bindingEndMarker)
    ? content.slice(content.indexOf(bindingEndMarker) + bindingEndMarker.length).replace(/^\s+/, "")
    : content;
  let adapted = withoutBinding
    .replaceAll("tmall-yijiu", definition.storeKey)
    .replaceAll("亿玖店", `${definition.shortName}店`)
    .replaceAll("亿玖", definition.shortName);
  if (definition.storeKey !== "tmall-yijiu") {
    adapted = adapted.replace(
      "浏览器不可连接时才从临时环境变量 `TMALL_SYCM_COOKIE_FILE` 或 Git 已忽略的",
      "浏览器不可连接时才从 Git 已忽略的店铺专属",
    );
  }
  const productMasterStart = adapted.indexOf("最后 M 复用");
  const productMasterEnd = adapted.indexOf("M 成功或任一阶段失败后", productMasterStart);
  if (productMasterStart >= 0) {
    if (productMasterEnd < 0) throw new Error("天猫 n8n 基础模板的 M 节点说明不完整");
    const productMasterDescription = definition.productMasterExportMode === "on_sale_pagewise_excel"
      ? `最后 M 复用同一${definition.shortName}店独立 Chromium 会话，进入千牛“商品 > 我的商品 > 出售中”，读取“商品总数 + 当前页/总页数”并要求总页数与每页 20 条口径一致；每页严格执行“勾选商品标题全选框 → 精确读回已选数量 → 更多批量操作 → excel商品批量导出 → 确认任务创建成功”。最后一页之前只关闭成功弹窗并点击右上角下一页，最后一页才点击“前往下载”。导出记录只接管本轮提交时间窗内、数量与页数完全一致的全部已完成任务，逐个下载到${definition.shortName}独立目录；全部分页文件分别通过发布模板、店铺和商品数校验后，先合并成一个无跨页重复且唯一商品数等于出售中总数的权威 XLSX，再只提交一次货品主数据导入并回查，禁止逐页导入互相覆盖。点击创建任务结果未决、导出记录多于预期、缺页、任务失败、商品数变化或跨页重复都失败关闭并保留活动清单。`
      : `最后 M 复用同一${definition.shortName}店独立 Chromium 会话，进入千牛“商品 > 出售中”，关闭右下角“重要通知”（若显示），再从右下角打开“商品管家”，在右侧聊天发送“导出全部商品”、确认任务、等待生成并下载 XLSX；随后校验发布模板、店铺、行数和哈希，提交货品主数据导入并回查。`;
    adapted = [adapted.slice(0, productMasterStart), productMasterDescription, adapted.slice(productMasterEnd)].join("");
  }
  return [
    "## 店铺绑定",
    `本模板固定绑定 \`${definition.shopName}\`（\`${definition.storeKey}\`）。工作流与店铺键不匹配时，helper 会在业务节点前失败关闭。`,
    `定时执行仍每日完成 A→B→C→P；M 货品主数据按上海日期每 ${definition.productMasterCadence.intervalDays} 天到期一次，初始到期日为 \`${definition.productMasterCadence.initialDueDate}\`。未到期时 M 返回 \`not_due\`，只负责关闭本店浏览器并释放 helper；到期失败不推进下次日期，翌日完整流程继续补跑。手动完整运行明确强制执行 M。`,
    bindingEndMarker,
    "",
    adapted,
  ].join("\n");
}

function setStoreHeader(node: WorkflowNode, storeKey: string) {
  if (node.type !== "n8n-nodes-base.httpRequest") return;
  const parameters = node.parameters ??= {};
  const url = String(parameters.url ?? "");
  parameters.sendHeaders = true;
  const headerParameters = parameters.headerParameters ??= { parameters: [] };
  const headers = Array.isArray(headerParameters.parameters) ? headerParameters.parameters : [];
  headerParameters.parameters = [
    ...headers.filter((header) => ![
      "x-teruisi-tmall-store-key",
      "x-teruisi-tmall-force-product-master",
      "x-teruisi-tmall-plan-start-date",
      "x-teruisi-tmall-plan-end-date",
      tmallDirectPmProtocolHeader,
    ].includes(String(header.name ?? "").toLowerCase())),
    { name: "X-TERUISI-TMALL-STORE-KEY", value: storeKey },
    ...((url.endsWith("/plan") || url.endsWith("/plan-backfill"))
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
    ...(url.endsWith("/product-master")
      ? [{
          name: "X-TERUISI-TMALL-FORCE-PRODUCT-MASTER",
          // n8n labels an execution started from the schedule node in the
          // editor as `manual` too, while CLI recovery executes the Manual
          // Trigger with mode `cli`. Force M only for the actual Manual Trigger
          // in editor/manual mode so neither path bypasses the three-day cadence.
          value: "={{ $mode === 'manual' && $('手动完整运行（强制 M）').isExecuted ? '1' : '0' }}",
        }]
      : []),
  ];
}

function replaceConnectionNode(value: unknown, oldName: string, newName: string): void {
  if (!value || typeof value !== "object") return;
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    if (key === "node" && child === oldName) (value as Record<string, unknown>)[key] = newName;
    else replaceConnectionNode(child, oldName, newName);
  }
}

function adaptProductMasterNode(workflow: WorkflowTemplate, definition: TmallN8nWorkflowDefinition) {
  const oldName = "M·商品管家批量导出、校验并导入";
  const newName = "M·出售中逐页导出、合并校验并导入";
  const targetName = definition.productMasterExportMode === "on_sale_pagewise_excel" ? newName : oldName;
  const candidates = workflow.nodes.filter((candidate) => candidate.name === oldName || candidate.name === newName);
  if (candidates.length !== 1) throw new Error("天猫 n8n 基础模板必须且只能包含一个货品 M 节点");
  const node = candidates[0]!;
  const sourceName = node.name;
  node.name = targetName;
  if (sourceName !== targetName && workflow.connections[sourceName]) {
    workflow.connections[targetName] = workflow.connections[sourceName];
    delete workflow.connections[sourceName];
  }
  replaceConnectionNode(workflow.connections, sourceName, targetName);
}

function adaptManualTrigger(workflow: WorkflowTemplate) {
  const node = workflow.nodes.find((candidate) => candidate.type === "n8n-nodes-base.manualTrigger");
  if (!node) throw new Error("天猫 n8n 基础模板缺少 manualTrigger");
  const targetName = "手动完整运行（强制 M）";
  const sourceName = node.name;
  node.name = targetName;
  if (sourceName !== targetName && workflow.connections[sourceName]) {
    workflow.connections[targetName] = workflow.connections[sourceName];
    delete workflow.connections[sourceName];
    replaceConnectionNode(workflow.connections, sourceName, targetName);
  }
}

export function buildTmallN8nWorkflow(
  source: WorkflowTemplate,
  definition: TmallN8nWorkflowDefinition,
): WorkflowTemplate {
  const workflow = structuredClone(source);
  workflow.id = definition.workflowId;
  workflow.name = definition.workflowName;
  workflow.active = false;
  workflow.versionId = definition.storeKey === "tmall-yijiu"
    ? source.versionId
    : stableUuid(`teruisi:${definition.storeKey}:tmall-daily:v1`);
  workflow.settings = { ...(workflow.settings ?? {}), executionOrder: "v1", timezone: "Asia/Shanghai" };

  const schedule = workflow.nodes.find((node) => node.type === "n8n-nodes-base.scheduleTrigger");
  if (!schedule) throw new Error("天猫 n8n 基础模板缺少 scheduleTrigger");
  const oldScheduleName = schedule.name;
  const scheduleParameters = schedule.parameters ??= {};
  scheduleParameters.rule = { interval: [{ field: "cronExpression", expression: definition.cronExpression }] };
  schedule.name = definition.scheduleName;
  if (oldScheduleName !== definition.scheduleName) {
    workflow.connections[definition.scheduleName] = workflow.connections[oldScheduleName];
    delete workflow.connections[oldScheduleName];
  }

  for (const node of workflow.nodes) {
    setStoreHeader(node, definition.storeKey);
    if (node.type === "n8n-nodes-base.stickyNote" && typeof node.parameters?.content === "string") {
      node.parameters.content = replaceStoreSpecificText(node.parameters.content, definition);
    }
  }
  adaptManualTrigger(workflow);
  adaptProductMasterNode(workflow, definition);
  if (definition.storeKey === "tmall-lili") adaptLiliDirectMaster(workflow);
  addDailyBackfillLoop(workflow);
  attachHourlyRetryTarget(workflow);
  return workflow;
}

export const tmallNextDayNode = "N·复查缺口并计划下一日";
export const tmallContinueNode = "还有缺口且预算充足？";

function addDailyBackfillLoop(workflow: WorkflowTemplate) {
  const plan = workflow.nodes.find(node => ["http://127.0.0.1:5791/plan", "http://127.0.0.1:5791/plan-backfill"].includes(String(node.parameters?.url)));
  const fetchNode = workflow.nodes.find(node => node.parameters?.url === "http://127.0.0.1:5791/fetch");
  const promotion = workflow.nodes.find(node => node.name.startsWith("P·"));
  const master = workflow.nodes.find(node => node.name.startsWith("M·"));
  if (!plan?.parameters || !fetchNode || !promotion || !master) throw new Error("天猫补缺循环缺少业务节点");
  plan.parameters.url = "http://127.0.0.1:5791/plan-backfill";
  const headers = plan.parameters.headerParameters?.parameters ?? [];
  const edge = (node: string) => ({ node, type: "main", index: 0 });
  const ifNode = (name: string, expression: string, position: number[]): WorkflowNode => ({
    id: stableUuid(`tmall-backfill:${name}`), name, type: "n8n-nodes-base.if", typeVersion: 2.2, position,
    parameters: { conditions: {
      options: { caseSensitive: true, leftValue: "", typeValidation: "strict", version: 2 },
      conditions: [{ id: stableUuid(`tmall-backfill:condition:${name}`), leftValue: expression,
        rightValue: true, operator: { type: "boolean", operation: "true", singleValue: true } }], combinator: "and",
    }, options: {} },
  });
  const complete = "全部缺失日已补齐？";
  const budget = "补缺未完成·已释放店铺资源";
  // Rebuilding from the generated base must produce exactly one loop.
  workflow.nodes = workflow.nodes.filter(node => ![tmallNextDayNode, tmallContinueNode, complete, budget].includes(node.name));
  workflow.nodes.push({
    id: stableUuid("tmall-backfill:next"), name: tmallNextDayNode, type: "n8n-nodes-base.httpRequest", typeVersion: 4.2,
    position: [740, 40], parameters: { method: "POST", url: "http://127.0.0.1:5791/next-day", sendHeaders: true,
      headerParameters: { parameters: [...headers.filter(header => ["X-TERUISI-N8N-EXECUTION-ID", "X-TERUISI-TMALL-STORE-KEY"].includes(header.name ?? "")),
        { name: "X-TERUISI-TMALL-BACKFILL-CYCLE", value: "={{ $runIndex }}" }] }, options: { timeout: 120000 } },
  }, ifNode(tmallContinueNode, "={{ $json.continueBackfill }}", [960, 40]),
  ifNode(complete, "={{ $json.dailyBackfill.status === 'completed' }}", [1400, 40]), {
    id: stableUuid("tmall-backfill:budget"), name: budget, type: "n8n-nodes-base.stopAndError", typeVersion: 1,
    position: [1620, 140], parameters: { errorMessage: "={{ '天猫补缺达到本轮预算，M 和浏览器已收尾；剩余 ' + $json.dailyBackfill.remainingDates.length + ' 天：' + $json.dailyBackfill.remainingDates.join(', ') }}" },
  });
  master.position = [1180, 40];
  workflow.connections[promotion.name] = { main: [[edge(tmallNextDayNode)]] };
  workflow.connections[tmallNextDayNode] = { main: [[edge(tmallContinueNode)]] };
  workflow.connections[tmallContinueNode] = { main: [[edge(fetchNode.name)], [edge(master.name)]] };
  workflow.connections[master.name] = { main: [[edge(complete)]] };
  workflow.connections[complete] = { main: [[], [edge(budget)]] };
  const note = workflow.nodes.find(node => node.name === "流程说明");
  if (typeof note?.parameters?.content === "string") {
    note.parameters.content = note.parameters.content
      .replaceAll("商品日与推广日每轮各最多处理一天。", "商品日与推广日每个循环只处理一天；P 后由 N 重新核验两类覆盖，再决定是否循环回 B。每店最多补 14 天，30 分钟后不再开启新日，最后 M 执行一次并收尾；预算耗尽仍有缺口则明确报未完成。")
      .replaceAll("认证通过后 A 默认直接计划昨天，显式目标日期也不按已有覆盖预先跳过", "认证通过后 A 固定注册起始日至昨天的范围，先查两类缺口并选择最早一天，只下载缺失部分");
  }
}

function renameWorkflowNode(workflow: WorkflowTemplate, node: WorkflowNode, targetName: string) {
  const sourceName = node.name;
  node.name = targetName;
  if (sourceName !== targetName && workflow.connections[sourceName]) {
    workflow.connections[targetName] = workflow.connections[sourceName];
    delete workflow.connections[sourceName];
  }
  replaceConnectionNode(workflow.connections, sourceName, targetName);
}

function addCandidateProtocolHeader(node: WorkflowNode, protocol: string = TMALL_YIJIU_DIRECT_PM_PROTOCOL) {
  const parameters = node.parameters ??= {};
  parameters.sendHeaders = true;
  const headerParameters = parameters.headerParameters ??= { parameters: [] };
  const headers = Array.isArray(headerParameters.parameters) ? headerParameters.parameters : [];
  headerParameters.parameters = [
    ...headers.filter((header) => String(header.name ?? "").toLowerCase() !== tmallDirectPmProtocolHeader),
    { name: "X-TERUISI-TMALL-CANDIDATE-PROTOCOL", value: protocol },
  ];
}

// Also accepts a live single-day definition: production adoption must not pull
// the unrelated, not-yet-adopted daily-backfill loop into the live workflow.
export function adaptLiliDirectMaster(workflow: WorkflowTemplate) {
  if (workflow.id !== "TmallLiliDaily2026") throw new Error("丽力 M 适配拒绝其他工作流");
  const masters = workflow.nodes.filter(node => node.name.startsWith("M·"));
  if (masters.length !== 1) throw new Error("丽力必须有唯一 M 节点");
  const master = masters[0]!;
  if (master.type !== "n8n-nodes-base.httpRequest" || master.parameters?.method !== "POST"
    || !["http://127.0.0.1:5791/product-master", `http://127.0.0.1:5791${tmallDirectProductMasterRoute}`].includes(String(master.parameters?.url))) {
    throw new Error("丽力 M 原路由或节点类型不符合受控适配契约");
  }
  const storeHeaders = master.parameters?.headerParameters?.parameters?.filter(
    header => String(header.name).toLowerCase() === "x-teruisi-tmall-store-key",
  ) ?? [];
  if (storeHeaders.length !== 1 || storeHeaders[0]?.value !== "tmall-lili") throw new Error("丽力 M 店铺头不匹配");
  master.parameters ??= {};
  master.parameters.url = `http://127.0.0.1:5791${tmallDirectProductMasterRoute}`;
  addCandidateProtocolHeader(master, TMALL_LILI_DIRECT_M_PROTOCOL);
  renameWorkflowNode(workflow, master, "M·MTOP 分批导出、合并校验并导入");
  workflow.meta = { ...(workflow.meta ?? {}), liliDirectMaster: { protocol: TMALL_LILI_DIRECT_M_PROTOCOL, intervalDays: 1 } };
  const note = workflow.nodes.find(node => node.name === "流程说明");
  if (note?.parameters && typeof note.parameters.content === "string" && !note.parameters.content.includes("## 丽力 M 接口分批")) {
    note.parameters.content += "\n\n## 丽力 M 接口分批\n丽力 M 改为浏览器登录态 MTOP 每 20 个商品一批串行导出，逐批校验后合并为一个文件并单次导入回查；不使用商品管家。P 不切换直连。M 每日更新，失败不推进节奏；旧管家活动任务必须先经明确确认归档，禁止跨协议接管。";
  }
  return workflow;
}

export function buildTmallYijiuDirectPmCandidateWorkflow(source: WorkflowTemplate): WorkflowTemplate {
  const definition = tmallN8nWorkflowDefinitions.find((candidate) => candidate.storeKey === "tmall-yijiu");
  if (!definition) throw new Error("缺少亿玖天猫工作流定义");
  const workflow = buildTmallN8nWorkflow(source, definition);
  workflow.id = definition.workflowId;
  workflow.name = "天猫店铺数据导入（亿玖 P/M 直连·每日 M）";
  workflow.active = false;
  workflow.versionId = stableUuid("teruisi:tmall-yijiu:direct-pm:daily-m:v2");
  workflow.meta = {
    ...(workflow.meta ?? {}),
    currentForYijiu: true,
    candidateProtocol: TMALL_YIJIU_DIRECT_PM_PROTOCOL,
    replacesWorkflowId: definition.workflowId,
  };

  const promotion = workflow.nodes.find((node) => node.name === "P·商品报表逐日下载、汇总导入并回查");
  const productMaster = workflow.nodes.find((node) => node.name === "M·出售中逐页导出、合并校验并导入");
  if (!promotion || !productMaster) throw new Error("亿玖基础模板缺少 P 或 M 节点");
  promotion.parameters ??= {};
  promotion.parameters.url = `http://127.0.0.1:5791${tmallDirectPromotionRoute}`;
  addCandidateProtocolHeader(promotion);
  renameWorkflowNode(workflow, promotion, "P·直连创建商品报表、下载、汇总导入并回查");

  productMaster.parameters ??= {};
  productMaster.parameters.url = `http://127.0.0.1:5791${tmallDirectProductMasterRoute}`;
  addCandidateProtocolHeader(productMaster);
  renameWorkflowNode(workflow, productMaster, "M·MTOP 分批导出、合并校验并导入");

  const credentialNote = workflow.nodes.find((node) => node.name === "凭证说明");
  if (credentialNote?.parameters && typeof credentialNote.parameters.content === "string") {
    credentialNote.parameters.content += "\n\n## 亿玖直连协议\nP/M 直连接口只复用亿玖独立 Chromium 的浏览器 Cookie 存储；csrfId、loginPointId、MTOP token、签名和 OSS 临时链接只在 helper 内存中存在，不写入 n8n、活动清单或日志。";
  }
  const flowNote = workflow.nodes.find((node) => node.name === "流程说明");
  if (flowNote?.parameters) {
    flowNote.parameters.content = [
      "## 亿玖 P/M 直连现行模板（仓库默认停用）",
      "这是同一工作流 ID 的现行替换版本，不是可并行激活的第二条流程。仓库文件固定 active=false；发布时必须先部署配套 helper，再在 n8n 中受控替换并确认只有最新版本激活。旧版本只保留历史审计，不得用于新的定时或恢复 execution。",
      "",
      "## A→B→C→P→M",
      "A 固定注册起始日至昨天的范围；B/C/P 每个循环只补最早缺失日，N 重新核验商品日和推广日覆盖后循环回 B。每店最多补 14 天，30 分钟后不再开启新日；最后 M 执行一次并收尾，预算耗尽仍有缺口明确报未完成。同一 execution ID、店铺键与共享 helper 串行认领不变。亿玖 M 按上海日期每天到期一次，成功后将 nextDueDate 推进到下一日，失败不推进并由下一次新的完整 execution 补跑。",
      "",
      "## P·阿里妈妈直连任务",
      "从亿玖独立浏览器的阿里妈妈下载列表真实请求中临时取得 csrfId 与 loginPointId；按同一天起止日期、分天、全部指标、last_click_by_effect_time、15 天累计、货品全站推广/关键词推广/人群推广/店铺直达四场景及商品+计划维度创建商品报表。创建前先写 report_submitting 栅栏，响应未决时禁止自动重提；成功后只按唯一 taskId 轮询，立即下载受控 OSS ZIP，校验店铺、日期、行数与哈希，再单次导入并回查日期覆盖。",
      "",
      "## M·MTOP 固定导出",
      "从千牛“商品 > 我的商品 > 出售中”的真实首屏请求捕获只读列表模板，固定每页 20 条、最多 100 页并核对 response total；itemId 排序后每 20 个一批。唯一写类路径逐字固定为 batchFastEdit.htm?optType=batchExportItem&action=submit，不能由工作流、配置或请求参数改写。每批提交前保存导出记录 id 基线，提交与下载串行；响应未决、出现多个新记录、行数/时间窗/商品 ID 不一致均失败关闭。全部批文件校验后合并成一个权威 XLSX，只导入一次并回查。",
      "",
      "## 切换门禁",
      "原 P 或 M 只要存在已进入业务动作的活动清单，直连协议拒绝接管；验证码、安全验证、店铺身份不符、token 失效、风控或页面/接口契约变化均保留清单并停止。",
    ].join("\n");
  }
  return workflow;
}

// Reuse the verified transport description and node configuration, but bind all
// identity-bearing fields again from Yiyong's own definition. No second ID or
// schedule is created. This candidate stays unpublished until the cutover gates
// (legacy M, cadence migration and matching deployed helper) have passed.
export function buildTmallYiyongDirectPmCandidateWorkflow(source: WorkflowTemplate): WorkflowTemplate {
  const definition = tmallN8nWorkflowDefinitions.find((item) => item.storeKey === "tmall-yiyong");
  if (!definition) throw new Error("缺少亿用天猫工作流定义");
  const protocol = tmallDirectPmProtocolForStore(definition.storeKey);
  if (!protocol) throw new Error("亿用直连协议未批准");
  const workflow = buildTmallN8nWorkflow(source, definition);
  const reference = buildTmallYijiuDirectPmCandidateWorkflow(source);
  workflow.name = "天猫店铺数据导入（亿用 P/M 直连试点·每日 M）";
  workflow.versionId = stableUuid("teruisi:tmall-yiyong:direct-pm:daily-m:v1");
  workflow.meta = {
    ...(workflow.meta ?? {}),
    candidateProtocol: protocol,
    replacesWorkflowId: definition.workflowId,
    trialForYiyong: true,
  };
  for (const [legacyName, directName, route] of [
    ["P·商品报表逐日下载、汇总导入并回查", "P·直连创建商品报表、下载、汇总导入并回查", tmallDirectPromotionRoute],
    ["M·商品管家批量导出、校验并导入", "M·MTOP 分批导出、合并校验并导入", tmallDirectProductMasterRoute],
  ]) {
    const node = workflow.nodes.find((item) => item.name === legacyName);
    if (!node) throw new Error("亿用基础模板缺少 P 或 M 节点");
    node.parameters ??= {};
    node.parameters.url = `http://127.0.0.1:5791${route}`;
    addCandidateProtocolHeader(node, protocol);
    renameWorkflowNode(workflow, node, directName!);
  }
  for (const name of ["凭证说明", "流程说明"]) {
    const note = workflow.nodes.find((item) => item.name === name);
    const content = reference.nodes.find((item) => item.name === name)?.parameters?.content;
    if (!note || typeof content !== "string") throw new Error("直连模板说明缺失");
    note.parameters ??= {};
    note.parameters.content = content.replaceAll("亿玖", "亿用")
      .replaceAll("tmall-yijiu", definition.storeKey)
      .replaceAll("2026-08-27", definition.productMasterCadence.initialDueDate)
      .replace("这是同一工作流 ID 的现行替换版本", "这是同一工作流 ID 的待验证候选版本")
      .replace("直连现行模板", "直连待验证试点模板");
  }
  return workflow;
}

export async function generateTmallN8nWorkflows() {
  const source = JSON.parse(await readFile(baseWorkflowFile, "utf8")) as WorkflowTemplate;
  if (source.active) throw new Error("拒绝从已激活的天猫工作流生成扩店模板");
  const generated: string[] = [];
  for (const definition of tmallN8nWorkflowDefinitions) {
    const outputPath = path.join(workflowDirectory, definition.fileName);
    const workflow = buildTmallN8nWorkflow(source, definition);
    await writeFile(outputPath, `${JSON.stringify(workflow, null, 2)}\n`, "utf8");
    generated.push(outputPath);
  }
  const candidatePath = path.join(workflowDirectory, tmallYijiuDirectPmCandidateFileName);
  const candidate = buildTmallYijiuDirectPmCandidateWorkflow(source);
  await writeFile(candidatePath, `${JSON.stringify(candidate, null, 2)}\n`, "utf8");
  generated.push(candidatePath);
  const yiyongPath = path.join(workflowDirectory, tmallYiyongDirectPmCandidateFileName);
  await writeFile(yiyongPath, `${JSON.stringify(buildTmallYiyongDirectPmCandidateWorkflow(source), null, 2)}\n`, "utf8");
  generated.push(yiyongPath);
  return generated;
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  const generated = await generateTmallN8nWorkflows();
  console.log(JSON.stringify({ ok: true, generated: generated.map((file) => path.basename(file)) }, null, 2));
}
