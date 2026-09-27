import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panelBytes = readFileSync("tools/operations-system-control.ps1");
const panel = panelBytes.toString("utf8");
const workerService = readFileSync("tools/worker-local-service.ps1", "utf8");
const djangoDomainServicePaths = [
  "tools/django-bi-service.ps1",
  "tools/django-netshop-service.ps1",
  "tools/django-market-service.ps1",
  "tools/django-products-service.ps1",
  "tools/django-inventory-service.ps1",
  "tools/django-access-control.ps1",
  "tools/django-erp-reference.ps1",
  "tools/django-workflow-service.ps1",
  "tools/django-workflow-cutover.ps1",
  "tools/django-workflow-operations-cutover.ps1",
  "tools/workflow-operations-production-smoke.ps1",
];

test("unified controller stays parseable by Windows PowerShell when Chinese text is present", () => {
  assert.deepEqual([...panelBytes.subarray(0, 3)], [0xef, 0xbb, 0xbf]);
  assert.match(panel, /teruisi-operations-system-control-v2/);
  assert.match(panel, /ValidateSet\("Panel", "Start", "Status", "StopWorker"\)/);
});

test("all system page actions explicitly use Google Chrome instead of the Windows default browser", () => {
  assert.match(panel, /function Get-GoogleChromeExecutable/);
  assert.match(panel, /Google\\Chrome\\Application\\chrome\.exe/);
  assert.match(panel, /function Open-SystemInGoogleChrome/);
  assert.match(panel, /Start-Process -FilePath \(Get-GoogleChromeExecutable\) -ArgumentList @\(\$Url\)/);
  const pageOpenActions = panel.slice(
    panel.indexOf("function Open-SystemInGoogleChrome"),
    panel.indexOf("$runContinueButton.Add_Click({"),
  );
  assert.equal((pageOpenActions.match(/Open-SystemInGoogleChrome/g) ?? []).length, 3);
  assert.doesNotMatch(pageOpenActions, /Start-Process \$ServerUrl/);
});

test("Django domain controllers and operators retain a UTF-8 BOM for Windows PowerShell 5.1", () => {
  for (const servicePath of djangoDomainServicePaths) {
    const serviceBytes = readFileSync(servicePath);
    assert.deepEqual(
      [...serviceBytes.subarray(0, 3)],
      [0xef, 0xbb, 0xbf],
      `${servicePath} must remain UTF-8 with BOM`,
    );
  }
});

test("all public start modes share one nonblocking system mutex", () => {
  assert.match(panel, /Local\\TERUISI\.Operations\.SystemControl\.v2/);
  assert.match(panel, /WaitOne\(\[TimeSpan\]::Zero\)/);
  assert.match(panel, /status = "start_in_progress"/);
  assert.match(panel, /本次请求未重复启动任何组件/);
  assert.match(panel, /唯一总控正在启动系统；为避免交错/);
  const startBlock = panel.slice(
    panel.indexOf("function Invoke-SystemStart"),
    panel.indexOf("function Invoke-StopWorker"),
  );
  assert.ok(startBlock.indexOf("Enter-SystemControlMutex") < startBlock.indexOf("Assert-ControllerDependencies"));
});

test("controller checks every authoritative Django PostgreSQL domain", () => {
  assert.match(panel, /django-local-service\.ps1/);
  assert.match(panel, /django-netshop-service\.ps1/);
  assert.match(panel, /django-market-service\.ps1/);
  assert.match(panel, /django-products-service\.ps1/);
  assert.match(panel, /django-workflow-service\.ps1/);
  assert.match(panel, /django-inventory-service\.ps1/);
  assert.match(panel, /django-access-control\.ps1/);
  assert.match(panel, /django-erp-reference\.ps1/);
  for (const component of ["core", "finance", "netshop", "market", "products", "workflow", "inventory", "customerService", "accessControl", "erpReference", "bi"]) {
    assert.match(panel, new RegExp(`${component} = Test-`));
  }
  assert.doesNotMatch(panel, /ErpReferenceSync -ceq "caught_up"/);
  assert.match(panel, /RuntimeAclVerification -ceq "root_only_status"/);
  assert.match(panel, /AuthorityProperty "PostgreSQLAuthority"/);
  assert.match(panel, /AuthorityProperty\]\.Value -cne "postgres"/);
  const aggregateBlock = panel.slice(
    panel.indexOf("function Get-DjangoAggregateState"),
    panel.indexOf("function Get-WorkerReleaseStatus"),
  );
  assert.match(aggregateBlock, /-Arguments @\("-Action", "AggregateStatus"\)/);
  assert.match(aggregateBlock, /teruisi-django-aggregate-status-v1/);
  const optimizedBranch = aggregateBlock.slice(
    aggregateBlock.indexOf("if (Test-DjangoAggregateStatusSupported)"),
    aggregateBlock.indexOf("} else {"),
  );
  assert.equal((optimizedBranch.match(/Invoke-JsonServiceAction/g) ?? []).length, 1);
  assert.doesNotMatch(optimizedBranch, /\$Django(?:Finance|Netshop|Market|Products|Workflow|Inventory)Service/);
  assert.match(aggregateBlock, /Safe rolling-upgrade compatibility|source controller usable/);
});

