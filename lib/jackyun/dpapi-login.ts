import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";
import type { Page } from "playwright-core";
import { readChromeBrowserProcessId } from "./cdp-client";
import { jackyunPowerShellUtf8Pipes } from "./dpapi-program";
import { readJackyunRuntimeCredential, windowsPowerShellEnvironment, type JackyunCredential, type JackyunLoginConfig } from "./windows-dpapi";

const execFileAsync = promisify(execFile);
const loginOrigin = "https://web.jackyun.com";
export type JackyunLoginSurface = { phase: "pending" | "login" | "authenticated" | "blocked"; reason?: string };

export function isJackyunLoginOrigin(url: string) {
  try { return new URL(url).origin === loginOrigin; } catch { return false; }
}

function windowsArguments(command: string) {
  return command.match(/(?:[^\s"]|"[^"]*")+/g)?.map(token => token.replace(/"/g, "")) ?? [];
}

export function resolveJackyunChromiumExecutable(localAppData = process.env.LOCALAPPDATA) {
  if (!localAppData || !/^[A-Za-z]:[\\/]/.test(localAppData)) {
    throw new Error("waiting_login：无法定位本机独立 Chromium 安装目录。");
  }
  return path.win32.join(localAppData, "Chromium", "Application", "chrome.exe");
}

type JackyunBrowserBinding = { chromePath: string; profileDirectory: string; port: number; headless?: true; processId?: number };

export function assertJackyunBrowserIdentity(identity: { executablePath: string; commandLine: string; ownedByCurrentUser: boolean },
  expected: JackyunBrowserBinding) {
  const normalize = (value: string) => path.win32.resolve(value).replace(/\\+$/, "").toLowerCase();
  const args = windowsArguments(identity.commandLine);
  const profiles = args.filter(arg => arg.startsWith("--user-data-dir="));
  const ports = args.filter(arg => arg.startsWith("--remote-debugging-port="));
  const headless = args.filter(arg => arg === "--headless" || arg.startsWith("--headless="));
  if (!identity.ownedByCurrentUser || normalize(identity.executablePath) !== normalize(expected.chromePath)
    || profiles.length !== 1 || ports.length !== 1
    || normalize(profiles[0].slice("--user-data-dir=".length)) !== normalize(expected.profileDirectory)
    || ports[0] !== `--remote-debugging-port=${expected.port}`
    || (expected.headless && (headless.length !== 1 || headless[0] !== "--headless=new"))) {
    throw new Error("waiting_login：专用浏览器进程、Windows 用户、Profile 或端口绑定不一致。");
  }
}

export function jackyunBrowserIdentityProgram(pid: number) {
  if (!Number.isSafeInteger(pid) || pid <= 0) throw new Error("吉客云浏览器进程 ID 无效。");
  return `$ErrorActionPreference='Stop'; ${jackyunPowerShellUtf8Pipes}
    $p=Get-CimInstance Win32_Process -Filter 'ProcessId=${pid}';
    $owner=Invoke-CimMethod -InputObject $p -MethodName GetOwnerSid;
    $pipeWriter.WriteLine((@{executablePath=$p.ExecutablePath;commandLine=$p.CommandLine;ownedByCurrentUser=($owner.Sid -eq [Security.Principal.WindowsIdentity]::GetCurrent().User.Value)} | ConvertTo-Json -Compress))`;
}

export async function verifyJackyunBrowserBinding(expected: JackyunBrowserBinding) {
  const pid = await readChromeBrowserProcessId(expected.port);
  if (expected.processId !== undefined && pid !== expected.processId) {
    throw new Error("waiting_login：调试端口不属于本次启动的独立浏览器，已停止接管。");
  }
  const script = jackyunBrowserIdentityProgram(pid);
  let stdout = "";
  try {
    const result = await execFileAsync("powershell.exe", ["-NoProfile", "-NonInteractive", "-EncodedCommand", Buffer.from(script, "utf16le").toString("base64")], {
      windowsHide: true, encoding: "utf8", timeout: 15000, maxBuffer: 16384,
      env: windowsPowerShellEnvironment(),
    });
    stdout = result.stdout;
    assertJackyunBrowserIdentity(JSON.parse(stdout), expected);
  } catch {
    throw new Error("waiting_login：专用浏览器进程和 Profile 身份验证未通过。");
  } finally { stdout = ""; }
}

/** Only observed, unique Jackyun controls are accepted. Never inspect input values. */
export async function inspectJackyunLoginSurface(page: Page, tenantId: string): Promise<JackyunLoginSurface> {
  if (page.url() === "about:blank") return { phase: "pending" };
  if (!isJackyunLoginOrigin(page.url())) return { phase: "blocked", reason: "origin_mismatch" };
  try {
    return await page.evaluate<JackyunLoginSurface>(`((tenant) => {
      const visible = (el) => {
        const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        return rect.width > 2 && rect.height > 2 && style.visibility !== "hidden" && style.display !== "none";
      };
      const found = (selector) => [...document.querySelectorAll(selector)].filter(visible);
      const password = found('input[type="password"]');
      const challengeInputs = found("input").some(input => /验证码|短信|验证手机/.test(input.getAttribute("placeholder") ?? ""));
      const text = document.body?.innerText ?? "";
      if (challengeInputs || /请拖动|滑动.{0,8}验证|请完成.{0,8}验证|安全验证|操作过于频繁|账号.{0,5}锁定/.test(text)) {
        return { phase: "blocked", reason: "challenge_present" };
      }
      if (/密码.{0,8}(?:错误|不正确)|(?:账号|用户名|工号).{0,10}(?:错误|不存在)|登录失败/.test(text)) {
        return { phase: "blocked", reason: "credential_rejected" };
      }
      const tenantLabels = found("#jlink-sn");
      const menus = found("#J-menu");
      if (tenantLabels.length || menus.length) {
        if (tenantLabels.length === 1 && menus.length === 1 && password.length === 0) {
          return tenantLabels[0].textContent?.trim() === tenant
            ? { phase: "authenticated" } : { phase: "blocked", reason: "tenant_mismatch" };
        }
        return { phase: "pending" };
      }
      if (!password.length) return { phase: "pending" };
      if (password.length !== 1 || password[0].id !== "txtPwd" || found("#selAccount").length !== 1 || found("#txtUserName").length !== 1
        || found("#txtPwd").length !== 1 || found("#btnLogin").length !== 1) {
        return { phase: "blocked", reason: "form_ambiguous" };
      }
      return { phase: "login" };
    })(${JSON.stringify(tenantId)})`);
  } catch { return { phase: "pending" }; }
}

export async function submitJackyunDpapiLogin(page: Page, config: JackyunLoginConfig,
  loadCredential: () => Promise<JackyunCredential> = () => readJackyunRuntimeCredential(config)) {
  if (process.env.PWDEBUG || /pw:|\*/i.test(process.env.DEBUG ?? "")) throw new Error("waiting_login：凭据登录不能在浏览器调试日志模式下执行。");
  if ((await inspectJackyunLoginSurface(page, config.tenantId)).phase !== "login") {
    throw new Error("waiting_login：唯一吉客云账号密码表单未就绪。");
  }
  // Pin the observed document. Locator auto-retry must not move credentials
  // into a different page if a navigation happens during asynchronous decrypt.
  const tenant = await page.locator("#selAccount").elementHandle();
  const account = await page.locator("#txtUserName").elementHandle();
  const password = await page.locator("#txtPwd").elementHandle();
  const button = await page.locator("#btnLogin").elementHandle();
  if (!tenant || !account || !password || !button) throw new Error("waiting_login：登录控件已变化。");
  const credential = await loadCredential();
  try {
    // Recheck after the asynchronous decrypt, before any secret reaches a page.
    if ((await inspectJackyunLoginSurface(page, config.tenantId)).phase !== "login") throw new Error("changed");
    await tenant.fill(config.tenantId, { timeout: 5000 });
    if (!isJackyunLoginOrigin(page.url())) throw new Error("changed");
    await account.fill(credential.username, { timeout: 5000 });
    if (!isJackyunLoginOrigin(page.url())) throw new Error("changed");
    await password.fill(credential.password, { timeout: 5000 });
    if ((await inspectJackyunLoginSurface(page, config.tenantId)).phase !== "login") throw new Error("changed");
    await button.click({ timeout: 5000 });
  } catch {
    // Playwright call logs can echo fill arguments. Never forward their errors.
    throw new Error("waiting_login：吉客云登录表单变化或提交结果未确定，已停止自动提交。");
  } finally {
    credential.username = ""; credential.password = "";
    await Promise.allSettled([tenant.dispose(), account.dispose(), password.dispose(), button.dispose()]);
  }
}

export async function waitForJackyunDpapiSession(deps: {
  inspect: () => Promise<JackyunLoginSurface>;
  submit: () => Promise<void>;
  initialWaitMs: number;
  afterSubmitWaitMs: number;
  readOnly?: boolean;
  signal?: AbortSignal;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
}) {
  const now = deps.now ?? Date.now;
  const sleep = deps.sleep ?? (ms => new Promise(resolve => setTimeout(resolve, ms)));
  let deadline = now() + deps.initialWaitMs;
  let submitted = false;
  do {
    if (deps.signal?.aborted) throw new Error("waiting_login：登录检查已取消。");
    const surface = await deps.inspect();
    if (surface.phase === "blocked") throw new Error(`waiting_login：吉客云登录已停止（${surface.reason}）。`);
    if (surface.phase === "authenticated") return { status: "authenticated" as const, authentication: submitted ? "windows_dpapi_credentials" : "existing_session" };
    if (surface.phase === "login" && !submitted) {
      if (deps.readOnly) return { status: "login_required" as const, authentication: "none" };
      submitted = true;
      await deps.submit();
      deadline = now() + deps.afterSubmitWaitMs;
    }
    if (now() >= deadline) break;
    await sleep(Math.min(500, deadline - now()));
  } while (now() <= deadline);
  throw new Error(`waiting_login：吉客云${submitted ? "提交后登录" : "初始页面"}状态未在限定时间内确认。`);
}
