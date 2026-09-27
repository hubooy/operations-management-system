import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import type { Page } from "playwright-core";
import { ensureAlimamaLogin, resetDedicatedAlimamaSession, trustedAlimamaLoginUrl } from "../tools/tmall-alimama-login";

const store = { storeKey: "tmall-yijiu", loginMode: "windows_dpapi_credentials" as const };
const clean = { challengePresent: false, credentialRejected: false, temporarilyLocked: false };

test("Both promotion paths use the guard; direct capture starts before navigation and returns only after authentication", async () => {
  const ui = await readFile(new URL("../tools/tmall-promotion-export.ts", import.meta.url), "utf8");
  assert.match(ui, /await ensureAlimamaLogin\(page, store/);
  const direct = await readFile(new URL("../tools/tmall-direct-promotion-export.ts", import.meta.url), "utf8");
  const discovery = direct.slice(direct.indexOf("export async function discoverTmallAlimamaIdentifiers"), direct.indexOf("async function apiCreateTask"));
  assert.ok(discovery.indexOf('page.on("request", captureIdentifiers)') < discovery.indexOf("await page.goto"));
  assert.ok(discovery.indexOf("await waitForIdentity") < discovery.indexOf("return identifiers"));
  assert.doesNotMatch(discovery, /apiCreateTask|\.post\(/);
  assert.match(discovery, /catch\(\(\) => null\)/);
});
function fixture(url = "https://one.alimama.com/index.html") {
  const context = {};
  const state = { url, authenticated: false, attempts: 0 };
  const page = {
    url: () => state.url,
    context: () => context,
    frames: () => [{ url: () => "https://login.taobao.com/member/login.jhtml" }, { url: () => "https://evil.example" }],
  } as unknown as Page;
  const identity = async () => { if (!state.authenticated) throw new Error("waiting_login"); };
  return { state, page, identity };
}

test("Alimama trusted origins reject lookalikes, HTTP and credential URLs", () => {
  assert.equal(trustedAlimamaLoginUrl("https://login.taobao.com/member/login.jhtml", true), true);
  for (const url of ["http://login.taobao.com", "https://login.taobao.com.evil.test", "https://evil.test/?next=login.taobao.com", "https://u:p@login.taobao.com", "https://login.taobao.com:444", "about:blank"]) {
    assert.equal(trustedAlimamaLoginUrl(url), false);
  }
  assert.equal(trustedAlimamaLoginUrl("https://one.alimama.com", true), false);
});

test("Existing Alimama session does not read credentials or submit", async () => {
  const f = fixture(); f.state.authenticated = true;
  await ensureAlimamaLogin(f.page, store, f.identity, {
    inspect: async () => clean,
    login: async () => { throw new Error("must not login"); },
  });
});

test("Expired session submits once through trusted frames then checks identity", async () => {
  const f = fixture("https://login.taobao.com/member/login.jhtml");
  await ensureAlimamaLogin(f.page, store, f.identity, {
    inspect: async () => clean,
    wait: async () => {},
    login: async (page, key) => {
      assert.equal(key, store.storeKey);
      assert.equal(page.frames().length, 1);
      f.state.attempts++;
      f.state.url = "https://one.alimama.com/index.html";
      f.state.authenticated = true;
      return { attempted: true, submitted: true, reason: "submitted" };
    },
  });
  assert.equal(f.state.attempts, 1);
});

test("Challenge, rejection and lock fail before credentials", async () => {
  for (const flag of ["challengePresent", "credentialRejected", "temporarilyLocked"]) {
    const f = fixture();
    await assert.rejects(ensureAlimamaLogin(f.page, store, f.identity, {
      inspect: async () => ({ ...clean, [flag]: true }),
      login: async () => { assert.fail("must not read credentials"); },
    }), /人工验证/);
  }
});

test("Uncertain login is sanitized and fenced across tabs/store keys", async () => {
  const f = fixture();
  await assert.rejects(ensureAlimamaLogin(f.page, store, f.identity, {
    inspect: async () => clean,
    login: async () => { throw new Error("secret-filled-argument"); },
  }), (error: Error) => !error.message.includes("secret") && /禁止自动重试/.test(error.message));
  await assert.rejects(ensureAlimamaLogin(f.page, { ...store, storeKey: "tmall-lili" }, f.identity, {
    inspect: async () => clean,
    login: async () => { assert.fail("second attempt"); },
  }), /本轮已尝试/);
});

test("Login origin cannot pass as authenticated", async () => {
  const f = fixture("https://login.taobao.com/member/login.jhtml");
  await assert.rejects(ensureAlimamaLogin(f.page, store, async () => {}, {
    timeoutMs: 0, inspect: async () => clean,
    login: async () => ({ attempted: true, submitted: true, reason: "submitted" }),
  }), /店铺身份未确认/);
});

test("Wrong dedicated-profile identity is cleared once before DPAPI login", async () => {
  const g = fixture();
  let resets = 0;
  await ensureAlimamaLogin(g.page, store, async () => {
    if (!g.state.authenticated) throw new Error("shop_identity_mismatch");
  }, {
    timeoutMs: 1_000,
    inspect: async () => clean,
    wait: async () => {},
    resetWrongSession: async () => {
      resets++;
      g.state.url = "https://login.taobao.com/member/login.jhtml";
    },
    login: async () => {
      g.state.attempts++;
      g.state.url = "https://one.alimama.com/index.html";
      g.state.authenticated = true;
      return { attempted: true, submitted: true, reason: "submitted" };
    },
  });
  assert.equal(resets, 1);
  assert.equal(g.state.attempts, 1);
});

test("Wrong identity may remain briefly after reset while the trusted login frame settles", async () => {
  const g = fixture();
  let resets = 0;
  let waits = 0;
  (g.page as unknown as { frames: () => Array<{ url: () => string }> }).frames = () => waits >= 2
    ? [{ url: () => "https://login.taobao.com/member/login.jhtml" }]
    : [];
  await ensureAlimamaLogin(g.page, store, async () => {
    if (!g.state.authenticated) throw new Error("shop_identity_mismatch");
  }, {
    timeoutMs: 1_000,
    inspect: async () => clean,
    wait: async () => {
      waits++;
      if (waits === 2) g.state.url = "https://login.taobao.com/member/login.jhtml";
    },
    resetWrongSession: async () => { resets++; },
    login: async () => {
      g.state.attempts++;
      g.state.url = "https://one.alimama.com/index.html";
      g.state.authenticated = true;
      return { attempted: true, submitted: true, reason: "submitted" };
    },
  });
  assert.equal(resets, 1);
  assert.equal(g.state.attempts, 1);
});

test("DPAPI submission waits for delayed exact identity without a second login attempt", async () => {
  const g = fixture("https://login.taobao.com/member/login.jhtml");
  let identityChecks = 0;
  await ensureAlimamaLogin(g.page, store, async () => {
    identityChecks++;
    if (identityChecks < 3) throw new Error("shop_identity_mismatch");
  }, {
    timeoutMs: 1_000,
    inspect: async () => clean,
    wait: async () => {},
    resetWrongSession: async () => { assert.fail("submitted login must not reset the session"); },
    login: async () => {
      g.state.attempts++;
      g.state.url = "https://one.alimama.com/index.html";
      return { attempted: true, submitted: true, reason: "submitted" };
    },
  });
  assert.equal(g.state.attempts, 1);
  assert.equal(identityChecks, 3);
});

test("Dedicated-session reset accepts a navigation abort only on a trusted login surface", async () => {
  for (const [redirectUrl, allowed] of [
    ["https://login.taobao.com/member/login.jhtml", true],
    ["https://evil.example/phishing", false],
  ] as const) {
    const state = { url: "https://one.alimama.com/index.html", storageCleared: false, cookiesCleared: false };
    const page = {
      url: () => state.url,
      evaluate: async () => { state.storageCleared = true; },
      context: () => ({ clearCookies: async () => { state.cookiesCleared = true; } }),
      goto: async () => {
        state.url = redirectUrl;
        throw new Error("net::ERR_ABORTED");
      },
    } as unknown as Page;
    if (allowed) await resetDedicatedAlimamaSession(page);
    else await assert.rejects(resetDedicatedAlimamaSession(page), /ERR_ABORTED/);
    assert.equal(state.storageCleared, true);
    assert.equal(state.cookiesCleared, true);
  }
});

test("Wrong identity without DPAPI remains fail closed", async () => {
  const g = fixture();
  await assert.rejects(ensureAlimamaLogin(g.page, { ...store, loginMode: "manual" }, async () => {
    throw new Error("shop_identity_mismatch");
  }, {
    inspect: async () => clean,
    resetWrongSession: async () => { assert.fail("manual store must not clear session"); },
  }), /无法安全切换/);
});

test("Unknown origin and missing credentials fail closed", async () => {
  const f = fixture("https://evil.example");
  await assert.rejects(ensureAlimamaLogin(f.page, store, f.identity), /来源未经许可/);
  const g = fixture();
  await assert.rejects(ensureAlimamaLogin(g.page, store, g.identity, {
    inspect: async () => clean,
    login: async () => ({ attempted: false, submitted: false, reason: "saved_credentials_missing" }),
  }), /禁止自动重试/);
});
