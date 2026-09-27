import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { chromium } from "playwright-core";
import { assertJackyunBrowserIdentity, inspectJackyunLoginSurface, isJackyunLoginOrigin, submitJackyunDpapiLogin, waitForJackyunDpapiSession, resolveJackyunChromiumExecutable, jackyunBrowserIdentityProgram, type JackyunLoginSurface } from "../lib/jackyun/dpapi-login";
import { assertJackyunLoginConfig, invokeJackyunVault, readJackyunRuntimeCredential, isRetryableJackyunCredentialPreparationFailure, windowsPowerShellEnvironment, type JackyunLoginConfig } from "../lib/jackyun/windows-dpapi";
import { jackyunDpapiProgram } from "../lib/jackyun/dpapi-program";

const config: JackyunLoginConfig = { version: 1, loginMode: "windows_dpapi_credentials", tenantId: "771168",
  profileDirectory: "D:\\test profiles\\吉客云", debuggingPort: 19223, initialWaitMs: 1000, afterSubmitWaitMs: 1000 };

// Detach only this disposable PowerShell child. Pipes remain connected, but
// Console.InputEncoding now reproduces ERROR_INVALID_HANDLE from background runs.
const withoutConsole = String.raw`
Add-Type -TypeDefinition 'using System.Runtime.InteropServices; public class JackyunTestConsole { [DllImport("kernel32.dll")] public static extern bool FreeConsole(); }'
[JackyunTestConsole]::FreeConsole() | Out-Null
`;

function runWithoutConsole(program: string, input = "") {
  return spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand",
    Buffer.from(withoutConsole + program, "utf16le").toString("base64")], {
    input, windowsHide: true, env: windowsPowerShellEnvironment(), encoding: "utf8", timeout: 15000, maxBuffer: 32768,
  });
}

function detachedVault(action: "read" | "status", vaultRoot: string, login = config) {
  return runWithoutConsole(jackyunDpapiProgram, JSON.stringify({ action, vaultRoot, tenantId: login.tenantId, profileDirectory: login.profileDirectory }));
}

test("background child reproduces old console failure and can verify process ownership with UTF-8 pipes", {
  skip: process.platform !== "win32", timeout: 30000,
}, () => {
  const old = runWithoutConsole(String.raw`
$ErrorActionPreference='Stop'
try { [Console]::InputEncoding=New-Object Text.UTF8Encoding($false); exit 2 }
catch { @{failed=$true;type=$_.Exception.GetType().FullName} | ConvertTo-Json -Compress; exit 1 }
`);
  assert.equal(old.status, 1);
  assert.equal(JSON.parse(old.stdout).failed, true);
  const identity = runWithoutConsole(jackyunBrowserIdentityProgram(process.pid));
  assert.equal(identity.status, 0);
  const observed = JSON.parse(identity.stdout);
  assert.equal(observed.executablePath.toLowerCase(), process.execPath.toLowerCase());
  assert.equal(observed.ownedByCurrentUser, true);
  for (const invalid of [0, -1, NaN, 1.5, Infinity]) assert.throws(() => jackyunBrowserIdentityProgram(invalid));
});

function sessionHarness(surfaces: JackyunLoginSurface[]) {
  let now = 0;
  let submits = 0;
  const sequence = [...surfaces];
  return { deps: { inspect: async () => sequence.length > 1 ? sequence.shift()! : sequence[0],
    submit: async () => { submits++; }, initialWaitMs: 1000, afterSubmitWaitMs: 1000,
    now: () => now, sleep: async (ms: number) => { now += ms; } }, submits: () => submits };
}

test("DPAPI config and site binding reject invalid modes, ports and lookalike hosts", () => {
  assert.equal(assertJackyunLoginConfig(config), config);
  for (const patch of [{ loginMode: "manual" }, { tenantId: "unknown" }, { debuggingPort: 0 }, { profileDirectory: "relative" }]) {
    assert.throws(() => assertJackyunLoginConfig({ ...config, ...patch } as JackyunLoginConfig));
  }
  assert.equal(isJackyunLoginOrigin("https://web.jackyun.com/login/login_web.html"), true);
  for (const url of ["http://web.jackyun.com", "https://web.jackyun.com.evil.test", "https://jackyun.com", "about:blank"]) {
    assert.equal(isJackyunLoginOrigin(url), false);
  }
  assert.ok(Buffer.from(jackyunDpapiProgram, "utf16le").toString("base64").length < 30000);
});

