import { closeChromeBrowser, launchDedicatedChrome, waitForChrome } from "../jackyun/cdp-client";

export type JdBrowserLaunchMode = { headless: boolean; visible: boolean; disableExtensions: boolean };

export function jdBrowserLaunchMode(interactiveLogin: boolean): JdBrowserLaunchMode {
  return interactiveLogin
    ? { headless: false, visible: true, disableExtensions: false }
    : { headless: true, visible: false, disableExtensions: true };
}

export type JdWareBrowserLaunchMode = JdBrowserLaunchMode & { startMinimized?: boolean };

export function jdWareBrowserLaunchMode(interactiveLogin: boolean): JdWareBrowserLaunchMode {
  return interactiveLogin
    ? { headless: false, visible: true, disableExtensions: false }
    : { headless: false, visible: false, disableExtensions: true, startMinimized: true };
}

export function isJdInteractiveBrowserFailure(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  return /尚未登录|登录状态无效|请.{0,20}登录|验证码|人机验证|安全验证|安全校验|滑块验证|访问验证|访问受限|账号异常|业务码\s*601|未经京东授权的软件操作|店铺身份.{0,12}不一致|登录身份.{0,12}不一致/.test(message);
}

export function hasJdInteractivePageGate(pageText: string) {
  return /验证码|人机验证|安全验证|安全校验|滑块验证|访问验证/.test(pageText);
}

export type JdInteractiveBrowserOptions = {
  executablePath: string;
  profileDirectory: string;
  profileName: string;
  port: number;
  startUrl: string;
  keepWindowHidden?: boolean;
};

type JdBrowserLifecycleDependencies = {
  closeChromeBrowser: typeof closeChromeBrowser;
  launchDedicatedChrome: (options: Parameters<typeof launchDedicatedChrome>[0]) => Promise<unknown | null>;
  waitForChrome: typeof waitForChrome;
};

const defaultLifecycleDependencies: JdBrowserLifecycleDependencies = {
  closeChromeBrowser,
  launchDedicatedChrome,
  waitForChrome,
};

type JdWareBrowserDependencies = Pick<JdBrowserLifecycleDependencies, "closeChromeBrowser" | "launchDedicatedChrome"> & {
  readUserAgent: (port: number) => Promise<string>;
};

async function readChromeUserAgent(port: number) {
  const response = await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(5_000) });
  if (!response.ok) throw new Error(`读取 Chromium 版本端点失败：HTTP ${response.status}。`);
  const version = await response.json() as { "User-Agent"?: unknown };
  if (typeof version["User-Agent"] !== "string" || !version["User-Agent"].trim()) {
    throw new Error("Chromium 版本端点缺少 User-Agent。");
  }
  return version["User-Agent"];
}

const defaultWareBrowserDependencies: JdWareBrowserDependencies = {
  closeChromeBrowser,
  launchDedicatedChrome,
  readUserAgent: readChromeUserAgent,
};

export async function launchJdWareBrowser(
  options: JdInteractiveBrowserOptions,
  interactiveLogin: boolean,
  dependencies: JdWareBrowserDependencies = defaultWareBrowserDependencies,
) {
  const userAgent = await dependencies.readUserAgent(options.port).catch(() => null);
  const replacingHeadless = typeof userAgent === "string" && /HeadlessChrome/i.test(userAgent);
  if (replacingHeadless) await dependencies.closeChromeBrowser(options.port);
  const launched = await dependencies.launchDedicatedChrome({
    ...options,
    ...jdWareBrowserLaunchMode(interactiveLogin),
  });
  if (replacingHeadless && !launched) {
    throw new Error("京东商品主数据浏览器模式切换时端口被并发进程占用，拒绝复用未知实例。");
  }
  if (options.keepWindowHidden && !launched) {
    throw new Error("京东商品主数据静默模式拒绝复用未受本次窗口守护控制的 Chromium 实例。");
  }
  const activeUserAgent = await dependencies.readUserAgent(options.port);
  if (/HeadlessChrome/i.test(activeUserAgent)) {
    if (launched) await dependencies.closeChromeBrowser(options.port);
    throw new Error("京东商品主数据浏览器仍带 HeadlessChrome 特征，已拒绝继续访问商品页。");
  }
  return { launched, replacedHeadless: replacingHeadless };
}

export async function revealJdBrowserForInteractiveFailure(
  options: JdInteractiveBrowserOptions,
  dependencies: JdBrowserLifecycleDependencies = defaultLifecycleDependencies,
) {
  // Close the headless process first: Chromium cannot safely open the same
  // persistent profile in a second process. A fresh visible process is the
  // only successful outcome; attaching to a concurrent process fails closed.
  await dependencies.closeChromeBrowser(options.port);
  const launched = await dependencies.launchDedicatedChrome({
    ...options,
    headless: false,
    visible: true,
    disableExtensions: false,
  });
  if (!launched) throw new Error("京东交互浏览器端口被并发进程占用，未打开新的可见窗口。");
  await dependencies.waitForChrome(options.port);
}
