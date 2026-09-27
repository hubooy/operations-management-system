import { randomUUID } from "node:crypto";
import { stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  type BrowserAutomationClient,
  closeChromeBrowser,
  connectJackyunTarget,
  evaluateValue,
  launchDedicatedChrome,
} from "../lib/jackyun/cdp-client";
import { PlaywrightPageClient, connectPlaywrightBrowser, connectPlaywrightJackyunTarget } from "../lib/jackyun/playwright-client";
import { downloadSignedOssExport } from "../lib/jackyun/oss-download";
import { assertBoundDownloadProvenance } from "../lib/jackyun/download-provenance";
import { readJsonFile, readJsonFileOr, writeJsonAtomic } from "../lib/jackyun/json-file";
import { jackyunModuleOrder, type JackyunModule } from "../lib/jackyun/post-download";
import { assertJackyunSnapshotEvidence, jackyunCaptureDate, jackyunExportFirstPolicyVersion, jackyunExportOrder, type JackyunHistoricalSnapshotEvidence } from "../lib/jackyun/run-contract";
import { withJackyunRunLock } from "../lib/jackyun/run-lock";
import { readJackyunLoginConfig } from "../lib/jackyun/windows-dpapi";
import { inspectJackyunLoginSurface, isJackyunLoginOrigin, submitJackyunDpapiLogin, verifyJackyunBrowserBinding, waitForJackyunDpapiSession } from "../lib/jackyun/dpapi-login";
import { selectJackyunExportTask, type JackyunExportTaskBinding, type JackyunExportTaskRecord } from "../lib/jackyun/export-task";
import type { BrowserExportConfirmation, BrowserHandoff } from "./jackyun-daily-runner";

import { jackyunWebSessionTransport, prepareWebSessionExport, submitWebSessionExport, readWebSessionTasks, waitForWebSessionTask } from "../lib/jackyun/web-session-export";
import { assert849ReprepareWindow } from "../lib/jackyun/web-session-recovery";
import { assert890ReprepareWindow } from "../lib/jackyun/http-scope-recovery";
import { captureDirectExport, createDirectSession, jackyunDirectTransport, readDirectTasks } from "../lib/jackyun/direct-export";
import type { JackyunHttpSession } from "../lib/jackyun/direct-http";

type Policy = {
  version: string;
  browser: {
    pageTimeoutMs: number;
    pollIntervalMs: number;
    fastPollIntervalMs?: number;
    actionTimeoutMs?: number;
    tableStableTimeoutMs?: number;
    exportTimeoutMs?: number;
    stableSamples: number;
    downloadDirectory: string;
    eventTimeoutMs: number;
    allowedDownloadHosts: string[];
    controller?: {
      chromePath?: string;
      profileDirectory?: string;
      debuggingPort?: number;
      startUrl?: string;
    };
  };
  modules: Record<JackyunModule, {
    pageName: string;
    requiresQuery: boolean;
    timeoutMs?: number;
    minimumSelectedWarehouses?: number;
    exportConfirmation?: { promptIncludes: string[]; button: string };
  }>;
};

type ModuleActionState = Partial<BrowserHandoff> & {
  exportTaskBinding?: JackyunExportTaskBinding;
  status: "pending" | "navigated" | "queried" | "export_armed" | "downloaded" | "handed_off" | "completed";
  webSession?: { baselineIds: string[]; baselineAt: string; pendingTaskId?: string };
  directPayloadSha256?: string;
  queryAttemptHistory?: { queryIntentAt: string; tableStableAt: string | null; repreparedAt: string }[];
  reprepareEvidence?: { originalIntentAt: string; permitSha256: string; originalControllerSha256: string };
  queryRetryCount?: number;
  queryRetryIntentAt?: string;
  tableReadbackFailure?: {
    code: "zero_rows" | "unstable" | "table_timeout";
    observedAt: string;
  };
  snapshotControlReadback?: Omit<
    JackyunHistoricalSnapshotEvidence,
    "queryIntentAt" | "queryRefreshSource" | "queryRefreshCompletedAt" | "tableStableAt"
  >;
  queryRefreshEvidence?: {
    queryIntentAt: string;
    completedAt: string;
    source: "miniui_grid_lifecycle" | "module_network_request";
  };
  timings?: {
    enterModuleMs?: number;
    tableStableMs?: number;
    exportToDownloadMs?: number;
    postDownloadMs?: number;
  };
};

export type JackyunControllerFailureCode = "FIELD_MISMATCH" | "TABLE_TIMEOUT" | "FILE_BINDING_FAILED";
export type JackyunControllerFailureStage = "field_readback" | "query_refresh" | "download_binding";

function controllerFailure(
  code: JackyunControllerFailureCode,
  stage: JackyunControllerFailureStage,
  message: string,
) {
  return Object.assign(new Error(`${code} [${stage}]: ${message}`), { code, stage });
}

export function assertHistoricalDateReadback(
  moduleKey: JackyunModule,
  targetDate: string,
  observedDates: readonly string[],
) {
  if (moduleKey !== "inventory" && moduleKey !== "inventory_age") {
    throw new Error(`历史快照日期读回不适用于模块 ${moduleKey}。`);
  }
  const observedDate = observedDates.length === 1 ? observedDates[0] : "";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(targetDate) || observedDate !== targetDate) {
    const actual = observedDates.length ? observedDates.join(" | ") : "未发现可验证的历史日期控件";
    throw controllerFailure(
      "FIELD_MISMATCH",
      "field_readback",
      `${moduleKey} 历史日期读回不一致：目标 ${targetDate}，实际 ${actual}。`,
    );
  }
  return observedDate;
}

type ControllerState = {
  exportTransport?: typeof jackyunWebSessionTransport | typeof jackyunDirectTransport;
  inspectionOnly?: true;
  version: 1;
  runId: string;
  policyVersion: string;
  updatedAt: string;
  modules: Partial<Record<JackyunModule, ModuleActionState>>;
};

type CliOptions = {
  runId: string;
  snapshotDate: string;
  asOfDate: string;
  eventRoot: string;
  outputRoot: string;
  chromePath?: string;
  profileDirectory?: string;
  debuggingPort?: number;
  headless: boolean;
  launchOnly: boolean;
  checkLoginOnly: boolean;
  authenticateOnly?: boolean;
  signal?: AbortSignal;
  /** Only the explicit n8n export-first protocol uses current queries and deferred imports. */
  exportOnlyModule?: JackyunModule;
  exportFirstBatch?: boolean;
  directHttp?: boolean;
  inspectWebSessionOnly?: boolean;
  /** Isolated calibration only: capture unsigned parameters and abort the final POST. */
  inspectApiPayload?: (module: JackyunModule, payload: { data: Record<string, string>; moduleCode: string; payloadSha256: string }, sourceRows: number) => Promise<void>;
  beforeModule?: (module: JackyunModule) => Promise<void>;
  afterModule?: (module: JackyunModule) => Promise<void>;
  /** Operator diagnosis: query and open menus, then return before any export intent/click. */
  inspectExportMenuOnly?: boolean;
  /** Bound existing task approved for resuming the original run; never a new export. */
  resumeTaskBinding?: JackyunExportTaskBinding;
  webConfirmationRecovery?: { originalExecutionId: string; executionId: string; permitSha256: string };
  httpScopeRecovery?: { originalExecutionId: string; executionId: string; permitSha256: string };
};

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const policyPath = path.join(projectRoot, "config", "jackyun-daily-policy.json");