test("credential preparation retries only before browser use and stops after a bounded budget", async () => {
  let calls = 0;
  const delays: number[] = [];
  const transient = new Error("waiting_login：吉客云 DPAPI 凭据配置或解密未完成（binding_input）。");
  const invoke = (async () => {
    calls++;
    if (calls < 3) throw transient;
    return JSON.stringify({ username: "fixture", password: "fixture-only" });
  }) as typeof invokeJackyunVault;
  assert.deepEqual(await readJackyunRuntimeCredential(config, { invoke, sleep: async ms => { delays.push(ms); } }),
    { username: "fixture", password: "fixture-only" });
  assert.equal(calls, 3);
  assert.deepEqual(delays, [500, 1000]);
  calls = 0;
  await assert.rejects(readJackyunRuntimeCredential(config, {
    invoke: (async () => { calls++; throw transient; }) as typeof invokeJackyunVault,
    sleep: async () => {},
  }), /binding_input/);
  assert.equal(calls, 3);
  calls = 0;
  await assert.rejects(readJackyunRuntimeCredential(config, {
    invoke: (async () => { calls++; throw new Error("waiting_login：吉客云 DPAPI 凭据配置或解密未完成（read）。"); }) as typeof invokeJackyunVault,
    sleep: async () => {},
  }), /（read）/);
  assert.equal(calls, 1);
  calls = 0;
  await assert.rejects(readJackyunRuntimeCredential(config, {
    invoke: (async () => { calls++; throw new Error("waiting_login：吉客云 DPAPI 凭据配置或解密未完成（binding_fields）。"); }) as typeof invokeJackyunVault,
    sleep: async () => {},
  }), /binding_fields/);
  assert.equal(calls, 1);
  calls = 0;
  await assert.rejects(readJackyunRuntimeCredential(config, {
    invoke: (async () => { calls++; throw new Error("waiting_login：吉客云 DPAPI 凭据配置或解密未完成（missing）。"); }) as typeof invokeJackyunVault,
    sleep: async () => {},
  }), /（missing）/);
  assert.equal(calls, 2); // One read and one failed status probe; no second read.
});

test("missing lookup retries only after fresh same-binding readiness and remains bounded", async () => {
  const missing = new Error("waiting_login：吉客云 DPAPI 凭据配置或解密未完成（missing / lookup=provider:0,file:1,directory:1）。");
  const actions: string[] = [], delays: number[] = [];
  const ready = JSON.stringify({ ok: true, ready: true, status: "ready" });
  let reads = 0;
  const credential = { username: "fixture", password: "fixture-only" };
  const invoke = (async (action: string, bound: typeof config) => {
    assert.equal(bound, config); actions.push(action);
    if (action === "status") return ready;
    if (++reads === 1) throw missing;
    return JSON.stringify(credential);
  }) as typeof invokeJackyunVault;
  assert.deepEqual(await readJackyunRuntimeCredential(config, { invoke, sleep: async ms => { delays.push(ms); } }), credential);
  assert.deepEqual(actions, ["read", "status", "read"]); assert.deepEqual(delays, [500]);
  for (const status of [JSON.stringify({ ok: true, ready: false, status: "missing" }), "malformed", "throws"]) {
    const calls: string[] = [];
    await assert.rejects(readJackyunRuntimeCredential(config, { invoke: (async (action: string) => {
      calls.push(action); if (action === "read") throw missing;
      if (status === "throws") throw new Error("synthetic-secret"); return status;
    }) as typeof invokeJackyunVault, sleep: async () => assert.fail("must not wait for a terminal failure") }), e => e === missing);
    assert.deepEqual(calls, ["read", "status"]);
  }
  const exhausted: string[] = [], waits: number[] = [];
  await assert.rejects(readJackyunRuntimeCredential(config, { invoke: (async (action: string) => {
    exhausted.push(action); if (action === "status") return ready; throw missing;
  }) as typeof invokeJackyunVault, sleep: async ms => { waits.push(ms); } }), e => {
    assert(e instanceof Error); assert.equal(e.message, missing.message);
    assert.equal(isRetryableJackyunCredentialPreparationFailure(e), true); return true;
  });
  assert.deepEqual(exhausted, ["read", "status", "read", "status", "read", "status"]);
  assert.deepEqual(waits, [500, 1000]);
  assert.equal(isRetryableJackyunCredentialPreparationFailure(missing), false);
});

