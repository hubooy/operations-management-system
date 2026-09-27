import { createHash } from "node:crypto";

// This is the authenticated website protocol, not the separately licensed Open API.
// Signing material and credentials are supplied at runtime and never serialized here.
export type JackyunSession = { accessToken: string; refreshToken: string; appkey: string; signingSecret: string;
  cookie?: string; userAgent?: string; ati?: string };
export type JackyunHttpOperation = keyof typeof operations;
const operations = {
  tasks: { path: "/jkyun/tms/taskmanage/sysTaskInfoList", method: "GET", replaySafe: true },
  validateExport: { path: "/jkyun/excel-service/manager/validateExcelExport", method: "POST", replaySafe: true },
  submitExport: { path: "/jkyun/excel-service/manager/startExcelExport", method: "POST", replaySafe: false },
  warehouses: { path: "/jkyun/erp-baseinfo/warehouse/warehousepullsimpl", method: "GET", replaySafe: true },
  owners: { path: "/jkyun/birc/open/erp/owner/list", method: "POST", replaySafe: true },
  inventoryCount: { path: "/jkyun/erp-stock/warehouseStock/stockSkuCount", method: "POST", replaySafe: true },
  goodsCount: { path: "/jkyun/erp-goods/search/getskulistbyconditioncount", method: "POST", replaySafe: true },
  ageCount: { path: "/jkyun/birc/open/erp/report/stockAgeReport/pageTotal", method: "POST", replaySafe: true },
  salesCount: { path: "/jkyun/oms-flow/trade/detailCount", method: "POST", replaySafe: true },
  roleFunctions: { path: "/jkyun/erp/open/role/getfunbyuserid", method: "GET", replaySafe: true },
  rolePermissions: { path: "/jkyun/erp/open/role/getpermissionbyuserid", method: "GET", replaySafe: true },
  dataFieldPermissions: { path: "/jkyun/erp-baseinfo/open/role/listdatafieldbyuserid", method: "GET", replaySafe: true },
} as const;
const origin = "https://web.jackyun.com";
export type JackyunServerClock = { requestStartedAt: string; receivedAt: string; serverDate: string };
class JackyunHttpTransportFailure extends Error {
  constructor(error?: unknown) {
    const value = error as { name?: string; cause?: { code?: string } } | undefined;
    const raw = value?.name === "TimeoutError" ? "TIMEOUT" : value?.cause?.code;
    const code = ["TIMEOUT", "UND_ERR_CONNECT_TIMEOUT", "UND_ERR_HEADERS_TIMEOUT", "ECONNRESET", "ETIMEDOUT", "ENOTFOUND", "ECONNREFUSED", "UND_ERR_SOCKET"].includes(raw ?? "") ? raw : "NETWORK_ERROR";
    super(`JACKYUN_HTTP_RESPONSE_UNVERIFIED:${code}`);
  }
}

function assertSession(value: JackyunSession) {
  if (![value.accessToken, value.refreshToken, value.appkey, value.signingSecret].every(v =>
    typeof v === "string" && v.length > 0 && v.length < 16384 && !/[\r\n]/.test(v))) {
    throw new Error("JACKYUN_SESSION_INVALID");
  }
  if ([value.cookie, value.userAgent, value.ati].some(v => v !== undefined && (typeof v !== "string" || v.length > 32768 || /[\r\n]/.test(v)))) throw new Error("JACKYUN_SESSION_INVALID");
}

