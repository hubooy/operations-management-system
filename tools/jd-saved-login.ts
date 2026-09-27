import type { Frame, Locator, Page } from "playwright-core";

import type { JdStore } from "../lib/jd/store-registry";
import { readJdRuntimeCredential, type JdRuntimeCredential } from "./jd-secure-credential";

export type JdAutomatedLoginResult = {
  attempted: boolean;
  submitted: boolean;
  reason:
    | "submitted"
    | "login_form_missing"
    | "challenge_present"
    | "login_control_missing"
    | "login_control_ambiguous";
};

export type JdLoginPageState = {
  challengePresent: boolean;
  credentialRejected: boolean;
  temporarilyLocked: boolean;
};

export const jdAutomatedLoginFormWaitMs = 20_000;

async function visibleCandidates(locator: Locator, limit = 20) {
  const candidates: Locator[] = [];
  const count = Math.min(await locator.count().catch(() => 0), limit);
  for (let index = 0; index < count; index += 1) {
    const candidate = locator.nth(index);
    if (await candidate.isVisible().catch(() => false)) candidates.push(candidate);
  }
  return candidates;
}

async function inspectFrameState(frame: Frame): Promise<JdLoginPageState> {
  return frame.evaluate(() => {
    const visible = (element: Element) => {
      const rect = element.getBoundingClientRect();
      const style = element.ownerDocument.defaultView?.getComputedStyle(element);
      return rect.width > 2 && rect.height > 2 && style?.display !== "none" && style?.visibility !== "hidden";
    };
    const fieldName = (input: HTMLInputElement) => [
      input.type,
      input.name,
      input.id,
      input.getAttribute("autocomplete"),
      input.getAttribute("placeholder"),
      input.getAttribute("aria-label"),
    ].filter(Boolean).join(" ");
    const text = String(document.body?.innerText ?? "");
    const challengePresent = /验证码|短信验证|短信验证码|动态验证码|安全验证|安全校验|人机验证|滑块验证|请.{0,8}(?:滑动|拖动)|访问验证/.test(text)
      || Array.from(document.querySelectorAll("iframe")).some((element) => visible(element)
        && /captcha|verify|challenge|risk|punish/i.test(element.getAttribute("src") ?? ""))
      || Array.from(document.querySelectorAll("input")).some((input) => visible(input)
        && /captcha|verify|challenge|验证码|校验码|动态码/i.test(fieldName(input)));
    return {
      challengePresent,
      credentialRejected: /账[号户].{0,12}(?:密码|登录).{0,12}(?:错误|不正确|不匹配)|密码.{0,10}(?:错误|不正确)|用户名.{0,10}(?:错误|不存在)|登录失败/.test(text),
      temporarilyLocked: /操作频繁|次数过多|登录过于频繁|账[号户].{0,8}(?:锁定|冻结|受限)/.test(text),
    };
  });
}

export async function inspectJdLoginPageState(page: Pick<Page, "frames">): Promise<JdLoginPageState> {
  const states = await Promise.all(page.frames().map((frame) => inspectFrameState(frame).catch(() => ({
    challengePresent: false,
    credentialRejected: false,
    temporarilyLocked: false,
  }))));
  return states.reduce<JdLoginPageState>((combined, state) => ({
    challengePresent: combined.challengePresent || state.challengePresent,
    credentialRejected: combined.credentialRejected || state.credentialRejected,
    temporarilyLocked: combined.temporarilyLocked || state.temporarilyLocked,
  }), { challengePresent: false, credentialRejected: false, temporarilyLocked: false });
}

async function secureLoginForm(frame: Frame) {
  const passwords = await visibleCandidates(frame.locator([
    "#nloginpwd",
    'input[name="nloginpwd"]',
    'input[type="password"]',
    'input[name*="password" i]',
    'input[id*="password" i]',
  ].join(",")));
  const accounts = (await visibleCandidates(frame.locator([
    "#loginname",
    'input[name="loginname"]',
    "#username",
    'input[name="username"]',
    'input[autocomplete="username"]',
    'input[type="email"]',
    'input[type="tel"]',
  ].join(",")))).filter((candidate) => candidate !== passwords[0]);
  if (passwords.length !== 1 || accounts.length !== 1) return null;
  return { frame, account: accounts[0]!, password: passwords[0]! };
}

