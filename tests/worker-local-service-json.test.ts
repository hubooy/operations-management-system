import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";

import {
  canonicalJson,
  sha256Bytes,
  windowsPathSha256,
  withPayloadSha256,
} from "../tools/worker-local-release.mjs";

const shells = ["powershell.exe", "pwsh.exe"];

test("PowerShell hot restart replaces only the exact Worker child and preserves its supervisor", () => {
  const servicePath = path.resolve("tools/worker-local-service.ps1").replaceAll("'", "''");
  const fixture = `
. '${servicePath}' -FunctionsOnly -AllowTestRuntimeRoot
$supervisor = [pscustomobject]@{ ProcessId = 100; ParentProcessId = 1; CreationDate = '20260903010000.000000+000' }
$worker = [pscustomobject]@{ ProcessId = 200; ParentProcessId = 100; CreationDate = '20260903010100.000000+000' }
$descendant = [pscustomobject]@{ ProcessId = 201; ParentProcessId = 200; CreationDate = '20260903010200.000000+000' }
$identity = [pscustomobject]@{ ReleaseRoot = 'D:\\fixture'; Path = 'D:\\fixture\\deployment-manifest.json'; Sha256 = ('a' * 64); Manifest = [pscustomobject]@{ processIdentity = [pscustomobject]@{ fixedWranglerArguments = @('dev') } } }
$internal = [pscustomobject]@{
  State = 'exact_release'; Identity = $identity; Supervisor = $supervisor
  Tree = @(
    [pscustomobject]@{ Process = $supervisor; Depth = 0 },
    [pscustomobject]@{ Process = $worker; Depth = 1 },
    [pscustomobject]@{ Process = $descendant; Depth = 2 }
  )
  PortIds = @(250); HelperPortIds = @(350)
}
$script:allowWorkerRoot = $false
$script:stopped = @()
function Get-ExactCurrentProcess([int]$Id) { if ($Id -eq 100) { return $supervisor }; return $null }
function Test-AllowedTreeProcess { return $true }
function Test-ExactWorkerRootProcess([object]$Process) { return $script:allowWorkerRoot -and [int]$Process.ProcessId -eq 200 }
function Stop-ExactProcessIdentity([object]$Snapshot) { $script:stopped += [int]$Snapshot.ProcessId }
function Get-WorkerStatusInternal { return [pscustomobject]@{ State = 'exact_release'; Supervisor = $supervisor; PortIds = @(251); Reason = $null } }
function Get-CimInstance { return @($descendant) }
function Start-Sleep {}
$ambiguousRejected = $false
try { [void](Restart-ExactWorkerChild $internal ([DateTime]::UtcNow.AddSeconds(5))) } catch { $ambiguousRejected = $_.Exception.Message -match 'exactly one' }
$script:allowWorkerRoot = $true
$result = Restart-ExactWorkerChild $internal ([DateTime]::UtcNow.AddSeconds(5))
[ordered]@{
  ambiguousRejected = $ambiguousRejected
  stopped = @($script:stopped)
  supervisorPid = [int]$result.Supervisor.ProcessId
  newPortPid = [int]$result.PortIds[0]
} | ConvertTo-Json -Compress
`;

  for (const shell of shells) {
    const result = spawnSync(shell, [
      "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", fixture,
    ], { encoding: "utf8", windowsHide: true, timeout: 15_000 });
    assert.equal(result.error, undefined, `${shell}: ${result.error?.message ?? "spawn failed"}`);
    assert.equal(result.status, 0, `${shell}: ${result.stdout}\n${result.stderr}`);
    const payload = JSON.parse(result.stdout.trim()) as {
      ambiguousRejected: boolean;
      stopped: number[];
      supervisorPid: number;
      newPortPid: number;
    };
    assert.equal(payload.ambiguousRejected, true, shell);
    assert.deepEqual(payload.stopped, [200, 201], shell);
    assert.equal(payload.supervisorPid, 100, shell);
    assert.equal(payload.newPortPid, 251, shell);
  }
});

