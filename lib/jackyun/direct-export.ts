import { createHash } from "node:crypto";
import type { Page, Route } from "playwright-core";
import { evaluateValue, type BrowserAutomationClient } from "./cdp-client";
import { JackyunHttpSession, signJackyunForm, type JackyunSession } from "./direct-http";
import type { JackyunModule } from "./post-download";
import { jackyunSalesPeriod } from "./sales-period";
import { prepareWebSessionExport, submitWebSessionExport, type WebTaskSnapshot } from "./web-session-export";

export const jackyunDirectTransport = "web_prepared_http_v1";
const schemas = {
  inventory: ["erp-stock/erp-stock/export", "warehouse.stock.sku.export", "branch_stock", "分仓库存查询"],
  combos: ["erp/erp/manysheetexport", "PackAgeGoodsInfoService,ChartPackage", "goods_managet_combination", "组合装及子件导出"],
  sales: ["oms/oms/excel", "11", "order_detail_list", "销售单明细账"],
  inventory_age: ["birc/birc/excel/v3/report", "stockAgeReport", "warehouse_age_analysis", "库龄分析"],
  products: ["erp-stock/erp-stock/export", "goods.sku.info.search", "goods_managet_query", "货品导出"],
} as const;
const signedFields = new Set(["timestamp", "access_token", "appkey", "sign"]);
const payloadFields = new Set(["serverName", "excelType", "headersJson", "conditionJson", "datasource", "isMerge", "typeName", "multiSheet", "exportTotal", "isSyn"]);

/** Current OMS converts the selected consign-time enum to numeric sort=4.
 * If either representation is present it must agree; other time types fail closed. */
export function isShipmentTimeFilter(filter: Record<string, unknown> | undefined) {
  if (!filter) return false;
  const legacy = Object.hasOwn(filter, "selectTimeStr"), current = Object.hasOwn(filter, "timeType");
  return (legacy || current) && (!legacy || filter.selectTimeStr === "tradeOrder.consign_time")
    && (!current || filter.timeType === 4);
}

export function validateDirectExportPayload(module: JackyunModule, postData: string, moduleCode: string, asOfDate?: string, salesStartDate?: string) {
  if (postData.length > 256 * 1024) throw new Error("HTTP_EXPORT_PAYLOAD_TOO_LARGE");
  const form = new URLSearchParams(postData), data: Record<string, string> = {};
  const seen = new Set<string>();
  for (const [key, value] of form) {
    if (seen.has(key) || (!signedFields.has(key) && !payloadFields.has(key))) throw new Error("HTTP_EXPORT_UNEXPECTED_PARAMETER");
    seen.add(key);
    if (!signedFields.has(key)) data[key] = value;
  }
  const [server, type, code, label] = schemas[module];
  if (moduleCode !== code || data.serverName !== server || data.excelType !== type
    || !(data.typeName === label || module === "inventory_age" && data.typeName === label + "(正式勿删)")
    || !["false", "true"].includes(data.isSyn) || data.multiSheet !== String(module === "combos") || data.datasource !== "") throw new Error("HTTP_EXPORT_SCHEMA_CHANGED");
  let condition: Record<string, unknown>, headers: { enName: string[]; showName: string[] }[];
  try {
    condition = JSON.parse(data.conditionJson);
    const raw = JSON.parse(data.headersJson);
    headers = Array.isArray(raw) ? raw : [raw];
  } catch { throw new Error("HTTP_EXPORT_SCHEMA_CHANGED"); }
  if (!condition || Array.isArray(condition) || condition.version !== "2.0" || headers.length !== (module === "combos" ? 2 : 1)
    || headers.some(h => !Array.isArray(h.enName) || !Array.isArray(h.showName) || !h.enName.length
      || h.enName.length !== h.showName.length || h.enName.some(f => typeof f !== "string"))) throw new Error("HTTP_EXPORT_SCHEMA_CHANGED");
  const includes = (index: number, fields: string[]) => fields.every(f => headers[index].enName.includes(f));
  if (module === "combos" && (!includes(0, ["goodsNo", "retailPrice"]) || !includes(1, ["mainGoodsNo", "goodsNo", "goodsAmount", "retailPrice"])
    || condition.packageGood !== "1" || !Array.isArray(condition.skuIds) || condition.skuIds.length)) throw new Error("HTTP_COMBO_SCOPE_CHANGED");
  if (module === "products" && (!includes(0, ["goodsNo", "skuProperitesName", "retailPrice"]) || condition.exportType !== 2
    || !Array.isArray(condition.skuIds) || condition.skuIds.length)) throw new Error("HTTP_SKU_SCOPE_CHANGED");
  if (module === "inventory" && (!includes(0, ["goodsNo", "warehouseName", "currentQuantity", "retailPrice"])
    || !Array.isArray(condition.ids) || condition.ids.length)) throw new Error("HTTP_INVENTORY_SCOPE_CHANGED");
  if (module === "inventory_age" && (!includes(0, ["goodsNo", "warehouseName", "stockAge"])
    || !Array.isArray(condition.orderIds) || condition.orderIds.length)) throw new Error("HTTP_AGE_SCOPE_CHANGED");
  if (module === "sales" && (!includes(0, ["goodsNo", "cost", "consignTime", "sellCount", "afterShareFee"])
    || !isShipmentTimeFilter(condition.filterOrderDetailDto as Record<string, unknown>))) throw new Error("HTTP_SALES_SCOPE_CHANGED");
  if (module === "sales") {
    const filter = condition.filterOrderDetailDto as Record<string, unknown>;
    if (!asOfDate || !/^\d{4}-\d{2}-\d{2}$/.test(asOfDate) || filter.timeBegin !== `${jackyunSalesPeriod(asOfDate, salesStartDate).startDate} 00:00:00`
      || filter.timeEnd !== `${asOfDate} 23:59:59`) throw new Error("HTTP_SALES_DATE_CHANGED");
  }
  return { data, moduleCode, payloadSha256: createHash("sha256").update(JSON.stringify(data)).digest("hex") };
}