async function selectUniquePasswordLoginMode(page: Page) {
  const candidates: Array<{ frame: Frame; control: Locator }> = [];
  for (const frame of page.frames()) {
    // The current JD merchant login renders the account/password tab as a
    // clickable outer div rather than a button or link. Scope the extension to
    // that observed stable class and retain the exact-label + global-uniqueness
    // fence; do not click its duplicate child span.
    const controls = await visibleCandidates(frame.locator('button,a,[role="button"],[role="tab"],div.tabs__item.tabs__item-click'));
    for (const control of controls) {
      const label = String(await control.textContent().catch(() => "")).replace(/\s+/g, "").trim();
      if (["账户登录", "账号登录", "密码登录", "账号密码登录"].includes(label)) {
        candidates.push({ frame, control });
      }
    }
  }
  if (candidates.length > 1) return "ambiguous" as const;
  if (candidates.length === 0) return "missing" as const;
  await candidates[0]!.control.click();
  await page.waitForTimeout(250);
  return "selected" as const;
}

/**
 * Fill one unique JD password form from the current Windows user's DPAPI
 * vault. Credentials never enter n8n, arguments, environment variables or
 * logs, and the page fields are never read back.
 */
export async function autoLoginJdWithWindowsDpapiCredential(
  page: Page,
  storeKey: string,
  loadCredential: (key: string) => Promise<JdRuntimeCredential> = readJdRuntimeCredential,
  waitMs = jdAutomatedLoginFormWaitMs,
): Promise<JdAutomatedLoginResult> {
  const deadline = Date.now() + Math.max(0, waitMs);
  let passwordModeSelected = false;
  let forms: Array<NonNullable<Awaited<ReturnType<typeof secureLoginForm>>>> = [];
  do {
    const state = await inspectJdLoginPageState(page);
    if (state.challengePresent) {
      return { attempted: false, submitted: false, reason: "challenge_present" };
    }
    forms = (await Promise.all(page.frames().map((frame) => secureLoginForm(frame).catch(() => null))))
      .filter((form): form is NonNullable<typeof form> => Boolean(form));
    if (forms.length > 1) {
      return { attempted: false, submitted: false, reason: "login_control_ambiguous" };
    }
    if (forms.length === 1) break;
    if (!passwordModeSelected) {
      const mode = await selectUniquePasswordLoginMode(page);
      if (mode === "ambiguous") {
        return { attempted: false, submitted: false, reason: "login_control_ambiguous" };
      }
      passwordModeSelected = mode === "selected";
      if (passwordModeSelected) continue;
    }
    if (Date.now() >= deadline) break;
    await page.waitForTimeout(Math.min(250, Math.max(0, deadline - Date.now())));
  } while (Date.now() <= deadline);
  if (forms.length !== 1) {
    return { attempted: false, submitted: false, reason: "login_form_missing" };
  }

  const form = forms[0]!;
  const credential = await loadCredential(storeKey);
  let username = credential.username;
  let password = credential.password;
  try {
    await form.account.fill(username);
    await form.password.fill(password);
  } finally {
    username = "";
    password = "";
    credential.username = "";
    credential.password = "";
  }

  const state = await inspectJdLoginPageState(page);
  if (state.challengePresent) {
    return { attempted: false, submitted: false, reason: "challenge_present" };
  }
  const controls = await visibleCandidates(form.frame.locator('#loginsubmit,button,input[type="submit"],[role="button"]'));
  const exact: Locator[] = [];
  for (const control of controls) {
    const label = String(await control.textContent().catch(() => "")
      || await control.getAttribute("value").catch(() => ""))
      .replace(/\s+/g, "").trim();
    if (["登录", "立即登录"].includes(label)) exact.push(control);
  }
  if (exact.length !== 1) {
    return {
      attempted: true,
      submitted: false,
      reason: exact.length > 1 ? "login_control_ambiguous" : "login_control_missing",
    };
  }
  await exact[0]!.click();
  return { attempted: true, submitted: true, reason: "submitted" };
}

export type JdSessionSurface = "authenticated" | "login" | "pending";

