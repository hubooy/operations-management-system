"use client";

import { Fragment, useEffect, useState } from "react";
import jackyunWorkflowDefinition from "@/automation/n8n/jackyun-five-dataset-api.workflow.json";
import tmallWorkflowDefinition from "@/automation/n8n/tmall-yijiu-direct-pm-candidate.workflow.json";
import jdWorkflowDefinition from "@/automation/n8n/jd-multi-store-daily.workflow.json";
import jdMarketWorkflowDefinition from "@/automation/n8n/jd-market-ranking-daily.chromium-silent-copy.workflow.json";
import jdPromotionWorkflowDefinition from "@/automation/n8n/jd-promotion-daily.workflow.json";
import jdPromotionCutMeatWorkflowDefinition from "@/automation/n8n/jd-promotion-cut-meat-20260813-14.workflow.json";
import type { ModuleViewKey } from "./shell/navigation-catalog";

type AppRole = "viewer" | "analyst" | "operator" | "admin";
type WorkflowKey = "jackyun" | "tmall" | "jd" | "jd_market" | "jd_promotion" | "jd_promotion_cut_meat";

type N8nWorkflowViewProps = {
  currentUser: { role: AppRole } | null;
  moduleView: ModuleViewKey<"n8n_workflows">;
  onModuleViewChange: (view: ModuleViewKey<"n8n_workflows">) => void;
};

type N8nWorkflowDefinition = {
  id: string;
  name: string;
  active: boolean;
  nodes: Array<{
    name: string;
    type: string;
    parameters?: { url?: string };
  }>;
};

type HelperHealthPayload = {
  ok?: boolean;
  stage?: string;
  busy?: boolean;
  activeWorkflow?: "tmall" | "jackyun" | "jd" | "jd-market" | "jd-promotion" | null;
  cookieSource?: "ready" | "missing" | "invalid";
  tmallProfile?: "ready" | "missing" | "invalid";
  jackyunProfile?: "ready" | "missing" | "invalid";
  jdProfiles?: "ready" | "missing" | "invalid";
  jdMarketProfile?: "ready" | "missing" | "invalid";
  jdPromotionProfile?: "ready" | "missing" | "invalid";
  jdPromotionCutMeatProfile?: "ready" | "missing" | "invalid";
};

export type HelperAvailabilityKind = "checking" | "ready" | "running" | "cookie-missing" | "offline";

type HelperAvailability = {
  kind: HelperAvailabilityKind;
  label: string;
  detail: string;
};

type WorkflowConfig = {
  key: WorkflowKey;
  definition: N8nWorkflowDefinition;
  subtitle: string;
  tags: string[];
  flowLabel: string;
  pipelineTitle: string;
  pipelineDescription: string;
  workflowMetric: string;
  scheduleMetric: string;
  scheduleDescription: string;
  scheduleTriggerLabel: string;
  iframeTitle: string;
  safetyNote: string;
  stageDetails: Record<string, { title: string; description: string }>;
};

const helperHealthUrl = "http://127.0.0.1:5791/health";