/** Matches jkUtils.jkGetSign, including unsigned empty/whitespace/undefined values. */
export function signJackyunForm(data: Record<string, unknown>, session: JackyunSession, timestamp: number) {
  assertSession(session);
  if (!Number.isSafeInteger(timestamp) || timestamp < 0) throw new Error("JACKYUN_TIMESTAMP_INVALID");
  const values: Record<string, string> = {};
  const signed: Record<string, string> = {
    timestamp: String(timestamp), access_token: `Bearer ${session.accessToken}`, appkey: session.appkey,
  };
  for (const [key, value] of Object.entries(data)) {
    if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(key) || ["timestamp", "access_token", "appkey", "sign", "__proto__", "constructor", "prototype"].includes(key)) {
      throw new Error("JACKYUN_PARAMETER_INVALID");
    }
    if (["function", "symbol", "bigint"].includes(typeof value)) throw new Error("JACKYUN_PARAMETER_INVALID");
    let serialized: string;
    try { serialized = typeof value === "object" && value !== null ? JSON.stringify(value) : String(value); }
    catch { throw new Error("JACKYUN_PARAMETER_INVALID"); }
    if (typeof serialized !== "string") throw new Error("JACKYUN_PARAMETER_INVALID");
    values[key] = serialized;
    if (value !== undefined && value !== "" && !(typeof value === "string" && /^\s+$/.test(value))) signed[key] = serialized;
  }
  const canonical = Object.keys(signed).sort().map(key => key + signed[key]).join("");
  const sign = createHash("md5").update(session.signingSecret + canonical + session.signingSecret).digest("hex").toUpperCase();
  return new URLSearchParams({ ...values, timestamp: signed.timestamp, access_token: signed.access_token, appkey: signed.appkey, sign });
}

type SessionOptions = {
  fetch?: typeof fetch;
  now?: () => number;
  allowRefresh?: boolean;
  /** Must atomically verify the previous pair before replacing both tokens. Failure poisons this owner. */
  publishSession: (previous: Readonly<JackyunSession>, next: Readonly<JackyunSession>) => Promise<void>;
};

