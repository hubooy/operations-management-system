import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { chromiumWindowGuardReadyTimeoutMs, chromiumWindowGuardScript } from "../lib/jackyun/cdp-client";
import {
  hasJdInteractivePageGate,
  isJdInteractiveBrowserFailure,
  jdBrowserLaunchMode,
  jdWareBrowserLaunchMode,
  launchJdWareBrowser,
  revealJdBrowserForInteractiveFailure,
} from "../lib/jd/browser-mode";

test("automated JD browsers disable extensions while interactive login preserves them", () => {
  assert.deepEqual(jdBrowserLaunchMode(false), { headless: true, visible: false, disableExtensions: true });
  assert.deepEqual(jdBrowserLaunchMode(true), { headless: false, visible: true, disableExtensions: false });
  assert.deepEqual(jdWareBrowserLaunchMode(false), {
    headless: false,
    visible: false,
    disableExtensions: true,
    startMinimized: true,
  });
  assert.deepEqual(jdWareBrowserLaunchMode(true), { headless: false, visible: true, disableExtensions: false });
});

test("strict silent headed Chromium uses an off-screen Win32 window guard", () => {
  assert.equal(chromiumWindowGuardReadyTimeoutMs, 15_000);
  const script = chromiumWindowGuardScript(1234);
  assert.match(script, /EnumWindows/);
  assert.match(script, /GetWindowThreadProcessId/);
  assert.match(script, /ShowWindowAsync\(hWnd, 0\)/);
  assert.match(script, /Start-Sleep -Milliseconds 75/);
  assert.throws(() => chromiumWindowGuardScript(0), /PID/);
});

test("only interactive JD failures request a visible browser", () => {
  for (const message of [
    "京东商家后台尚未登录。请在专用浏览器中完成登录后重新运行。",
    "京东商智登录状态无效",
    "请完成滑块验证",
    "当前登录身份与受控店铺不一致",
    "京东商品查询未返回正数总行数（HTTP 200，业务码 601，总行数 missing）：未经京东授权的软件操作",
  ]) assert.equal(isJdInteractiveBrowserFailure(new Error(message)), true, message);

  for (const message of [
    "等待新的京东导出任务完成超时",
    "导入批次身份不匹配",
    "下载任务存在多个候选",
    "运营管理系统 HTTP 500",
  ]) assert.equal(isJdInteractiveBrowserFailure(new Error(message)), false, message);

  assert.equal(hasJdInteractivePageGate("请完成滑块验证后继续"), true);
  assert.equal(hasJdInteractivePageGate("商品明细 查询 下载数据"), false);
});

test("JD ware browser replaces a stale HeadlessChrome before launching minimized headed Chromium", async () => {
  const calls: string[] = [];
  const userAgents = ["Mozilla/5.0 HeadlessChrome/151.0.0.0", "Mozilla/5.0 Chrome/151.0.0.0"];
  const result = await launchJdWareBrowser({
    executablePath: "chrome.exe",
    profileDirectory: "D:\\profiles\\jd-store",
    profileName: "Profile 1",
    port: 9224,
    startUrl: "https://example.test/jd",
    keepWindowHidden: true,
  }, false, {
    readUserAgent: async () => userAgents.shift() ?? "Mozilla/5.0 Chrome/151.0.0.0",
    closeChromeBrowser: async (port) => { calls.push(`close:${port}`); return true; },
    launchDedicatedChrome: async (options) => {
      assert.equal(options.profileName, "Profile 1");
      calls.push(`launch:${options.headless}:${options.visible}:${options.disableExtensions}:${options.startMinimized}:${options.keepWindowHidden}:${options.port}`);
      return {};
    },
  });
  assert.deepEqual(calls, ["close:9224", "launch:false:false:true:true:true:9224"]);
  assert.equal(result.replacedHeadless, true);
});

test("JD ware browser safely reuses an existing non-headless Chromium without closing it", async () => {
  const calls: string[] = [];
  const result = await launchJdWareBrowser({
    executablePath: "chrome.exe",
    profileDirectory: "D:\\profiles\\jd-store",
    profileName: "Profile 1",
    port: 9224,
    startUrl: "https://example.test/jd",
  }, false, {
    readUserAgent: async () => "Mozilla/5.0 Chrome/151.0.0.0",
    closeChromeBrowser: async () => { calls.push("close"); return true; },
    launchDedicatedChrome: async (options) => {
      calls.push(`launch:${options.headless}:${options.visible}:${options.disableExtensions}:${options.startMinimized}`);
      return null;
    },
  });
  assert.deepEqual(calls, ["launch:false:false:true:true"]);
  assert.equal(result.replacedHeadless, false);
});

