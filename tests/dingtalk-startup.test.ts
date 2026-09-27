import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

for (const shell of ["powershell.exe", "pwsh.exe"]) {
  test(`${shell}: receiver startup approval and singleton fail closed`, () => {
    const result = spawnSync(shell, ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
      path.resolve("tests/dingtalk-startup-state.test.ps1"), "-Controller", path.resolve("tools/django-ai.ps1")],
    { encoding: "utf8", windowsHide: true, timeout: 15000,
      env: shell === "powershell.exe" ? { ...process.env, PSModulePath: path.join(process.env.SystemRoot || "C:\\Windows", "System32/WindowsPowerShell/v1.0/Modules") } : process.env });
    assert.equal(result.status, 0, result.error?.message || result.stderr || result.stdout);
    assert.match(result.stdout, /isolated state and process tests passed/);
  });
}

test("system startup invokes the approved receiver after Worker readiness, including an already running system", () => {
  const source = readFileSync("tools/worker-local-service.ps1", "utf8");
  const start = source.slice(source.indexOf("function Invoke-WorkerSystemStart"), source.indexOf("function Stop-WorkerOnly"));
  assert.equal((start.match(/Start-SystemDingTalkReceiver/g) || []).length, 2);
  assert.ok(start.indexOf("Ensure-DjangoSystemReady") < start.indexOf("Start-SystemDingTalkReceiver"));
  assert.match(start, /if \(\$status.State -eq "exact_release"\) \{\s+Start-SystemDingTalkReceiver/);
  assert.match(start, /\$startResult = Start-VerifiedWorkerSupervisor[^\r\n]+\s+Start-SystemDingTalkReceiver\s+return \$startResult/);
  assert.match(source, /if \(\$Action -eq "Start"\) \{\s+\$startResult = if \(\$JoinedConcurrentLifecycle\) \{ Get-JoinedWorkerStartResult \$identity \} else \{ Invoke-WorkerSystemStart \$identity \}/);
  const joined = source.slice(source.indexOf("function Get-JoinedWorkerStartResult"), source.indexOf("function Invoke-WorkerSystemStart"));
  assert.doesNotMatch(joined, /Start-SystemDingTalkReceiver|Invoke-DjangoStartProcess|Ensure-DjangoSystemReady/);
  const ai = readFileSync("tools/django-ai.ps1", "utf8");
  assert.match(ai, /"AutoStartDingTalk" \{ Invoke-WithServiceMutex \{ Start-ConfiguredDingTalkReceiver \} \}/);
  assert.match(ai, /"StopDingTalk" \{ Invoke-WithServiceMutex \{ Set-DingTalkStartup \$false; Stop-OwnedProcess/);
  const stop = ai.slice(ai.indexOf("function Stop-AiStack"), ai.indexOf("function Read-DingTalkStartup"));
  assert.doesNotMatch(stop, /Set-DingTalkStartup/);
});

test("isolated startup never reaches the production receiver; installed controller failures remain visible", () => {
  const controller = path.resolve("tools/worker-local-service.ps1").replaceAll("'", "''");
  const script = `
. '${controller}' -FunctionsOnly -AllowTestRuntimeRoot
$script:calls=0
function Test-IsIsolatedTestRuntime { $true }
function Invoke-DjangoStartProcess { $script:calls++; throw 'Production dispatch' }
Start-SystemDingTalkReceiver
if($script:calls -ne 0){throw 'Isolation failed'}
function Test-IsIsolatedTestRuntime { $false }
function Test-Path { $true }
function Assert-NoReparsePath {}
function Invoke-WebRequest { @{StatusCode=200} }
function Invoke-DjangoStartProcess($Controller,$ControlAction) {
  if($ControlAction -cne 'AutoStartDingTalk' -or $Controller -notlike '*django-ai.ps1'){throw 'Wrong controller'}
  $script:calls++; return @{ExitCode=1}
}
$denied=$false
try { Start-SystemDingTalkReceiver } catch { $denied=$_.Exception.Message -match 'receiver startup failed' }
if(-not $denied -or $script:calls -ne 1){throw 'Failure hidden or repeated'}
Write-Output 'isolated and bounded'
`;
  const result = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", Buffer.from(script, "utf16le").toString("base64")], { encoding: "utf8", windowsHide: true, timeout: 15000 });
  assert.equal(result.status, 0, result.stderr || result.stdout);
});
