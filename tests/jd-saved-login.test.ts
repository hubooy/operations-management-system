import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import type { Frame, Page } from "playwright-core";

import { autoLoginJdWithWindowsDpapiCredential, inspectJdLoginPageState, jdAutomatedLoginFormWaitMs, jdSessionSurfaceDecision, waitForJdSessionSurface } from "../tools/jd-saved-login";

function secureLoginPage(options: { challenge?: boolean; forms?: number; controls?: number } = {}) {
  const filled = { account: "", password: "", clicked: false };
  const frames = Array.from({ length: options.forms ?? 1 }, () => {
    const account = {
      isVisible: async () => true,
      fill: async (value: string) => { filled.account = value; },
    };
    const password = {
      isVisible: async () => true,
      fill: async (value: string) => { filled.password = value; },
    };
    const controls = Array.from({ length: options.controls ?? 1 }, () => ({
      isVisible: async () => true,
      textContent: async () => "登 录",
      getAttribute: async () => null,
      click: async () => { filled.clicked = true; },
    }));
    const collection = (items: unknown[]) => ({
      count: async () => items.length,
      nth: (index: number) => items[index],
    });
    return {
      evaluate: async () => ({
        challengePresent: Boolean(options.challenge),
        credentialRejected: false,
        temporarilyLocked: false,
      }),
      locator: (selector: string) => {
        if (selector.includes("#nloginpwd")) return collection([password]);
        if (selector.includes("#loginname")) return collection([account]);
        return collection(controls);
      },
    } as unknown as Frame;
  });
  return { page: { frames: () => frames } as unknown as Page, filled };
}

test("JD DPAPI login fills one unique form once and clears the in-memory credential", async () => {
  const { page, filled } = secureLoginPage();
  const credential = { username: "vault-account", password: "vault-password" };
  const result = await autoLoginJdWithWindowsDpapiCredential(page, "jd-yiyong-director", async () => credential);
  assert.deepEqual(result, { attempted: true, submitted: true, reason: "submitted" });
  assert.deepEqual(filled, { account: "vault-account", password: "vault-password", clicked: true });
  assert.deepEqual(credential, { username: "", password: "" });
});

test("JD DPAPI login fails closed before reading credentials for challenge or cross-frame ambiguity", async () => {
  let reads = 0;
  const challenged = secureLoginPage({ challenge: true });
  assert.deepEqual(await autoLoginJdWithWindowsDpapiCredential(
    challenged.page,
    "jd-yiyong-director",
    async () => { reads += 1; return { username: "unused", password: "unused" }; },
  ), { attempted: false, submitted: false, reason: "challenge_present" });
  assert.equal(reads, 0);
  assert.equal(challenged.filled.clicked, false);

  const ambiguous = secureLoginPage({ forms: 2 });
  assert.deepEqual(await autoLoginJdWithWindowsDpapiCredential(
    ambiguous.page,
    "jd-yiyong-director",
    async () => { reads += 1; return { username: "unused", password: "unused" }; },
  ), { attempted: false, submitted: false, reason: "login_control_ambiguous" });
  assert.equal(reads, 0);
});

test("JD DPAPI login waits for a delayed unique password form and still submits only once", async () => {
  let samples = 0;
  let reads = 0;
  let submissions = 0;
  const account = { isVisible: async () => true, fill: async () => undefined };
  const password = { isVisible: async () => true, fill: async () => undefined };
  const submit = {
    isVisible: async () => true,
    textContent: async () => "登录",
    getAttribute: async () => null,
    click: async () => { submissions += 1; },
  };
  const collection = (items: unknown[]) => ({ count: async () => items.length, nth: (index: number) => items[index] });
  const frame = {
    evaluate: async () => ({ challengePresent: false, credentialRejected: false, temporarilyLocked: false }),
    locator: (selector: string) => {
      if (selector.includes("#nloginpwd")) return collection(samples >= 2 ? [password] : []);
      if (selector.includes("#loginname")) return collection(samples >= 2 ? [account] : []);
      if (selector.includes("#loginsubmit")) return collection(samples >= 2 ? [submit] : []);
      return collection([]);
    },
  } as unknown as Frame;
  const page = {
    frames: () => [frame],
    waitForTimeout: async () => { samples += 1; },
  } as unknown as Page;
  const result = await autoLoginJdWithWindowsDpapiCredential(page, "jd-chudian-weizhang", async () => {
    reads += 1;
    return { username: "vault-account", password: "vault-password" };
  }, 1_000);
  assert.deepEqual(result, { attempted: true, submitted: true, reason: "submitted" });
  assert.equal(reads, 1);
  assert.equal(submissions, 1);
  assert.ok(samples >= 2);
  assert.equal(jdAutomatedLoginFormWaitMs, 20_000);
});

