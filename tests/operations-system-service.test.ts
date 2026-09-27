import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import test from "node:test";

const serviceBytes = readFileSync("tools/operations-system-service.ps1");
const service = serviceBytes.toString("utf8");
const launcher = readFileSync("运营系统.bat", "utf8");
const legacyLauncher = readFileSync("运行项目.bat", "utf8");
const packageJson = JSON.parse(readFileSync("package.json", "utf8")) as { scripts: Record<string, string> };

function block(start: string, end: string) {
  const from = service.indexOf(start);
  const to = service.indexOf(end, from + 1);
  assert.ok(from >= 0 && to > from, `expected ${start} before ${end}`);
  return service.slice(from, to);
}

test("lifecycle service stays parseable by Windows PowerShell 5.1 with Chinese text", () => {
  assert.deepEqual([...serviceBytes.subarray(0, 3)], [0xef, 0xbb, 0xbf]);
  assert.match(service, /ValidateSet\("Start", "Stop", "Restart", "Status", "Logs", "Menu"\)/);
  assert.match(service, /teruisi-operations-system-service-v1/);
  assert.match(service, /本脚本只用于 Windows 本机正式环境/);
  assert.doesNotMatch(service, /\?\?|\?\./, "PowerShell 7-only operators are not allowed");
});

test("Start never bypasses the unified controller and the single start engine", () => {
  const startBlock = block("function Invoke-Start", "function Invoke-StopWorkerOnly");
  assert.match(startBlock, /Invoke-ControlledScript \$WindowsPowerShellPath \$ControllerPath \$arguments "start"/);
  assert.doesNotMatch(startBlock, /\$DjangoServicePath/);
  assert.doesNotMatch(startBlock, /\$WorkerServicePath/);
  assert.doesNotMatch(service, /\$DjangoServicePath[^\n]*"Start"/);
  assert.doesNotMatch(service, /\$WorkerServicePath[^\n]*"-Action", "Start"/);
});