const workflowConfigs: Record<WorkflowKey, WorkflowConfig> = {
  jackyun: {
    key: "jackyun",
    definition: jackyunWorkflowDefinition as N8nWorkflowDefinition,
    subtitle: "吉客云 ERP 货品、分仓库存、库龄、销售和组合装的一体化每日导入流程。",
    tags: ["吉客云 ERP", "Asia/Shanghai", "五类严格串行"],
    flowLabel: "A → B → C → D → E",
    pipelineTitle: "五表先导出后导入",
    pipelineDescription: "手动启动后，依次完成五表导出、完整校验、统一导入和结果核验。",
    workflowMetric: "吉客云导入系统",
    scheduleMetric: "00:10",
    scheduleDescription: "上海时区 · 每天运行一次",
    scheduleTriggerLabel: "每天",
    iframeTitle: "吉客云导入系统 n8n 工作流",
    safetyNote: "页面只嵌入本机编辑器，吉客云账号、密码、Cookie、Token 和 Session 均不进入运营系统。五表全部下载并通过校验后才开始导入；库存和库龄使用实际采集日，销售按发货时间从月初统计至昨天。最终以五类精确批次核验结果为准。",
    stageDetails: {
      A: { title: "固定采集与销售日期", description: "按上海时区固定库存采集日及销售截止日，核验执行条件。" },
      B: { title: "依次导出五表", description: "完成分仓库存、组合装及子件、销售明细、库龄、SKU 货品的导出和下载。" },
      C: { title: "五表完整校验", description: "核对原文件、日期、行数和业务范围，并完成导入演练。" },
      D: { title: "统一导入系统", description: "按货品、分仓库存、库龄、销售、组合装顺序导入，保留来源和批次。" },
      E: { title: "独立结果核验", description: "重读审计和五类精确批次，确认实际导入结果。" },
    },
  },
  tmall: {
    key: "tmall",
    definition: tmallWorkflowDefinition as N8nWorkflowDefinition,
    subtitle: "天猫货品主数据、生意参谋 SPU 分天数据与阿里妈妈商品推广报表的一体化导入流程。",
    tags: ["天猫-志高亿玖专卖店", "Asia/Shanghai", "本机安全执行"],
    flowLabel: "A → B → C → P → M",
    pipelineTitle: "五段式安全导入链路",
    pipelineDescription: "每日从商品日计划开始并完成推广；M 到期时更新货品主数据，未到期则安全关闭本店浏览器并释放 helper。",
    workflowMetric: "天猫店铺数据导入",
    scheduleMetric: "11:00",
    scheduleDescription: "上海时区 · 每天运行一次",
    scheduleTriggerLabel: "每天",
    iframeTitle: "天猫店铺数据导入 n8n 工作流",
    safetyNote: "页面只嵌入本机编辑器，Cookie、明文账号、密码、Token 和 Session 均不进入运营系统或 n8n。本地 Worker 自动守护一次性环回服务；A 先启动受控独立 Chromium，登录失效时仅由当前 Windows 用户解密对应店铺的 DPAPI 凭据并向唯一表单提交一次，验证码或安全验证仍要求人工处理。A→B→C→P→M 绑定同一 n8n execution；A/B/C/P 每日执行，M 按每店持久的三日节奏错峰更新，未到期返回 not_due，手动完整运行强制 M。M 实际成功、not_due 或失败终态都会关闭本轮受控 Chromium；到期 M 失败不推进日期，也不回滚已完成回查的商品日或推广导入。所有导入接口只在业务范围与规范化后的完整业务内容都一致时返回 duplicate。",
    stageDetails: {
      M: { title: "货品主数据（三日错峰）", description: "到期时，亿玖、拓丰和马思图逐页导出并合并，其余店铺使用商品管家；未到期不创建任务，只安全收尾。失败会在次日完整流程补跑。" },
      A: { title: "登录预检与目标日计划", description: "启动店铺独立 Chromium，仅从 Windows DPAPI 凭据库向唯一登录表单提交一次并核验店铺身份；通过后默认生成昨天。" },
      B: { title: "逐日下载", description: "每个业务日独立下载生意参谋 XLS，并核验店铺身份、文件类型与日期覆盖。" },
      C: { title: "签收导入", description: "签收受控文件，按业务范围与规范化完整内容判重，并回查批次、行数、店铺与同日覆盖。" },
      P: { title: "商品推广报表", description: "进入阿里妈妈商品报表，营销场景全选、维度选择商品和计划；目标日逐日下载，按商品汇总计划行，校验、导入并完成覆盖回查。" },
    },
  },
  jd: {
    key: "jd",
    definition: jdWorkflowDefinition as N8nWorkflowDefinition,
    subtitle: "京东四店商品 SKU 主数据、商智 SKU 分天和 SPU 分天的一体化下载与导入流程。",
    tags: ["京东四店", "Asia/Shanghai", "严格串行与独立复核"],
    flowLabel: "A → B → C",
    pipelineTitle: "三段式京东多店铺安全导入链路",
    pipelineDescription: "手动或每日定时触发；任一店铺或数据集失败都会停止后续任务。",
    workflowMetric: "京东多店铺商品数据导入",
    scheduleMetric: "10:00",
    scheduleDescription: "上海时区 · 固化昨天所在月的完整范围",
    scheduleTriggerLabel: "每日",
    iframeTitle: "京东多店铺商品数据统一下载与导入 n8n 工作流",
    safetyNote: "页面只嵌入本机编辑器，京东账号、密码、Cookie、Token 和 Session 均不进入运营系统。A 固化昨天所在月 1 日至昨天的完整日期范围并预检独立会话；B 逐店串行运行商品主数据、SKU 分天和 SPU 分天；C 独立重读审计，复核批次、零告警、店铺身份和日期范围。",
    stageDetails: {
      A: { title: "生成多店计划", description: "按上海时区固定昨天所在月 1 日至昨天，预检本机系统与四店独立 Chrome profile。" },
      B: { title: "四店串行执行", description: "每店依次完成商品 SKU 主数据、商智 SKU 分天、商智 SPU 分天的下载、导入和落库回查。" },
      C: { title: "独立结果核验", description: "重读 runner 审计，逐店逐数据集核对完成批次、零告警、店铺和完整日期范围。" },
    },
  },
  jd_market: {
    key: "jd_market",
    definition: jdMarketWorkflowDefinition as N8nWorkflowDefinition,
    subtitle: "京东市场商品交易榜单 SKU 按日缺口的自动下载、签收、导入与覆盖回查。",
    tags: ["京东商智", "7 类目", "Profile 3 静默 Chromium"],
    flowLabel: "A → B → C",
    pipelineTitle: "三段式市场商品榜单日补齐链路",
    pipelineDescription: "按上海时区补到昨天；每轮只处理系统真实缺失日。",
    workflowMetric: "京东市场商品榜单日补齐",
    scheduleMetric: "10:30",
    scheduleDescription: "上海时区 · 缺失日串行补跑",
    scheduleTriggerLabel: "每日",
    iframeTitle: "京东市场商品榜单缺失日下载与导入（Chromium 静默下载副本）n8n 工作流",
    safetyNote: "A 按 7 个完整榜单身份只读计算缺失日期；B 使用 Profile 3 隐藏 Chromium，每个未完成分块前捕获当前类目的新鲜精确请求，文件签收并通过大小、SHA-256、身份、日期和行数重验后才调用正式市场导入接口；响应丢失只能由新的 n8n execution 从 A 安全接管唯一未闭环计划。C 回查原计划全部目标日。下载、严格 completed proof 和覆盖回查缺一不可。",
    stageDetails: {
      A: { title: "计算 7 类目缺失日期", description: "逐类目读取 pop、SKU、全部价格带的日覆盖，计划到上海时区昨天，并优先接管唯一可恢复计划。" },
      B: { title: "静默分块导出并导入", description: "Profile 3 隐藏 Chromium 串行处理 7 类目；每个未完成分块捕获新鲜精确请求，签收 CSV 并取得严格导入证明。" },
      C: { title: "原目标覆盖回查", description: "按原计划逐类目复核每个目标日均已落库，任一日期仍缺失则整轮失败关闭。" },
    },
  },
  jd_promotion: {
    key: "jd_promotion",
    definition: jdPromotionWorkflowDefinition as N8nWorkflowDefinition,
    subtitle: "京东志高商用设备旗舰店京准通 AI 推广明细的逐日生成、下载、校验、导入与回查。",
    tags: ["京准通", "志高商用设备旗舰店", "Default profile"],
    flowLabel: "A → B → C",
    pipelineTitle: "三段式京准通推广安全导入链路",
    pipelineDescription: "默认处理上海时区昨天；任务、文件、范围和批次任一不唯一都会停止。",
    workflowMetric: "京东 AI 推广数据导入",
    scheduleMetric: "10:40",
    scheduleDescription: "上海时区 · 每天处理昨天",
    scheduleTriggerLabel: "每日",
    iframeTitle: "京东志高商用设备 AI 推广数据下载与导入 n8n 工作流",
    safetyNote: "工作流固定绑定 jd-yiyong-director，不保存京东凭证。A 固化日期与执行所有者；B 只接管唯一下载任务，验证 UTF-8 CSV、连续日期覆盖、账户集合和 SHA-256 后按精确范围导入；C 独立重验文件与 completed 批次。",
    stageDetails: {
      A: { title: "固化日期与店铺", description: "按上海时区确定昨天，绑定 n8n execution ID、志高商用设备旗舰店和 Default profile。" },
      B: { title: "生成下载并导入", description: "设置自定义日期，防重生成任务，只下载唯一候选 CSV，校验后调用 jd_promotion 精确范围导入。" },
      C: { title: "独立文件与批次复验", description: "重新读取文件，核对 SHA-256、行数、账户集合、日期范围及精确 completed 批次。" },
    },
  },
  jd_promotion_cut_meat: {
    key: "jd_promotion_cut_meat",
    definition: jdPromotionCutMeatWorkflowDefinition as N8nWorkflowDefinition,
    subtitle: "京东志高切肉机旗舰店京准通 AI 推广明细的逐日生成、下载、校验、导入与回查。",
    tags: ["京准通", "志高切肉机旗舰店", "Profile 2"],
    flowLabel: "A → B → C",
    pipelineTitle: "切肉机旗舰店三段式推广安全导入链路",
    pipelineDescription: "默认处理上海时区昨天；店铺、日期、任务、文件和批次任一不唯一都会停止。",
    workflowMetric: "切肉机店 AI 推广导入",
    scheduleMetric: "10:50",
    scheduleDescription: "上海时区 · 每天处理昨天",
    scheduleTriggerLabel: "每日",
    iframeTitle: "京东志高切肉机 AI 推广数据下载与导入 n8n 工作流",
    safetyNote: "工作流固定绑定 jd-maidehao-operator1、志高切肉机旗舰店、shopId 745866 和 Profile 2，不保存京东凭证。A 固化店铺、日期与执行所有者；B 只接管唯一下载任务并完成严格导入回查；C 独立重验文件与 completed 批次。",
    stageDetails: {
      A: { title: "固化切肉机店铺与日期", description: "按上海时区确定昨天，绑定 n8n execution ID、Profile 2 和志高切肉机旗舰店。" },
      B: { title: "生成下载并导入", description: "设置自定义日期，只下载唯一候选 CSV，校验后按切肉机店精确范围导入。" },
      C: { title: "独立文件与批次复验", description: "重读文件并核对 SHA-256、行数、账户集合、日期范围及精确 completed 批次。" },
    },
  },
};