test("JD DPAPI login selects the unique current div password-mode control before reading credentials", async () => {
  let modeSelected = false;
  let modeClicks = 0;
  let reads = 0;
  let submissions = 0;
  const account = { isVisible: async () => true, fill: async () => undefined };
  const unrelatedLanguageInput = { isVisible: async () => true, fill: async () => { throw new Error("language input must not be filled"); } };
  const password = { isVisible: async () => true, fill: async () => undefined };
  const mode = {
    isVisible: async () => true,
    textContent: async () => "账号密码登录",
    click: async () => { modeSelected = true; modeClicks += 1; },
  };
  const submit = {
    isVisible: async () => true,
    textContent: async () => "登录",
    getAttribute: async () => null,
    click: async () => { submissions += 1; },
  };
  const collection = (items: unknown[]) => ({ count: async () => items.length, nth: (index: number) => items[index] });
  const frame = {
    evaluate: async () => ({ challengePresent: false, credentialRejected: false, temporarilyLocked: false }),
    locator: (selector: string) => {
      if (selector.includes("#nloginpwd")) return collection(modeSelected ? [password] : []);
      if (selector.includes("#loginname")) {
        if (!modeSelected) return collection([]);
        return collection(selector.includes('input[type="text"]') ? [account, unrelatedLanguageInput] : [account]);
      }
      if (selector.includes("#loginsubmit")) return collection(modeSelected ? [submit] : []);
      if (selector.includes("div.tabs__item.tabs__item-click")) return collection(modeSelected ? [] : [mode]);
      return collection([]);
    },
  } as unknown as Frame;
  const page = { frames: () => [frame], waitForTimeout: async () => undefined } as unknown as Page;
  const result = await autoLoginJdWithWindowsDpapiCredential(page, "jd-maidehao-operator1", async () => {
    reads += 1;
    return { username: "vault-account", password: "vault-password" };
  }, 1_000);
  assert.deepEqual(result, { attempted: true, submitted: true, reason: "submitted" });
  assert.equal(modeClicks, 1);
  assert.equal(reads, 1);
  assert.equal(submissions, 1);
});

test("JD login state combines challenge, credential rejection and lock signals without reading fields", async () => {
  const page = {
    frames: () => [{
      evaluate: async () => ({ challengePresent: true, credentialRejected: false, temporarilyLocked: false }),
    }, {
      evaluate: async () => ({ challengePresent: false, credentialRejected: true, temporarilyLocked: true }),
    }] as unknown as Frame[],
  } as Pick<Page, "frames">;
  assert.deepEqual(await inspectJdLoginPageState(page), {
    challengePresent: true,
    credentialRejected: true,
    temporarilyLocked: true,
  });
  const source = await readFile(new URL("../tools/jd-saved-login.ts", import.meta.url), "utf8");
  assert.doesNotMatch(source, /(?:account|password)\.value\b/);
});