test("Stop halts the Worker through its identity gate before the Django stack, under the panel mutex", () => {
  const stopBlock = block("function Invoke-Stop {", "function Invoke-Restart");
  assert.ok(stopBlock.indexOf("Enter-SystemControlMutex") < stopBlock.indexOf("Invoke-StopWorkerOnly"));
  assert.doesNotMatch(stopBlock, /Invoke-StopDjango/);
  assert.match(stopBlock, /Exit-SystemControlMutex \$lease/);
  assert.match(service, /Local\\TERUISI\.Operations\.SystemControl\.v2/);
  assert.match(service, /WaitOne\(\[TimeSpan\]::Zero\)/);
  const workerStop = block("function Invoke-StopWorkerOnly", "function Invoke-Stop {");
  assert.match(workerStop, /\$arguments = @\("-Action", "Stop"\)/);
  assert.match(workerStop, /if \(-not \$KeepBackend\) \{ \$arguments \+= "-IncludeBackend" \}/);
  assert.match(workerStop, /backendStopped -ne \$true/);
  assert.match(workerStop, /@\("stopped", "already_stopped", "stale_receipt_cleared"\)/);
  assert.doesNotMatch(service, /\$DjangoPowerShellPath \$DjangoServicePath @\("-Action", "Stop"/);
});

test("hot restart keeps Django/PostgreSQL running and performs one gated Worker restart", () => {
  const restartBlock = block("function Invoke-Restart", "function Get-ProcessStartUnixMilliseconds");
  assert.match(restartBlock, /if \(\$KeepBackend\)/);
  assert.match(restartBlock, /Enter-SystemControlMutex/);
  assert.match(restartBlock, /\$WorkerServicePath @\("-Action", "Restart"\)/);
  assert.match(restartBlock, /status -ne "restarted"/);
  assert.match(restartBlock, /backendRestarted = \$false/);
  assert.match(restartBlock, /elapsedMilliseconds = if \(\$restartStatus\.PSObject\.Properties\.Name -contains "elapsedMilliseconds"\)/);
  assert.match(restartBlock, /\$WorkerServicePath @\("-Action", "RestartFull"\)/);
  assert.doesNotMatch(
    restartBlock.slice(restartBlock.indexOf("if ($KeepBackend)"), restartBlock.indexOf("return") + "return".length),
    /Invoke-StopDjango|Invoke-StopWorkerOnly/,
  );
});

test("child scripts are waited on through file redirection, never an inherited pipeline", () => {
  const invocation = block("function Invoke-ControlledScript", "function Copy-NewOutput");
  assert.match(invocation, /Start-Process -FilePath \$Executable/);
  assert.match(invocation, /-RedirectStandardOutput \$logs\.Stdout -RedirectStandardError \$logs\.Stderr -PassThru/);
  assert.match(invocation, /\$process\.WaitForExit\(500\)/);
  assert.doesNotMatch(invocation, /=\s*&\s*\$Executable/);
  assert.match(service, /function Write-Line[\s\S]*Write-Host/);
  assert.doesNotMatch(block("function Copy-NewOutput", "function Get-SystemStatus"), /Write-Output/);
});

test("background mode detaches a hidden process and binds its receipt to the process start time", () => {
  const background = block("function Start-InBackground", "function Show-Status");
  assert.match(background, /-WindowStyle Hidden/);
  assert.match(background, /"-File", "`"\$PSCommandPath`"", "-Action", \$Action/);
  assert.match(background, /processStartedAtUnixMs = \$startedAt/);
  assert.match(background, /已有后台任务在执行/);
  const receipt = block("function Read-BackgroundReceipt", "function Start-InBackground");
  assert.match(receipt, /\(Get-ProcessStartUnixMilliseconds \$process\) -eq \[int64\]\$receipt\.processStartedAtUnixMs/);
  assert.match(service, /if \(\$Background -and \$Action -in @\("Start", "Stop", "Restart"\)\)/);
});

function findPowerShell(): string | null {
  for (const candidate of ["pwsh", "pwsh.exe", "powershell.exe"]) {
    const probe = spawnSync(candidate, ["-NoProfile", "-NonInteractive", "-Command", "$PSVersionTable.PSVersion.Major"], {
      encoding: "utf8",
      windowsHide: true,
    });
    if (!probe.error && probe.status === 0 && /^\d+/.test(probe.stdout.trim())) return candidate;
  }
  return null;
}

test("lifecycle service helpers pass their PowerShell unit test when a shell is available", (context) => {
  const shell = findPowerShell();
  if (!shell) {
    context.skip("no PowerShell on PATH");
    return;
  }
  const result = spawnSync(
    shell,
    ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", "tests/operations-system-service.test.ps1"],
    { encoding: "utf8", windowsHide: true },
  );
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /PASS: operations-system-service helpers/);
});

test("batch launcher and npm scripts route every action to the lifecycle service", () => {
  assert.match(launcher, /tools\\operations-system-service\.ps1/);
  assert.ok([...Buffer.from(launcher, "utf8")].every((byte) => byte < 0x80), "cmd wrapper must remain ASCII-only");
  for (const option of ["start", "start-bg", "stop", "stop-worker", "restart", "restart-full", "restart-bg", "status", "logs"]) {
    assert.match(launcher, new RegExp(`if /i "%~1"=="${option}"`));
  }
  assert.match(launcher, /-Action Menu/);
  assert.match(launcher, /-Action Stop -KeepBackend/);
  assert.match(launcher, /-Action Start -Open -Background/);
  assert.match(launcher, /:restart[\s\S]*?-Action Restart -KeepBackend -Open/);
  assert.match(launcher, /:restart_full[\s\S]*?-Action Restart -Open/);
  assert.equal(packageJson.scripts["system:start"], "powershell -NoProfile -ExecutionPolicy Bypass -File tools/operations-system-service.ps1 -Action Start");
  assert.equal(packageJson.scripts["system:stop"], "powershell -NoProfile -ExecutionPolicy Bypass -File tools/operations-system-service.ps1 -Action Stop");
  assert.equal(packageJson.scripts["system:restart"], "powershell -NoProfile -ExecutionPolicy Bypass -File tools/operations-system-service.ps1 -Action Restart -KeepBackend");
  assert.equal(packageJson.scripts["system:restart:full"], "powershell -NoProfile -ExecutionPolicy Bypass -File tools/operations-system-service.ps1 -Action Restart");
  assert.equal(packageJson.scripts["system:logs"], "powershell -NoProfile -ExecutionPolicy Bypass -File tools/operations-system-service.ps1 -Action Logs");
  assert.equal(packageJson.scripts["backend:dev"], "node tools/django-dev-backend.mjs start");
});

test("legacy launcher delegates browser opening to the Chrome-aware unified controller", () => {
  assert.match(legacyLauncher, /tools\\operations-system-control\.ps1" -Action Start -Open/);
  assert.match(legacyLauncher, /Google Chrome/);
  assert.doesNotMatch(legacyLauncher, /start "" "http:\/\/localhost:3000"/);
});

test("batch launcher opens and exits its menu through real cmd.exe", (context) => {
  if (process.platform !== "win32") {
    context.skip("cmd.exe is only available on Windows");
    return;
  }
  const result = spawnSync("cmd.exe", ["/d", "/c", "echo 0|运营系统.bat"], {
    cwd: process.cwd(),
    encoding: "utf8",
    windowsHide: true,
    timeout: 15_000,
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.doesNotMatch(`${result.stdout}\n${result.stderr}`, /not recognized|不是内部或外部命令/i);
  assert.match(result.stdout, /={20,}/);
  assert.match(result.stdout, /Worker \+ Django\/PostgreSQL/);
  assert.match(result.stdout, /\b0\s+/);
});