function shanghaiDate(offsetDays = 0) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date());
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? "";
  const value = new Date(`${part("year")}-${part("month")}-${part("day")}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() + offsetDays);
  return value.toISOString().slice(0, 10);
}

function parseCli(): CliOptions {
  const values = new Map<string, string>();
  // Daily automation must not take over the user's visible desktop. Profile
  // setup is intentionally headed so a human can complete the one-time login.
  let headless = true;
  let launchOnly = false;
  let checkLoginOnly = false;
  let authenticateOnly = false;
  const args = process.argv.slice(2);
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--headless") { headless = true; continue; }
    if (args[index] === "--headed") { headless = false; continue; }
    if (args[index] === "--launch-only") { launchOnly = true; continue; }
    if (args[index] === "--check-login") { checkLoginOnly = true; continue; }
    if (args[index] === "--authenticate-only") { authenticateOnly = true; continue; }
    const next = args[index + 1];
    if (!next || next.startsWith("--")) throw new Error(`参数 ${args[index]} 缺少取值。`);
    values.set(args[index], next);
    index += 1;
  }
  const runId = values.get("--run-id") ?? (launchOnly || checkLoginOnly || authenticateOnly ? `login-${shanghaiDate(0).replace(/-/g, '')}` : undefined);
  if (!runId || !/^[A-Za-z0-9._-]+$/.test(runId)) throw new Error("浏览器 controller 必须提供有效 --run-id。");
  return {
    runId,
    snapshotDate: values.get("--snapshot") ?? shanghaiDate(-1),
    asOfDate: values.get("--as-of") ?? shanghaiDate(-1),
    eventRoot: path.resolve(values.get("--event-dir") ?? path.join(projectRoot, "outputs", "jackyun-browser-events")),
    outputRoot: path.resolve(values.get("--output-root") ?? path.join(projectRoot, "outputs", "jackyun-import-runs")),
    chromePath: values.get("--chrome-path"),
    profileDirectory: values.get("--profile-dir"),
    debuggingPort: values.has("--debug-port") ? Number(values.get("--debug-port")) : undefined,
    headless,
    launchOnly,
    checkLoginOnly,
    authenticateOnly,
  };
}

function eventFileName(index: number, module: JackyunModule) {
  return `${String(index + 1).padStart(2, "0")}-${module}.json`;
}

function jsDocumentsPrelude(urlHints: string[] = []) {
  return `
    const urlHints = ${JSON.stringify(urlHints)};
    const documents = [];
    const visit = (doc, include) => {
      if (include) documents.push(doc);
      for (const frame of doc.querySelectorAll('iframe,frame')) {
        const rect = frame.getBoundingClientRect();
        const style = frame.ownerDocument.defaultView.getComputedStyle(frame);
        if (rect.width <= 100 || rect.height <= 100 || style.visibility === 'hidden' || style.display === 'none') continue;
        const nextInclude = include || urlHints.some((hint) => (frame.src || '').includes(hint));
        try { if (frame.contentDocument) visit(frame.contentDocument, nextInclude); } catch {}
      }
    };
    visit(document, urlHints.length === 0);
    const visible = (el) => {
      const rect = el.getBoundingClientRect();
      const style = el.ownerDocument.defaultView.getComputedStyle(el);
      return rect.width > 2 && rect.height > 2 && style.visibility !== 'hidden' && style.display !== 'none';
    };
    const normalize = (value) => String(value || '').replace(/[（）]/g, (c) => c === '（' ? '(' : ')').replace(/\\s+/g, '').trim();
  `;
}

async function pageText(client: BrowserAutomationClient) {
  return evaluateValue<string>(client, `(() => { ${jsDocumentsPrelude()} return documents.map((doc) => doc.body?.innerText || '').join(String.fromCharCode(10)); })()`);
}

export function productModeState(body: string): "sku" | "goods" | "loading" {
  if (/规格模式[（(]?SKU[）)]?/i.test(body)) return "sku";
  if (/货品模式/i.test(body)) return "goods";
  return "loading";
}

export function extractStockAgeOwnerId(postData: string) {
  let decoded = postData;
  try { decoded = decodeURIComponent(postData.replace(/\+/g, " ")); } catch { /* use the original request body */ }
  const match = decoded.match(/["']?ownerId["']?\s*[:=]\s*["']?(\d{6,32})/i);
  return match?.[1];
}

async function waitForProductModeState(
  client: BrowserAutomationClient,
  timeoutMs: number,
  pollIntervalMs: number,
) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const body = await pageText(client);
    if (isLikelyJackyunLoginPage(body)) throw new Error("当前是吉客云登录页，请先完成登录后再继续自动化。");
    const state = productModeState(body);
    if (state !== "loading") return state;
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
  }
  throw new Error("货品查询页未加载出货品/规格模式控件，已停止导出。");
}

type NestedControlTarget = { controlId: string; inputId?: string };

export async function waitForNestedControls(
  client: BrowserAutomationClient,
  urlFragment: string,
  targets: NestedControlTarget[],
  timeoutMs: number,
  pollIntervalMs: number,
) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const ready = await evaluateValue<boolean>(client, `(() => {
      const urlFragment = ${JSON.stringify(urlFragment)};
      const targets = ${JSON.stringify(targets)};
      let ready = false;
      const visit = (doc) => {
        if (ready) return;
        let href = '';
        try { href = doc.location?.href || ''; } catch {}
        if (href.includes(urlFragment)) {
          const mini = doc.defaultView?.mini;
          ready = targets.every(({ controlId, inputId }) => {
            let control = null;
            try { control = mini?.get?.(controlId) ?? null; } catch {}
            return Boolean(control || (inputId && doc.getElementById(inputId)));
          });
          if (ready) return;
        }
        try {
          for (const frame of doc.querySelectorAll('iframe,frame')) {
            try { if (frame.contentDocument) visit(frame.contentDocument); } catch {}
            if (ready) return;
          }
        } catch {}
      };
      visit(document);
      return ready;
    })()`);
    if (ready) return;
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
  }
  throw new Error(`模块页面控件尚未就绪：${urlFragment} / ${targets.map((item) => item.controlId).join(",")}`);
}

export async function retryOnceAfterAmbiguousBrowserResult<T>(
  action: () => Promise<T>,
  retryDelayMs = 300,
) {
  try {
    return await action();
  } catch (error) {
    // A timed-out page evaluation may still be running inside Chrome. Retrying
    // it would create a second side effect while the first one is unresolved.
    if (error instanceof Error && error.name === "JackyunBrowserTimeoutError") throw error;
    if (retryDelayMs > 0) await new Promise((resolve) => setTimeout(resolve, retryDelayMs));
    return await action();
  }
}

export async function readStockAgeOwnerIdFromPage(client: BrowserAutomationClient) {
  const value = await evaluateValue<string | null>(client, `(() => {
    let ownerId = null;
    const visit = (doc) => {
      if (ownerId) return;
      let href = '';
      try { href = doc.location?.href || ''; } catch {}
      if (/warehouse_age_analysis/.test(href)) {
        const control = doc.defaultView?.mini?.get?.('ownerId');
        const current = control?.getValue ? String(control.getValue()) : String(doc.getElementById('ownerId')?.value || '');
        if (/^\\d{6,32}$/.test(current)) { ownerId = current; return; }
      }
      try {
        for (const frame of doc.querySelectorAll('iframe,frame')) {
          try { if (frame.contentDocument) visit(frame.contentDocument); } catch {}
          if (ownerId) return;
        }
      } catch {}
    };
    visit(document);
    return ownerId;
  })()`);
  return value && /^\d{6,32}$/.test(value) ? value : undefined;
}

export function shouldIssueModuleQuery(
  requiresQuery: boolean,
  state: { status?: string; queryIntentAt?: string },
) {
  return requiresQuery && (!state.queryIntentAt || state.status === "navigated");
}

async function currentUrl(client: BrowserAutomationClient) {
  return evaluateValue<string>(client, "location.href");
}

async function activeContentUrl(client: BrowserAutomationClient) {
  return evaluateValue<string | null>(client, `(() => {
    const frames = Array.from(document.querySelectorAll('iframe,frame'))
      .map((frame) => {
        const rect = frame.getBoundingClientRect();
        const style = getComputedStyle(frame);
        return {
          src: frame.src || '',
          area: rect.width * rect.height,
          visible: rect.width > 100 && rect.height > 100 && style.visibility !== 'hidden' && style.display !== 'none',
        };
      })
      .filter((frame) => frame.visible && frame.src && !/cockpit/i.test(frame.src))
      .sort((a, b) => b.area - a.area);
    return frames[0]?.src ?? null;
  })()`);
}

function moduleUrlHints(moduleKey: JackyunModule) {
  const hints: Partial<Record<JackyunModule, string[]>> = {
    products: ["goods_managet_list"],
    inventory: ["branch_stock"],
    inventory_age: ["warehouse_age_analysis"],
    sales: ["order_detail_list"],
    combos: ["goods_managet_combination"],
  };
  return hints[moduleKey] ?? [];
}

const moduleMenuRoutes: Record<JackyunModule, { direct: string; main: string; fallbacks: string[] }> = {
  products: { direct: ".menu-goods_managet_query", main: ".menu-erpGoodsManaget", fallbacks: [".menu-erpGoodsManage", ".menu-goods-managet", ".menu-goodsManaget"] },
  inventory: { direct: ".menu-branch_stock", main: ".menu-erpStock", fallbacks: [".menu-erpInventory", ".menu-stock", ".menu-inventory"] },
  inventory_age: { direct: ".menu-warehouse_age_analysis", main: ".menu-erpStock", fallbacks: [".menu-erpInventory", ".menu-stock", ".menu-inventory"] },
  sales: { direct: ".menu-order_detail_list", main: ".menu-oms", fallbacks: [".menu-sales", ".menu-order", ".menu-oms-order"] },
  combos: { direct: ".menu-goods_managet_combination", main: ".menu-erpGoodsManaget", fallbacks: [".menu-erpGoodsManage", ".menu-goods-managet", ".menu-goodsManaget"] },
};

const minimalGridExportHeaders = {
  products: ["货品编号", "货品名称", "固定成本价", "基础单位"],
  inventory: ["货品编号", "货品名称", "规格", "单位", "仓库", "固定成本价", "库存数量"],
  inventory_age: ["仓库", "货品编号", "货品名称", "库存数量", "库龄(天)"],
} as const;

const stockAgeExportTemplate = {
  serverName: "birc/birc/excel/v3/report",
  excelType: "stockAgeReport",
  headersJson: {
    enName: [
      "warehouseName", "goodsNo", "goodsName", "skuName", "skuBarcode", "unit", "cateName", "brandName",
      "stockQty", "stockAge", "retailPrice", "minPrice", "wholesalePrice", "memberPrice", "costPrice",
      "fixedCostPrice", "assistInfo", "goodsTypeName", "accountingQuantity", "costAmt",
    ],
    showName: [
      "仓库", "货品编号", "货品名称", "规格", "条码", "单位", "分类", "品牌", "库存数量", "库龄(天)",
      "零售价", "最低售价", "批发价", "会员价", "当前成本价", "固定成本价", "辅助显示", "货品类型",
      "核算数量", "成本金额",
    ],
    permissionsFieldList: [],
  },
  datasource: "",
  typeName: "库龄分析(正式勿删)",
  multiSheet: false,
  exportTotal: "",
} as const;

async function clickMenuSelector(client: BrowserAutomationClient, selector: string) {
  // JackYun's sub-menu <a> items live inside #J-sub-menu-ctn, which is hidden
  // (rect 0x0, offsetParent false) until the parent main menu is hovered.
  // Native el.click() and CDP Input events are silently ignored on hidden
  // elements by the browser.  jQuery's .trigger('click') runs the bound
  // handler directly regardless of visibility, which is the only reliable way
  // to open a module from a cold workbench.  Fall back to native click when
  // jQuery is not present.
  return evaluateValue<boolean>(client, `(() => {
    const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) return false;
    if (typeof window.jQuery === 'function') {
      window.jQuery(el).trigger('click');
      return true;
    }
    el.click();
    return true;
  })()`);
}

async function waitForContentReady(client: BrowserAutomationClient, urlHints: string[], timeoutMs: number, pollMs: number) {
  // Chrome 重启或模块切换后，内容 iframe 的 mini 控件（grid 等）初始化需要时间。
  // 轮询页面内容直到就绪（mini 控件注册或 pageText 有实质内容），避免导出时 grid 不存在。
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const ready = await evaluateValue<boolean>(client, `(() => {
    ${jsDocumentsPrelude(urlHints)}
    for (const doc of documents) {
      const win = doc.defaultView;
      if (win && win.mini) {
        if (typeof win.mini.getComponents === 'function' && win.mini.getComponents().length > 0) return true;
        if (win.jkUtils && win.jkUtils.downloadFile) return true;
      }
    }
    const text = documents.map((d) => d.body?.innerText || '').join(' ');
    if (text.replace(/\\s/g, '').length > 200) return true;
    return false;
  })()`);
    if (ready) return;
    await new Promise((resolve) => setTimeout(resolve, pollMs));
  }
}

async function enterModule(client: BrowserAutomationClient, policy: Policy, moduleKey: JackyunModule) {
  const pageName = policy.modules[moduleKey].pageName;
  // A selected sidebar item only proves that the menu received a click.  It
  // does not prove that the content iframe has navigated (the dashboard can
  // remain visible while the menu item is marked active), so route identity
  // is the only safe no-op/navigation success criterion here.
  const currentContentUrl = await activeContentUrl(client);
  if (currentContentUrl && moduleUrlHints(moduleKey).some((hint) => currentContentUrl.includes(hint))) {
    // 即使 url 命中（模块已打开），Chrome 重启后 grid 控件可能还没初始化。
    // 检查 grid 是否真正就绪（有 exportAllPage 方法的 mini 控件）。
    const hints = moduleUrlHints(moduleKey);
    const gridReady = await evaluateValue<boolean>(client, `(() => {
    ${jsDocumentsPrelude(hints)}
    for (const doc of documents) {
      const win = doc.defaultView;
      if (win && win.mini && typeof win.mini.getComponents === 'function') {
        for (const c of win.mini.getComponents()) {
          if (c && typeof c.exportAllPage === 'function' && typeof c.getColumns === 'function') return true;
        }
      }
    }
    return false;
  })()`);
    if (gridReady) return;
    // grid 不就绪——Chrome 重启后 iframe 恢复了 src 但 JS 没重新执行。
    // 强制重新加载内容 iframe，让 grid 重新初始化。
    await evaluateValue<boolean>(client, `(() => {
    const hints = ${JSON.stringify(hints)};
    const visit = (doc) => {
      for (const f of doc.querySelectorAll('iframe,frame')) {
        if (hints.some(h => (f.src || '').includes(h))) { f.src = f.src; return true; }
        try { if (f.contentDocument && visit(f.contentDocument)) return true; } catch {}
      }
      return false;
    };
    return visit(document);
  })()`);
    // 等 iframe 重新加载 + grid 初始化
    await new Promise((r) => setTimeout(r, 3000));
    await waitForContentReady(client, hints, actionTimeout(policy, moduleKey), fastPoll(policy));
    return;
  }
  const previousContentUrl = await activeContentUrl(client);
  const route = moduleMenuRoutes[moduleKey];
  const selectors = [route.direct, route.main, ...route.fallbacks];
  let clicked = false;
  for (const selector of selectors) {
    if (await clickMenuSelector(client, selector)) {
      clicked = true;
      break;
    }
  }
  if (!clicked) {
    const deadline = Date.now() + actionTimeout(policy, moduleKey);
    while (Date.now() < deadline && !clicked) {
      for (const selector of selectors) {
        if (await clickMenuSelector(client, selector)) {
          clicked = true;
          break;
        }
      }
      if (!clicked) {
        try {
          await clickAnyTextEventually(client, [pageName, ...moduleUrlHints(moduleKey)], fastPoll(policy), fastPoll(policy));
          clicked = true;
        } catch {
          // keep polling selectors until timeout
        }
      }
      if (!clicked) await new Promise((resolve) => setTimeout(resolve, fastPoll(policy)));
    }
  }
  if (!clicked) {
    const body = await pageText(client).catch(() => "");
    throw new Error(`吉客云模块菜单不存在：${pageName}（已尝试 ${selectors.join(", ")}；页面前缀：${body.slice(0, 300)}）`);
  }
  await waitForActiveModule(
    client,
    moduleKey,
    pageName,
    actionTimeout(policy, moduleKey),
    previousContentUrl,
    fastPoll(policy),
  );
  // Chrome 重启后 grid 控件初始化需要时间，等待页面内容就绪再继续
  await waitForContentReady(client, moduleUrlHints(moduleKey), actionTimeout(policy, moduleKey), fastPoll(policy));
}

function moduleTimeout(policy: Policy, moduleKey: JackyunModule) {
  const defaults: Record<JackyunModule, number> = {
    products: 30_000,
    inventory: 45_000,
    inventory_age: 45_000,
    sales: 60_000,
    combos: 45_000,
  };
  return policy.modules[moduleKey].timeoutMs ?? defaults[moduleKey] ?? policy.browser.pageTimeoutMs;
}

function actionTimeout(policy: Policy, moduleKey: JackyunModule) {
  return Math.min(policy.browser.actionTimeoutMs ?? 15_000, moduleTimeout(policy, moduleKey));
}

function exportTimeout(policy: Policy, moduleKey: JackyunModule) {
  if (policy.version === jackyunExportFirstPolicyVersion) return Math.max(actionTimeout(policy, moduleKey), policy.browser.exportTimeoutMs ?? 300_000);
  return Math.max(actionTimeout(policy, moduleKey), Math.min(policy.browser.exportTimeoutMs ?? 60_000, moduleTimeout(policy, moduleKey)));
}

function fastPoll(policy: Policy) {
  return Math.max(100, Math.min(policy.browser.fastPollIntervalMs ?? 200, policy.browser.pollIntervalMs));
}

export async function confirmJackyunComboExport(client: BrowserAutomationClient, urlHints: string[], promptParts: string[],
  button: string, timeoutMs: number, pollMs: number) {
  const read = () => evaluateValue<{ x: number; y: number; ready: boolean } | null>(client, `(() => {
    ${jsDocumentsPrelude(urlHints)}
    const dialogs=documents.flatMap(doc=>Array.from(doc.querySelectorAll('.mini-messagebox')).filter(visible));
    if(!dialogs.length) return null;
    if(dialogs.length!==1 || !${JSON.stringify(promptParts)}.every(part=>dialogs[0].innerText.includes(part))) throw new Error('组合装导出确认弹窗不唯一或内容不符');
    const buttons=Array.from(dialogs[0].querySelectorAll('a.mini-button,button')).filter(el=>visible(el)&&normalize(el.innerText)===normalize(${JSON.stringify(button)}));
    if(buttons.length!==1) throw new Error('组合装导出确认按钮不唯一');
    const el=buttons[0];
    if(el.matches(':disabled,[aria-disabled="true"],.mini-disabled') || getComputedStyle(el).pointerEvents==='none') throw new Error('组合装导出确认按钮不可用');
    const control=el.ownerDocument.defaultView.mini?.get?.(el.id);
    if(control && (control.enabled===false || control.readOnly===true)) throw new Error('组合装导出确认按钮不可用');
    const r=el.getBoundingClientRect();let x=r.left+r.width/2,y=r.top+r.height/2,win=el.ownerDocument.defaultView;
    if(!el.contains(el.ownerDocument.elementFromPoint(x,y))) throw new Error('组合装导出确认按钮被遮挡');
    while(win.frameElement){const f=win.frameElement,b=f.getBoundingClientRect();x+=b.left;y+=b.top;win=win.parent;if(win.document.elementFromPoint(x,y)!==f)throw new Error('组合装导出确认框被遮挡');}
    // MiniUI binds Button.onclick asynchronously after rendering the dialog.
    // Visibility alone can lead to a trusted click before the handler exists.
    return {x,y,ready:typeof el.onclick==='function'};
  })()`);
  const deadline = Date.now() + timeoutMs;
  let target = await read();
  while (!target?.ready && Date.now() < deadline) { await new Promise(resolve => setTimeout(resolve, pollMs)); target = await read(); }
  if (!target?.ready) throw new Error("组合装导出确认弹窗或按钮处理函数未就绪。");
  const point = { x: target.x, y: target.y };
  await client.send("Input.dispatchMouseEvent", { type: "mouseMoved", ...point, button: "none" });
  const afterMove = await read();
  if (!afterMove?.ready || afterMove.x !== target.x || afterMove.y !== target.y) throw new Error("组合装导出确认按钮位置或处理函数变化。");
  const confirmedAt = new Date().toISOString();
  await client.send("Input.dispatchMouseEvent", { type: "mousePressed", ...point, button: "left", clickCount: 1 });
  await client.send("Input.dispatchMouseEvent", { type: "mouseReleased", ...point, button: "left", clickCount: 1 });
  while (Date.now() < deadline) {
    if (!await read()) return confirmedAt;
    await new Promise(resolve => setTimeout(resolve, pollMs));
  }
  throw new Error("组合装确认框未关闭；保留原导出意图，禁止再次点击。");
}

async function clickText(client: BrowserAutomationClient, text: string) {
  const result = await evaluateValue<{ clicked: boolean; actual?: string; x?: number; y?: number }>(client, `(() => {
    ${jsDocumentsPrelude()}
    const wanted = normalize(${JSON.stringify(text)});
    const candidates = documents.flatMap((doc) => Array.from(doc.querySelectorAll('button,a,li,span,div')))
      .filter((el) => visible(el) && normalize(el.innerText || el.textContent) === wanted)
      .sort((a, b) => {
        const rank = (el) => {
          const cls = String(el.className || '');
          const rect = el.getBoundingClientRect();
          if (/tip-button|button-blue|mini-button|x-btn|\\bbtn\\b/i.test(cls)) return 0;
          if (/toolbar-item|menuitem/i.test(cls)) return 1;
          if (/tab/i.test(cls) || (rect.top > 65 && rect.top < 120)) return 8;
          return 5;
        };
        const rankDelta = rank(a) - rank(b);
        if (rankDelta) return rankDelta;
        return a.getBoundingClientRect().width * a.getBoundingClientRect().height - b.getBoundingClientRect().width * b.getBoundingClientRect().height;
      });
    const el = candidates[0];
    if (!el) return { clicked: false };
    el.scrollIntoView({ block: 'center', inline: 'center' });
    const point = (node) => {
      let rect = node.getBoundingClientRect();
      let x = rect.left + rect.width / 2;
      let y = rect.top + rect.height / 2;
      let win = node.ownerDocument.defaultView;
      while (win && win.frameElement) {
        const frameRect = win.frameElement.getBoundingClientRect();
        x += frameRect.left;
        y += frameRect.top;
        win = win.parent;
      }
      return { x, y };
    };
    return { clicked: true, actual: (el.innerText || el.textContent || '').trim(), ...point(el) };
  })()`);
  if (!result.clicked) {
    const body = await pageText(client).catch(() => "");
    throw new Error(`clickable text not found: ${text}; pageText=${body.slice(0, 500)}`);
  }
  await client.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: result.x, y: result.y, button: "none" });
  await client.send("Input.dispatchMouseEvent", { type: "mousePressed", x: result.x, y: result.y, button: "left", clickCount: 1 });
  await client.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: result.x, y: result.y, button: "left", clickCount: 1 });
  return result.actual ?? text;
}

async function clickAnyText(client: BrowserAutomationClient, texts: string[]) {
  let lastError: unknown;
  for (const text of texts) {
    try { return await clickText(client, text); } catch (error) { lastError = error; }
  }
  throw lastError instanceof Error ? lastError : new Error(`页面未找到：${texts.join(" / ")}`);
}

async function clickAnyTextEventually(
  client: BrowserAutomationClient,
  texts: string[],
  timeoutMs: number,
  pollIntervalMs = 100,
) {
  const deadline = Date.now() + timeoutMs;
  let lastError: unknown;
  while (Date.now() < deadline) {
    try {
      return await clickAnyText(client, texts);
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
    }
  }
  throw lastError instanceof Error ? lastError : new Error(`页面未找到：${texts.join(" / ")}`);
}

async function isActiveText(client: BrowserAutomationClient, text: string) {
  return evaluateValue<boolean>(client, `(() => {
    ${jsDocumentsPrelude()}
    const wanted = normalize(${JSON.stringify(text)});
    const elements = documents.flatMap((doc) => Array.from(doc.querySelectorAll('span,div,a,li')))
      .filter((el) => visible(el) && normalize(el.innerText || el.textContent) === wanted);
    return elements.some((el) => {
      let current = el;
      for (let depth = 0; current && depth < 6; depth += 1, current = current.parentElement) {
        if (/active|selected|focus|x-tab-active/i.test(current.className || '') || current.getAttribute('aria-selected') === 'true') return true;
      }
      return false;
    });
  })()`);
}

async function setDateInputs(client: BrowserAutomationClient, values: string[], urlHints: string[] = []) {
  return evaluateValue<string[]>(client, `(() => {
    ${jsDocumentsPrelude(urlHints)}
    const isDateLikeValue = (value) => /^\\d{4}-\\d{2}-\\d{2}(?: \\d{2}:\\d{2}:\\d{2})?$/.test(value || '');
    const resolveControl = (input) => {
      const mini = input.ownerDocument.defaultView?.mini;
      if (!mini?.get) return null;
      const ids = [input.id, String(input.id || '').replace(/\\$text$/, '')].filter(Boolean);
      for (const id of ids) {
        try {
          const control = mini.get(id);
          if (control && typeof control.setValue === 'function' && typeof control.getValue === 'function') return control;
        } catch {}
      }
      return null;
    };
    const dateInputs = documents.flatMap((doc) => Array.from(doc.querySelectorAll('input')))
      .map((input) => ({ input, control: resolveControl(input) }))
      .filter(({ input, control }) => {
        if (!visible(input) || input.disabled || (input.readOnly && !control)) return false;
        const key = ((input.id || '') + ' ' + (input.name || '')).toLowerCase();
        if (/select.*time|timestr|time.*str/.test(key)) return false;
        const semanticText = [
          key,
          input.placeholder || '',
          input.getAttribute('aria-label') || '',
          input.getAttribute('title') || '',
          Array.from(input.labels || []).map((label) => label.textContent || '').join(' '),
          input.parentElement?.previousElementSibling?.textContent || '',
          input.closest('td')?.previousElementSibling?.textContent || '',
        ].join(' ');
        return /日期|快照|统计日|库存日|截止日|业务日|date/i.test(semanticText);
      })
      .sort((a, b) => {
        const rank = ({ input: el }) => {
          const key = ((el.id || '') + ' ' + (el.name || '')).toLowerCase();
          if (/begin|start|from/.test(key)) return 0;
          if (/end|to/.test(key)) return 1;
          if (isDateLikeValue(el.value || '')) return 2;
          return 3;
        };
        return rank(a) - rank(b);
      });
    if (dateInputs.length !== ${values.length}) {
      throw new Error('历史日期控件候选不唯一：期望 ${values.length} 个，实际 ' + dateInputs.length + ' 个');
    }
    const targets = dateInputs;
    const expected = ${JSON.stringify(values)};
    targets.forEach(({ input, control }, index) => {
      if (control) {
        control.setValue(expected[index]);
        if (typeof control.doValueChanged === 'function') control.doValueChanged();
        if (typeof control.onValueChanged === 'function') control.onValueChanged();
      } else if (input.value !== expected[index]) {
        const setter = Object.getOwnPropertyDescriptor(input.ownerDocument.defaultView.HTMLInputElement.prototype, 'value')?.set;
        setter ? setter.call(input, expected[index]) : input.value = expected[index];
      }
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      input.dispatchEvent(new Event('blur', { bubbles: true }));
    });
    const asDateText = (value, input) => {
      if (value instanceof Date && !Number.isNaN(value.getTime())) {
        const pad = (part) => String(part).padStart(2, '0');
        return value.getFullYear() + '-' + pad(value.getMonth() + 1) + '-' + pad(value.getDate());
      }
      const text = String(value ?? '');
      if (/^\\d{4}-\\d{2}-\\d{2}/.test(text)) return text.slice(0, 10);
      return String(input.value || '').slice(0, 10);
    };
    return targets.map(({ input, control }) => asDateText(control ? control.getValue() : input.value, input));
  })()`);
}

export async function rightClickDataRow(client: BrowserAutomationClient, urlHints: string[] = [], singleClick = false) {
  const point = await evaluateValue<{ found: boolean; x?: number; y?: number }>(client, `(() => {
    ${jsDocumentsPrelude(urlHints)}
    const rowScopes = documents.flatMap((doc) => Array.from(doc.querySelectorAll('#grid-goods_managet,#gridOrderDetail,#datagrid,.mini-grid')))
      .filter((el) => visible(el) && el.getBoundingClientRect().width > 500);
    const searchRoots = rowScopes.length ? rowScopes : documents;
    const rows = searchRoots.flatMap((root) => Array.from(root.querySelectorAll(${JSON.stringify(singleClick
      ? ".mini-grid-row,.x-grid-item,.x-grid-row" : ".mini-grid-row,.x-grid-item,.x-grid-row,[role=row],tbody tr")})))
      .filter((el) => visible(el) && el.getBoundingClientRect().width > 500 && (el.innerText || '').trim().length > 5)
      .sort((a, b) => {
        const rank = (el) => el.closest?.('#grid-goods_managet') ? -1 : (/mini-grid-row|x-grid-row|x-grid-item/i.test(String(el.className || '')) ? 0 : 1);
        const rankDelta = rank(a) - rank(b);
        if (rankDelta) return rankDelta;
        return b.getBoundingClientRect().width - a.getBoundingClientRect().width;
      });
    const el = rows[0];
    if (!el) return { found: false };
    const rect = el.getBoundingClientRect();
    const docWidth = el.ownerDocument.documentElement.clientWidth || 1200;
    let x = Math.max(rect.left + Math.min(200, rect.width / 2), 350);
    x = Math.min(x, rect.right - 20, docWidth - 20);
    if (x <= rect.left) x = rect.left + rect.width / 2;
    let y = rect.top + rect.height / 2;
    let win = el.ownerDocument.defaultView;
    while (win && win.frameElement) {
      const frameRect = win.frameElement.getBoundingClientRect();
      x += frameRect.left;
      y += frameRect.top;
      win = win.parent;
    }
    return { found: true, x, y };
  })()`);
  if (!point.found) throw new Error("没有找到可右键导出的真实数据行。");
  await client.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: point.x, y: point.y, button: "none" });
  await client.send("Input.dispatchMouseEvent", { type: "mousePressed", x: point.x, y: point.y, button: "right", buttons: 2, clickCount: 1 });
  await client.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: point.x, y: point.y, button: "right", buttons: 0, clickCount: 1 });
  if (singleClick) return;
  await evaluateValue<boolean>(client, `(() => {
    ${jsDocumentsPrelude(urlHints)}
    const rowScopes = documents.flatMap((doc) => Array.from(doc.querySelectorAll('#grid-goods_managet,#gridOrderDetail,#datagrid,.mini-grid')))
      .filter((el) => visible(el) && el.getBoundingClientRect().width > 500);
    const searchRoots = rowScopes.length ? rowScopes : documents;
    const rows = searchRoots.flatMap((root) => Array.from(root.querySelectorAll('.mini-grid-row,.x-grid-item,.x-grid-row,[role=row],tbody tr')))
      .filter((el) => visible(el) && el.getBoundingClientRect().width > 500 && (el.innerText || '').trim().length > 5)
      .sort((a, b) => {
        const rank = (el) => el.closest?.('#grid-goods_managet') ? -1 : (/mini-grid-row|x-grid-row|x-grid-item/i.test(String(el.className || '')) ? 0 : 1);
        const rankDelta = rank(a) - rank(b);
        if (rankDelta) return rankDelta;
        return b.getBoundingClientRect().width - a.getBoundingClientRect().width;
      });
    const el = rows[0];
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const x = Math.min(Math.max(rect.left + Math.min(200, rect.width / 2), rect.left + 20), rect.right - 20);
    const y = rect.top + rect.height / 2;
    const view = el.ownerDocument.defaultView;
    for (const type of ['mousedown', 'mouseup', 'contextmenu']) {
      el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view, button: 2, buttons: type === 'mouseup' ? 0 : 2, clientX: x, clientY: y }));
    }
    return true;
  })()`);
}

async function triggerMinimalGridExportAllPage(
  client: BrowserAutomationClient,
  urlHints: string[],
  gridIds: string[],
  requiredHeaders: readonly string[],
) {
  await installDownloadFileHook(client, urlHints);
  return evaluateValue<boolean>(client, `(async () => {
    ${jsDocumentsPrelude(urlHints)}
    const gridIds = ${JSON.stringify(gridIds)};
    const requiredHeaders = ${JSON.stringify(requiredHeaders)};
    const findItem = (items, name) => {
      for (const item of items || []) {
        if (item?.name === name) return item;
        const child = findItem(item?.children, name);
        if (child) return child;
      }
      return null;
    };
    for (const doc of documents) {
      const win = doc.defaultView;
      const mini = win?.mini;
      if (!mini?.get) continue;
      const candidates = [];
      for (const id of gridIds) {
        const grid = mini.get(id);
        if (grid) candidates.push(grid);
      }
      if (typeof mini.getComponents === 'function') {
        try { candidates.push(...mini.getComponents()); } catch {}
      }
      for (const bucket of [mini.components, mini._components]) {
        if (bucket && typeof bucket === 'object') {
          try { candidates.push(...Object.values(bucket)); } catch {}
        }
      }
      for (const el of doc.querySelectorAll('[id]')) {
        try {
          const component = mini.get(el.id);
          if (component) candidates.push(component);
        } catch {}
      }
      const seen = new Set();
      const grids = candidates
        .filter((grid) => grid && typeof grid.exportAllPage === 'function' && typeof grid.getColumns === 'function' && !seen.has(grid) && seen.add(grid))
        .sort((a, b) => {
          const score = (grid) => {
            const total = Number(grid.totalCount ?? (typeof grid.getTotalCount === 'function' ? grid.getTotalCount() : 0) ?? 0);
            const rows = Number(typeof grid.getData === 'function' ? (grid.getData()?.length ?? 0) : 0);
            return Math.max(total, rows);
          };
          return score(b) - score(a);
        });
      for (const grid of grids) {
        const columns = grid.getColumns();
        const selected = requiredHeaders.map((header) => columns.find((column) => column.header === header)).filter(Boolean);
        const missing = requiredHeaders.filter((header) => !selected.some((column) => column.header === header));
        if (missing.length) continue;
        const item = findItem(grid.contextMenuItems || grid.customMenuItems || [], 'exportAll');
        // v4 的 grid.exportAllPage() 会异步走完整导出流程（startExcelExport → downloadFile(OSS)）。
        // 旧版通过 hook startExcelExport 捕获 config 再重新导出，但 v4 下 hook 会中断异步流程
        // 且 100ms 等待不够（v4 异步导出需要数秒）。不 hook，让 v4 自然完成导出。
        // installDownloadFileHook 已捕获 OSS URL，通用流程负责等本地下载。
        try {
          if (item && typeof item.click === 'function') await Promise.resolve(item.click.call(item, grid));
          else { const r = grid.exportAllPage(grid); if (r && typeof r.then === 'function') await r; }
          // v4 的 exportAllPage 异步走完整导出流程（performValidation → exportOrder →
          // startExcelExport → downloadFile(OSS)），需要数秒完成。等 8 秒让流程启动。
          await new Promise((resolve) => setTimeout(resolve, 8000));
        } catch {}
        return true;
      }
    }
    return false;
  })()`);
}

async function triggerStockAgePayloadExport(client: BrowserAutomationClient, ownerId: string) {
  if (!/^\d{6,32}$/.test(ownerId)) throw new Error("库龄导出货主标识无效，已停止导出。");
  await installDownloadFileHook(client, moduleUrlHints("inventory_age"));
  const payload = {
    ...stockAgeExportTemplate,
    conditionJson: {
      ownerId,
      warehouseId: "",
      skuId: "",
      brandId: "",
      cateId: "",
      warehouseCompanyId: "",
      stockAgeMin: "",
      stockAgeMax: "",
      goodsTypeCodes: "",
      includeBlockWarehouse: "0",
      pageIndex: 0,
      pageSize: 1000,
      sortField: "",
      sortOrder: "",
      cols: [...stockAgeExportTemplate.headersJson.enName],
      orderIds: [],
      version: "2.0",
    },
  };
  return evaluateValue<boolean>(client, `(async () => {
    const payload = ${JSON.stringify(payload)};
    let api = globalThis.jkUtils;
    if (!api) {
      const visit = (doc) => {
        try {
          if (doc.defaultView?.jkUtils) return doc.defaultView.jkUtils;
          for (const frame of doc.querySelectorAll('iframe,frame')) {
            try {
              const found = frame.contentDocument && visit(frame.contentDocument);
              if (found) return found;
            } catch {}
          }
        } catch {}
        return null;
      };
      api = visit(document);
    }
    if (!api || typeof api.jkAjax !== 'function') throw new Error('stock age export API unavailable');
    await api.jkAjax({
      url: '/jkyun/excel-service/manager/validateExcelExport',
      type: 'post',
      data: { ...payload },
    });
    await api.jkAjax({
      url: '/jkyun/excel-service/manager/startExcelExport',
      type: 'post',
      data: { ...payload, isSyn: 'false' },
      timeout: 120000,
    });
    return true;
  })()`);
}

async function triggerComboDetailExportAllPage(client: BrowserAutomationClient, urlHints: string[]) {
  await installDownloadFileHook(client, urlHints);
  return evaluateValue<boolean>(client, `(() => {
    ${jsDocumentsPrelude(urlHints)}
    const findItem = (items, name) => {
      for (const item of items || []) {
        if (item?.name === name) return item;
        const child = findItem(item?.children, name);
        if (child) return child;
      }
      return null;
    };
    for (const doc of documents) {
      const grid = doc.defaultView?.mini?.get?.('grid-goods_managet');
      const item = findItem(grid?.contextMenuItems || grid?.customMenuItems || [], 'exportAll_2');
      if (item && typeof item.click === 'function') {
        item.click.call(item);
        return true;
      }
    }
    return false;
  })()`);
}

async function triggerSalesMinimalExportAllPage(client: BrowserAutomationClient, urlHints: string[]) {
  // v4 的 grid.exportAllPage() 会异步走完整导出流程（performValidation → exportOrder →
  // startExcelExport → jkUtils.downloadFile(OSS)）。旧版通过 hook startExcelExport 拦截
  // 配置再重新导出，但 v4 下 hook 会中断异步流程导致文件不完整（只导出1列）。
  // 正确做法：只 bypass 校验（performValidation/valideIsWithSkuImg），不 hook
  // startExcelExport，让 v4 自然完成导出。installDownloadFileHook 已捕获 OSS URL，
  // 通用流程只接受 hook/CDP 捕获的本轮 OSS URL，再下载到 run/module 专属目录。
  await installDownloadFileHook(client, urlHints);
  return evaluateValue<boolean>(client, `(async () => {
    ${jsDocumentsPrelude(urlHints)}
    for (const doc of documents) {
      const win = doc.defaultView;
      const grid = win?.mini?.get?.('gridOrderDetail');
      const orderExport = win?.omsUtils?.orderExportV2;
      if (!grid || !orderExport || typeof grid.exportAllPage !== 'function') continue;
      const originalPerformValidation = orderExport.performValidation;
      const originalValideIsWithSkuImg = orderExport.valideIsWithSkuImg;
      orderExport.performValidation = function(_validation, _data, callback) { callback(); };
      orderExport.valideIsWithSkuImg = function(_setting, _rows, callback) { callback(false); };
      try {
        const r = grid.exportAllPage();
        if (r && typeof r.then === 'function') await r;
        // 给 v4 异步导出流程一点时间启动（startExcelExport → downloadFile）
        await new Promise((resolve) => setTimeout(resolve, 500));
      } finally {
        orderExport.performValidation = originalPerformValidation;
        orderExport.valideIsWithSkuImg = originalValideIsWithSkuImg;
      }
      return true;
    }
    return false;
  })()`);
}

async function installDownloadFileHook(client: BrowserAutomationClient, urlHints: string[]) {
  await evaluateValue<boolean>(client, `(() => {
    ${jsDocumentsPrelude(urlHints)}
    let installed = false;
    for (const doc of documents) {
      const win = doc.defaultView;
      const jkUtils = win?.jkUtils;
      if (!jkUtils || typeof jkUtils.downloadFile !== 'function') continue;
      if (!win.__codexDownloadFileHookInstalled) {
        const original = jkUtils.downloadFile;
        win.__codexDownloadFileHookInstalled = true;
        jkUtils.downloadFile = function(url) {
          win.__codexLastDownloadFileUrl = String(url || '');
          return original.apply(this, arguments);
        };
      }
      win.__codexLastDownloadFileUrl = null;
      installed = true;
    }
    return installed;
  })()`);
}

async function findCurrentDownloadEvidence(
  client: BrowserAutomationClient,
  urlHints: string[],
  captured: () => CapturedDownloadUrlEvidence | undefined,
  timeoutMs: number,
  pollIntervalMs = 200,
) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const direct = captured();
    if (direct) return direct;
    const value = await evaluateValue<string | null>(client, `(() => {
      ${jsDocumentsPrelude(urlHints)}
      for (const doc of documents) {
        const win = doc.defaultView;
        if (win?.__codexLastDownloadFileUrl) return win.__codexLastDownloadFileUrl;
      }
      return null;
    })()`, Math.min(10_000, timeoutMs));
    if (value) {
      return {
        url: value,
        observedAt: new Date().toISOString(),
        source: "page_download_hook" as const,
      };
    }
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
  }
  return undefined;
}

export function readRowCountTextState(text: string) {
  const approximate = /共\s*[\d,]+\+\s*条/.test(text) && text.includes("查看总数");
  const exactCounts = [...text.matchAll(/共\s*([\d,]+)\s*条/g)]
    .map((match) => Number(match[1].replace(/,/g, "")))
    .filter(Number.isSafeInteger);
  return { approximate, exactCounts };
}

export type QueryRefreshTracking = {
  token: string;
  module: JackyunModule;
  queryIntentAt: string;
  requiredDate?: string;
  currentCapture?: boolean;
  pageProbeArmed: boolean;
  pageStartedAt?: string;
  pageCompletedAt?: string;
  networkStartedAt?: string;
  networkCompletedAt?: string;
  networkFailedAt?: string;
  dispose?: () => void;
};

type TableReadbackSnapshot = {
  text: string;
  gridTotals: number[];
  anyGridLoading: boolean;
  probes: Array<{
    token: string;
    startedAt?: number | null;
    completedAt?: number | null;
    failedAt?: number | null;
  }>;
};

export function isModuleQueryRefreshRequest(
  params: Record<string, unknown>,
  moduleKey: JackyunModule,
  requiredDate?: string,
) {
  const request = params.request as { url?: unknown; postData?: unknown } | undefined;
  const resourceType = String(params.type ?? "").toLowerCase();
  if (resourceType && resourceType !== "xhr" && resourceType !== "fetch") return false;
  const rawContext = [request?.url, request?.postData]
    .map((value) => String(value ?? "").toLowerCase())
    .join(" ");
  let decodedContext = rawContext;
  try { decodedContext = decodeURIComponent(rawContext); } catch { /* malformed payload: use raw text */ }
  const context = `${rawContext} ${decodedContext}`;
  const modulePatterns: Record<JackyunModule, RegExp> = {
    products: /goods_managet_list|goods.*manage/,
    inventory: /branch_stock|branchstock|stock.*query/,
    inventory_age: /warehouse_age_analysis|warehouse.*age|stock.*age|birc/,
    sales: /order_detail_list|order.*detail/,
    combos: /goods_managet_combination|goods.*combination/,
  };
  // Current SKU inventory queries use this gateway route, without either a
  // branch_stock page name or a "query" verb. Keep the route exact so totals,
  // auxiliary grids and export requests cannot stand in for the main query.
  let currentInventoryQuery = false;
  try {
    const url = new URL(String(request?.url ?? ""));
    currentInventoryQuery = url.pathname.toLowerCase() === "/jkyun/erp-stock/warehousestock/stockskulist";
  } catch { /* existing module matching still handles non-URL test contexts */ }
  if (currentInventoryQuery && moduleKey !== "inventory") return false;
  const matchesModule = currentInventoryQuery || moduleUrlHints(moduleKey).some((hint) => context.includes(hint.toLowerCase()))
    || modulePatterns[moduleKey].test(context);
  if (!matchesModule) return false;
  if (!requiredDate) return true;
  const dateTokens = [requiredDate, requiredDate.replace(/-/g, "/"), requiredDate.replace(/-/g, "")];
  return dateTokens.some((token) => context.includes(token));
}

export async function armQueryRefreshTracking(
  client: BrowserAutomationClient,
  moduleKey: JackyunModule,
  queryIntentAt: string,
  urlHints: string[],
  requiredDate?: string,
  currentCapture = false,
) {
  const token = `query-${moduleKey}-${randomUUID()}`;
  const tracking: QueryRefreshTracking = {
    token,
    module: moduleKey,
    queryIntentAt,
    requiredDate,
    currentCapture,
    pageProbeArmed: false,
  };
  const pendingRequestIds = new Set<string>();
  const responseStatusByRequestId = new Map<string, number>();
  const unsubscribeRequest = client.on("Network.requestWillBeSent", (params) => {
    if (!isModuleQueryRefreshRequest(params, moduleKey, requiredDate)) return;
    const requestId = String(params.requestId ?? "");
    if (!requestId) return;
    pendingRequestIds.add(requestId);
    delete tracking.networkCompletedAt;
    tracking.networkStartedAt ??= new Date().toISOString();
  });
  const unsubscribeResponse = client.on("Network.responseReceived", (params) => {
    const requestId = String(params.requestId ?? "");
    if (!pendingRequestIds.has(requestId)) return;
    const response = params.response && typeof params.response === "object"
      ? params.response as Record<string, unknown>
      : null;
    const status = Number(response?.status);
    responseStatusByRequestId.set(requestId, status);
    if (!Number.isFinite(status) || status < 200 || status >= 300) {
      tracking.networkFailedAt ??= new Date().toISOString();
      delete tracking.networkCompletedAt;
    }
  });
  const unsubscribeFinished = client.on("Network.loadingFinished", (params) => {
    const requestId = String(params.requestId ?? "");
    if (!pendingRequestIds.delete(requestId)) return;
    const status = responseStatusByRequestId.get(requestId);
    responseStatusByRequestId.delete(requestId);
    if (!Number.isFinite(status) || Number(status) < 200 || Number(status) >= 300) {
      tracking.networkFailedAt ??= new Date().toISOString();
      delete tracking.networkCompletedAt;
      return;
    }
    if (!pendingRequestIds.size && !tracking.networkFailedAt) {
      tracking.networkCompletedAt = new Date().toISOString();
    }
  });
  const unsubscribeFailed = client.on("Network.loadingFailed", (params) => {
    const requestId = String(params.requestId ?? "");
    if (!pendingRequestIds.delete(requestId)) return;
    responseStatusByRequestId.delete(requestId);
    delete tracking.networkCompletedAt;
    tracking.networkFailedAt ??= new Date().toISOString();
  });
  tracking.dispose = () => {
    unsubscribeRequest();
    unsubscribeResponse();
    unsubscribeFinished();
    unsubscribeFailed();
  };

  const hookedGrids = await evaluateValue<number>(client, `(() => {
    ${jsDocumentsPrelude(urlHints)}
    const token = ${JSON.stringify(token)};
    let hooked = 0;
    for (const doc of documents) {
      const win = doc.defaultView;
      const mini = win?.mini;
      if (!win || !mini) continue;
      const state = { token, armedAt: Date.now(), startedAt: null, completedAt: null, failedAt: null, sawLoading: false };
      win.__codexQueryRefreshProbe = state;
      const candidates = [];
      for (const id of ['gridOrderDetail', 'grid-goods_managet', 'grid', 'mainGrid', 'datagrid']) {
        try { const grid = mini.get?.(id); if (grid) candidates.push(grid); } catch {}
      }
      if (typeof mini.getComponents === 'function') {
        try { candidates.push(...mini.getComponents()); } catch {}
      }
      for (const bucket of [mini.components, mini._components]) {
        if (bucket && typeof bucket === 'object') {
          try { candidates.push(...Object.values(bucket)); } catch {}
        }
      }
      const seen = new Set();
      const grids = candidates.filter((grid) => {
        if (!grid || seen.has(grid)) return false;
        seen.add(grid);
        return typeof grid.on === 'function'
          && (typeof grid.getTotalCount === 'function' || typeof grid.getData === 'function' || 'totalCount' in grid);
      });
      for (const grid of grids) {
        const current = () => win.__codexQueryRefreshProbe?.token === token ? win.__codexQueryRefreshProbe : null;
        const begin = () => {
          const probe = current();
          if (!probe) return;
          probe.startedAt ??= Date.now();
          probe.sawLoading = true;
        };
        const complete = () => {
          const probe = current();
          if (!probe?.startedAt) return;
          probe.completedAt ??= Date.now();
        };
        const fail = () => {
          const probe = current();
          if (!probe) return;
          probe.failedAt ??= Date.now();
        };
        try {
          grid.on('beforeload', begin);
          grid.on('preload', begin);
          grid.on('load', complete);
          grid.on('loaderror', fail);
          hooked += 1;
        } catch {}
      }
    }
    return hooked;
  })()`).catch(() => 0);
  tracking.pageProbeArmed = hookedGrids > 0;
  return tracking;
}

function completedQueryRefreshEvidence(tracking: QueryRefreshTracking) {
  const intentMs = Date.parse(tracking.queryIntentAt);
  const requiresDateBoundNetwork = tracking.module === "inventory" || tracking.module === "inventory_age";
  const validSequence = (startedAt?: string, completedAt?: string) => {
    const startedMs = Date.parse(startedAt ?? "");
    const completedMs = Date.parse(completedAt ?? "");
    return Number.isFinite(intentMs)
      && Number.isFinite(startedMs)
      && Number.isFinite(completedMs)
      && startedMs >= intentMs
      && completedMs >= startedMs;
  };
  if ((!requiresDateBoundNetwork || tracking.currentCapture || /^\d{4}-\d{2}-\d{2}$/.test(tracking.requiredDate ?? ""))
    && validSequence(tracking.networkStartedAt, tracking.networkCompletedAt)) {
    return { source: "module_network_request" as const, completedAt: tracking.networkCompletedAt! };
  }
  if (!requiresDateBoundNetwork && validSequence(tracking.pageStartedAt, tracking.pageCompletedAt)) {
    return { source: "miniui_grid_lifecycle" as const, completedAt: tracking.pageCompletedAt! };
  }
  return undefined;
}

async function readTableReadbackSnapshot(
  client: BrowserAutomationClient,
  urlHints: string[],
  queryToken?: string,
) {
  return evaluateValue<TableReadbackSnapshot>(client, `(() => {
    ${jsDocumentsPrelude(urlHints)}
    const queryToken = ${JSON.stringify(queryToken ?? "")};
    const gridTotals = [];
    const probes = [];
    let anyGridLoading = false;
    for (const doc of documents) {
      const win = doc.defaultView;
      const mini = win?.mini;
      const candidates = [];
      if (mini) {
        for (const id of ['gridOrderDetail', 'grid-goods_managet', 'grid', 'mainGrid', 'datagrid']) {
          try { const grid = mini.get?.(id); if (grid) candidates.push(grid); } catch {}
        }
        if (typeof mini.getComponents === 'function') {
          try { candidates.push(...mini.getComponents()); } catch {}
        }
        for (const bucket of [mini.components, mini._components]) {
          if (bucket && typeof bucket === 'object') {
            try { candidates.push(...Object.values(bucket)); } catch {}
          }
        }
      }
      const seen = new Set();
      const grids = candidates.filter((grid) => {
        if (!grid || seen.has(grid)) return false;
        seen.add(grid);
        return typeof grid.getTotalCount === 'function' || typeof grid.getData === 'function' || 'totalCount' in grid;
      });
      let documentGridLoading = false;
      for (const grid of grids) {
        let loading = false;
        try { loading = typeof grid.isLoading === 'function' ? Boolean(grid.isLoading()) : grid.isLoading === true; } catch {}
        if (loading) {
          documentGridLoading = true;
          anyGridLoading = true;
          continue;
        }
        let total = Number.NaN;
        try { total = Number(grid.totalCount ?? (typeof grid.getTotalCount === 'function' ? grid.getTotalCount() : Number.NaN)); } catch {}
        if (Number.isSafeInteger(total) && total >= 0) gridTotals.push(total);
      }
      const probe = win?.__codexQueryRefreshProbe;
      if (probe?.token === queryToken) {
        if (documentGridLoading) {
          probe.startedAt ??= Date.now();
          probe.sawLoading = true;
        } else if (probe.sawLoading && probe.startedAt && !probe.completedAt) {
          probe.completedAt = Date.now();
        }
        probes.push({
          token: probe.token,
          startedAt: probe.startedAt,
          completedAt: probe.completedAt,
          failedAt: probe.failedAt,
        });
      }
    }
    return {
      text: documents.map((doc) => doc.body?.innerText || '').join(String.fromCharCode(10)),
      gridTotals,
      anyGridLoading,
      probes,
    };
  })()`);
}

export async function stableRowCount(
  client: BrowserAutomationClient,
  policy: Policy,
  urlHints: string[] = [],
  queryRefresh?: QueryRefreshTracking,
) {
  let previous: number | null = null;
  let stable = 0;
  let observedZero = false;
  let requestedExactTotal = false;
  let refreshAcknowledged = !queryRefresh;
  const deadline = Date.now() + (policy.browser.tableStableTimeoutMs ?? policy.browser.pageTimeoutMs);
  while (Date.now() < deadline) {
    const snapshot = await readTableReadbackSnapshot(client, urlHints, queryRefresh?.token);
    if (queryRefresh) {
      const pageEvidence = snapshot.probes.find((probe) => probe.token === queryRefresh.token && probe.startedAt && probe.completedAt);
      if (pageEvidence?.startedAt && pageEvidence.completedAt) {
        queryRefresh.pageStartedAt = new Date(pageEvidence.startedAt).toISOString();
        queryRefresh.pageCompletedAt = new Date(pageEvidence.completedAt).toISOString();
      }
      const completed = completedQueryRefreshEvidence(queryRefresh);
      if (!completed) {
        previous = null;
        stable = 0;
        await new Promise((resolve) => setTimeout(resolve, fastPoll(policy)));
        continue;
      }
      if (!refreshAcknowledged) {
        refreshAcknowledged = true;
        previous = null;
        stable = 0;
        await new Promise((resolve) => setTimeout(resolve, fastPoll(policy)));
        continue;
      }
    }
    if (snapshot.anyGridLoading) {
      previous = null;
      stable = 0;
      await new Promise((resolve) => setTimeout(resolve, fastPoll(policy)));
      continue;
    }
    const textState = readRowCountTextState(snapshot.text);
    if (textState.approximate) {
      if (!requestedExactTotal) {
        requestedExactTotal = true;
        await clickText(client, "查看总数");
      }
      await new Promise((resolve) => setTimeout(resolve, Math.max(500, fastPoll(policy))));
      continue;
    }
    const counts = textState.exactCounts;
    const gridTotal = counts.length || !snapshot.gridTotals.length ? null : Math.max(...snapshot.gridTotals);
    const count = counts.length ? Math.max(...counts) : gridTotal ?? 0;
    if ((counts.length || gridTotal !== null) && count === 0) observedZero = true;
    if (count > 0 && count === previous) stable += 1;
    else stable = 1;
    previous = count || null;
    if (previous && stable >= policy.browser.stableSamples) return previous;
    await new Promise((resolve) => setTimeout(resolve, fastPoll(policy)));
  }
  if (queryRefresh && !completedQueryRefreshEvidence(queryRefresh)) {
    const requiredRefresh = queryRefresh.module === "inventory" || queryRefresh.module === "inventory_age"
      ? queryRefresh.currentCapture ? "当前采集的模块网络请求" : `包含目标日期 ${queryRefresh.requiredDate ?? "缺失"} 的模块网络请求`
      : "目标网格加载或模块网络请求";
    throw controllerFailure(
      "TABLE_TIMEOUT",
      "query_refresh",
      `${queryRefresh.module} 未观测到本轮查询触发的${requiredRefresh}完成；拒绝把旧表格当作新结果。`,
    );
  }
  const error = new Error(observedZero ? "页面查询明确返回 0 行，已停止导出。" : "页面总行数在规定时间内未稳定。");
  Object.assign(error, { code: observedZero ? "zero_rows" : "unstable" });
  throw error;
}

export function shouldRetryZeroRowQuery(moduleState: ModuleActionState) {
  return moduleState.status === "queried"
    && moduleState.tableReadbackFailure?.code === "zero_rows"
    && (moduleState.queryRetryCount ?? 0) === 0
    && Boolean(moduleState.queryIntentAt)
    && !moduleState.tableStableAt
    && !moduleState.expectedSourceRows
    && !moduleState.exportIntentAt
    && !moduleState.filePath;
}

function isLikelyJackyunLoginPage(body: string) {
  return /忘记密码|为企业注册吉客号|忘记吉客号|登录|验证码/i.test(body)
    && !/货品查询|分仓库存查询|库龄分析|销售单明细账|组合装查询|主菜单|一级菜单/.test(body);
}

export type JackyunSessionStatus = "authenticated" | "login_required" | "unknown";

export function classifyJackyunSession(body: string): JackyunSessionStatus {
  if (isLikelyJackyunLoginPage(body)) return "login_required";
  if (/货品查询/.test(body)
    && /分仓库存查询|库龄分析|销售单明细账|组合装查询|主菜单|一级菜单/.test(body)) {
    return "authenticated";
  }
  return "unknown";
}

export type SavedCredentialLoginResult = {
  attempted: boolean;
  submitted: boolean;
  reason: "submitted" | "login_form_missing" | "challenge_present" | "saved_credentials_missing" | "login_control_missing";
};

/**
 * Submit only credentials that Chrome itself has autofilled in the dedicated
 * profile. The controller never reads field values and never accepts secrets
 * from files, environment variables, arguments, or logs.
 */
export async function autoLoginWithSavedBrowserCredentials(
  client: BrowserAutomationClient,
  waitMs = 5_000,
): Promise<SavedCredentialLoginResult> {
  return evaluateValue<SavedCredentialLoginResult>(client, `(async () => {
    const deadline = Date.now() + ${Math.max(0, waitMs)};
    const visible = (el) => {
      const rect = el.getBoundingClientRect();
      const style = el.ownerDocument.defaultView.getComputedStyle(el);
      return rect.width > 2 && rect.height > 2 && style.visibility !== 'hidden' && style.display !== 'none';
    };
    const documents = () => {
      const found = [];
      const visit = (doc) => {
        found.push(doc);
        for (const frame of doc.querySelectorAll('iframe,frame')) {
          if (!visible(frame)) continue;
          try { if (frame.contentDocument) visit(frame.contentDocument); } catch {}
        }
      };
      visit(document);
      return found;
    };
    const fieldName = (input) => [
      input.type,
      input.name,
      input.id,
      input.getAttribute('autocomplete'),
      input.getAttribute('placeholder'),
      input.getAttribute('aria-label'),
    ].filter(Boolean).join(' ');
    const hasChallenge = (doc) => {
      const text = String(doc.body?.innerText || '');
      if (/安全验证|人机验证|短信验证码|动态验证码|请.{0,8}(?:滑动|拖动)/.test(text)) return true;
      if (Array.from(doc.querySelectorAll('iframe')).some((frame) => visible(frame) && /captcha|verify|challenge/i.test(frame.src || ''))) return true;
      return Array.from(doc.querySelectorAll('input')).some((input) => visible(input)
        && /captcha|verify|challenge|验证码|校验码|动态码/i.test(fieldName(input)));
    };
    let focused = false;
    for (;;) {
      const docs = documents();
      const loginDoc = docs.find((doc) => Array.from(doc.querySelectorAll('input')).some((input) => visible(input)
        && (String(input.type || '').toLowerCase() === 'password' || /pass|密码/i.test(fieldName(input)))));
      if (!loginDoc) return { attempted: false, submitted: false, reason: 'login_form_missing' };
      if (docs.some(hasChallenge)) return { attempted: false, submitted: false, reason: 'challenge_present' };
      const inputs = Array.from(loginDoc.querySelectorAll('input')).filter(visible);
      const password = inputs.find((input) => String(input.type || '').toLowerCase() === 'password' || /pass|密码/i.test(fieldName(input)));
      const account = inputs.find((input) => input !== password && /user|account|login|phone|mobile|name|账号|手机|吉客号/i.test(fieldName(input)))
        || inputs.find((input) => input !== password && ['text', 'tel', 'email'].includes(String(input.type || 'text').toLowerCase()));
      if (!account || !password) return { attempted: false, submitted: false, reason: 'login_form_missing' };
      if (!focused) {
        focused = true;
        account.focus();
        password.focus();
        account.focus();
      }
      const accountAutofilled = (() => { try { return account.matches(':-webkit-autofill'); } catch { return false; } })();
      const passwordAutofilled = (() => { try { return password.matches(':-webkit-autofill'); } catch { return false; } })();
      if (accountAutofilled && passwordAutofilled) {
        for (const input of [account, password]) {
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.dispatchEvent(new Event('change', { bubbles: true }));
        }
        const candidates = Array.from(loginDoc.querySelectorAll('button,input[type="submit"],input[type="button"],[role="button"],a,div,span'))
          .filter(visible)
          .map((element) => ({
            element,
            text: String(element.tagName === 'INPUT' ? element.getAttribute('value') || '' : element.textContent || '').replace(/\\s+/g, '').trim(),
            area: element.getBoundingClientRect().width * element.getBoundingClientRect().height,
          }))
          .filter((item) => ['登录', '立即登录'].includes(item.text))
          .sort((left, right) => left.area - right.area);
        const control = candidates[0]?.element;
        if (!control || control.hasAttribute('disabled') || control.getAttribute('aria-disabled') === 'true') {
          return { attempted: true, submitted: false, reason: 'login_control_missing' };
        }
        control.click();
        return { attempted: true, submitted: true, reason: 'submitted' };
      }
      if (Date.now() >= deadline) return { attempted: false, submitted: false, reason: 'saved_credentials_missing' };
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  })()`, waitMs + 2_000);
}

export async function getJackyunSessionStatus(port: number): Promise<JackyunSessionStatus> {
  const target = await connectJackyunTarget(port).catch(() => null);
  if (!target) return "unknown";
  try {
    const textStatus = classifyJackyunSession(await pageText(target.client));
    if (textStatus !== "unknown") return textStatus;
    const domStatus = await evaluateValue<{ hasLoginForm: boolean; hasMenuShell: boolean }>(target.client, `(() => {
      const visible = (el) => {
        const rect = el.getBoundingClientRect();
        const style = el.ownerDocument.defaultView.getComputedStyle(el);
        return rect.width > 2 && rect.height > 2 && style.visibility !== 'hidden' && style.display !== 'none';
      };
      const docs = [];
      const visit = (doc) => {
        docs.push(doc);
        for (const frame of doc.querySelectorAll('iframe,frame')) {
          try { if (frame.contentDocument) visit(frame.contentDocument); } catch {}
        }
      };
      visit(document);
      const hasLoginForm = docs.some((doc) => Array.from(doc.querySelectorAll('input')).some((input) => visible(input)
        && String(input.type || '').toLowerCase() === 'password'));
      const menuSelectors = ${JSON.stringify([...new Set(Object.values(moduleMenuRoutes).flatMap((route) => [route.direct, route.main, ...route.fallbacks]))])};
      const hasMenuShell = docs.some((doc) => menuSelectors.some((selector) => doc.querySelector(selector)));
      return { hasLoginForm, hasMenuShell };
    })()`);
    if (domStatus.hasLoginForm) return "login_required";
    if (domStatus.hasMenuShell) return "authenticated";
    return "unknown";
  } catch {
    return "unknown";
  } finally {
    target.client.close();
  }
}

async function waitForPageTextParts(client: BrowserAutomationClient, parts: string[], timeoutMs: number, pollIntervalMs = 100) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const body = await pageText(client);
    if (isLikelyJackyunLoginPage(body)) throw new Error("当前是吉客云登录页，请先完成登录后再继续自动化。");
    if (/验证码|重新登录|账号登录/.test(body) && parts.some((part) => !body.includes(part))) {
      throw new Error("吉客云出现登录验证，已停止后续模块。");
    }
    if (parts.every((part) => body.includes(part))) return body;
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
  }
  throw new Error(`页面未出现预期内容：${parts.join(" / ")}`);
}

async function waitForActiveModule(client: BrowserAutomationClient, moduleKey: JackyunModule, text: string, timeoutMs: number, previousContentUrl?: string | null, pollIntervalMs = 200) {
  const deadline = Date.now() + timeoutMs;
  const urlHints = moduleUrlHints(moduleKey);
  while (Date.now() < deadline) {
    const body = await pageText(client);
    if (isLikelyJackyunLoginPage(body)) throw new Error("当前是吉客云登录页，请先完成登录后再继续自动化。");
    if (/验证码|重新登录|账号登录/.test(body) && !body.includes(text)) throw new Error("吉客云出现登录验证，已停止后续模块。");
    const contentUrl = await activeContentUrl(client);
    if (contentUrl && urlHints.some((hint) => contentUrl.includes(hint))) return;
    // Do not accept a highlighted sidebar label or an arbitrary iframe URL as
    // navigation success.  The controller must be able to tie the visible
    // content frame to this module before changing any fields or exporting.
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
  }
  throw new Error(`无法确认当前激活模块：${text}`);
}

export type CapturedDownloadUrlEvidence = {
  url: string;
  observedAt: string;
  source: "browser_download_event" | "module_network_request" | "page_download_hook" | "task_download_record";
};

export async function waitForJackyunExportTask(client: BrowserAutomationClient, expected: {
  module: JackyunModule; sourceRows: number; exportIntentAt: string; allowedHosts: readonly string[];
  binding?: JackyunExportTaskBinding;
}, timeoutMs: number, pollMs: number) {
  const deadline = Date.now() + timeoutMs;
  let opened = false;
  let refreshedAt = Date.now();
  while (Date.now() < deadline) {
    const records = await evaluateValue<JackyunExportTaskRecord[]>(client, `(() => {
      ${jsDocumentsPrelude(["/system/taskList.html"])}
      return documents.flatMap(doc => Array.from(doc.querySelectorAll('[id^="sys-"]')).filter(visible).map(el => {
        const button = el.querySelector('.download-btn');
        let attachments = []; try { attachments = JSON.parse(button?.getAttribute('data-attas') || '[]'); } catch {}
        return { taskId: el.id, label: (el.querySelector('.filename')?.textContent || '').trim(),
          createdAt: Number(button?.getAttribute('data-gmtcreate')), completed: !!el.querySelector('.state.success'),
          urls: Array.isArray(attachments) ? attachments.map(a => String(a.attachmentUrl || '')) : [] };
      }));
    })()`);
    const result = selectJackyunExportTask(records, { ...expected, observedAt: new Date().toISOString() });
    if (result) return result;
    if (!opened) {
      const entry = await evaluateValue<{ x: number; y: number } | null>(client, `(() => {
        const candidates = Array.from(document.querySelectorAll('img[title="文件下载记录和系统任务"]')).filter(el=>{
          const r=el.getBoundingClientRect(); return r.width>2 && r.height>2;
        });
        if(candidates.length>1) throw new Error('下载记录入口不唯一');
        if(!candidates.length) return null;
        const r=candidates[0].getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2};
      })()`);
      if (entry) {
        await client.send("Input.dispatchMouseEvent", { type: "mouseMoved", ...entry, button: "none" });
        await client.send("Input.dispatchMouseEvent", { type: "mousePressed", ...entry, button: "left", clickCount: 1 });
        await client.send("Input.dispatchMouseEvent", { type: "mouseReleased", ...entry, button: "left", clickCount: 1 });
        opened = true;
      }
    }
    if (opened && Date.now() - refreshedAt >= 10_000) {
      const refresh = await evaluateValue<{ x: number; y: number } | null>(client, `(() => {
        ${jsDocumentsPrelude(["/system/taskList.html"])}
        const matches=documents.flatMap(doc=>Array.from(doc.querySelectorAll('i.fa-refresh[title="刷新"]')).filter(visible));
        if(matches.length!==1) return null;
        const el=matches[0], r=el.getBoundingClientRect(); let x=r.left+r.width/2,y=r.top+r.height/2,win=el.ownerDocument.defaultView;
        if(el.ownerDocument.elementFromPoint(x,y)!==el) return null;
        while(win.frameElement){const f=win.frameElement, b=f.getBoundingClientRect();x+=b.left;y+=b.top;win=win.parent;}
        return {x,y};
      })()`);
      if (refresh) {
        await client.send("Input.dispatchMouseEvent", { type: "mouseMoved", ...refresh, button: "none" });
        await client.send("Input.dispatchMouseEvent", { type: "mousePressed", ...refresh, button: "left", clickCount: 1 });
        await client.send("Input.dispatchMouseEvent", { type: "mouseReleased", ...refresh, button: "left", clickCount: 1 });
      }
      refreshedAt = Date.now();
    }
    await new Promise(resolve => setTimeout(resolve, Math.max(500, pollMs)));
  }
  throw new Error("导出任务仍未出现唯一已完成附件；保留原意图，禁止重复导出。");
}

export function assertBoundDownloadUrl(
  evidence: CapturedDownloadUrlEvidence | undefined,
  exportIntentAt: string,
  allowedHosts: readonly string[],
) {
  const exportIntentMs = Date.parse(exportIntentAt);
  const observedMs = Date.parse(evidence?.observedAt ?? "");
  let sourceHost = "";
  try { sourceHost = evidence ? new URL(evidence.url).hostname : ""; } catch { /* handled below */ }
  if (!evidence
    || !Number.isFinite(exportIntentMs)
    || !Number.isFinite(observedMs)
    || observedMs < exportIntentMs
    || !allowedHosts.includes(sourceHost)) {
    throw controllerFailure(
      "FILE_BINDING_FAILED",
      "download_binding",
      "未捕获到导出 intent 之后且属于白名单主机的本轮浏览器下载事件/URL；共享目录文件名和修改时间不能作为归属证据。",
    );
  }
  return evidence.url;
}

async function findOssUrl(
  captured: () => CapturedDownloadUrlEvidence | undefined,
  allowedHosts: readonly string[],
  exportIntentAt: string,
  timeoutMs: number,
) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const direct = captured();
    if (direct) return assertBoundDownloadUrl(direct, exportIntentAt, allowedHosts);
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw controllerFailure(
    "FILE_BINDING_FAILED",
    "download_binding",
    "导出后未捕获到本轮 OSS 下载事件/URL；不会扫描共享目录或重复点击导出。",
  );
}

function salesStartDate(asOfDate: string) {
  const asOf = new Date(`${asOfDate}T00:00:00Z`);
  const next = new Date(asOf);
  next.setUTCDate(next.getUTCDate() + 1);
  if (next.getUTCDate() === 1) return `${asOfDate.slice(0, 8)}01`;
  return `${asOfDate.slice(0, 8)}01`;
}

async function waitForResult(resultPath: string, timeoutMs: number, signal?: AbortSignal, pollIntervalMs = 250) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (signal?.aborted) throw signal.reason instanceof Error ? signal.reason : new Error("浏览器 controller 已取消。");
    if (await stat(resultPath).catch(() => null)) return readJsonFile<Record<string, unknown>>(resultPath);
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
  }
  throw new Error(`等待下载后处理结果超时：${resultPath}`);
}

async function persistControllerState(filePath: string, state: ControllerState) {
  state.updatedAt = new Date().toISOString();
  await writeJsonAtomic(filePath, state);
}

export async function withOwnedControllerChromeCleanup<T>(
  ownsBrowser: boolean,
  port: number,
  action: () => Promise<T>,
  closeBrowser: (port: number) => Promise<boolean> = closeChromeBrowser,
): Promise<T> {
  try {
    return await action();
  } finally {
    if (ownsBrowser) await closeBrowser(port);
  }
}

export async function setShipmentTimeType(client: BrowserAutomationClient) {
  const actual = await evaluateValue<string>(client, `(() => {
    ${jsDocumentsPrelude(["order_detail"])}
    const candidates = [];
    for (const doc of documents) {
      const mini = doc.defaultView?.mini;
      if (!mini) continue;
      for (const element of doc.querySelectorAll('#selectTimeStr,.mini-combobox')) {
        if (!visible(element)) continue;
        const control = mini.get?.(element.id);
        const data = control?.getData?.();
        if (!Array.isArray(data)) continue;
        const textField = control.textField || 'text';
        const matches = data.filter(item => String(item[textField] ?? '').trim() === '发货时间');
        if (matches.length === 1) candidates.push({control, item: matches[0], textField});
      }
    }
    if (candidates.length !== 1) throw new Error('统计时间类型控件缺失或不唯一');
    const {control, item, textField} = candidates[0];
    const valueField = control.valueField || 'id';
    if (item[valueField] == null) throw new Error('发货时间选项缺少实际取值');
    control.setValue(item[valueField]);
    control.setText?.(item[textField]);
    control.doValueChanged?.();
    if (String(control.getValue()) !== String(item[valueField])) throw new Error('发货时间取值读回不一致');
    return String(control.getText?.() ?? '').trim();
  })()`);
  if (actual !== "发货时间") throw controllerFailure("FIELD_MISMATCH", "field_readback", "统计时间类型未读回为发货时间。");
  return actual;
}

async function readExportMenuInspection(client: BrowserAutomationClient, urlHints: string[]) {
  return evaluateValue(client, `(() => {
    ${jsDocumentsPrelude(urlHints)}
    return documents.flatMap(doc => Array.from(doc.querySelectorAll('.mini-menuitem-text'))
      .filter(visible).map(el => {
        const rect = el.getBoundingClientRect();
        let x = rect.left + rect.width / 2, y = rect.top + rect.height / 2, win = doc.defaultView;
        const hit = doc.elementFromPoint(x,y), frames = [];
        while (win.frameElement) {
          const frame = win.frameElement, r = frame.getBoundingClientRect(); x+=r.left; y+=r.top; win=win.parent;
          const hit = win.document.elementFromPoint(x,y);
          frames.push({ x, y, correct: hit === frame, hit: hit?.tagName + '.' + hit?.className, frame: frame.tagName + '.' + frame.className,
            width: r.width, height: r.height, layoutWidth: frame.offsetWidth, layoutHeight: frame.offsetHeight, innerWidth: frame.contentWindow.innerWidth });
        }
        return { path: doc.location.pathname, text: (el.textContent || '').trim(),
          left: rect.left, top: rect.top, width: rect.width, height: rect.height,
          scrollY: doc.defaultView.scrollY, viewportHeight: doc.defaultView.innerHeight,
          itemHit: el.closest('.mini-menuitem')?.contains(hit), frames };
      }));
  })()`);
}

export async function findExportMenuTarget(client: BrowserAutomationClient, urlHints: string[], parentLabel?: string) {
  return evaluateValue<{ text: string; x: number; y: number } | null>(client, `(() => {
    ${jsDocumentsPrelude(urlHints)}
    const wanted = ${JSON.stringify(parentLabel ?? null)};
    const matches = [];
    for (const doc of documents) for (const el of doc.querySelectorAll('.mini-menuitem-text')) {
      if (!visible(el)) continue;
      const text = (el.textContent || '').trim();
      if (wanted ? normalize(text) !== normalize(wanted) : !/^导出所有页(?:\\s*[(（].*[)）])?$/.test(text)) continue;
      const rect = el.getBoundingClientRect();
      let x = rect.left + rect.width / 2, y = rect.top + rect.height / 2, win = doc.defaultView;
      if (x < 0 || y < 0 || x >= win.innerWidth || y >= win.innerHeight) continue;
      const item = el.closest('.mini-menuitem');
      if (!item?.contains(doc.elementFromPoint(x, y)) || /disabled/.test(item.className)) continue;
      if (wanted && !Array.from(item.querySelectorAll('.mini-menuitem-allow')).some(visible)) continue;
      let unoccluded = true;
      while (win.frameElement) {
        const frame = win.frameElement, frameRect = frame.getBoundingClientRect();
        x += frameRect.left; y += frameRect.top; win = win.parent;
        if (x < 0 || y < 0 || x >= win.innerWidth || y >= win.innerHeight || win.document.elementFromPoint(x, y) !== frame) {
          unoccluded = false; break;
        }
      }
      if (unoccluded) matches.push({ text, x, y });
    }
    if (matches.length > 1) throw new Error('导出菜单目标不唯一');
    return matches[0] || null;
  })()`);
}

export async function prepareExportAllPagesMenu(client: BrowserAutomationClient, moduleKey: JackyunModule, urlHints: string[], timeoutMs: number, pollMs: number) {
  const deadline = Date.now() + timeoutMs;
  do {
    const leaf = await findExportMenuTarget(client, urlHints);
    if (leaf) return leaf;
    const parent = await findExportMenuTarget(client, urlHints, moduleKey === "combos" ? "导出组合装及子件" : "导出");
    if (parent) {
      // MiniUI can redraw the parent beneath the pointer after the context
      // menu opens. Re-enter the actual parent to fire its submenu hover.
      // Neither preparation nor its retries send a mouse button event.
      await client.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: Math.max(5, parent.x - 160), y: parent.y, button: "none" });
      await new Promise(resolve => setTimeout(resolve, 100));
      await client.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: parent.x, y: parent.y, button: "none" });
    }
    await new Promise(resolve => setTimeout(resolve, Math.max(500, pollMs)));
  } while (Date.now() < deadline);
  throw new Error("EXPORT_MENU_NOT_READY：未找到可点击的本模块导出所有页菜单，尚未发送导出点击。");
}

export async function clickPreparedExportAllPages(client: BrowserAutomationClient, urlHints: string[], beforeClick: () => Promise<void>) {
  const target = await findExportMenuTarget(client, urlHints);
  if (!target) throw new Error("EXPORT_MENU_NOT_READY：导出所有页菜单已消失，尚未发送导出点击。");
  await client.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: target.x, y: target.y, button: "none" });
  const readback = await findExportMenuTarget(client, urlHints);
  if (!readback || readback.text !== target.text || Math.abs(readback.x - target.x) > 1 || Math.abs(readback.y - target.y) > 1) {
    throw new Error("EXPORT_MENU_NOT_READY：导出所有页菜单发生移动，尚未发送导出点击。");
  }
  await beforeClick();
  await client.send("Input.dispatchMouseEvent", { type: "mousePressed", x: target.x, y: target.y, button: "left", clickCount: 1 });
  await client.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: target.x, y: target.y, button: "left", clickCount: 1 });
}

async function runController(options: CliOptions) {
  if (options.directHttp && (!options.exportFirstBatch || options.inspectWebSessionOnly || options.webConfirmationRecovery)) throw new Error("HTTP 导出必须使用独立的五表运行。");
  const batchTransport = options.directHttp ? jackyunDirectTransport : jackyunWebSessionTransport;
  const exportFirst = options.exportOnlyModule || options.exportFirstBatch;
  if (options.exportFirstBatch && (options.exportOnlyModule || options.resumeTaskBinding || options.inspectExportMenuOnly)) throw new Error("网页批量模式不能接管旧单表任务。");
  if (options.inspectWebSessionOnly && (!options.exportFirstBatch || !options.runId.startsWith("inspect-web-"))) throw new Error("网页诊断必须使用独立运行身份。");
  if (options.inspectApiPayload && !options.inspectWebSessionOnly) throw new Error("接口模板采集只允许隔离的网页诊断。");
  if (options.inspectExportMenuOnly && (!exportFirst || !options.runId.startsWith("inspect-menu-")
    || !path.resolve(options.outputRoot).startsWith(path.join(projectRoot, "outputs", "jackyun-menu-inspection") + path.sep))) {
    throw new Error("菜单诊断必须使用独立诊断目录和运行 ID。");
  }
  const policy = await readJsonFile<Policy>(exportFirst
    ? path.join(projectRoot, "config", "jackyun-export-first-policy.json") : policyPath);
  if (exportFirst && ((options.exportOnlyModule && !jackyunModuleOrder.includes(options.exportOnlyModule))
    || policy.version !== jackyunExportFirstPolicyVersion
    || options.snapshotDate !== jackyunCaptureDate(new Date().toISOString()))) {
    throw new Error("先导出后导入协议的模块、策略或实际采集日无效。");
  }
  const chromePath = options.chromePath ?? policy.browser.controller?.chromePath ?? "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
  const profileDirectory = path.resolve(options.profileDirectory ?? policy.browser.controller?.profileDirectory ?? path.join(projectRoot, ".runtime", "jackyun-chrome-profile"));
  const port = options.debuggingPort ?? policy.browser.controller?.debuggingPort ?? 9223;
  const startUrl = policy.browser.controller?.startUrl ?? "https://web.jackyun.com/home/mainframe_web_horizontal.html";
  const loginConfig = await readJackyunLoginConfig(projectRoot);
  if (path.resolve(loginConfig.profileDirectory).toLowerCase() !== profileDirectory.toLowerCase()
    || loginConfig.debuggingPort !== port || !isJackyunLoginOrigin(startUrl)) {
    throw new Error("waiting_login：登录策略与专用 Profile、端口或站点不一致。");
  }
  const launchedBrowser = await launchDedicatedChrome({ executablePath: chromePath, profileDirectory, port, startUrl, headless: options.launchOnly ? false : options.headless });
  if (options.launchOnly) return { status: "chrome_ready", profileDirectory, port };
  const ownsBrowser = Boolean(launchedBrowser);

  return withOwnedControllerChromeCleanup(ownsBrowser, port, async () => {
  await verifyJackyunBrowserBinding({ chromePath, profileDirectory, port });
  const loginBrowser = await connectPlaywrightBrowser(port);
  try {
    const candidates = loginBrowser.contexts().flatMap(context => context.pages())
      .filter(page => page.url() === "about:blank" || isJackyunLoginOrigin(page.url()));
    if (candidates.length !== 1) throw new Error("waiting_login：专用浏览器的吉客云登录页面不唯一。");
    const page = candidates[0];
    if (page.url() === "about:blank") await page.goto(startUrl, { waitUntil: "domcontentloaded", timeout: 60000 });
    const loginResult = await waitForJackyunDpapiSession({
      inspect: () => inspectJackyunLoginSurface(page, loginConfig.tenantId),
      submit: () => submitJackyunDpapiLogin(page, loginConfig),
      initialWaitMs: loginConfig.initialWaitMs, afterSubmitWaitMs: loginConfig.afterSubmitWaitMs,
      readOnly: options.checkLoginOnly, signal: options.signal,
    });
    if (options.checkLoginOnly || options.authenticateOnly) return { ...loginResult, port, tenantVerified: loginResult.status === "authenticated" };
    console.log(JSON.stringify({ type: "jackyun_login", ...loginResult, tenantVerified: true }));
  } finally {
    await loginBrowser.close();
  }

  const eventDirectory = path.join(options.eventRoot, options.runId);
  const runDirectory = path.join(options.outputRoot, options.runId);
  const controllerStatePath = path.join(runDirectory, "browser-controller-state.json");
  const state = await readJsonFileOr<ControllerState>(controllerStatePath, {
    version: 1, runId: options.runId, policyVersion: policy.version,
    ...(options.exportFirstBatch ? { exportTransport: batchTransport } : {}),
    ...(options.inspectWebSessionOnly ? { inspectionOnly: true as const } : {}), updatedAt: new Date().toISOString(), modules: {},
  });
  if (Boolean(state.inspectionOnly) !== Boolean(options.inspectWebSessionOnly)) throw new Error("只读诊断不能升级为正式导出。");
  if (state.exportTransport !== (options.exportFirstBatch ? batchTransport : undefined)) throw new Error("不同导出传输版本的运行状态不能互相接管。");
  if (state.runId !== options.runId || state.policyVersion !== policy.version) throw new Error("浏览器 controller 状态与当前运行参数不一致。");
  if (options.inspectExportMenuOnly && Object.values(state.modules).some(module => module?.exportIntentAt || module?.filePath)) {
    throw new Error("菜单诊断不能接管业务运行。");
  }

  // Playwright owns the browser/page lifecycle. A browser-level CDP session
  // remains only for signed-export download evidence from legacy MiniUI pages.
  const playwrightBrowser = await connectPlaywrightBrowser(port);
  const browserClientBrowser = await connectPlaywrightBrowser(port);
  const browserClientContext = browserClientBrowser.contexts()[0] ?? await browserClientBrowser.newContext();
  const browserClientPage = browserClientContext.pages()[0] ?? await browserClientContext.newPage();
  const browserClient = new PlaywrightPageClient(
    browserClientPage,
    await browserClientContext.newCDPSession(browserClientPage),
  );
  try {
  await browserClient.send("Browser.setDownloadBehavior", { behavior: "deny", eventsEnabled: true });
  let capturedDownloadUrl: CapturedDownloadUrlEvidence | undefined;
  browserClient.on("Browser.downloadWillBegin", (params) => {
    const url = typeof params.url === "string" ? params.url : undefined;
    if (url) capturedDownloadUrl = { url, observedAt: new Date().toISOString(), source: "browser_download_event" };
  });

  const moduleOrder = options.exportFirstBatch ? jackyunExportOrder : jackyunModuleOrder;
  for (const moduleKey of moduleOrder) {
    const index = jackyunModuleOrder.indexOf(moduleKey);
    if (options.signal?.aborted) throw options.signal.reason instanceof Error ? options.signal.reason : new Error("浏览器 controller 已取消。");
    if (options.exportOnlyModule && moduleKey !== options.exportOnlyModule) continue;
    await options.beforeModule?.(moduleKey);
    const resultPath = path.join(eventDirectory, `${eventFileName(index, moduleKey)}.result.json`);
    const existingResult = await readJsonFileOr<Record<string, unknown> | null>(resultPath, null);
    if (existingResult && ["completed", "duplicate_ignored"].includes(String(existingResult.status))) {
      state.modules[moduleKey] = { ...(state.modules[moduleKey] ?? { status: "pending" }), status: "completed" };
      continue;
    }

    const previousQuery = state.modules[moduleKey];
    if (options.directHttp && previousQuery?.queryIntentAt && !previousQuery.exportIntentAt && !previousQuery.filePath) {
      const history = previousQuery.queryAttemptHistory ?? [];
      if (!Array.isArray(history) || history.length >= 5) throw new Error("未提交的查询已经多次失败，保留审计并停止。");
      // No export was armed. A restarted process must issue a NEW read-only query,
      // rather than accepting the lost process's table or network completion.
      state.modules[moduleKey] = { status: "pending", queryAttemptHistory: [...history, {
        queryIntentAt: previousQuery.queryIntentAt, tableStableAt: previousQuery.tableStableAt ?? null, repreparedAt: new Date().toISOString(),
      }] };
      await persistControllerState(controllerStatePath, state);
    }
    const moduleState = state.modules[moduleKey] ?? { status: "pending" as const };
    if (moduleState.reprepareEvidence && options.directHttp && (!options.exportFirstBatch || moduleKey !== "sales" || options.runId !== "n8n-export-first-890"
      || options.httpScopeRecovery?.originalExecutionId !== "890"
      || options.httpScopeRecovery.permitSha256 !== moduleState.reprepareEvidence.permitSha256)) throw new Error("HTTP 销售恢复缺少独占 n8n 许可。");
    if (moduleState.reprepareEvidence && !options.directHttp && (!options.exportFirstBatch || moduleKey !== "combos" || options.runId !== "n8n-export-first-849"
      || options.webConfirmationRecovery?.originalExecutionId !== "849"
      || options.webConfirmationRecovery.permitSha256 !== moduleState.reprepareEvidence.permitSha256)) throw new Error("组合装恢复缺少独占 n8n 许可。");
    state.modules[moduleKey] = moduleState;
    const { client, page } = await connectPlaywrightJackyunTarget(playwrightBrowser, { startUrl });
    // The report iframe has a 1024px minimum width. Chrome's default headless
    // viewport can horizontally scroll its left filter button off-screen.
    if (options.directHttp && options.headless) await page.setViewportSize({ width: 1920, height: 1080 });
    if (options.headless && ownsBrowser) await page.setViewportSize({ width: 1920, height: 1080 });
    page.setDefaultTimeout(actionTimeout(policy, moduleKey));
    page.setDefaultNavigationTimeout(moduleTimeout(policy, moduleKey));
    await client.send("Page.enable");
    await client.send("Runtime.enable");
    await client.send("Network.enable");
    let stockAgeOwnerId: string | undefined;
    let queryRefreshTracking: QueryRefreshTracking | undefined;
    client.on("Network.requestWillBeSent", (params) => {
      const request = params.request as { url?: string; postData?: string } | undefined;
      if (!request?.url) return;
      try {
        if (policy.browser.allowedDownloadHosts.includes(new URL(request.url).hostname)) {
          capturedDownloadUrl = {
            url: request.url,
            observedAt: new Date().toISOString(),
            source: "module_network_request",
          };
        }
      } catch { /* ignore */ }
      if (moduleKey === "inventory_age" && request.postData && /birc|warehouse.*age|stock.*age/i.test(request.url)) {
        stockAgeOwnerId ??= extractStockAgeOwnerId(request.postData);
      }
    });
    const issueTrackedQuery = async (queryIntentAt: string) => {
      queryRefreshTracking?.dispose?.();
      queryRefreshTracking = await armQueryRefreshTracking(
        client,
        moduleKey,
        queryIntentAt,
        moduleUrlHints(moduleKey),
        !exportFirst && (moduleKey === "inventory" || moduleKey === "inventory_age") ? options.snapshotDate : undefined,
        Boolean(exportFirst),
      );
      await clickAnyTextEventually(
        client,
        ["筛选", "查询"],
        actionTimeout(policy, moduleKey),
        fastPoll(policy),
      );
    };

    let directTask: ReturnType<typeof selectJackyunExportTask> = null;
    const withHttp = async <T>(callback: (http: JackyunHttpSession) => Promise<T>): Promise<T> => {
      // The dedicated context cannot issue its own refresh while the HTTP owner is active.
      await page.context().setOffline(true);
      try { return await callback(await createDirectSession(client, loginConfig.tenantId)); }
      finally { await page.context().setOffline(false); }
    };
    const waitBoundTask = async (http?: JackyunHttpSession): Promise<ReturnType<typeof selectJackyunExportTask>> => {
      if (directTask) return directTask;
      if (options.directHttp && !http) return withHttp(session => waitBoundTask(session));
      const expected = { module: moduleKey, sourceRows: moduleState.expectedSourceRows!, exportIntentAt: moduleState.exportIntentAt!,
        allowedHosts: policy.browser.allowedDownloadHosts, binding: moduleState.exportTaskBinding ?? (options.resumeTaskBinding?.module === moduleKey ? options.resumeTaskBinding : undefined) };
      if (!options.exportFirstBatch) return waitForJackyunExportTask(client, expected, exportTimeout(policy, moduleKey), fastPoll(policy));
      if (!moduleState.webSession) throw new Error("网页提交缺少任务基线，禁止恢复或重复导出。");
      return waitForWebSessionTask(client, { ...expected, ...moduleState.webSession, observedAt: new Date().toISOString() }, {
        timeoutMs: exportTimeout(policy, moduleKey), signal: options.signal,
        ...(http ? { readTasks: (module: JackyunModule, since: string) => readDirectTasks(http, module, since) } : {}), onTask: async taskId => {
          moduleState.webSession!.pendingTaskId = taskId; await persistControllerState(controllerStatePath, state);
        },
      });
    };
    if (moduleState.exportIntentAt && !moduleState.filePath) {
      capturedDownloadUrl = undefined;
      const task = exportFirst ? await waitBoundTask() : undefined;
      if (task) { moduleState.exportTaskBinding = task.binding; await persistControllerState(controllerStatePath, state); }
      const recoveryUrl = task?.url ?? await findOssUrl(
        () => capturedDownloadUrl,
        policy.browser.allowedDownloadHosts,
        moduleState.exportIntentAt,
        exportTimeout(policy, moduleKey),
      );
      const recovered = await downloadSignedOssExport({
        url: recoveryUrl,
        downloadDirectory: policy.browser.downloadDirectory,
        runId: options.runId,
        module: moduleKey,
        policyVersion: policy.version,
        exportIntentAt: moduleState.exportIntentAt,
        allowedHosts: policy.browser.allowedDownloadHosts,
        timeoutMs: exportTimeout(policy, moduleKey),
      });
      moduleState.filePath = recovered.filePath;
      moduleState.downloadProvenance = recovered.provenance;
      moduleState.downloadEventAt = recovered.provenance.completedAt;
      moduleState.status = "downloaded";
      await persistControllerState(controllerStatePath, state);
    }

    if (!moduleState.filePath) {
    const urlBeforeNavigation = await currentUrl(client);
    const moduleWasActive = /web\.jackyun\.com/i.test(urlBeforeNavigation)
      && await isActiveText(client, policy.modules[moduleKey].pageName);
    if (moduleState.queryIntentAt && !moduleWasActive) {
      throw new Error(`${moduleKey} 已执行过查询，但浏览器页面状态已丢失；为避免重复查询已停止。`);
    }
    if (!/web\.jackyun\.com/i.test(urlBeforeNavigation)) {
      await client.send("Page.navigate", { url: startUrl });
      await new Promise((resolve) => setTimeout(resolve, 1_000));
    }
    if (!moduleState.navigationIntentAt) {
      moduleState.navigationIntentAt = new Date().toISOString();
      await persistControllerState(controllerStatePath, state);
      moduleState.status = "navigated";
      await persistControllerState(controllerStatePath, state);
    }
    const enterModuleStartedAt = Date.now();
    if (!moduleState.filePath) {
    await enterModule(client, policy, moduleKey);
    moduleState.timings = {
      ...moduleState.timings,
      enterModuleMs: Date.now() - enterModuleStartedAt,
    };
    await persistControllerState(controllerStatePath, state);

    const fieldChecks: NonNullable<BrowserHandoff["fieldChecks"]> = [];
    if (moduleKey === "products") {
      const initialMode = await waitForProductModeState(
        client,
        actionTimeout(policy, moduleKey),
        fastPoll(policy),
      );
      if (initialMode !== "sku") {
        await clickAnyText(client, ["规格模式", "货品模式"]);
        await clickAnyTextEventually(
          client,
          ["规格模式(SKU)", "规格模式（SKU）", "SKU模式"],
          actionTimeout(policy, moduleKey),
          fastPoll(policy),
        );
      }
      const verifiedMode = await waitForProductModeState(
        client,
        actionTimeout(policy, moduleKey),
        fastPoll(policy),
      );
      if (verifiedMode !== "sku") throw new Error("货品模式未能读回确认为 SKU。");
      fieldChecks.push({ field: "模式", value: "规格模式(SKU)", verifiedAt: new Date().toISOString() });
    }
    if (moduleKey === "inventory") {
      // JackYun v4: 仓库选择是顶部工具栏的 mini-buttonedit 下拉框 (#warehouseCom)，
      // 不是旧版的"仓库→全选→确定"对话框。用 mini.get API 打开 popup，点
      // .select_all_check 全选，读回"已勾选:N条"，关闭 popup。
      await waitForNestedControls(
        client,
        "branch_stock_main",
        [{ controlId: "warehouseCom" }],
        actionTimeout(policy, moduleKey),
        fastPoll(policy),
      );
      const readWarehouseSelection = () => evaluateValue<{ count?: number; error?: string }>(client, `(() => {
    let target = null;
    const visit = (d) => { if (d.location && /branch_stock_main/.test(d.location.href)) { target = d; return; } try { for (const f of d.querySelectorAll('iframe,frame')) { try { if (f.contentDocument) visit(f.contentDocument); } catch(e){} } } catch(e){} };
    visit(document);
    if (!target) return { error: 'no branch_stock_main doc' };
    const w = target.defaultView;
    if (!w.mini) return { error: 'no mini on branch_stock_main window' };
    const ctrl = w.mini.get('warehouseCom');
    if (!ctrl) return { error: 'no warehouseCom control' };
    const cur = ctrl.getValue ? String(ctrl.getValue()) : '';
    return { count: cur.split(',').filter(Boolean).length };
  })()`);
      const minimumWarehouses = policy.modules.inventory.minimumSelectedWarehouses ?? 1;
      let selectResult = await retryOnceAfterAmbiguousBrowserResult(readWarehouseSelection, 500);
      if ((selectResult.count ?? 0) < minimumWarehouses) {
        // Keep MiniUI mutations in short page evaluations. Selecting all can
        // rebuild the nested frame; waiting inside the same evaluation loses
        // its response even though the click itself succeeded.
        const openWarehousePopup = () => evaluateValue<{ opened?: boolean; error?: string }>(client, `(() => {
    let target = null;
    const visit = (d) => { if (d.location && /branch_stock_main/.test(d.location.href)) { target = d; return; } try { for (const f of d.querySelectorAll('iframe,frame')) { try { if (f.contentDocument) visit(f.contentDocument); } catch(e){} } } catch(e){} };
    visit(document);
    const ctrl = target?.defaultView?.mini?.get?.('warehouseCom');
    if (!ctrl) return { error: 'no warehouseCom control' };
    try { if (!ctrl.isShowPopup || !ctrl.isShowPopup()) ctrl.showPopup(); }
    catch(e) { return { error: 'showPopup failed: '+String(e).slice(0,80) }; }
    return { opened: true };
  })()`);
        const popupResult = await retryOnceAfterAmbiguousBrowserResult(openWarehousePopup, 500);
        if (popupResult.error) throw new Error(`库存仓库弹窗无法打开：${popupResult.error}`);
        await new Promise((resolve) => setTimeout(resolve, 500));

        let clickFailure = "";
        try {
          const clickResult = await evaluateValue<{ clicked?: boolean; error?: string }>(client, `(() => {
    let target = null;
    const visit = (d) => { if (d.location && /branch_stock_main/.test(d.location.href)) { target = d; return; } try { for (const f of d.querySelectorAll('iframe,frame')) { try { if (f.contentDocument) visit(f.contentDocument); } catch(e){} } } catch(e){} };
    visit(document);
    const w = target?.defaultView;
    const ctrl = w?.mini?.get?.('warehouseCom');
    if (!ctrl) return { error: 'no warehouseCom control' };
    const popup = ctrl.popup || (ctrl.getPopup && ctrl.getPopup());
    const el = popup && (popup.el || popup._el || popup);
    let cb = null;
    try { if (el && typeof el.querySelector === 'function') cb = el.querySelector('.select_all_check'); } catch {}
    if (!cb) { try { cb = target.querySelector('.mini-popup .select_all_check'); } catch {} }
    if (!cb) { try { cb = target.querySelector('.select_all_check'); } catch {} }
    if (!cb) return { error: 'no .select_all_check in popup' };
    if (w.jQuery) w.jQuery(cb).trigger('click'); else cb.click();
    return { clicked: true };
  })()`);
          clickFailure = clickResult.error ?? "";
        } catch (error) {
          // A frame rebuild can discard this response after applying the
          // click. The readback below decides whether the action succeeded.
          clickFailure = error instanceof Error ? error.message.slice(-200) : String(error).slice(-200);
        }

        const readbackDeadline = Date.now() + Math.min(actionTimeout(policy, moduleKey), 5_000);
        do {
          await new Promise((resolve) => setTimeout(resolve, 300));
          try { selectResult = await readWarehouseSelection(); } catch { continue; }
          if ((selectResult.count ?? 0) >= minimumWarehouses) break;
        } while (Date.now() < readbackDeadline);
        if ((selectResult.count ?? 0) < minimumWarehouses && clickFailure) {
          selectResult = { ...selectResult, error: clickFailure };
        }
      }
      const selected = selectResult.count;
      if (!selected || selected < minimumWarehouses) {
        throw new Error(`库存仓库全选状态无法读回或选择数量异常：${JSON.stringify(selectResult)}`);
      }
      fieldChecks.push({ field: "仓库", value: `已勾选:${selected}条`, verifiedAt: new Date().toISOString() });
      // Formal inventory snapshots are historical facts. A real-time page or
      // a date control whose value cannot be read back exactly must stop before
      // the query/export intent is recorded.
      if (!exportFirst) try {
        const dates = await setDateInputs(client, [options.snapshotDate], moduleUrlHints(moduleKey));
        const observedDate = assertHistoricalDateReadback(moduleKey, options.snapshotDate, dates);
        const controlReadbackAt = new Date().toISOString();
        moduleState.snapshotControlReadback = {
          version: 1,
          module: moduleKey,
          runId: options.runId,
          source: "historical_date_control",
          targetDate: options.snapshotDate,
          observedDate,
          controlReadbackAt,
        };
        fieldChecks.push({ field: "日期", value: observedDate, verifiedAt: controlReadbackAt });
      } catch (error) {
        if ((error as { code?: unknown }).code === "FIELD_MISMATCH") throw error;
        const detail = error instanceof Error ? error.message : String(error);
        throw controllerFailure(
          "FIELD_MISMATCH",
          "field_readback",
          `${moduleKey} 未发现可验证并可精确读回的历史日期控件（目标 ${options.snapshotDate}）：${detail}`,
        );
      }
    }
    if (moduleKey === "inventory_age") {
      if (!exportFirst) try {
        const dates = await setDateInputs(client, [options.snapshotDate], moduleUrlHints(moduleKey));
        const observedDate = assertHistoricalDateReadback(moduleKey, options.snapshotDate, dates);
        const controlReadbackAt = new Date().toISOString();
        moduleState.snapshotControlReadback = {
          version: 1,
          module: moduleKey,
          runId: options.runId,
          source: "historical_date_control",
          targetDate: options.snapshotDate,
          observedDate,
          controlReadbackAt,
        };
        fieldChecks.push({ field: "日期", value: observedDate, verifiedAt: controlReadbackAt });
      } catch (error) {
        if ((error as { code?: unknown }).code === "FIELD_MISMATCH") throw error;
        const detail = error instanceof Error ? error.message : String(error);
        throw controllerFailure(
          "FIELD_MISMATCH",
          "field_readback",
          `${moduleKey} 未发现可验证并可精确读回的历史日期控件（目标 ${options.snapshotDate}）：${detail}`,
        );
      }
      stockAgeOwnerId = await readStockAgeOwnerIdFromPage(client);
      if (stockAgeOwnerId) {
        fieldChecks.push({ field: "货主范围", value: "页面条件已读回", verifiedAt: new Date().toISOString() });
      }
    }
    if (moduleKey === "sales") {
      const expected = [`${salesStartDate(options.asOfDate)} 00:00:00`, `${options.asOfDate} 23:59:59`];
      // v4 sales 页面用 laydate 日期控件 (#timeBegin$text / #timeEnd$text)，
      // 直接通过 id 定位并设值，绕过 setDateInputs 的 iframe 遍历（order_detail iframe 可能在 tab 切换时被判定不可见）
      await waitForNestedControls(
        client,
        "order_detail",
        [
          { controlId: "timeBegin", inputId: "timeBegin$text" },
          { controlId: "timeEnd", inputId: "timeEnd$text" },
        ],
        actionTimeout(policy, moduleKey),
        fastPoll(policy),
      );
      if (exportFirst) {
        const timeType = await setShipmentTimeType(client);
        fieldChecks.push({ field: "统计时间类型", value: timeType, verifiedAt: new Date().toISOString() });
      }
      const dates = await evaluateValue<string[]>(client, `(() => {
    let target = null;
    const visit = (d) => { if (d.location && /order_detail/.test(d.location.href)) { target = d; return; } try { for (const f of d.querySelectorAll('iframe,frame')) { try { if (f.contentDocument) visit(f.contentDocument); } catch(e){} } } catch(e){} };
    visit(document);
    if (!target) throw new Error('sales: order_detail doc not found');
    const expected = ${JSON.stringify(expected)};
    const w = target.defaultView;
    const ctrlIds = ['timeBegin', 'timeEnd'];
    const inputIds = ['timeBegin$text', 'timeEnd$text'];
    const results = [];
    for (let i = 0; i < ctrlIds.length; i++) {
      const val = expected[i];
      // 优先用 mini.get().setValue() (mini-textbox 控件)
      const ctrl = w.mini && w.mini.get(ctrlIds[i]);
      if (ctrl && typeof ctrl.setValue === 'function') {
        try { ctrl.setValue(val); if (typeof ctrl.doValueChanged === 'function') ctrl.doValueChanged(); if (typeof ctrl.onValueChanged === 'function') ctrl.onValueChanged(); } catch(e){}
      }
      // 同时直接设 input value 并触发事件（laydate 监听）
      const input = target.getElementById(inputIds[i]);
      if (input) {
        const setter = Object.getOwnPropertyDescriptor(w.HTMLInputElement.prototype, 'value')?.set;
        setter ? setter.call(input, val) : (input.value = val);
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        input.dispatchEvent(new Event('blur', { bubbles: true }));
        input.dispatchEvent(new Event('focus', { bubbles: true }));
        input.dispatchEvent(new Event('focusout', { bubbles: true }));
      }
      // 读回：优先 ctrl.getValue，回退 input.value
      const readBack = (ctrl && typeof ctrl.getValue === 'function') ? String(ctrl.getValue()) : (input ? input.value : '');
      results.push(readBack);
    }
    return results;
  })()`);
      if (dates.join("|") !== expected.join("|")) {
        throw new Error(`销售日期区间读回不一致：期望 ${expected.join(" 至 ")}，实际 ${dates.join(" 至 ")}。`);
      } else {
        fieldChecks.push({ field: "日期区间", value: expected.join(" 至 "), verifiedAt: new Date().toISOString() });
      }
    }
    moduleState.fieldChecks = fieldChecks;

    if (shouldIssueModuleQuery(policy.modules[moduleKey].requiresQuery, moduleState)) {
      moduleState.queryIntentAt = new Date().toISOString();
      await persistControllerState(controllerStatePath, state);
      await issueTrackedQuery(moduleState.queryIntentAt);
      if (moduleKey === "inventory_age") {
        const ownerDeadline = Date.now() + Math.min(actionTimeout(policy, moduleKey), 2_000);
        while (!stockAgeOwnerId && Date.now() < ownerDeadline) {
          await new Promise((resolve) => setTimeout(resolve, fastPoll(policy)));
        }
        stockAgeOwnerId ??= await readStockAgeOwnerIdFromPage(client);
        if (!stockAgeOwnerId) throw new Error("库龄查询未捕获到本轮货主范围，已停止导出。");
      }
      moduleState.status = "queried";
      delete moduleState.tableReadbackFailure;
      await persistControllerState(controllerStatePath, state);
    }
    if (!moduleState.expectedSourceRows) {
      const retryZeroRowQuery = async () => {
        moduleState.queryRetryCount = (moduleState.queryRetryCount ?? 0) + 1;
        moduleState.queryRetryIntentAt = new Date().toISOString();
        moduleState.queryIntentAt = moduleState.queryRetryIntentAt;
        delete moduleState.tableReadbackFailure;
        await persistControllerState(controllerStatePath, state);
        await issueTrackedQuery(moduleState.queryIntentAt);
      };
      if (shouldRetryZeroRowQuery(moduleState)) await retryZeroRowQuery();
      const runStableReadback = () => {
        if (policy.modules[moduleKey].requiresQuery && !queryRefreshTracking) {
          throw controllerFailure(
            "TABLE_TIMEOUT",
            "query_refresh",
            `${moduleKey} 已存在查询 intent，但当前进程没有该次点击的刷新跟踪上下文；拒绝恢复时接受旧表格。`,
          );
        }
        return stableRowCount(
          client,
          policy,
          moduleUrlHints(moduleKey),
          policy.modules[moduleKey].requiresQuery ? queryRefreshTracking : undefined,
        );
      };
      const tableStableStartedAt = Date.now();
      try {
        moduleState.expectedSourceRows = await runStableReadback();
      } catch (error) {
        const errorCode = (error as { code?: unknown }).code;
        const code = errorCode === "zero_rows"
          ? "zero_rows"
          : errorCode === "TABLE_TIMEOUT"
            ? "table_timeout"
            : "unstable";
        moduleState.tableReadbackFailure = { code, observedAt: new Date().toISOString() };
        await persistControllerState(controllerStatePath, state);
        if (!shouldRetryZeroRowQuery(moduleState)) {
          queryRefreshTracking?.dispose?.();
          throw error;
        }
        await retryZeroRowQuery();
        try {
          moduleState.expectedSourceRows = await runStableReadback();
        } catch (retryError) {
          const retryErrorCode = (retryError as { code?: unknown }).code;
          const retryCode = retryErrorCode === "zero_rows"
            ? "zero_rows"
            : retryErrorCode === "TABLE_TIMEOUT"
              ? "table_timeout"
              : "unstable";
          moduleState.tableReadbackFailure = { code: retryCode, observedAt: new Date().toISOString() };
          await persistControllerState(controllerStatePath, state);
          queryRefreshTracking?.dispose?.();
          throw retryError;
        }
      }
      if (queryRefreshTracking) {
        const refreshEvidence = completedQueryRefreshEvidence(queryRefreshTracking);
        if (!refreshEvidence) {
          queryRefreshTracking.dispose?.();
          throw controllerFailure(
            "TABLE_TIMEOUT",
            "query_refresh",
            `${moduleKey} 表格稳定前没有可绑定到本轮查询的刷新完成证据。`,
          );
        }
        moduleState.queryRefreshEvidence = {
          queryIntentAt: queryRefreshTracking.queryIntentAt,
          completedAt: refreshEvidence.completedAt,
          source: refreshEvidence.source,
        };
        queryRefreshTracking.dispose?.();
      }
      delete moduleState.tableReadbackFailure;
      moduleState.tableStableAt = new Date().toISOString();
      moduleState.timings = {
        ...moduleState.timings,
        tableStableMs: Date.now() - tableStableStartedAt,
      };
      if (moduleKey === "inventory" || moduleKey === "inventory_age") {
        const snapshotControl = moduleState.snapshotControlReadback;
        if (!snapshotControl && !exportFirst) {
          throw controllerFailure(
            "FIELD_MISMATCH",
            "field_readback",
            `${moduleKey} 缺少历史日期控件的精确读回证据。`,
          );
        }
        if (!moduleState.queryRefreshEvidence || !moduleState.queryIntentAt) {
          throw controllerFailure(
            "TABLE_TIMEOUT",
            "query_refresh",
            `${moduleKey} 缺少可绑定到历史日期条件的查询刷新证据。`,
          );
        }
        moduleState.snapshotEvidence = exportFirst ? {
          version: 1,
          module: moduleKey,
          runId: options.runId,
          source: "current_query",
          targetDate: options.snapshotDate,
          queryIntentAt: moduleState.queryRefreshEvidence.queryIntentAt,
          queryRefreshSource: "module_network_request",
          queryRefreshCompletedAt: moduleState.queryRefreshEvidence.completedAt,
          tableStableAt: moduleState.tableStableAt,
        } : {
          ...snapshotControl!,
          queryIntentAt: moduleState.queryRefreshEvidence.queryIntentAt,
          queryRefreshSource: moduleState.queryRefreshEvidence.source,
          queryRefreshCompletedAt: moduleState.queryRefreshEvidence.completedAt,
          tableStableAt: moduleState.tableStableAt,
        };
        if (exportFirst) {
          if (moduleState.queryRefreshEvidence.source !== "module_network_request") {
            throw controllerFailure("TABLE_TIMEOUT", "query_refresh", "当前快照缺少本模块成功网络响应。");
          }
          assertJackyunSnapshotEvidence(moduleState.snapshotEvidence, {
            module: moduleKey, runId: options.runId, snapshotDate: options.snapshotDate,
            policyVersion: policy.version, navigationIntentAt: moduleState.navigationIntentAt,
            exportIntentAt: new Date().toISOString(),
          });
        }
      }
      fieldChecks.push({ field: "页面总数", value: `共 ${moduleState.expectedSourceRows} 条`, verifiedAt: moduleState.tableStableAt });
      moduleState.fieldChecks = fieldChecks;
      await persistControllerState(controllerStatePath, state);
    }

    capturedDownloadUrl = undefined;
    if (options.inspectExportMenuOnly) {
      if (moduleState.exportIntentAt || moduleState.filePath) throw new Error("菜单诊断不能接管业务运行。");
      await rightClickDataRow(client, moduleUrlHints(moduleKey), true);
      const rightClickMenu = await readExportMenuInspection(client, moduleUrlHints(moduleKey));
      const prepared = await prepareExportAllPagesMenu(client, moduleKey, moduleUrlHints(moduleKey), actionTimeout(policy, moduleKey), fastPoll(policy))
        .catch(() => ({ error: "EXPORT_MENU_NOT_READY" }));
      const afterHover = await readExportMenuInspection(client, moduleUrlHints(moduleKey));
      client.close();
      return { status: "menu_inspected", runId: options.runId, module: moduleKey, rightClickMenu, prepared, afterHover };
    }
    if (options.inspectWebSessionOnly) {
      if (options.inspectApiPayload) {
        const captured = await captureDirectExport(client, page, moduleKey, moduleKey === "combos" ? async () => {
          const rule = policy.modules.combos.exportConfirmation;
          if (!rule) throw new Error("组合装导出确认规则缺失。");
          await confirmJackyunComboExport(client, moduleUrlHints(moduleKey), rule.promptIncludes, rule.button, actionTimeout(policy, moduleKey), fastPoll(policy));
        } : undefined, options.asOfDate);
        try {
          await options.inspectApiPayload(moduleKey, { data: captured.data, moduleCode: captured.moduleCode, payloadSha256: captured.payloadSha256 }, moduleState.expectedSourceRows);
        } finally { await captured.cancel(); }
        client.close(); continue;
      }
      await prepareWebSessionExport(client, moduleKey);
      const baseline = await readWebSessionTasks(client, moduleKey);
      console.log(JSON.stringify({ type: "jackyun_web_preflight", module: moduleKey, rows: moduleState.expectedSourceRows, tasks: baseline.records.length }));
      client.close(); continue;
    }
    if (!moduleState.exportIntentAt) {
      const armExport = async () => {
        moduleState.exportIntentAt = new Date().toISOString();
        moduleState.status = "export_armed";
        await persistControllerState(controllerStatePath, state);
      };
      if (options.directHttp) {
        const baseline = await withHttp(http => readDirectTasks(http, moduleKey, moduleState.reprepareEvidence?.originalIntentAt));
        if (moduleState.reprepareEvidence) assert890ReprepareWindow(moduleState.reprepareEvidence.originalIntentAt, baseline.records);
        moduleState.webSession = { baselineIds: baseline.records.map(r => r.taskId), baselineAt: new Date().toISOString() };
        await armExport();
        const captured = await captureDirectExport(client, page, moduleKey, moduleKey === "combos" ? async () => {
          const rule = policy.modules.combos.exportConfirmation;
          if (!rule) throw new Error("组合装导出确认规则缺失。");
          const confirmedAt = await confirmJackyunComboExport(client, moduleUrlHints(moduleKey), rule.promptIncludes, rule.button, actionTimeout(policy, moduleKey), fastPoll(policy));
          moduleState.exportConfirmation = { prompt: rule.promptIncludes.join("，"), button: rule.button, confirmedAt };
          await persistControllerState(controllerStatePath, state);
        } : undefined, options.asOfDate);
        try {
          moduleState.directPayloadSha256 = captured.payloadSha256;
          await persistControllerState(controllerStatePath, state);
          await withHttp(async http => {
            const response = await http.request("submitExport", captured.data, captured.moduleCode);
            await captured.complete(response);
            directTask = await waitBoundTask(http);
          });
        } catch (error) { await captured.cancel(); throw error; }
      } else if (options.exportFirstBatch) {
        const token = await prepareWebSessionExport(client, moduleKey);
        if (moduleState.reprepareEvidence) {
          const original = await readWebSessionTasks(client, moduleKey, moduleState.reprepareEvidence.originalIntentAt);
          assert849ReprepareWindow(moduleState.expectedSourceRows,moduleState.reprepareEvidence.originalIntentAt,original.records);
        }
        const baseline = await readWebSessionTasks(client, moduleKey);
        moduleState.webSession = { baselineIds: baseline.records.map(r => r.taskId), baselineAt: new Date().toISOString() };
        await armExport();
        await submitWebSessionExport(client, token);
      } else {
      if (!exportFirst) await armExport();
      const directExportStarted = exportFirst ? false : moduleKey === "sales"
        ? await triggerSalesMinimalExportAllPage(client, moduleUrlHints(moduleKey))
        : moduleKey === "inventory_age"
          ? await triggerStockAgePayloadExport(client, stockAgeOwnerId ?? "")
          : moduleKey === "combos"
            ? await triggerComboDetailExportAllPage(client, moduleUrlHints(moduleKey))
            : await triggerMinimalGridExportAllPage(
                client,
                moduleUrlHints(moduleKey),
                moduleKey === "products" ? ["grid-goods_managet"] : [],
                minimalGridExportHeaders[moduleKey],
              );
      if (!directExportStarted) await rightClickDataRow(client, moduleUrlHints(moduleKey), Boolean(exportFirst));
      if (exportFirst) {
        await prepareExportAllPagesMenu(client, moduleKey, moduleUrlHints(moduleKey), actionTimeout(policy, moduleKey), fastPoll(policy));
        await clickPreparedExportAllPages(client, moduleUrlHints(moduleKey), armExport);
      } else if (moduleKey === "combos" && !directExportStarted) {
        await clickAnyTextEventually(client, ["导出组合装及子件"], actionTimeout(policy, moduleKey), fastPoll(policy));
        await clickAnyTextEventually(client, ["导出所有页", "导出所有页(限500000行)", "导出所有页（限500000行）"], actionTimeout(policy, moduleKey), fastPoll(policy));
      } else if (!directExportStarted) {
        await clickAnyTextEventually(client, ["导出"], actionTimeout(policy, moduleKey), fastPoll(policy));
        await clickAnyTextEventually(client, ["导出所有页(限500000行)", "导出所有页（限500000行）", "导出所有页"], actionTimeout(policy, moduleKey), fastPoll(policy));
      }
      }
      if (moduleKey === "combos" && !options.directHttp) {
        const confirmationPolicy = policy.modules.combos.exportConfirmation;
        if (!confirmationPolicy) throw new Error("组合装导出确认规则缺失。");
        await waitForPageTextParts(client, confirmationPolicy.promptIncludes, actionTimeout(policy, moduleKey), fastPoll(policy));
        const confirmedAt = exportFirst
          ? await confirmJackyunComboExport(client, moduleUrlHints(moduleKey), confirmationPolicy.promptIncludes,
              confirmationPolicy.button, actionTimeout(policy, moduleKey), fastPoll(policy))
          : new Date().toISOString();
        if (!exportFirst) await clickText(client, confirmationPolicy.button);
        moduleState.exportConfirmation = { prompt: confirmationPolicy.promptIncludes.join("，"), button: confirmationPolicy.button, confirmedAt };
        await persistControllerState(controllerStatePath, state);
      }
    }
    }  // end if (!moduleState.filePath) — 跳过浏览器操作

    if (!moduleState.filePath) {
      const task = exportFirst ? await waitBoundTask() : undefined;
      if (task) { moduleState.exportTaskBinding = task.binding; await persistControllerState(controllerStatePath, state); }
      const downloadEvidence = task ? { url: task.url, observedAt: task.binding.observedAt, source: "task_download_record" as const } : await findCurrentDownloadEvidence(
        client,
        moduleUrlHints(moduleKey),
        () => capturedDownloadUrl,
        exportTimeout(policy, moduleKey),
        fastPoll(policy),
      );
      const ossUrl = assertBoundDownloadUrl(
        downloadEvidence,
        moduleState.exportIntentAt!,
        policy.browser.allowedDownloadHosts,
      );
      const downloaded = await downloadSignedOssExport({
        url: ossUrl,
        downloadDirectory: policy.browser.downloadDirectory,
        runId: options.runId,
        module: moduleKey,
        policyVersion: policy.version,
        exportIntentAt: moduleState.exportIntentAt!,
        allowedHosts: policy.browser.allowedDownloadHosts,
        timeoutMs: exportTimeout(policy, moduleKey),
      });
      moduleState.filePath = downloaded.filePath;
      moduleState.downloadProvenance = downloaded.provenance;
      moduleState.downloadEventAt = downloaded.provenance.completedAt;
      moduleState.status = "downloaded";
      moduleState.timings = {
        ...moduleState.timings,
        exportToDownloadMs: Date.now() - Date.parse(moduleState.exportIntentAt!),
      };
      await persistControllerState(controllerStatePath, state);
    }
    }

    try {
      assertBoundDownloadProvenance(moduleState.downloadProvenance, policy.browser.allowedDownloadHosts, {
        runId: options.runId,
        module: moduleKey,
        policyVersion: policy.version,
      });
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      throw controllerFailure("FILE_BINDING_FAILED", "download_binding", detail);
    }
    if (moduleState.downloadEventAt !== moduleState.downloadProvenance.completedAt) {
      throw controllerFailure(
        "FILE_BINDING_FAILED",
        "download_binding",
        `${moduleKey} downloadEventAt 必须与本轮 provenance.completedAt 完全一致。`,
      );
    }
    const handoff: BrowserHandoff = {
      schemaVersion: 2,
      runId: options.runId,
      policyVersion: policy.version,
      module: moduleKey,
      filePath: moduleState.filePath,
      navigationIntentAt: moduleState.navigationIntentAt!,
      queryIntentAt: moduleState.queryIntentAt,
      tableStableAt: moduleState.tableStableAt!,
      exportIntentAt: moduleState.exportIntentAt!,
      exportConfirmation: moduleState.exportConfirmation as BrowserExportConfirmation | undefined,
      downloadEventAt: moduleState.downloadEventAt!,
      expectedSourceRows: moduleState.expectedSourceRows!,
      downloadProvenance: moduleState.downloadProvenance,
      snapshotEvidence: moduleState.snapshotEvidence,
      sourceRowCountCorrection: moduleState.sourceRowCountCorrection,
      fieldChecks: moduleState.fieldChecks,
      evidence: {
        controller: "dedicated_chrome_playwright",
        ...(options.exportFirstBatch ? { exportTransport: batchTransport, taskQuerySource: options.directHttp ? "direct_http_api" : "web_session_api" } : {}),
        ...(options.directHttp ? { directPayloadSha256: moduleState.directPayloadSha256 } : {}),
        policyVersion: policy.version,
        sourceUrlHash: moduleState.downloadProvenance?.sourceUrlHash ?? null,
        exportTaskBinding: moduleState.exportTaskBinding ?? null,
      },
    };
    const eventPath = path.join(eventDirectory, eventFileName(index, moduleKey));
    await writeJsonAtomic(eventPath, handoff);
    moduleState.status = "handed_off";
    await persistControllerState(controllerStatePath, state);
    await options.afterModule?.(moduleKey);
    if (exportFirst) {
      client.close();
      continue;
    }
    const result = await waitForResult(`${eventPath}.result.json`, policy.browser.eventTimeoutMs, options.signal, fastPoll(policy));
    if (!["completed", "duplicate_ignored"].includes(String(result.status))) throw new Error(`${moduleKey} 下载后处理未完成。`);
    moduleState.status = "completed";
    moduleState.timings = {
      ...moduleState.timings,
      postDownloadMs: Date.now() - Date.parse(moduleState.downloadEventAt!),
    };
    await persistControllerState(controllerStatePath, state);
    client.close();
  }
  return { status: options.inspectWebSessionOnly ? "web_session_inspected" : exportFirst ? "exported" : "completed", runId: options.runId, controllerStatePath };
  } finally {
    browserClient.close();
    await Promise.allSettled([
      browserClientBrowser.close(),
      playwrightBrowser.close(),
    ]);
  }
  });
}

if (path.resolve(process.argv[1] ?? "") === path.resolve(fileURLToPath(import.meta.url))) {
  const cliOptions = parseCli();
  const cliLoginConfig = await readJackyunLoginConfig(projectRoot);
  if (cliOptions.authenticateOnly || cliOptions.checkLoginOnly || cliOptions.launchOnly) {
    const health = await fetch("http://127.0.0.1:5791/health", { signal: AbortSignal.timeout(2000) }).catch(() => null);
    const value = health?.ok ? await health.json() as { busy?: boolean; activeWorkflow?: unknown } : null;
    if (value?.busy || value?.activeWorkflow) throw new Error("共享辅助服务正忙，暂不能维护专用登录。");
  }
  withJackyunRunLock(
    { runId: cliOptions.runId, purpose: cliOptions.checkLoginOnly || cliOptions.authenticateOnly ? "browser_login_check" : "browser_controller",
      lockDirectory: path.join(path.dirname(cliLoginConfig.profileDirectory), "jackyun-automation.lock") },
    () => runController(cliOptions),
  )
    .then((result) => console.log(JSON.stringify(result)))
    .catch((error: unknown) => {
      console.error(error instanceof Error ? error.message : String(error));
      process.exit(1);
    });
}

export { runController };