/** Captures the final POST after the website's real validators, before a task can be created. */
export async function captureDirectExport(client: BrowserAutomationClient, page: Pick<Page, "route" | "unroute" | "goto">,
  module: JackyunModule, confirm?: () => Promise<void>, asOfDate?: string) {
  let resolve!: (value: ReturnType<typeof validateDirectExportPayload>) => void, reject!: (error: Error) => void;
  const result = new Promise<ReturnType<typeof validateDirectExportPayload>>((yes, no) => { resolve = yes; reject = no; });
  void result.catch(() => {});
  let paused: Route | undefined;
  let invalid = false;
  let settled = false;
  let enabled = false;
  const pattern = "**/startExcelExport*";
  const handler = async (route: Route) => {
      try {
        if (paused) {
          invalid = true;
          await route.abort("aborted");
          throw new Error();
        }
        paused = route;
        const request = route.request();
        if (request.url() !== "https://web.jackyun.com/jkyun/excel-service/manager/startExcelExport" || request.method() !== "POST") throw new Error();
        const code = Object.entries(await request.allHeaders()).find(([key]) => key.toLowerCase() === "module_code")?.[1] ?? "";
        resolve(validateDirectExportPayload(module, request.postData() ?? "", code, asOfDate));
      } catch (error) {
        invalid = true;
        reject(new Error(error instanceof Error && /^HTTP_[A-Z_]+$/.test(error.message) ? error.message : "HTTP_EXPORT_CAPTURE_REJECTED"));
      }
  };
  const abortPage = async () => {
    // Abort the exact held request BEFORE unloading/unrouting. Unrouting alone can release it.
    if (paused) await paused.abort("aborted").catch(() => {});
    await page.goto("about:blank", { waitUntil: "commit", timeout: 10000 });
  };
  const close = async (success: boolean, response?: unknown) => {
    if (settled) return;
    settled = true;
    try {
      if (!paused || !success || invalid) {
        // Destroy outstanding handlers before releasing interception; a late callback must never submit.
        await abortPage();
        if (success) throw new Error("HTTP_EXPORT_CAPTURE_REJECTED");
      } else {
        try {
          await paused.fulfill({ status: 200, contentType: "application/json;charset=UTF-8", body: JSON.stringify({ code: 200, result: response }) });
        } catch {
          await abortPage();
          throw new Error("HTTP_EXPORT_BROWSER_ACK_FAILED");
        }
      }
    } finally {
      if (enabled) await page.unroute(pattern, handler);
    }
  };
  const timer = setTimeout(() => reject(new Error("HTTP_EXPORT_CAPTURE_TIMEOUT")), 20000);
  try {
    await page.route(pattern, handler);
    enabled = true;
    const token = await prepareWebSessionExport(client, module);
    await submitWebSessionExport(client, token);
    await confirm?.();
    const payload = await result;
    return { ...payload, complete: (response: unknown) => close(true, response), cancel: () => close(false) };
  } catch (error) { await close(false); throw error; }
  finally { clearTimeout(timer); }
}

