[CmdletBinding()]
param(
  [string]$PostgresBin = "D:\teruisi-runtime\django-sales\postgresql-17.11\bin",
  [string]$Python = "D:\teruisi-runtime\django-sales\venv\Scripts\python.exe",
  [int]$Port = 55439
)
$ErrorActionPreference = "Stop"
if ($Port -lt 49152 -or $Port -gt 65535) { throw "Use a separate high test port" }
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) { throw "Test port occupied" }
$project = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$testRoot = Join-Path $project (".runtime\bi-permissions-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$cluster = Join-Path $testRoot "cluster"
$pgctl = Join-Path $PostgresBin "pg_ctl.exe"
$started = $false
$saved = @{}
foreach ($key in @("TERUISI_DJANGO_DATABASE_URL", "TERUISI_DJANGO_ENVIRONMENT", "DJANGO_DEBUG", "TERUISI_BI_PERMISSION_TEST")) {
  $saved[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
}
try {
  & (Join-Path $PostgresBin "initdb.exe") -D $cluster -U postgres --auth=trust --encoding=UTF8 --locale=C | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Isolated initdb failed" }
  $startArgs = @("start", "-D", ('"' + $cluster + '"'), "-w", "-l", ('"' + (Join-Path $testRoot "postgres.log") + '"'),
    "-o", ('"-p ' + $Port + ' -h 127.0.0.1 -c max_connections=40"'))
  $start = Start-Process $pgctl -ArgumentList $startArgs -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $testRoot "start.stdout.log") -RedirectStandardError (Join-Path $testRoot "start.stderr.log")
  $start.WaitForExit()
  if ($start.ExitCode -ne 0) { throw "Isolated PostgreSQL start failed" }
  $started = $true
  $env:TERUISI_DJANGO_DATABASE_URL = "postgresql://postgres@127.0.0.1:$Port/postgres?connect_timeout=5"
  $env:TERUISI_DJANGO_ENVIRONMENT = "test"
  $env:DJANGO_DEBUG = "true"
  $env:TERUISI_BI_PERMISSION_TEST = "1"
  & $Python (Join-Path $project "backend\manage.py") test bi.tests inventory.tests.test_replenishment_health --noinput --verbosity 1
  if ($LASTEXITCODE -ne 0) { throw "BI PostgreSQL regression failed" }
} finally {
  foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key], "Process") }
  if ($started) {
    & $pgctl stop -D $cluster -m fast -w
    if ($LASTEXITCODE -ne 0) { Write-Error "Isolated cluster stop failed: $cluster" }
  }
  Write-Output "Isolated test artifacts: $testRoot"
}