test("Django Start capture returns when only an inherited descendant log handle remains", async () => {
  const runtime = await mkdtemp(path.join(tmpdir(), "teruisi-django-start-capture-"));
  try {
    const servicePath = path.resolve("tools/worker-local-service.ps1");
    const fixturePath = path.join(runtime, "django-start-fixture.ps1");
    const childPidPath = path.join(runtime, "child.pid");
    await writeFile(fixturePath, `
param([string]$Action, [string]$RuntimeRoot)
if ($Action -cne 'Start') { exit 2 }
$child = Start-Process powershell.exe -NoNewWindow -PassThru -ArgumentList @(
  '-NoProfile', '-NonInteractive', '-Command', 'Start-Sleep -Seconds 8'
)
[System.IO.File]::WriteAllText((Join-Path $RuntimeRoot 'child.pid'), [string]$child.Id)
Write-Output 'fixture started'
exit 0
`, "utf8");

    const quote = (value: string) => value.replaceAll("'", "''");
    const fixture = `
. '${quote(servicePath)}' -FunctionsOnly -AllowTestRuntimeRoot -RuntimeRoot '${quote(runtime)}'
$DjangoService = '${quote(fixturePath)}'
$DjangoRuntimeTools = '${quote(runtime)}'
$FixedDjangoRuntimeRoot = '${quote(runtime)}'
$childPid = $null
$timer = [System.Diagnostics.Stopwatch]::StartNew()
try {
  $result = Invoke-DjangoStartProcess
  $timer.Stop()
  $childPid = [int][System.IO.File]::ReadAllText('${quote(childPidPath)}')
  $child = Get-CimInstance Win32_Process -Filter "ProcessId = $childPid"
  if (-not $child -or [string]$child.CommandLine -notmatch 'Start-Sleep -Seconds 8') {
    throw 'fixture descendant identity changed'
  }
  [ordered]@{
    exitCode = [int]$result.ExitCode
    elapsedMilliseconds = [int]$timer.ElapsedMilliseconds
    descendantStillRunning = $true
  } | ConvertTo-Json -Compress
} finally {
  if ($childPid) { Stop-Process -Id $childPid -Force -ErrorAction SilentlyContinue }
}
`;
    const result = spawnSync("powershell.exe", [
      "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", fixture,
    ], { encoding: "utf8", windowsHide: true, timeout: 15_000 });
    assert.equal(result.error, undefined, result.error?.message);
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
    const output = JSON.parse(result.stdout.trim());
    assert.equal(output.exitCode, 0);
    assert.equal(output.descendantStillRunning, true);
    assert.ok(output.elapsedMilliseconds < 4_000, JSON.stringify(output));
  } finally {
    await rm(runtime, { recursive: true, force: true });
  }
});

test("PowerShell 5 and pwsh preserve ISO JSON strings and enforce canonical bytes", async () => {
  const servicePath = path.resolve("tools/worker-local-service.ps1");
  const escapedServicePath = servicePath.replaceAll("'", "''");
  const serviceSource = await readFile(servicePath, "utf8");
  assert.match(serviceSource, /Parameters\.ContainsKey\("DateKind"\)/);
  assert.match(serviceSource, /ConvertFrom-Json -InputObject \$InputJson -DateKind String/);
  assert.equal((serviceSource.match(/ConvertFrom-ExactJson/g) ?? []).length, 6);

  const fixture = `
. '${escapedServicePath}' -FunctionsOnly -AllowTestRuntimeRoot
$createdAt = '2026-08-30T13:00:00.000Z'
$core = [ordered]@{ createdAt = $createdAt; status = 'ok' }
$payloadSha256 = Get-Sha256Bytes ([System.Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson $core)))
$record = [ordered]@{ createdAt = $createdAt; payloadSha256 = $payloadSha256; status = 'ok' }
$canonical = ConvertTo-CanonicalJson $record
$raw = [System.Text.UTF8Encoding]::new($false).GetBytes($canonical + "\`n")
$parsed = ConvertFrom-ExactJson $canonical 'ISO fixture'
if (-not ($parsed.createdAt -is [string])) { throw 'ISO timestamp was not preserved as a string' }
Assert-CanonicalJsonPayload $raw $parsed 'payloadSha256' 'ISO fixture'
$nonCanonicalRejected = $false
try {
  $badRaw = [System.Text.UTF8Encoding]::new($false).GetBytes(' ' + $canonical + "\`n")
  Assert-CanonicalJsonPayload $badRaw $parsed 'payloadSha256' 'noncanonical fixture'
} catch { $nonCanonicalRejected = $true }
if (-not $nonCanonicalRejected) { throw 'noncanonical JSON was accepted' }
$invalidRejected = $false
try { [void](ConvertFrom-ExactJson '{"createdAt":' 'invalid fixture') }
catch {
  if ($_.Exception.Message -cne 'invalid fixture is not valid JSON') { throw 'invalid JSON error was not bounded' }
  $invalidRejected = $true
}
if (-not $invalidRejected) { throw 'invalid JSON was accepted' }
[ordered]@{
  canonical = $canonical
  dateType = $parsed.createdAt.GetType().FullName
  invalidRejected = $invalidRejected
  nonCanonicalRejected = $nonCanonicalRejected
} | ConvertTo-Json -Compress
`;

  for (const shell of shells) {
    const result = spawnSync(shell, [
      "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", fixture,
    ], { encoding: "utf8", windowsHide: true });
    assert.equal(result.error, undefined, `${shell}: ${result.error?.message ?? "spawn failed"}`);
    assert.equal(result.status, 0, `${shell}: ${result.stdout}\n${result.stderr}`);
    const output = JSON.parse(result.stdout.trim());
    assert.equal(output.dateType, "System.String", shell);
    assert.equal(output.invalidRejected, true, shell);
    assert.equal(output.nonCanonicalRejected, true, shell);
    assert.match(output.canonical, /"createdAt":"2026-08-30T13:00:00\.000Z"/);
  }
});