export function canManageN8nWorkflow(role: AppRole | undefined) {
  return role === "operator" || role === "admin";
}

export function shouldMountN8nWorkflowEditor(
  role: AppRole | undefined,
  helperKind: HelperAvailabilityKind,
) {
  return canManageN8nWorkflow(role) && helperKind === "ready";
}

function checkingHelper(key: WorkflowKey): HelperAvailability {
  return {
    kind: "checking",
    label: "正在检测辅助服务",
    detail: key === "jackyun"
      ? "正在确认 5791 环回服务、本机运营系统和吉客云专用 Chrome profile。"
      : key === "jd" || key === "jd_market" || key === "jd_promotion" || key === "jd_promotion_cut_meat"
        ? key === "jd_market"
          ? "正在确认 5791 环回服务、本机运营系统和榜单受控店铺的独立 Chrome profile。"
          : key === "jd_promotion"
            ? "正在确认 5791 环回服务、本机运营系统和志高商用设备旗舰店 Default profile。"
            : key === "jd_promotion_cut_meat"
              ? "正在确认 5791 环回服务、本机运营系统和志高切肉机旗舰店 Profile 2。"
            : "正在确认 5791 环回服务、本机运营系统和四店独立 Chrome profile。"
        : "正在确认 5791 环回服务和亿玖店专属 Chromium profile 是否可用。",
  };
}