/** One instance per held Jackyun run lock. No background timer or second refresh owner. */
export class JackyunHttpSession {
  #session: JackyunSession;
  #generation = 0;
  #refreshing?: Promise<void>;
  #poisoned = false;
  #fetch: typeof fetch;
  #now: () => number;
  #publish: SessionOptions["publishSession"];
  #allowRefresh: boolean;
  #clock?: JackyunServerClock;
  get serverClock(): JackyunServerClock | undefined { return this.#clock && { ...this.#clock }; }
  constructor(session: JackyunSession, options: SessionOptions) {
    assertSession(session);
    this.#session = { ...session };
    this.#fetch = options.fetch ?? fetch;
    this.#now = options.now ?? Date.now;
    this.#publish = options.publishSession;
    this.#allowRefresh = options.allowRefresh === true;
  }

  #headers(moduleCode = ""): Record<string, string> {
    return { Authorization: `Bearer ${this.#session.accessToken}`, module_code: moduleCode,
      "Content-Type": "application/x-www-form-urlencoded", Origin: origin, Referer: origin + "/", "X-Requested-With": "XMLHttpRequest",
      ...(this.#session.cookie ? { Cookie: this.#session.cookie } : {}),
      ...(this.#session.userAgent ? { "User-Agent": this.#session.userAgent } : {}),
      ...(this.#session.ati ? { ati: this.#session.ati } : {}),
    };
  }

  async #json(path: string, init: RequestInit, timeoutMs = 15000): Promise<{ status: number; body: Record<string, unknown> }> {
    try {
      this.#clock = undefined;
      const requestStartedAt = new Date(this.#now()).toISOString();
      const response = await this.#fetch(origin + path, { ...init, redirect: "error", signal: AbortSignal.timeout(timeoutMs) })
        .catch(error => { throw new JackyunHttpTransportFailure(error); });
      if (!response.headers.get("content-type")?.toLowerCase().includes("application/json")) throw new Error();
      const reader = response.body?.getReader();
      if (!reader) throw new Error();
      const chunks: Uint8Array[] = [];
      let size = 0;
      try {
        for (;;) {
          const part = await reader.read().catch(error => { throw new JackyunHttpTransportFailure(error); });
          if (part.done) break;
          size += part.value.byteLength;
          if (size > 2 * 1024 * 1024) throw new Error();
          chunks.push(part.value);
        }
      } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
      const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      if (!body || Array.isArray(body) || typeof body !== "object") throw new Error();
      const serverDate = response.headers.get("date");
      if (serverDate && Number.isFinite(Date.parse(serverDate))) this.#clock = { requestStartedAt, receivedAt: new Date(this.#now()).toISOString(), serverDate: new Date(Date.parse(serverDate)).toISOString() };
      return { status: response.status, body };
    } catch (error) {
      // A transport error can contain signed URLs, tokens or response contents.
      if (error instanceof JackyunHttpTransportFailure) throw error;
      throw new Error("JACKYUN_HTTP_RESPONSE_UNVERIFIED");
    }
  }

  async #refresh(observedGeneration: number) {
    if (this.#poisoned) throw new Error("JACKYUN_SESSION_RELOGIN_REQUIRED");
    if (observedGeneration !== this.#generation) return;
    if (this.#refreshing) return this.#refreshing;
    this.#refreshing = (async () => {
      const previous = { ...this.#session };
      try {
        const { status, body } = await this.#json("/auth/refresh", {
          method: "POST", headers: { ...this.#headers(), clientId: "jackyun_web_browser" },
          body: new URLSearchParams({ refreshToken: previous.refreshToken }).toString(),
        });
        if (status !== 200 || typeof body.access_token !== "string" || typeof body.refresh_token !== "string") throw new Error();
        const next = { ...previous, accessToken: body.access_token, refreshToken: body.refresh_token };
        if (previous.cookie) next.cookie = previous.cookie.split(/;\s*/).map(part => part.startsWith("token=") ? `token=${encodeURIComponent(next.accessToken)}` : part).join("; ");
        assertSession(next);
        await this.#publish(Object.freeze(previous), Object.freeze(next));
        this.#session = next;
        this.#generation++;
      } catch {
        this.#poisoned = true;
        throw new Error("JACKYUN_SESSION_RELOGIN_REQUIRED");
      }
    })();
    try { await this.#refreshing; } finally { this.#refreshing = undefined; }
  }

  async request<T>(operation: JackyunHttpOperation, data: Record<string, unknown>, moduleCode = ""): Promise<T> {
    const spec = operations[operation];
    if (!spec || !/^[A-Za-z0-9_.-]{0,100}$/.test(moduleCode)) throw new Error("JACKYUN_OPERATION_INVALID");
    if (this.#poisoned) throw new Error("JACKYUN_SESSION_RELOGIN_REQUIRED");
    // A request starting during renewal must not send the superseded token.
    if (this.#refreshing) await this.#refreshing;
    for (let attempt = 0; attempt < 2; attempt++) {
      const generation = this.#generation;
      const form = signJackyunForm(data, this.#session, this.#now());
      const headers = this.#headers(moduleCode);
      let response: { status: number; body: Record<string, unknown> };
      try {
        response = await this.#json(spec.path + (spec.method === "GET" ? "?" + form.toString() : ""), {
          method: spec.method, headers, ...(spec.method === "POST" ? { body: form.toString() } : {}),
        }, operation === "submitExport" ? 120000 : 15000);
      } catch (error) {
        if (operation === "tasks" && attempt === 0 && error instanceof JackyunHttpTransportFailure) {
          await new Promise(resolve => setTimeout(resolve, 250));
          continue;
        }
        throw error;
      }
      const { status, body } = response;
      if (["0190210000", "0190210006", "0031117002"].includes(String(body.subCode))) throw new Error("JACKYUN_HTTP_VERIFICATION_REQUIRED");
      if (status === 200 && body.code === 200 && body.result && typeof body.result === "object") return body.result as T;
      if (status === 401 && body.subCode === "0190210003" && attempt === 0 && spec.replaySafe && this.#allowRefresh) {
        await this.#refresh(generation);
        continue;
      }
      // No replay after task submission, transport uncertainty, denial or risk verification.
      throw new Error(spec.replaySafe ? "JACKYUN_HTTP_REQUEST_REJECTED" : "JACKYUN_EXPORT_SUBMISSION_UNCONFIRMED");
    }
    throw new Error("JACKYUN_SESSION_RELOGIN_REQUIRED");
  }
}