test("JD session guard waits through a blank business URL and catches the delayed passport redirect", async () => {
  assert.equal(jdSessionSurfaceDecision("https://jdsz.jd.com/product", "", false), "pending");
  assert.equal(jdSessionSurfaceDecision("https://passport.jd.com/new/login.aspx", "", false), "login");
  assert.equal(jdSessionSurfaceDecision("https://jdsz.jd.com/product", "商品明细", false), "authenticated");
  assert.equal(jdSessionSurfaceDecision(
    "https://jdsz.jd.com/szweb/view/industry/industry-top.html",
    "行业榜单 商品榜 下载数据",
    false,
  ), "authenticated");
  let sample = 0;
  const frame = {
    url: () => sample === 0 ? "https://jdsz.jd.com/szweb/view/product/productDetail.html" : "https://passport.jd.com/new/login.aspx",
    locator: (selector: string) => selector === "body"
      ? { innerText: async () => sample === 0 ? "" : "账号 密码 登录" }
      : { count: async () => sample === 0 ? 0 : 1 },
  } as unknown as Frame;
  const page = {
    frames: () => [frame],
    waitForTimeout: async () => { sample += 1; },
  } as unknown as Page;
  assert.equal(await waitForJdSessionSurface(page, 1_000), "login");
});

test("JD session guard gives a child passport frame precedence over an authenticated market shell", async () => {
  const frame = (url: string, bodyText: string, hasPassword: boolean) => ({
    url: () => url,
    locator: (selector: string) => selector === "body"
      ? { innerText: async () => bodyText }
      : { count: async () => hasPassword ? 1 : 0 },
  }) as unknown as Frame;
  const page = {
    frames: () => [
      frame("https://jdsz.jd.com/szweb/view/industry/industry-top.html", "行业榜单 商品榜 下载数据", false),
      frame("https://passport.jd.com/new/login.aspx", "账号 密码 登录", true),
    ],
    waitForTimeout: async () => undefined,
  } as unknown as Page;
  assert.equal(await waitForJdSessionSurface(page, 0), "login");
});

test("JD DPAPI credentials never travel through n8n, environment variables, CLI secrets or logs", async () => {
  const credentialSource = await readFile(new URL("../tools/jd-secure-credential.ts", import.meta.url), "utf8");
  const vaultSource = await readFile(new URL("../tools/jd-credential-vault.ps1", import.meta.url), "utf8");
  const workflow = await readFile(new URL("../automation/n8n/jd-multi-store-daily.workflow.json", import.meta.url), "utf8");
  assert.doesNotMatch(credentialSource, /process\.env|console\.|--(?:username|password)|JD_(?:USERNAME|PASSWORD)/i);
  assert.doesNotMatch(vaultSource, /Write-(?:Host|Output).*password|ConvertTo-SecureString[^\n]+-Key\b/i);
  assert.doesNotMatch(workflow, /--(?:username|password)|JD_(?:USERNAME|PASSWORD)|"credentials"\s*:/i);
  assert.match(vaultSource, /ProtectedData.*Protect/);
  assert.match(vaultSource, /DataProtectionScope.*CurrentUser/);
  assert.match(vaultSource, /ZeroFreeBSTR/);
});

test("all JD product and market flows authenticate before business identity or export actions", async () => {
  const ware = await readFile(new URL("../tools/jackyun-ware-export.ts", import.meta.url), "utf8");
  const daily = await readFile(new URL("../tools/jdsz-product-detail-export.ts", import.meta.url), "utf8");
  const market = await readFile(new URL("../tools/jd-market-ranking-daily.ts", import.meta.url), "utf8");
  const wareOpen = ware.slice(ware.indexOf("async function openTargetPage"), ware.indexOf("async function dismissJdMenuUpdateNotice"));
  assert.match(wareOpen, /ensureJdStoreAuthenticatedSession/);
  assert.match(wareOpen, /loginMode === "windows_dpapi_credentials"/);
  assert.match(wareOpen, /waitForJdWareQueryOrAutomatedLoginRedirect/);
  assert.ok(wareOpen.indexOf("authenticateAndRestoreTarget") < wareOpen.indexOf("verifyJdWareProductQueryResponse"));
  const dailyRun = daily.slice(daily.indexOf("async function run()"), daily.indexOf("async function main()"));
  assert.ok(dailyRun.indexOf("ensureJdStoreAuthenticatedSession") < dailyRun.indexOf("readAndAssertJdProductDetailStoreIdentity"));
  const marketRun = market.slice(market.indexOf("export async function runJdMarketDailyPlan"));
  assert.ok(marketRun.indexOf("ensureJdStoreAuthenticatedSession") < marketRun.indexOf("assertStoreIdentity(page, plan)"));
});