function helperAvailability(payload: HelperHealthPayload, key: WorkflowKey): HelperAvailability {
  if (payload.ok !== true) throw new Error("invalid_health_response");
  if (key === "jackyun" && payload.jackyunProfile !== "ready") {
    return {
      kind: "cookie-missing",
      label: "吉客云专用会话待恢复",
      detail: "辅助服务在线，但专用 Chrome profile 缺失或结构无效；先执行 npm run jackyun:login 完成人工登录。",
    };
  }
  if (key === "tmall" && payload.tmallProfile !== "ready") {
    return {
      kind: "cookie-missing",
      label: "天猫专用会话待恢复",
      detail: "辅助服务在线，但亿玖店专属 Chromium profile 缺失或结构无效；请先使用专属快捷方式完成首次登录。",
    };
  }
  if ((key === "jd" && payload.jdProfiles !== "ready") || (key === "jd_market" && payload.jdMarketProfile !== "ready")
    || (key === "jd_promotion" && payload.jdPromotionProfile !== "ready")
    || (key === "jd_promotion_cut_meat" && payload.jdPromotionCutMeatProfile !== "ready")) {
    return {
      kind: "cookie-missing",
      label: "京东店铺会话待恢复",
      detail: key === "jd_market"
        ? "辅助服务在线，但榜单受控店铺的独立 Chrome profile 缺失或结构无效；恢复该店铺会话后重新检测。"
        : key === "jd_promotion"
          ? "辅助服务在线，但志高商用设备旗舰店的 Default profile 缺失或结构无效；恢复该店铺会话后重新检测。"
          : key === "jd_promotion_cut_meat"
            ? "辅助服务在线，但志高切肉机旗舰店的 Profile 2 缺失或结构无效；恢复该店铺会话后重新检测。"
          : "辅助服务在线，但至少一个京东独立 Chrome profile 缺失或结构无效；恢复对应店铺会话后重新检测。",
    };
  }
  if (payload.busy || payload.stage !== "ready") {
    const activeLabel = payload.activeWorkflow === "jackyun" ? "吉客云" : payload.activeWorkflow === "tmall" ? "天猫" : payload.activeWorkflow === "jd-market" ? "京东市场榜单" : payload.activeWorkflow === "jd-promotion" ? "京东推广" : payload.activeWorkflow === "jd" ? "京东" : "当前";
    return {
      kind: "running",
      label: `${activeLabel}流程执行中`,
      detail: "辅助服务正在处理当前串行链路，请等待本轮完成并自动重新待命后再发起下一轮。",
    };
  }
  return key === "jackyun" ? {
    kind: "ready",
    label: "可以安全启动",
    detail: "服务与专用 profile 已就绪；A 会跳过已有完整当日结果，任一失败都会阻断后续五类任务。",
  } : key === "jd" || key === "jd_market" || key === "jd_promotion" || key === "jd_promotion_cut_meat" ? {
    kind: "ready",
    label: "可以安全启动",
    detail: key === "jd_market"
      ? "服务与京东独立 profile 已就绪；A/B/C 会按真实缺失日串行下载、签收、导入并回查。"
      : key === "jd_promotion"
        ? "服务与志高商用设备旗舰店 Default profile 已就绪；A/B/C 会按目标日期生成、下载、导入并独立回查。"
        : key === "jd_promotion_cut_meat"
          ? "服务与志高切肉机旗舰店 Profile 2 已就绪；A/B/C 会按上海时区昨天生成、下载、导入并独立回查。"
        : "服务与四店独立 profile 已就绪；A/B/C 会保持跨店串行、批次幂等与独立落库回查。",
  } : {
    kind: "ready",
    label: "辅助服务已就绪",
    detail: payload.cookieSource === "ready"
      ? "服务、亿玖店专属 profile 和备用 Cookie 文件均已就绪；Cookie 身份及千牛、阿里妈妈登录有效性会在对应阶段再次核验。"
      : "服务与亿玖店专属 profile 已就绪；流程优先读取该浏览器会话，备用 Cookie 文件未配置时不会阻止执行，登录异常仍会安全停止。",
  };
}

