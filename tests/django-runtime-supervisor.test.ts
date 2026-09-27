import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const supervisorPath = path.join(root, "tools", "django-runtime-supervisor.ps1");
const servicePath = path.join(root, "tools", "django-local-service.ps1");
const fixturePath = path.join(root, "tests", "django-runtime-supervisor-state.test.ps1");
const powershell = path.join(
  process.env.SystemRoot ?? "C:\\Windows",
  "System32",
  "WindowsPowerShell",
  "v1.0",
  "powershell.exe",
);

test("supervisor completes direct Start without waiting for or killing inherited-output descendants", {
  skip: process.platform !== "win32", timeout: 25_000,
}, () => {
  const result = spawnSync(powershell, ["-NoProfile", "-NonInteractive", "-File",
    path.join(root, "tests/django-supervisor-start-output.test.ps1")],
  { encoding: "utf8", windowsHide: true, timeout: 20_000 });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.deepEqual(JSON.parse(result.stdout.trim()), { directExit: 23, descendantPreserved: true, noProductionChanges: true });
});

test("Django runtime supervisor and service operator parse under PowerShell 5", async (t) => {
  if (process.platform !== "win32" || !existsSync(powershell)) {
    t.skip("Windows PowerShell 5 is unavailable");
    return;
  }
  for (const scriptPath of [supervisorPath, servicePath]) {
    const escaped = scriptPath.replaceAll("'", "''");
    const command = [
      "$tokens=$null; $errors=$null;",
      `$source=[IO.File]::ReadAllText('${escaped}',[Text.Encoding]::UTF8);`,
      "[System.Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors) | Out-Null;",
      "if($errors.Count){$errors | ForEach-Object {$_.Message}; exit 1}",
    ].join(" ");
    const result = spawnSync(powershell, [
      "-NoProfile", "-NonInteractive", "-Command", command,
    ], { encoding: "utf8", windowsHide: true });
    assert.equal(result.status, 0, result.stderr || result.stdout);
  }
});

test("machine-readable service status preserves the bounded root-only probe", async () => {
  const service = await readFile(servicePath, "utf8");
  const statusBlock = service.match(
    /function Show-ServiceStatus \{([\s\S]*?)\r?\n\}\r?\n\r?\nfunction Install-StartupShortcut/,
  )?.[1];
  assert.ok(statusBlock);
  assert.match(service, /\[switch\]\$Json/);
  assert.match(statusBlock, /Assert-RuntimeRootAclHardened/);
  assert.doesNotMatch(statusBlock, /Assert-RuntimeAclHardened/);
  assert.match(statusBlock, /CheckedAt = \[DateTimeOffset\]::UtcNow/);
  assert.match(statusBlock, /if \(\$Json\.IsPresent\)/);
  assert.match(statusBlock, /ConvertTo-Json -Compress/);
});

test("explicit Stop disarms before stopping and supervised Start is fenced under the service mutex", async () => {
  const service = await readFile(servicePath, "utf8");
  const dispatch = service.slice(service.indexOf("if ($env:TERUISI_DJANGO_SERVICE_LIBRARY_ONLY"));
  const stopBlock = dispatch.match(
    /"Stop" \{([\s\S]*?)\r?\n\s*\}\r?\n\s*"Status"/,
  )?.[1] ?? "";
  assert.ok(stopBlock.indexOf("Write-ServiceDesiredState \"stopped\"") >= 0);
  assert.ok(stopBlock.indexOf("Write-ServiceDesiredState \"stopped\"") < stopBlock.indexOf("Stop-ServiceStack"));
  assert.match(service, /SupervisorExpectedDesiredStateSha256/);
  assert.match(service, /Assert-SupervisorStartFence/);
  assert.match(service, /Get-FileSha256 \$SupervisorDesiredStatePath/);
  assert.match(service, /desiredState -cne "running"/);
  assert.match(service, /Assert-DjangoSupervisorStopped \$Operation/);
  assert.match(service, /发现未登记的 Django supervisor；拒绝修改或自动终止/);
  assert.match(
    dispatch,
    /Assert-SupervisorStartFence \$SupervisorExpectedDesiredStateSha256\s+Start-ServiceStack/,
  );
});

test("supervisor restarts only exact stopped processes and never auto-fixes data or ownership failures", async () => {
  const script = await readFile(supervisorPath, "utf8");
  assert.match(script, /postgresql_process_stopped/);
  assert.match(script, /managed_child_process_stopped/);
  assert.match(script, /ownership_or_port_conflict/);
  assert.doesNotMatch(script, /erp_reference_stale_or_diverged|ErpReferenceSync/);
  assert.match(script, /running_process_not_ready/);
  assert.match(script, /recoverable = \$false/);
  assert.match(script, /SupervisorFailureThreshold = 2/);
  assert.match(script, /SupervisorRestartWindowMinutes = 15/);
  assert.match(script, /SupervisorMaxRestartAttempts = 3/);
  assert.match(script, /Invoke-SupervisorProbe[\s\S]*restart_preflight_changed/);
  assert.match(script, /SupervisorExpectedDesiredStateSha256/);
  assert.doesNotMatch(script, /Stop-ServiceStack|Stop-Postgres|Stop-OwnedProcess/);
  assert.doesNotMatch(script, /worker-local|n8n|tmall|jd-/i);
});

test("supervisor process ownership and login replacement are fail-closed", async () => {
  const script = await readFile(supervisorPath, "utf8");
  assert.match(script, /Threading\.Mutex/);
  assert.match(script, /Write-SupervisorProcessReceipt/);
  assert.match(script, /creationDate/);
  assert.match(script, /Test-CommandLineReferencesPath/);
  assert.match(script, /scriptPathSha256/);
  assert.match(script, /Remove-PreviousBootProcessRecordIfSafe/);
  assert.match(script, /PID 已复用或进程身份不一致/);
  assert.match(script, /ConfirmedStartupReplacement/);
  assert.match(script, /只有整套 Django 服务已健康时才能安装 supervisor 登录启动项/);
  assert.match(script, /现有 Django 登录启动项不是受控 one-shot 或 supervisor；拒绝覆盖/);
  assert.match(script, /Arguments -cne \$expectedArguments/);
  assert.match(script, /RestoreOneShotStartup/);
  assert.match(script, /恢复 one-shot 登录启动项前必须先 Disarm supervisor/);
  assert.match(script, /恢复 one-shot 登录启动项前必须等待 supervisor 进程退出/);
  assert.match(script, /existingSnapshot/);
});

test("alerts are deduplicated local outbox events with the mandated DingTalk identity constraint", async () => {
  const script = await readFile(supervisorPath, "utf8");
  assert.match(script, /pending_local_outbox/);
  assert.match(script, /志高助手_to_测试群聊_only/);
  assert.match(script, /containsBusinessData = \$false/);
  assert.match(script, /containsCredentials = \$false/);
  assert.match(script, /TotalMinutes -lt 30/);
  assert.doesNotMatch(script, /DINGTALK_ROBOT_WEBHOOK|access_token|robotCode|openConversationId/);
});

test("classification and corrupt-state gates pass isolated PowerShell fixtures", async (t) => {
  if (process.platform !== "win32" || !existsSync(powershell)) {
    t.skip("Windows PowerShell 5 is unavailable");
    return;
  }
  const result = spawnSync(powershell, [
    "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
    "-File", fixturePath,
    "-ToolScript", supervisorPath,
  ], { encoding: "utf8", windowsHide: true });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.deepEqual(JSON.parse(result.stdout.trim()), { status: "completed" });
});