test("browser process must match the Windows owner, executable, profile and exact unique port flags", () => {
  const expected = { chromePath: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", profileDirectory: config.profileDirectory, port: 19223 };
  const identity = { executablePath: expected.chromePath, ownedByCurrentUser: true,
    commandLine: `"${expected.chromePath}" --remote-debugging-port=19223 "--user-data-dir=${config.profileDirectory}"` };
  assert.doesNotThrow(() => assertJackyunBrowserIdentity(identity, expected));
  for (const patch of [{ ownedByCurrentUser: false }, { executablePath: "C:\\other.exe" },
    { commandLine: identity.commandLine.replace("19223", "9223") },
    { commandLine: identity.commandLine + " --remote-debugging-port=19223" },
    { commandLine: identity.commandLine.replace("test profiles", "another profile") }]) {
    assert.throws(() => assertJackyunBrowserIdentity({ ...identity, ...patch }, expected));
  }
});

test("scheduled API login requires independent Chromium and an unambiguous headless process", () => {
  const executable = resolveJackyunChromiumExecutable("C:\\Users\\fixture user\\AppData\\Local");
  assert.equal(executable, "C:\\Users\\fixture user\\AppData\\Local\\Chromium\\Application\\chrome.exe");
  for (const root of ["", "relative", "\\\\remote\\profile", "C:relative"]) {
    assert.throws(() => resolveJackyunChromiumExecutable(root));
  }
  const expected = { chromePath: executable, profileDirectory: config.profileDirectory, port: 19223, headless: true as const };
  const identity = { executablePath: executable, ownedByCurrentUser: true,
    commandLine: `"${executable}" --remote-debugging-port=19223 "--user-data-dir=${config.profileDirectory}"` };
  assert.doesNotThrow(() => assertJackyunBrowserIdentity({ ...identity, commandLine: identity.commandLine + " --headless=new" }, expected));
  for (const flags of ["", " --headless", " --headless=false", " --headless=old", " --headless=new --headless=new", " --headless=new --headless=false"]) {
    assert.throws(() => assertJackyunBrowserIdentity({ ...identity, commandLine: identity.commandLine + flags }, expected));
  }
  assert.throws(() => assertJackyunBrowserIdentity({ ...identity, executablePath: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    commandLine: identity.commandLine + " --headless=new" }, expected));
});

test("initial loading waits for an actual login page and a successful tenant shell", async () => {
  const h = sessionHarness([{ phase: "pending" }, { phase: "login" }, { phase: "pending" }, { phase: "authenticated" }]);
  assert.equal((await waitForJackyunDpapiSession(h.deps)).authentication, "windows_dpapi_credentials");
  assert.equal(h.submits(), 1);
});

test("existing session and read-only probes never decrypt or submit", async () => {
  for (const phase of ["authenticated", "login"] as const) {
    const h = sessionHarness([{ phase }]);
    await waitForJackyunDpapiSession({ ...h.deps, readOnly: true });
    assert.equal(h.submits(), 0);
  }
});

test("unknown pages time out and a submitted login is never retried", async () => {
  for (const phase of ["pending", "login"] as const) {
    const h = sessionHarness([{ phase }]);
    await assert.rejects(waitForJackyunDpapiSession(h.deps), /限定时间/);
    assert.equal(h.submits(), phase === "login" ? 1 : 0);
  }
});

test("wrong tenant, challenges, rejected credentials and uncertain submission stop immediately", async () => {
  for (const reason of ["tenant_mismatch", "challenge_present", "credential_rejected", "form_ambiguous"]) {
    const h = sessionHarness([{ phase: "blocked", reason }]);
    await assert.rejects(waitForJackyunDpapiSession(h.deps));
    assert.equal(h.submits(), 0);
  }
  const h = sessionHarness([{ phase: "login" }]);
  let attempts = 0;
  await assert.rejects(waitForJackyunDpapiSession({ ...h.deps, submit: async () => { attempts++; throw new Error("uncertain"); } }));
  assert.equal(attempts, 1);
});

test("aborted login cannot consume a credential", async () => {
  const h = sessionHarness([{ phase: "login" }]);
  await assert.rejects(waitForJackyunDpapiSession({ ...h.deps, signal: AbortSignal.abort() }), /取消/);
  assert.equal(h.submits(), 0);
});

const chromePath = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
test("observed Jackyun three-field DOM, hidden recovery forms, tenant gate and error redaction", {
  skip: process.platform !== "win32" || !existsSync(chromePath), timeout: 30000,
}, async () => {
  const browser = await chromium.launch({ executablePath: chromePath, headless: true });
  try {
    const page = await browser.newPage();
    // All requests are fulfilled locally; this fixture never connects to Jackyun.
    await page.route("**/*", route => route.fulfill({ contentType: "text/html", body: `<!doctype html><body>
      <input id="selAccount" placeholder="请输入或选择吉客号">
      <input id="txtUserName" placeholder="请输入手机号或工号">
      <input id="txtPwd" type="password" placeholder="请输入密码">
      <input id="btnLogin" type="button" value="登 录">
      <div hidden><input type="password"><input placeholder="请输入验证码"></div>
      <script>document.querySelector('#btnLogin').onclick=()=>{document.body.dataset.submitted='yes'}</script></body>` }));
    await page.goto("https://web.jackyun.com/login/login_web.html");
    assert.equal((await inspectJackyunLoginSurface(page, config.tenantId)).phase, "login");
    const fake = { username: "fixture-user", password: "fixture-not-real-password" };
    await submitJackyunDpapiLogin(page, config, async () => fake);
    assert.deepEqual(fake, { username: "", password: "" });
    assert.equal(await page.locator("body").getAttribute("data-submitted"), "yes");
    assert.equal(await page.locator("#selAccount").inputValue(), config.tenantId);
    await page.locator("body").evaluate(el => { el.insertAdjacentHTML("beforeend", '<input type="password">'); });
    let loads = 0;
    await assert.rejects(submitJackyunDpapiLogin(page, config, async () => { loads++; return { username: "x", password: "y" }; }));
    assert.equal(loads, 0);
    await page.setContent('<span id="jlink-sn">123456</span><ul id="J-menu"><li>货品</li></ul>');
    assert.equal((await inspectJackyunLoginSurface(page, config.tenantId)).reason, "tenant_mismatch");
    await page.setContent('<span id="jlink-sn">771168</span><ul id="J-menu"><li>货品</li></ul>');
    assert.equal((await inspectJackyunLoginSurface(page, config.tenantId)).phase, "authenticated");
    await page.goto("https://web.jackyun.com/login/login_web.html");
    const stale = { username: "fixture-user", password: "fixture-not-real-password" };
    await assert.rejects(submitJackyunDpapiLogin(page, config, async () => {
      await page.goto("https://lookalike.invalid/");
      return stale;
    }), error => {
      assert.doesNotMatch(String(error), /fixture-user|fixture-not-real-password/);
      return true;
    });
    assert.deepEqual(stale, { username: "", password: "" });
  } finally { await browser.close(); }
});

test("Windows DPAPI roundtrip rejects copied bindings, corrupted ciphertext and exposed ACLs", {
  skip: process.platform !== "win32", timeout: 60000,
}, async () => {
  const root = await mkdtemp(path.join(tmpdir(), "jackyun-dpapi-fixture-"));
  const exposedRoot = await mkdtemp(path.join(tmpdir(), "jackyun-dpapi-fixture-"));
  const binding = `TERUISI-JACKYUN:v1:${config.tenantId}:${config.profileDirectory.toLowerCase()}`;
  const key = createHash("sha256").update(binding).digest("hex");
  const fixture = String.raw`
    $ErrorActionPreference='Stop'; Add-Type -AssemblyName System.Security
    $inputReader=New-Object IO.StreamReader([Console]::OpenStandardInput(),(New-Object Text.UTF8Encoding($false,$true)),$false)
    $r=$inputReader.ReadToEnd()|ConvertFrom-Json
    $acl=New-Object Security.AccessControl.DirectorySecurity
    $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl.SetOwner($sid); $acl.SetAccessRuleProtection($true,$false)
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')))
    Set-Acl -LiteralPath $r.root -AclObject $acl
    $plain=@{tenantId=$r.tenant;profile=$r.profile;username='模拟账号🧪';password='模拟密码🔐'}|ConvertTo-Json -Compress
    $cipher=[Security.Cryptography.ProtectedData]::Protect([Text.Encoding]::UTF8.GetBytes($plain),[Text.Encoding]::UTF8.GetBytes($r.binding),[Security.Cryptography.DataProtectionScope]::CurrentUser)
    @{version=1;binding=$r.key;ciphertext=[Convert]::ToBase64String($cipher)}|ConvertTo-Json -Compress|Set-Content -LiteralPath (Join-Path $r.root ($r.key+'.json')) -Encoding UTF8
  `;
  const environment = windowsPowerShellEnvironment();
  try {
    const prepared = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-EncodedCommand", Buffer.from(fixture, "utf16le").toString("base64")], {
      input: JSON.stringify({ root, tenant: config.tenantId, profile: config.profileDirectory.toLowerCase(), binding, key }),
      windowsHide: true, env: environment, timeout: 15000,
    });
    assert.equal(prepared.status, 0, prepared.stderr.toString().slice(0, 3000));
    assert.equal(JSON.parse(await invokeJackyunVault("status", config, root)).ready, true);
    const fakeCredential = { username: "模拟账号🧪", password: "模拟密码🔐" };
    assert.deepEqual(JSON.parse(await invokeJackyunVault("read", config, root)), fakeCredential);
    const detachedStatus = detachedVault("status", root);
    assert.equal(detachedStatus.status, 0);
    assert.deepEqual(JSON.parse(detachedStatus.stdout), { ok: true, ready: true, status: "ready" });
    const detachedRead = detachedVault("read", root);
    assert.equal(detachedRead.status, 0);
    assert.deepEqual(JSON.parse(detachedRead.stdout), fakeCredential);
    // Simulate a provider false-negative against an existing synthetic vault.
    // The independent .NET observation is diagnostic and must never allow a read.
    const forcedMissing = runWithoutConsole(jackyunDpapiProgram.replace(
      "if (-not (Test-Path -LiteralPath $vaultFile)) {", "if ($true) {"),
    JSON.stringify({ action: "read", vaultRoot: root, tenantId: config.tenantId, profileDirectory: config.profileDirectory }));
    assert.equal(forcedMissing.status, 1);
    assert.equal(JSON.parse(forcedMissing.stdout).stage, "missing");
    assert.deepEqual(JSON.parse(forcedMissing.stdout).lookup, { provider: false, file: true, directory: true });
    assert.doesNotMatch(forcedMissing.stdout + forcedMissing.stderr, /模拟账号|模拟密码|ciphertext|vaultRoot|profileDirectory/);
    const absentRoot = path.join(root, "absent-vault");
    await assert.rejects(invokeJackyunVault("read", config, absentRoot), /missing.*lookup=provider:0,file:0,directory:0/);
    const file = path.join(root, `${key}.json`);
    const bytes = await readFile(file, "utf8");
    assert.doesNotMatch(bytes, /模拟账号|模拟密码/);
    const wrong = { ...config, tenantId: "999999" };
    const wrongKey = createHash("sha256").update(`TERUISI-JACKYUN:v1:999999:${config.profileDirectory.toLowerCase()}`).digest("hex");
    await writeFile(path.join(root, `${wrongKey}.json`), JSON.stringify({ ...JSON.parse(bytes.replace(/^\uFEFF/, "")), binding: wrongKey }));
    await assert.rejects(invokeJackyunVault("read", wrong, root), /waiting_login/);
    const wrongBinding = detachedVault("read", root, wrong);
    assert.equal(wrongBinding.status, 1);
    assert.equal(JSON.parse(wrongBinding.stdout).stage, "read");
    await writeFile(file, JSON.stringify({ version: 1, binding: key, ciphertext: "corrupt" }));
    await assert.rejects(invokeJackyunVault("read", config, root), /waiting_login/);
    const corrupted = detachedVault("read", root);
    assert.equal(corrupted.status, 1);
    assert.equal(JSON.parse(corrupted.stdout).stage, "read");
    assert.doesNotMatch(corrupted.stdout + corrupted.stderr, /模拟账号|模拟密码|ciphertext/);
    await writeFile(file, bytes);
    // Copying otherwise-valid ciphertext into a directory with inherited ACLs
    // must be refused, even though DPAPI itself would still decrypt it.
    await writeFile(path.join(exposedRoot, `${key}.json`), bytes);
    await assert.rejects(invokeJackyunVault("read", config, exposedRoot), /waiting_login/);
    assert.equal(detachedVault("read", exposedRoot).status, 1);
  } finally {
    assert.ok(path.resolve(root).startsWith(path.resolve(tmpdir()) + path.sep));
    assert.ok(path.basename(root).startsWith("jackyun-dpapi-fixture-"));
    await rm(root, { recursive: true, force: true });
    assert.ok(path.resolve(exposedRoot).startsWith(path.resolve(tmpdir()) + path.sep));
    assert.ok(path.basename(exposedRoot).startsWith("jackyun-dpapi-fixture-"));
    await rm(exposedRoot, { recursive: true, force: true });
  }
});