/** Browser requests must be offline for this owner's lifetime; caller holds the dedicated profile lock. */
export async function createDirectSession(client: BrowserAutomationClient, tenantId: string) {
  if (!/^\d{4,12}$/.test(tenantId)) throw new Error("HTTP_SESSION_BINDING_INVALID");
  const guard = `if(location.origin!=='https://web.jackyun.com'||document.querySelector('#jlink-sn')?.textContent?.trim()!==${JSON.stringify(tenantId)}
    ||typeof jkUtils?.jkGetSign!=='function')throw Error('HTTP_SESSION_BINDING_INVALID');`;
  const session = await evaluateValue<JackyunSession>(client, `(()=>{${guard}
    let plate=topWindow.jkMainFramePage.plate;
    if(jkUtils.isInJkyunCef()&&topWindow.signPlate==='jkyun')plate='jackyun';
    if(!['jackyun','jackyun_wdgj'].includes(plate)||isInCef)throw Error('HTTP_SESSION_PLATFORM_UNSUPPORTED');
    const s=jkUtils.signInfo[plate]();
    return {accessToken:s.token,refreshToken:localStorage.getItem('refresh_token'),appkey:s.appkey,signingSecret:s.secret,
      cookie:document.cookie,userAgent:navigator.userAgent,ati:jkUtils.getAti()||''};
  })()`);
  const probe = { keyWords: "签名校验 &+", pageIndex: 0, pageSize: 10, empty: "", nil: null };
  const webSign = await evaluateValue<Record<string, string>>(client, `jkUtils.jkGetSign(${JSON.stringify(probe)},null,false)`);
  if (webSign.sign !== signJackyunForm(probe, session, Number(webSign.timestamp)).get("sign")) throw new Error("HTTP_SIGNATURE_PARITY_FAILED");
  return new JackyunHttpSession(session, { allowRefresh: true, publishSession: async (previous, next) => {
    try {
      await evaluateValue(client, `(()=>{${guard}
        if(jkUtils.cookie.getCookie('token')!==${JSON.stringify(previous.accessToken)}||localStorage.getItem('refresh_token')!==${JSON.stringify(previous.refreshToken)})throw Error('SESSION_CAS_FAILED');
        jkUtils.cookie.setCookie('token',${JSON.stringify(next.accessToken)},'','basePath');
        localStorage.setItem('refresh_token',${JSON.stringify(next.refreshToken)});
        if(jkUtils.cookie.getCookie('token')!==${JSON.stringify(next.accessToken)}||localStorage.getItem('refresh_token')!==${JSON.stringify(next.refreshToken)})throw Error('SESSION_PUBLISH_FAILED');
        return true;
      })()`);
    } catch { throw new Error("HTTP_SESSION_PUBLICATION_FAILED"); }
  } });
}

export async function readDirectTasks(http: JackyunHttpSession, module: JackyunModule, since = new Date().toISOString()): Promise<WebTaskSnapshot> {
  const sinceMs = Math.floor(Date.parse(since) / 1000) * 1000;
  if (!Number.isFinite(sinceMs)) throw new Error("HTTP_TASK_TIME_INVALID");
  const snapshot: WebTaskSnapshot = { records: [], failedIds: [] };
  const seen = new Set<string>();
  const deadline = Date.now() + 60000;
  for (let pageIndex = 0; pageIndex < 10; pageIndex++) {
    if (Date.now() >= deadline) throw new Error("HTTP_TASK_WINDOW_TIMEOUT");
    const res = await http.request<{ data: Record<string, unknown>[]; pageInfo: { total: number } }>("tasks", {
      pageIndex, pageSize: 10, timeStamp: Date.now(), keyWords: schemas[module][3],
    });
    if (!Array.isArray(res.data) || res.data.length > 10 || !Number.isSafeInteger(res.pageInfo?.total) || res.pageInfo.total < 0) throw new Error("HTTP_TASK_LIST_INVALID");
    for (const row of res.data) {
      const taskId = "sys-" + row.id;
      if (!/^sys-\d{1,20}$/.test(taskId) || typeof row.id === "number" && !Number.isSafeInteger(row.id)
        || !Number.isFinite(Number(row.gmtCreate)) || typeof row.taskTitle !== "string"
        || row.attachmentList != null && !Array.isArray(row.attachmentList)) throw new Error("HTTP_TASK_RECORD_INVALID");
      if (seen.has(taskId) || snapshot.records.length && Number(row.gmtCreate) > snapshot.records.at(-1)!.createdAt) throw new Error("HTTP_TASK_PAGINATION_CHANGED");
      seen.add(taskId);
      snapshot.records.push({ taskId, label: row.taskTitle.replace(/^【成功】|【失败】/, ""), createdAt: Number(row.gmtCreate), completed: Number(row.taskStatus) === 4,
        urls: ((row.attachmentList ?? []) as { attachmentUrl: unknown }[]).map(a => String(a.attachmentUrl ?? "")) });
      if (Number(row.taskStatus) === 5) snapshot.failedIds.push(taskId);
    }
    if (snapshot.records.length === res.pageInfo.total || snapshot.records.some(r => r.createdAt < sinceMs)) return snapshot;
    if (res.data.length < 10) throw new Error("HTTP_TASK_PAGINATION_CHANGED");
  }
  throw new Error("HTTP_TASK_LIST_TRUNCATED");
}
