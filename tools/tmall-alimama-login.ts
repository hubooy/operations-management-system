import type { Page } from "playwright-core";
import type { TmallStore } from "../lib/netshop/tmall-store-registry";
import { autoLoginTmallWithWindowsDpapiCredential, inspectTmallLoginPageState } from "./tmall-saved-login";

const attempts = new WeakMap<object, string>();
const wrongSessionResets = new WeakMap<object, string>();
const loginHosts = new Set(["login.taobao.com", "loginmyseller.taobao.com"]);

export function trustedAlimamaLoginUrl(value: string, frame = false): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password && !url.port
      && (loginHosts.has(url.hostname) || (!frame && url.hostname === "one.alimama.com"));
  } catch { return false; }
}

export async function resetDedicatedAlimamaSession(page: Page) {
  if (new URL(page.url()).hostname !== "one.alimama.com") {
    throw new Error("unexpected_origin");
  }
  await page.evaluate(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
  await page.context().clearCookies();
  try {
    await page.goto("https://one.alimama.com/index.html", {
      waitUntil: "domcontentloaded",
      timeout: 60_000,
    });
  } catch (error) {
    // Clearing the login cookies can make Alimama abort the navigation while it
    // redirects to Taobao login. The next loop still inspects the page, limits
    // credential submission to trusted login frames, and verifies store identity.
    if (!trustedAlimamaLoginUrl(page.url())) throw error;
  }
}

/** Login only: never creates a report. An uncertain submission consumes the attempt. */
export async function ensureAlimamaLogin(
  page: Page,
  store: Pick<TmallStore, "storeKey" | "loginMode">,
  assertIdentity: () => Promise<void>,
  options: {
    timeoutMs?: number;
    wait?: () => Promise<void>;
    inspect?: typeof inspectTmallLoginPageState;
    login?: typeof autoLoginTmallWithWindowsDpapiCredential;
    resetWrongSession?: (page: Page) => Promise<void>;
  } = {},
) {
  const deadline = Date.now() + (options.timeoutMs ?? 60_000);
  const inspect = options.inspect ?? inspectTmallLoginPageState;
  const login = options.login ?? autoLoginTmallWithWindowsDpapiCredential;
  let submitted = false;
  do {
    if (!trustedAlimamaLoginUrl(page.url())) throw new Error("waiting_login：阿里妈妈登录页面来源未经许可");
    const state = await inspect(page);
    if (state.challengePresent || state.credentialRejected || state.temporarilyLocked) {
      throw new Error("waiting_login：阿里妈妈需要人工验证或凭据被拒绝，停止自动登录");
    }
    let needsLogin = new URL(page.url()).hostname !== "one.alimama.com";
    try {
      // Never accept store-name text on a login origin as authenticated identity.
      if (new URL(page.url()).hostname === "one.alimama.com") {
        await assertIdentity();
        return;
      }
    } catch (error) {
      if (!(error instanceof Error) || !/^(waiting_login|shop_identity_mismatch)/.test(error.message)) throw error;
      if (error.message.startsWith("shop_identity_mismatch")) {
        const resetStoreKey = wrongSessionResets.get(page.context());
        const attemptStoreKey = attempts.get(page.context());
        if (store.loginMode !== "windows_dpapi_credentials"
          || (attemptStoreKey && attemptStoreKey !== store.storeKey)
          || (resetStoreKey && resetStoreKey !== store.storeKey)) {
          throw new Error("waiting_login：阿里妈妈店铺身份不符且无法安全切换，请人工登录");
        }
        if (!resetStoreKey && !attemptStoreKey) {
          wrongSessionResets.set(page.context(), store.storeKey);
          try {
            await (options.resetWrongSession ?? resetDedicatedAlimamaSession)(page);
          } catch {
            throw new Error("waiting_login：阿里妈妈错店会话清理未确认成功，请人工登录");
          }
          if (Date.now() >= deadline) break;
          await (options.wait ?? (() => new Promise<void>((resolve) => setTimeout(resolve, 500))))();
          continue;
        }
        // The cleared page can briefly retain the old SPA identity while its
        // trusted Taobao login frame loads. After DPAPI submission the correct
        // identity text can also render after the main URL returns. Do not reset
        // or submit again; keep waiting behind the exact identity proof.
        needsLogin = true;
      } else {
        needsLogin = true;
      }
    }
    const frames = page.frames().filter((frame) => trustedAlimamaLoginUrl(frame.url(), true));
    if (needsLogin && !submitted && frames.length) {
      if (store.loginMode !== "windows_dpapi_credentials" || attempts.has(page.context())) {
        throw new Error("waiting_login：阿里妈妈自动登录不可用或本轮已尝试，请人工登录");
      }
      // Context-level fencing also prevents a second attempt from a different tab/store.
      attempts.set(page.context(), store.storeKey);
      try {
        // Existing helper only needs frames; prevent credentials from reaching arbitrary iframes.
        const scoped = { frames: () => trustedAlimamaLoginUrl(page.url())
          ? page.frames().filter((frame) => trustedAlimamaLoginUrl(frame.url(), true)) : [] } as unknown as Page;
        const result = await login(scoped, store.storeKey, undefined, undefined,
          (frame) => trustedAlimamaLoginUrl(page.url()) && trustedAlimamaLoginUrl(frame.url(), true));
        if (!result.submitted) throw new Error("not_submitted");
        submitted = true;
      } catch {
        // Playwright fill errors may include their argument. Never propagate them.
        throw new Error("waiting_login：阿里妈妈加密凭据登录未确认成功，禁止自动重试");
      }
    }
    if (Date.now() >= deadline) break;
    await (options.wait ?? (() => new Promise<void>((resolve) => setTimeout(resolve, 500))))();
  } while (Date.now() < deadline);
  throw new Error("waiting_login：阿里妈妈店铺身份未确认，未开始推广业务");
}