export default function N8nWorkflowView({ currentUser, moduleView, onModuleViewChange }: N8nWorkflowViewProps) {
  const selectedWorkflowKey: WorkflowKey = moduleView;
  const [frameKey, setFrameKey] = useState(0);
  const [frameReady, setFrameReady] = useState(false);
  const [helperRefreshKey, setHelperRefreshKey] = useState(0);
  const [helperStatus, setHelperStatus] = useState<HelperAvailability>(() => checkingHelper("jackyun"));
  const config = workflowConfigs[selectedWorkflowKey];
  const workflow = config.definition;
  const workflowUrl = `http://localhost:5678/workflow/${encodeURIComponent(workflow.id)}`;
  const canManageWorkflow = canManageN8nWorkflow(currentUser?.role);
  const workflowEditorReady = shouldMountN8nWorkflowEditor(currentUser?.role, helperStatus.kind);
  const requestStages = workflow.nodes
    .filter((node) => node.type === "n8n-nodes-base.httpRequest")
    .map((node) => {
      const code = node.name.split("·", 1)[0] || "?";
      const details = config.stageDetails[code] ?? { title: node.name, description: "执行受控的本机工作流步骤。" };
      return {
        code,
        name: details.title,
        description: details.description,
        endpoint: node.parameters?.url?.replace(/^https?:\/\/127\.0\.0\.1:5791/, "") || "本机环回服务",
      };
    });
  const triggerCount = workflow.nodes.filter((node) =>
    node.type === "n8n-nodes-base.manualTrigger" || node.type === "n8n-nodes-base.scheduleTrigger"
  ).length;

  useEffect(() => {
    let cancelled = false;
    let activeController: AbortController | null = null;
    let pollTimer: number | null = null;
    let lastKind: HelperAvailability["kind"] | "" = "";
    let stableChecks = 0;
    let checkGeneration = 0;
    const recordKind = (kind: HelperAvailability["kind"]) => {
      stableChecks = kind === lastKind ? stableChecks + 1 : 0;
      lastKind = kind;
    };
    const scheduleNext = () => {
      if (cancelled || document.visibilityState === "hidden") return;
      const delay = stableChecks >= 2 ? 15_000 : 5_000;
      pollTimer = window.setTimeout(() => void check(), delay);
    };
    const check = async () => {
      const generation = ++checkGeneration;
      if (pollTimer !== null) window.clearTimeout(pollTimer);
      pollTimer = null;
      activeController?.abort();
      const controller = new AbortController();
      activeController = controller;
      const timeout = window.setTimeout(() => controller.abort(), 2_000);
      try {
        const response = await fetch(helperHealthUrl, { cache: "no-store", signal: controller.signal });
        const payload = await response.json() as HelperHealthPayload;
        if (!response.ok) throw new Error("helper_unavailable");
        if (!cancelled && generation === checkGeneration) {
          const availability = helperAvailability(payload, selectedWorkflowKey);
          recordKind(availability.kind);
          if (availability.kind !== "ready") setFrameReady(false);
          setHelperStatus(availability);
        }
      } catch {
        if (!cancelled && generation === checkGeneration) {
          recordKind("offline");
          setFrameReady(false);
          setHelperStatus({
            kind: "offline",
            label: "辅助服务离线",
            detail: "请用受控启动命令重启本地 Worker；5791 服务会自动拉起并在每轮结束后重新待命。",
          });
        }
      } finally {
        window.clearTimeout(timeout);
        if (activeController === controller) activeController = null;
        if (generation === checkGeneration) scheduleNext();
      }
    };

    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        checkGeneration += 1;
        if (pollTimer !== null) window.clearTimeout(pollTimer);
        pollTimer = null;
        activeController?.abort();
        return;
      }
      void check();
    };

    void check();
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      cancelled = true;
      checkGeneration += 1;
      activeController?.abort();
      if (pollTimer !== null) window.clearTimeout(pollTimer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [helperRefreshKey, selectedWorkflowKey]);

  useEffect(() => {
    setHelperStatus(checkingHelper(selectedWorkflowKey));
    setFrameReady(false);
    setFrameKey((value) => value + 1);
  }, [selectedWorkflowKey]);

  const refreshFrame = () => {
    setFrameReady(false);
    setFrameKey((key) => key + 1);
  };

  const refreshHelperStatus = () => {
    setHelperStatus(checkingHelper(selectedWorkflowKey));
    setHelperRefreshKey((key) => key + 1);
  };

  const selectWorkflow = (key: WorkflowKey) => {
    if (key === selectedWorkflowKey) return;
    onModuleViewChange(key);
  };

  return (
    <section className="n8n-workflow-module" data-testid="n8n-workflow-module">
      <nav className="n8n-workflow-switcher" role="tablist" aria-label="工作流选择">
        {(Object.keys(workflowConfigs) as WorkflowKey[]).map((key) => (
          <button
            type="button"
            role="tab"
            id={`n8n-workflow-tab-${key}`}
            aria-controls={`n8n-workflow-panel-${key}`}
            key={key}
            className={selectedWorkflowKey === key ? "is-active" : ""}
            aria-selected={selectedWorkflowKey === key}
            tabIndex={selectedWorkflowKey === key ? 0 : -1}
            onClick={() => selectWorkflow(key)}
            onKeyDown={(event) => {
              const tabs = Array.from(event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]') ?? []);
              const index = tabs.indexOf(event.currentTarget);
              const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index - 1 + tabs.length) % tabs.length : -1;
              if (nextIndex >= 0) { event.preventDefault(); tabs[nextIndex]?.focus(); tabs[nextIndex]?.click(); }
            }}
          >
            <span>{key === "jackyun" ? "吉" : key === "tmall" ? "天" : key === "jd_market" ? "榜" : key === "jd_promotion" ? "推" : key === "jd_promotion_cut_meat" ? "切" : "京"}</span>
            <strong>{workflowConfigs[key].definition.name}</strong>
          </button>
        ))}
      </nav>

      <div role="tabpanel" id={`n8n-workflow-panel-${selectedWorkflowKey}`} aria-labelledby={`n8n-workflow-tab-${selectedWorkflowKey}`}>

      <section className="n8n-workflow-hero">
        <div className="n8n-workflow-hero-copy">
          <span className="n8n-workflow-kicker"><i /> n8n automation</span>
          <h2>{workflow.name}</h2>
          <p>{config.subtitle}</p>
          <div className="n8n-workflow-tags">
            {config.tags.map((tag) => <span key={tag}>{tag}</span>)}
          </div>
        </div>
        <div className="n8n-workflow-hero-status">
          <span className="is-draft">仓库模板</span>
          <strong>{config.flowLabel}</strong>
          <small>工作流 ID · {workflow.id} · 实际发布状态以 n8n 画布为准</small>
        </div>
      </section>

      <section className="n8n-workflow-metrics" aria-label="工作流摘要">
        <article><span>工作流</span><strong>1</strong><small>{config.workflowMetric}</small></article>
        <article><span>处理阶段</span><strong>{requestStages.length}</strong><small>{requestStages.map((stage) => stage.name).join("、")}</small></article>
        <article><span>触发入口</span><strong>{triggerCount}</strong><small>手动运行 + 定时补跑</small></article>
        <article><span>补跑时段</span><strong>{config.scheduleMetric}</strong><small>{config.scheduleDescription}</small></article>
      </section>

      <section className="panel n8n-pipeline-panel" aria-labelledby="n8n-pipeline-title">
        <div className="n8n-panel-heading">
          <div><span>FLOW OVERVIEW</span><h3 id="n8n-pipeline-title">{config.pipelineTitle}</h3><p>{config.pipelineDescription}</p></div>
          <div className="n8n-loopback-status">
            <span className={`n8n-helper-pill is-${helperStatus.kind}`}><i />{helperStatus.label}</span>
            <span className="n8n-loopback-badge">仅访问 127.0.0.1:5791</span>
          </div>
        </div>
        <div className="n8n-pipeline-flow">
          <div className="n8n-trigger-stack" aria-label="触发入口">
            <div><i className="manual">↗</i><span><strong>手动运行</strong><small>人工确认后启动</small></span></div>
            <div><i className="schedule">◷</i><span><strong>定时补跑</strong><small>{config.scheduleMetric} {config.scheduleTriggerLabel}</small></span></div>
          </div>
          <span className="n8n-flow-arrow" aria-hidden="true">→</span>
          {requestStages.map((stage, index) => (
            <Fragment key={stage.code}>
              <article className="n8n-stage-card">
                <div><span>{stage.code}</span><code>{stage.endpoint}</code></div>
                <strong>{stage.name}</strong>
                <p>{stage.description}</p>
              </article>
              {index < requestStages.length - 1 && <span className="n8n-flow-arrow" aria-hidden="true">→</span>}
            </Fragment>
          ))}
        </div>
      </section>

      <section className="panel n8n-editor-panel" aria-labelledby="n8n-editor-title">
        <div className="n8n-panel-heading n8n-editor-heading">
          <div><span>LIVE WORKFLOW</span><h3 id="n8n-editor-title">n8n 工作流画布</h3><p>直接使用本机 n8n 登录态；运营系统不会读取或保存 n8n 凭证。</p></div>
          {workflowEditorReady && <div className="n8n-editor-actions">
            <button type="button" onClick={refreshFrame}>刷新画布</button>
            <a href={workflowUrl} target="_blank" rel="noreferrer">在 n8n 中打开 ↗</a>
          </div>}
        </div>
        {canManageWorkflow ? <>
          <div className={`n8n-helper-banner is-${helperStatus.kind}`} data-helper-status={helperStatus.kind} aria-live="polite">
            <span><i />{helperStatus.label}</span>
            <p>{helperStatus.detail}</p>
            <button type="button" onClick={refreshHelperStatus}>重新检测</button>
          </div>
          {workflowEditorReady ? <div className="n8n-frame-shell">
            {!frameReady && <div className="n8n-frame-loading" role="status"><span /><strong>正在连接本机 n8n</strong><small>如果出现登录页，请先完成 n8n 登录。</small></div>}
            <iframe
              key={`${selectedWorkflowKey}-${frameKey}`}
              className="n8n-workflow-frame"
              src={workflowUrl}
              title={config.iframeTitle}
              onLoad={() => setFrameReady(true)}
              referrerPolicy="no-referrer"
              sandbox="allow-downloads allow-forms allow-modals allow-popups allow-popups-to-escape-sandbox allow-same-origin allow-scripts"
              allow="clipboard-read; clipboard-write"
            />
          </div> : <div className="n8n-frame-shell">
            <div className="n8n-helper-gate" role={helperStatus.kind === "checking" ? "status" : "alert"}>
              <span>执行门禁</span>
              <strong>{helperStatus.label}</strong>
              <p>{helperStatus.detail}</p>
              <button type="button" onClick={refreshHelperStatus}>重新检测</button>
            </div>
          </div>}
          <footer className="n8n-editor-note"><span>安全与去重</span><p>{config.safetyNote}</p></footer>
        </> : <div className="n8n-access-card">
          <span>锁</span><div><strong>需要操作员或管理员权限</strong><p>当前账号可查看流程概览，但不能加载可执行的 n8n 编辑器。该限制防止只读账号绕过系统权限发起真实导入。</p></div>
        </div>}
      </section>
      </div>
    </section>
  );
}