export function jdSessionSurfaceDecision(url: string, bodyText: string, hasPassword: boolean): JdSessionSurface {
  if (/passport|login/i.test(url)
    || (hasPassword && /登录/.test(bodyText) && /账号|账户|手机|用户名/.test(bodyText))) return "login";
  if (/^https:\/\/jdsz\.jd\.com\/szweb\/view\/industry\/industry-top\.html(?:$|[?#])/i.test(url)
    && /行业榜单/.test(bodyText) && /商品榜/.test(bodyText) && /下载数据/.test(bodyText)) return "authenticated";
  if (/商品明细|下载中心|导出查询商品|批量操作|商品管理|出售中的商品|商品列表/.test(bodyText)) return "authenticated";
  return "pending";
}

async function readJdSessionSurface(page: Page): Promise<JdSessionSurface> {
  const surfaces = await Promise.all(page.frames().map(async (frame) => {
    const bodyText = await frame.locator("body").innerText({ timeout: 1_000 }).catch(() => "");
    const hasPassword = await frame.locator('input[type="password"],#nloginpwd').count()
      .then((count) => count > 0)
      .catch(() => false);
    return jdSessionSurfaceDecision(frame.url(), bodyText, hasPassword);
  }));
  if (surfaces.includes("login")) return "login";
  if (surfaces.includes("authenticated")) return "authenticated";
  return "pending";
}

export async function waitForJdSessionSurface(page: Page, timeoutMs = 15_000): Promise<JdSessionSurface> {
  const deadline = Date.now() + Math.max(0, timeoutMs);
  do {
    const surface = await readJdSessionSurface(page);
    if (surface !== "pending") return surface;
    if (Date.now() >= deadline) break;
    await page.waitForTimeout(Math.min(250, Math.max(0, deadline - Date.now())));
  } while (Date.now() <= deadline);
  return "pending";
}

export async function isJdLoginSurface(page: Page) {
  return await readJdSessionSurface(page) === "login";
}

export async function ensureJdStoreAuthenticatedSession(
  page: Page,
  store: Pick<JdStore, "storeKey" | "shopName" | "loginMode">,
  timeoutMs = 60_000,
) {
  const initialState = await inspectJdLoginPageState(page);
  if (initialState.challengePresent) {
    throw new Error(`waiting_login：${store.shopName} 出现验证码或安全验证，需要人工处理`);
  }
  const initialSurface = await waitForJdSessionSurface(page);
  if (initialSurface === "authenticated") {
    return { status: "authenticated" as const, authentication: "existing_session" as const };
  }
  if (initialSurface === "pending") {
    throw new Error(`waiting_login：${store.shopName} 登录状态在有界等待后仍无法确认，需要人工检查`);
  }
  if (store.loginMode !== "windows_dpapi_credentials") {
    throw new Error(`waiting_login：${store.shopName} 独立 Chromium 尚未登录，请先人工登录`);
  }

  const login = await autoLoginJdWithWindowsDpapiCredential(page, store.storeKey);
  if (login.reason === "challenge_present") {
    throw new Error(`waiting_login：${store.shopName} 出现验证码或安全验证，需要人工处理`);
  }
  if (!login.submitted) {
    const reason = login.reason === "login_control_ambiguous"
      ? "登录表单或按钮不唯一"
      : login.reason === "login_control_missing"
        ? "登录按钮缺失或不可用"
        : "唯一账号密码表单尚未就绪";
    throw new Error(`waiting_login：${store.shopName} ${reason}，请人工检查`);
  }

  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const state = await inspectJdLoginPageState(page);
    if (state.challengePresent) {
      throw new Error(`waiting_login：${store.shopName} 自动登录后出现验证码或安全验证，需要人工处理`);
    }
    if (state.credentialRejected) {
      throw new Error(`waiting_login：${store.shopName} 本机加密凭据未被平台接受，请重新配置并人工核验`);
    }
    if (state.temporarilyLocked) {
      throw new Error(`waiting_login：${store.shopName} 登录操作受限或过于频繁，需要稍后人工检查`);
    }
    const surface = await readJdSessionSurface(page);
    if (surface === "authenticated") {
      return { status: "authenticated" as const, authentication: "windows_dpapi_credentials" as const };
    }
    await page.waitForTimeout(250);
  }
  throw new Error(`waiting_login：${store.shopName} 自动提交后仍未完成登录，可能需要人工安全验证`);
}
