import { spawn, type ChildProcess } from "node:child_process";
import { mkdir } from "node:fs/promises";

type PendingCall = {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timeout: ReturnType<typeof setTimeout>;
};

export type CdpEventHandler = (params: Record<string, unknown>) => void;

/**
 * The controller can use either the legacy raw DevTools client or a Playwright
 * page backed client.  Keeping this small contract lets download provenance
 * continue to use CDP events while page interaction moves to Playwright.
 */
export type BrowserAutomationClient = {
  send<T = Record<string, unknown>>(method: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<T>;
  on(method: string, handler: CdpEventHandler): () => void;
  close(): void;
};

export type ChromeTarget = {
  id: string;
  type: string;
  title: string;
  url: string;
  webSocketDebuggerUrl?: string;
};

export class CdpClient {
  private nextId = 1;
  private readonly pending = new Map<number, PendingCall>();
  private readonly handlers = new Map<string, Set<CdpEventHandler>>();

  private constructor(private readonly socket: WebSocket) {}

  static async connect(webSocketUrl: string, timeoutMs = 10_000) {
    const socket = new WebSocket(webSocketUrl);
    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("连接 Chrome DevTools 超时。")), timeoutMs);
      socket.addEventListener("open", () => {
        clearTimeout(timeout);
        resolve();
      }, { once: true });
      socket.addEventListener("error", () => {
        clearTimeout(timeout);
        reject(new Error("无法连接 Chrome DevTools。"));
      }, { once: true });
    });
    const client = new CdpClient(socket);
    socket.addEventListener("message", (event) => client.handleMessage(String(event.data)));
    socket.addEventListener("close", () => client.rejectAll(new Error("Chrome DevTools 连接已关闭。")));
    return client;
  }

  on(method: string, handler: CdpEventHandler) {
    const current = this.handlers.get(method) ?? new Set<CdpEventHandler>();
    current.add(handler);
    this.handlers.set(method, current);
    return () => current.delete(handler);
  }

  async send<T = Record<string, unknown>>(method: string, params: Record<string, unknown> = {}, timeoutMs = 60_000): Promise<T> {
    const id = this.nextId++;
    const result = new Promise<T>((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Chrome DevTools 调用超时：${method}`));
      }, timeoutMs);
      this.pending.set(id, { resolve: (value) => resolve(value as T), reject, timeout });
    });
    this.socket.send(JSON.stringify({ id, method, params }));
    return result;
  }

  close() {
    this.socket.close();
  }

  private handleMessage(text: string) {
    const message = JSON.parse(text) as {
      id?: number;
      method?: string;
      params?: Record<string, unknown>;
      result?: unknown;
      error?: { message?: string };
    };
    if (message.id) {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      clearTimeout(pending.timeout);
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message ?? "未知 Chrome DevTools 错误。"));
      else pending.resolve(message.result);
      return;
    }
    if (message.method) {
      for (const handler of this.handlers.get(message.method) ?? []) handler(message.params ?? {});
    }
  }

  private rejectAll(error: Error) {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timeout);
      pending.reject(error);
    }
    this.pending.clear();
  }
}

export async function listChromeTargets(port: number): Promise<ChromeTarget[]> {
  const response = await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(5_000) });
  if (!response.ok) throw new Error(`读取 Chrome targets 失败：HTTP ${response.status}。`);
  return await response.json() as ChromeTarget[];
}

export async function connectChromeBrowser(port: number) {
  const response = await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(5_000) });
  if (!response.ok) throw new Error(`读取 Chrome 版本端点失败：HTTP ${response.status}。`);
  const version = await response.json() as { webSocketDebuggerUrl?: string };
  if (!version.webSocketDebuggerUrl) throw new Error("Chrome 版本端点缺少浏览器 WebSocket 地址。");
  return CdpClient.connect(version.webSocketDebuggerUrl);
}

export async function closeChromeBrowser(port: number, timeoutMs = 10_000) {
  const browser = await connectChromeBrowser(port).catch(() => null);
  if (!browser) return false;
  try {
    // Chrome may close the DevTools socket before acknowledging Browser.close.
    // The bounded port check below is the authoritative result.
    await browser.send("Browser.close", {}, 3_000).catch(() => undefined);
  } finally {
    browser.close();
  }
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const alive = await listChromeTargets(port).then(() => true).catch(() => false);
    if (!alive) return true;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`专用 Chrome 未在 ${timeoutMs}ms 内退出，拒绝同时打开同一 profile。`);
}

export async function readChromeBrowserProcessId(port: number) {
  const browser = await connectChromeBrowser(port);
  try {
    const result = await browser.send<{ processInfo?: Array<{ id?: number; type?: string }> }>("SystemInfo.getProcessInfo", {}, 5_000);
    const candidates = (result.processInfo ?? []).filter((processInfo) => processInfo.type === "browser" && Number.isSafeInteger(processInfo.id) && Number(processInfo.id) > 0);
    if (candidates.length !== 1) throw new Error(`Chromium CDP 返回的 browser 进程数量不是 1：${candidates.length}。`);
    return Number(candidates[0]!.id);
  } finally {
    browser.close();
  }
}

export async function waitForChrome(port: number, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      await listChromeTargets(port);
      return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
  throw new Error(`专用 Chrome 未在 ${timeoutMs}ms 内开放调试端口 ${port}。`);
}

export function chromiumWindowGuardScript(browserPid: number, debugPort?: number) {
  if (!Number.isSafeInteger(browserPid) || browserPid <= 0) throw new Error("Chromium 窗口守护进程 PID 无效。");
  if (debugPort !== undefined && (!Number.isSafeInteger(debugPort) || debugPort <= 0 || debugPort > 65_535)) {
    throw new Error("Chromium 窗口守护调试端口无效。");
  }
  return `$ErrorActionPreference = 'Stop'
$targetPid = ${browserPid}
$debugPort = ${debugPort ?? 0}
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class TeruisiChromiumWindowGuard {
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc callback, IntPtr extraData);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int command);
  public static void Hide(uint targetPid) {
    EnumWindows(delegate(IntPtr hWnd, IntPtr lParam) {
      uint processId;
      GetWindowThreadProcessId(hWnd, out processId);
      if (processId == targetPid && IsWindowVisible(hWnd)) ShowWindowAsync(hWnd, 0);
      return true;
    }, IntPtr.Zero);
  }
}
"@
[TeruisiChromiumWindowGuard]::Hide([uint32]$targetPid)
[Console]::Out.WriteLine('READY')
${debugPort ? `$missingChecks = 0
while ($missingChecks -lt 40) {
  if ($null -eq (Get-Process -Id $targetPid -ErrorAction SilentlyContinue)) {
    $replacementPid = Get-NetTCPConnection -LocalPort $debugPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess
    if ($null -eq $replacementPid) { $missingChecks += 1; Start-Sleep -Milliseconds 125; continue }
    $targetPid = [int]$replacementPid
  }
  $missingChecks = 0
  [TeruisiChromiumWindowGuard]::Hide([uint32]$targetPid)
  Start-Sleep -Milliseconds 75
}` : `while ($null -ne (Get-Process -Id $targetPid -ErrorAction SilentlyContinue)) {
  [TeruisiChromiumWindowGuard]::Hide([uint32]$targetPid)
  Start-Sleep -Milliseconds 75
}`}`;
}

export const chromiumWindowGuardReadyTimeoutMs = 15_000;

export async function startChromiumWindowGuard(browserPid: number, debugPort: number, timeoutMs = chromiumWindowGuardReadyTimeoutMs) {
  if (process.platform !== "win32") throw new Error("Chromium 严格静默窗口模式当前只支持 Windows。");
  const encoded = Buffer.from(chromiumWindowGuardScript(browserPid, debugPort), "utf16le").toString("base64");
  const guard = spawn("powershell.exe", [
    "-NoLogo",
    "-NoProfile",
    "-NonInteractive",
    "-WindowStyle",
    "Hidden",
    "-EncodedCommand",
    encoded,
  ], { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  await new Promise<void>((resolve, reject) => {
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => finish(new Error("Chromium 窗口守护进程未在时限内就绪。")), timeoutMs);
    const finish = (error?: Error) => {
      clearTimeout(timer);
      guard.off("error", onError);
      guard.off("exit", onExit);
      guard.stdout?.off("data", onStdout);
      guard.stderr?.off("data", onStderr);
      if (error) reject(error); else resolve();
    };
    const onError = (error: Error) => finish(error);
    const onExit = (code: number | null) => finish(new Error(`Chromium 窗口守护进程提前退出：${code ?? "unknown"}${stderr ? ` (${stderr.slice(-300)})` : ""}${stdout ? ` [stdout=${JSON.stringify(stdout.slice(-300))}]` : ""}`));
    const onStdout = (chunk: Buffer) => {
      stdout = `${stdout}${String(chunk)}`.slice(-1_000);
      if (/(?:^|\r?\n)READY(?:\r?\n|$)/.test(stdout)) finish();
    };
    const onStderr = (chunk: Buffer) => { stderr = `${stderr}${String(chunk)}`.slice(-1_000); };
    guard.once("error", onError);
    guard.once("exit", onExit);
    guard.stdout?.on("data", onStdout);
    guard.stderr?.on("data", onStderr);
  }).catch((error) => {
    guard.kill();
    throw error;
  });
  guard.stdout?.destroy();
  guard.stderr?.destroy();
  guard.unref();
  return guard;
}

export async function launchDedicatedChrome(options: {
  executablePath: string;
  profileDirectory: string;
  profileName?: string;
  port: number;
  startUrl: string;
  headless?: boolean;
  visible?: boolean;
  disableExtensions?: boolean;
  startMinimized?: boolean;
  keepWindowHidden?: boolean;
}) {
  try {
    await listChromeTargets(options.port);
    return null;
  } catch {
    // Launch below.
  }
  await mkdir(options.profileDirectory, { recursive: true });
  if (options.profileName && !/^(?:Default|Profile [1-9]\d*)$/.test(options.profileName)) {
    throw new Error("Chromium profile 名称无效。");
  }
  const args = [
    `--remote-debugging-port=${options.port}`,
    `--user-data-dir=${options.profileDirectory}`,
    ...(options.profileName ? [`--profile-directory=${options.profileName}`] : []),
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-session-crashed-bubble",
    options.startUrl,
  ];
  if (options.disableExtensions) args.unshift("--disable-extensions");
  if (options.headless) args.unshift("--headless=new");
  if (options.keepWindowHidden) {
    args.unshift("--window-position=-32000,-32000");
  }
  if (options.startMinimized || options.keepWindowHidden) {
    args.unshift(
      "--start-minimized",
      "--disable-background-timer-throttling",
      "--disable-backgrounding-occluded-windows",
      "--disable-renderer-backgrounding",
    );
  }
  const child: ChildProcess = spawn(options.executablePath, args, { detached: true, stdio: "ignore", windowsHide: !options.visible });
  try {
    if (!Number.isSafeInteger(child.pid) || Number(child.pid) <= 0) throw new Error("Chromium 启动进程 PID 无效。");
    child.unref();
    await waitForChrome(options.port);
    if (options.keepWindowHidden && !options.headless) {
      await startChromiumWindowGuard(await readChromeBrowserProcessId(options.port), options.port);
    }
  } catch (error) {
    child.kill();
    await closeChromeBrowser(options.port).catch(() => false);
    throw error;
  }
  return child;
}

export async function connectJackyunTarget(port: number) {
  const targets = await listChromeTargets(port);
  const target = targets.find((item) => item.type === "page" && /jackyun\.com/i.test(item.url))
    ?? targets.find((item) => item.type === "page" && item.webSocketDebuggerUrl);
  if (!target?.webSocketDebuggerUrl) throw new Error("专用 Chrome 中没有可控制的吉客云页面。");
  return { target, client: await CdpClient.connect(target.webSocketDebuggerUrl) };
}

export async function evaluateValue<T>(client: BrowserAutomationClient, expression: string, timeoutMs = 60_000): Promise<T> {
  const response = await client.send<{
    result: { value?: T; description?: string };
    exceptionDetails?: { text?: string; exception?: { description?: string } };
  }>("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true, userGesture: true }, timeoutMs);
  if (response.exceptionDetails) {
    throw new Error(response.exceptionDetails.exception?.description ?? response.exceptionDetails.text ?? "页面脚本执行失败。");
  }
  return response.result.value as T;
}