test("controller delegates mutation to one start engine and performs final health gates", () => {
  const startBlock = panel.slice(
    panel.indexOf("function Invoke-SystemStart"),
    panel.indexOf("function Invoke-StopWorker"),
  );
  assert.match(startBlock, /调用唯一启动引擎/);
  assert.match(startBlock, /Invoke-VisibleServiceAction -ScriptPath \$LocalWorkerStarter -Arguments @\("-Action", "Start"\)/);
  assert.doesNotMatch(startBlock, /-ScriptPath \$DjangoService -Arguments @\("-Action", "Start"\)/);
  assert.match(startBlock, /finalState\.state -notin @\("Running", "BackendDegraded"\)/);
  assert.match(startBlock, /pageProbe\.StatusCode -ne 200/);
  const coldPreflight = startBlock.slice(0, startBlock.indexOf("$changed = $false"));
  assert.match(coldPreflight, /Get-WorkerReleaseStatus -Refresh/);
  assert.doesNotMatch(coldPreflight, /Get-SystemState -Refresh/);
  assert.match(startBlock, /if \(\$initialWorker\.State -ceq "exact_release"\) \{[\s\S]*?Get-SystemState -Refresh/);
});

test("controller waits only for the direct start-engine process instead of a durable child pipeline", () => {
  const invocationBlock = panel.slice(
    panel.indexOf("function Invoke-VisibleServiceAction"),
    panel.indexOf("function Test-CoreDjangoReady"),
  );
  assert.match(invocationBlock, /Start-Process -FilePath \$PowerShellExecutable/);
  assert.match(invocationBlock, /-RedirectStandardOutput \$serviceStdoutPath/);
  assert.match(invocationBlock, /-RedirectStandardError \$serviceStderrPath/);
  assert.match(invocationBlock, /\$serviceProcess\.WaitForExit\(\)/);
  assert.match(invocationBlock, /\[System\.IO\.File\]::ReadAllText/);
  assert.match(invocationBlock, /\[System\.IO\.File\]::Delete\(\$temporaryLog\)/);
  assert.match(invocationBlock, /catch \[System\.IO\.IOException\]/);
  assert.match(invocationBlock, /direct service exit code remains authoritative/);
  assert.match(invocationBlock, /failed service also left an unreadable diagnostic handle/);
  assert.match(invocationBlock, /false failure during best-effort cleanup/);
  assert.doesNotMatch(invocationBlock, /=\s*&\s*\$PowerShellExecutable/);
  assert.ok(invocationBlock.indexOf("WaitForExit()") < invocationBlock.indexOf("ReadAllText"));
});

test("canonical start engine enforces Django readiness before Worker verification", () => {
  const startBlock = workerService.slice(
    workerService.indexOf("function Invoke-WorkerSystemStart"),
    workerService.indexOf("function Stop-WorkerOnly"),
  );
  assert.ok(startBlock.indexOf("Ensure-DjangoSystemReady") < startBlock.indexOf("Invoke-ReleaseVerification"));
  assert.match(workerService, /DjangoNetshopService/);
  assert.match(workerService, /DjangoMarketService/);
  assert.match(workerService, /DjangoProductsService/);
  assert.match(workerService, /DjangoWorkflowService/);
  assert.match(workerService, /DjangoInventoryService/);
  assert.match(workerService, /DjangoAccessControlService/);
  assert.match(workerService, /DjangoErpReferenceService/);
  assert.match(workerService, /accessControl = Test-DjangoDomainReady/);
  assert.match(workerService, /erpReference = Test-DjangoDomainReady/);
  assert.doesNotMatch(workerService, /ErpReferenceSync -ceq "caught_up"/);
  assert.match(workerService, /RuntimeAclVerification -ceq "root_only_status"/);
  assert.match(workerService, /function Test-IsIsolatedTestRuntime/);
  assert.match(workerService, /actualRuntime\.Equals\(\$productionRuntime/);
  assert.match(workerService, /if \(Test-IsIsolatedTestRuntime\) \{ return \}/);
  const aggregateBlock = workerService.slice(
    workerService.indexOf("function Get-DjangoSystemReadiness"),
    workerService.indexOf("function Ensure-DjangoSystemReady"),
  );
  assert.match(aggregateBlock, /\$DjangoService "AggregateStatus"/);
  const optimizedReadinessBranch = aggregateBlock.slice(
    aggregateBlock.indexOf("if (Test-DjangoAggregateStatusSupported)"),
    aggregateBlock.indexOf("} else {"),
  );
  assert.equal((optimizedReadinessBranch.match(/Invoke-DjangoStatusJson/g) ?? []).length, 1);
  assert.match(aggregateBlock, /Safe rolling-upgrade compatibility/);
});

test("canonical engine waits only for the direct Django Start controller process", () => {
  const invocationBlock = workerService.slice(
    workerService.indexOf("function Invoke-DjangoStartProcess"),
    workerService.indexOf("function Test-DjangoDomainReady"),
  );
  const readinessBlock = workerService.slice(
    workerService.indexOf("function Ensure-DjangoSystemReady"),
    workerService.indexOf("function Assert-NoReparsePath"),
  );
  assert.match(invocationBlock, /Start-Process -FilePath \(Get-DjangoControlPowerShell\)/);
  assert.match(invocationBlock, /-RedirectStandardOutput \$stdoutPath/);
  assert.match(invocationBlock, /-RedirectStandardError \$stderrPath/);
  assert.match(invocationBlock, /\$process\.WaitForExit\(\)/);
  assert.match(invocationBlock, /direct process exit code authoritative/);
  assert.match(readinessBlock, /\$djangoStart = Invoke-DjangoStartProcess/);
  assert.doesNotMatch(readinessBlock, /@\(& \(Get-DjangoControlPowerShell\)[^\n]+-Action Start/);
  assert.equal((readinessBlock.match(/Get-DjangoSystemReadiness/g) ?? []).length, 1);
  assert.match(readinessBlock, /does not exit successfully until every/);
});

test("concurrent-start wait reuses a ten-second exact status result", () => {
  const statusBlock = panel.slice(
    panel.indexOf("function Get-WorkerReleaseStatus"),
    panel.indexOf("function Invoke-SystemHealthProbe"),
  );
  const waitBlock = panel.slice(
    panel.indexOf("function Wait-ForExactWorkerRelease"),
    panel.indexOf("function Invoke-SystemStart"),
  );
  assert.match(statusBlock, /TotalSeconds -lt 10/);
  assert.match(waitBlock, /Start-Sleep -Seconds 1/);
  assert.match(waitBlock, /Get-WorkerReleaseStatus\s*\r?\n/);
  assert.doesNotMatch(waitBlock, /Get-WorkerReleaseStatus -Refresh/);
});

test("canonical engine clears only an exact validated stale receipt", () => {
  const startBlock = workerService.slice(
    workerService.indexOf("function Invoke-WorkerSystemStart"),
    workerService.indexOf("function Stop-WorkerOnly"),
  );
  assert.match(startBlock, /status\.State -eq "stale_or_invalid_receipt" -and -not \$status\.Supervisor -and \$status\.Receipt/);
  assert.match(startBlock, /Remove-ExactProcessReceipt \$identity/);
  assert.match(startBlock, /status\.State -ne "stopped"/);
  assert.ok(startBlock.indexOf("Ensure-DjangoSystemReady") < startBlock.lastIndexOf("$status = Get-WorkerStatusInternal $identity"));
  assert.match(startBlock, /unknown\/ambiguous; refusing takeover/);
});

test("controller verifies Worker liveness, readiness and helper health without proxying", () => {
  assert.match(panel, /_teruisi\/local\/health\/live/);
  assert.match(panel, /_teruisi\/local\/health\/ready/);
  assert.match(panel, /127\.0\.0\.1:5791\/health/);
  assert.match(panel, /httpHandler\.UseProxy = \$false/);
  assert.match(panel, /TryAddWithoutValidation\("x-teruisi-local-health", "1"\)/);
  assert.match(panel, /readinessPayload\.code -eq "django_unavailable"/);
  assert.equal(panel.match(/healthState = "BackendDegraded"/g)?.length, 1);
});

test("desktop panel launches the same controller instead of a lower-level service", () => {
  const clickBlock = panel.slice(
    panel.indexOf("$startButton.Add_Click({"),
    panel.indexOf("$stopButton.Add_Click({"),
  );
  assert.match(clickBlock, /-File", "`"\$ControllerPath`"", "-Action", "Start"/);
  assert.doesNotMatch(clickBlock, /-File", "`"\$LocalWorkerStarter/);
  assert.match(panel, /-RedirectStandardOutput \$script:launchStdoutLog/);
  assert.match(panel, /-RedirectStandardError \$script:launchStderrLog/);
  assert.match(panel, /Get-LaunchLogSummary/);
  assert.match(panel, /完整性校验可能需要数分钟/);
});

test("pause remains narrowly scoped to the exact immutable Worker", () => {
  const stopBlock = panel.slice(
    panel.indexOf("function Invoke-StopWorker"),
    panel.indexOf('if ($Action -ne "Panel")'),
  );
  assert.match(stopBlock, /-ScriptPath \$LocalWorkerStarter -Arguments @\("-Action", "Stop"\)/);
  assert.match(stopBlock, /Django\/PostgreSQL 后端继续运行/);
  assert.doesNotMatch(stopBlock, /DjangoService[\s\S]*"Stop"/);
  assert.doesNotMatch(stopBlock, /Stop-Process|taskkill/);
  assert.match(panel, /暂停网页服务/);
});

test("controller never invokes legacy build or development launchers", () => {
  assert.doesNotMatch(panel, /start-local-worker\.mjs|vinext\s+(?:dev|start)|wrangler\s+dev|--build/);
  assert.match(panel, /tools\\worker-local-service\.ps1/);
  assert.match(panel, /pwsh\.exe/);
});