test("PowerShell 5 and pwsh Status parse an ISO-dated canonical manifest without recursion", async () => {
  const runtime = await mkdtemp(path.join(tmpdir(), "teruisi-worker-json-status-"));
  try {
    const releaseId = "20260830T130000Z-fedcba9876543210";
    const releaseRoot = path.join(runtime, "releases", releaseId);
    await mkdir(releaseRoot, { recursive: true });
    const protectedSourceRoot = "D:\\运营管理系统";
    const manifest = withPayloadSha256({
      version: "teruisi-local-worker-release-v1",
      releaseId,
      createdAt: "2026-08-30T13:00:00.000Z",
      source: {},
      build: {},
      runtime: {
        runtimeRootPathSha256: windowsPathSha256(runtime),
        releaseRootPathSha256: windowsPathSha256(releaseRoot),
        protectedSourceRoot,
        protectedSourceRootPathSha256: windowsPathSha256(protectedSourceRoot),
        helperMode: "supervisor_managed_immutable_bundle",
        helperHost: "127.0.0.1",
        helperPort: 5791,
        helperMutableRoot: protectedSourceRoot,
        helperMutableRootPathSha256: windowsPathSha256(protectedSourceRoot),
      },
      artifacts: {},
      processIdentity: {
        supervisorEntrypoint: "tools/worker-local-runtime-supervisor.mjs",
        serviceControl: "tools/worker-local-service.ps1",
        manifestFile: "deployment-manifest.json",
        processReceipt: "state/worker-process.json",
        processReceiptVersion: "teruisi-local-worker-process-v1",
        wranglerEntrypoint: "node_modules/wrangler/bin/wrangler.js",
        wranglerCliEntrypoint: "node_modules/wrangler/wrangler-dist/cli.js",
        fixedWranglerArguments: ["dev"],
        helperEntrypoint: "helper/tmall-workflow-helper.mjs",
        fixedHelperArguments: ["serve", "--port", "5791"],
      },
    }, "manifestPayloadSha256");
    const manifestPath = path.join(releaseRoot, "deployment-manifest.json");
    const manifestRaw = Buffer.from(`${canonicalJson(manifest)}\n`, "utf8");
    await writeFile(manifestPath, manifestRaw);
    const pointer = withPayloadSha256({
      version: "teruisi-local-worker-current-v1",
      releaseId,
      manifestRelativePath: `releases/${releaseId}/deployment-manifest.json`,
      manifestSha256: sha256Bytes(manifestRaw),
    }, "pointerPayloadSha256");
    await writeFile(path.join(runtime, "current-deployment.json"), `${canonicalJson(pointer)}\n`, "utf8");

    for (const shell of shells) {
      const result = spawnSync(shell, [
        "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", path.resolve("tools/worker-local-service.ps1"),
        "-Action", "Status", "-RuntimeRoot", runtime, "-AllowTestRuntimeRoot", "-Json",
      ], { encoding: "utf8", windowsHide: true });
      assert.equal(result.error, undefined, `${shell}: ${result.error?.message ?? "spawn failed"}`);
      assert.equal(result.status, 0, `${shell}: ${result.stdout}\n${result.stderr}`);
      const status = JSON.parse(result.stdout.trim());
      assert.equal(status.releaseId, releaseId, shell);
      assert.equal(status.manifestSha256, sha256Bytes(manifestRaw), shell);
    }
  } finally {
    await rm(runtime, { recursive: true, force: true });
  }
});
