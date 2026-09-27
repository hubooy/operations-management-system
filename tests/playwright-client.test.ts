import assert from "node:assert/strict";
import test from "node:test";

import { connectPlaywrightJackyunTarget } from "../lib/jackyun/playwright-client";

test("JD target creates one named page instead of borrowing the first old WareList tab", async () => {
  let oldGotoCalls = 0;
  let createdGotoCalls = 0;
  let newPageCalls = 0;
  const context = {
    pages: () => [oldPage],
    newPage: async () => { newPageCalls += 1; return createdPage; },
    newCDPSession: async () => ({}),
  };
  const oldPage = {
    url: () => "https://wares-jdm.jd.com/ware/wareList?old=1",
    evaluate: async () => "",
    goto: async () => { oldGotoCalls += 1; },
    context: () => context,
  };
  const createdPage = {
    url: () => "about:blank",
    evaluate: async () => undefined,
    goto: async () => { createdGotoCalls += 1; },
    context: () => context,
  };
  const browser = { contexts: () => [context] };

  const result = await connectPlaywrightJackyunTarget(browser as never, {
    startUrl: "https://wares-jdm.jd.com/ware/wareList",
    workerName: "codex-jd-ware-export",
    targetUrlPattern: /wares-jdm\.jd\.com/i,
    requireMini: false,
  });

  assert.equal(result.page, createdPage);
  assert.equal(newPageCalls, 1);
  assert.equal(oldGotoCalls, 0);
  assert.equal(createdGotoCalls, 1);
});

test("JD reuses its named page even though WareList has no MiniUI", async () => {
  let newPageCalls = 0;
  let evaluationCalls = 0;
  const context = {
    pages: () => [namedPage],
    newPage: async () => { newPageCalls += 1; return namedPage; },
    newCDPSession: async () => ({}),
  };
  const namedPage = {
    url: () => "https://wares-jdm.jd.com/ware/wareList",
    evaluate: async () => (evaluationCalls++ === 0 ? "codex-jd-ware-export" : false),
    goto: async () => undefined,
    context: () => context,
  };
  const browser = { contexts: () => [context] };

  const result = await connectPlaywrightJackyunTarget(browser as never, {
    workerName: "codex-jd-ware-export",
    targetUrlPattern: /wares-jdm\.jd\.com/i,
    requireMini: false,
  });

  assert.equal(result.page, namedPage);
  assert.equal(newPageCalls, 0);
});

test("JD refuses a named page that has navigated outside the requested target", async () => {
  let staleGotoCalls = 0;
  let createdGotoCalls = 0;
  let newPageCalls = 0;
  const context = {
    pages: () => [staleNamedPage],
    newPage: async () => { newPageCalls += 1; return createdPage; },
    newCDPSession: async () => ({}),
  };
  const staleNamedPage = {
    url: () => "https://jdsz.jd.com/stockweb/sz/view/ihs/InventoryHealth.html",
    evaluate: async () => "teruisi-jd-market-ranking",
    goto: async () => { staleGotoCalls += 1; },
    context: () => context,
  };
  const createdPage = {
    url: () => "about:blank",
    evaluate: async () => undefined,
    goto: async () => { createdGotoCalls += 1; },
    context: () => context,
  };
  const browser = { contexts: () => [context] };

  const result = await connectPlaywrightJackyunTarget(browser as never, {
    startUrl: "https://jdsz.jd.com/szweb/view/industry/industry-top.html",
    workerName: "teruisi-jd-market-ranking",
    targetUrlPattern: /industry-top\.html/i,
    requireMini: false,
  });

  assert.equal(result.page, createdPage);
  assert.equal(newPageCalls, 1);
  assert.equal(staleGotoCalls, 0);
  assert.equal(createdGotoCalls, 1);
});