test("strict silent JD ware mode fails closed instead of reusing an unguarded browser", async () => {
  await assert.rejects(
    launchJdWareBrowser({
      executablePath: "chrome.exe",
      profileDirectory: "D:\\profiles\\jd-store",
      profileName: "Profile 1",
      port: 9224,
      startUrl: "https://example.test/jd",
      keepWindowHidden: true,
    }, false, {
      readUserAgent: async () => "Mozilla/5.0 Chrome/151.0.0.0",
      closeChromeBrowser: async () => true,
      launchDedicatedChrome: async () => null,
    }),
    /静默模式拒绝复用/,
  );
});

test("JD ware browser verifies the active runtime is not HeadlessChrome", async () => {
  const calls: string[] = [];
  await assert.rejects(
    launchJdWareBrowser({
      executablePath: "chrome.exe",
      profileDirectory: "D:\\profiles\\jd-store",
      profileName: "Profile 1",
      port: 9224,
      startUrl: "https://example.test/jd",
    }, false, {
      readUserAgent: async () => "HeadlessChrome/151",
      closeChromeBrowser: async () => { calls.push("close"); return true; },
      launchDedicatedChrome: async () => ({ pid: 1 }),
    }),
    /仍带 HeadlessChrome 特征/,
  );
  assert.deepEqual(calls, ["close", "close"]);
});

test("JD ware browser fails closed if a concurrent process takes the port during mode replacement", async () => {
  await assert.rejects(
    launchJdWareBrowser({
      executablePath: "chrome.exe",
      profileDirectory: "D:\\profiles\\jd-store",
      profileName: "Profile 1",
      port: 9224,
      startUrl: "https://example.test/jd",
    }, false, {
      readUserAgent: async () => "HeadlessChrome/151",
      closeChromeBrowser: async () => true,
      launchDedicatedChrome: async () => null,
    }),
    /端口被并发进程占用/,
  );
});

test("interactive recovery closes headless Chromium before opening one visible process", async () => {
  const calls: string[] = [];
  await revealJdBrowserForInteractiveFailure({
    executablePath: "chrome.exe",
    profileDirectory: "D:\\profiles\\jd-store",
    profileName: "Profile 1",
    port: 9224,
    startUrl: "https://example.test/jd",
  }, {
    closeChromeBrowser: async (port) => { calls.push(`close:${port}`); return true; },
    launchDedicatedChrome: async (options) => {
      calls.push(`launch:${options.headless}:${options.visible}:${options.disableExtensions}:${options.port}`);
      return {};
    },
    waitForChrome: async (port) => { calls.push(`wait:${port}`); },
  });
  assert.deepEqual(calls, ["close:9224", "launch:false:true:false:9224", "wait:9224"]);

  await assert.rejects(
    revealJdBrowserForInteractiveFailure({
      executablePath: "chrome.exe",
      profileDirectory: "D:\\profiles\\jd-store",
      profileName: "Profile 1",
      port: 9224,
      startUrl: "https://example.test/jd",
    }, {
      closeChromeBrowser: async () => true,
      launchDedicatedChrome: async () => null,
      waitForChrome: async () => undefined,
    }),
    /并发进程占用/,
  );
});

test("JD master uses minimized headed Chromium while product detail keeps shared headless mode", async () => {
  const master = await readFile("tools/jackyun-ware-export.ts", "utf8");
  const daily = await readFile("tools/jdsz-product-detail-export.ts", "utf8");
  const cdp = await readFile("lib/jackyun/cdp-client.ts", "utf8");
  assert.match(master, /launchJdWareBrowser\([\s\S]*options\.interactiveLogin\)/);
  assert.match(master, /startUrl:\s*options\.interactiveLogin\s*\?\s*targetUrl\s*:\s*"about:blank"/);
  assert.doesNotMatch(master, /connectPlaywrightJackyunTarget\(browser,\s*\{\s*startUrl:\s*targetUrl/);
  assert.match(daily, /jdBrowserLaunchMode\(options\.interactiveLogin\)/);
  assert.doesNotMatch(master, /jdBrowserLaunchMode\(options\.interactiveLogin\)/);
  assert.doesNotMatch(daily, /startUrl:\s*targetUrl,\s*headless:\s*false/);
  assert.match(master, /options\.visibleRecovery\s*&&\s*interactiveAttentionRequired/);
  assert.match(daily, /options\.visibleRecovery\s*&&\s*!options\.interactiveLogin/);
  assert.match(cdp, /options\.startMinimized[\s\S]*--start-minimized/);
  assert.match(cdp, /options\.disableExtensions[\s\S]*--disable-extensions/);
  assert.match(cdp, /options\.keepWindowHidden[\s\S]*--window-position=-32000,-32000/);
  assert.match(cdp, /startChromiumWindowGuard/);
  assert.match(cdp, /--profile-directory=\$\{options\.profileName\}/);
  assert.match(cdp, /--disable-background-timer-throttling/);
  assert.match(cdp, /--disable-backgrounding-occluded-windows/);
  assert.match(cdp, /--disable-renderer-backgrounding/);
});
