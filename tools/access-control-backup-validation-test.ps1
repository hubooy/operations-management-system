param([Parameter(Mandatory=$true)][string]$ManifestPath)
$ErrorActionPreference = "Stop"
$env:TERUISI_DJANGO_MAINTENANCE_LIBRARY_ONLY = "1"
. (Join-Path $PSScriptRoot "django-postgres-maintenance.ps1")
$fixture = (Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json).evidence
Assert-MaintenanceEvidence $fixture $fixture.database.name $fixture.database.user $fixture.database.serverPort
if ($null -ne $fixture.PSObject.Properties['accessControl']) { throw "Use a pre-access-control manifest fixture" }
$fixture.migrations = @($fixture.migrations) + @([pscustomobject]@{app='access_control';name='0001_initial'})
$fixture | Add-Member -NotePropertyName accessControl -NotePropertyValue ([pscustomobject]@{
  revision=[int64]1; sourceDigest=('a'*64); status='postgres'; authorityEpoch='00000000-0000-4000-8000-000000000001';
  cutoverId='access-control-backup-fixture'; migrationRunId=('access-control-' + ('b'*32))
})
foreach($name in @('users','roles','permission_audits','data_revisions','write_authority','write_request_receipts','migration_runs')) {
  $fixture.tables | Add-Member -NotePropertyName ('access_control_' + $name) -NotePropertyValue ([int64]1)
}
function Assert-Accepted { Assert-MaintenanceEvidence $fixture $fixture.database.name $fixture.database.user $fixture.database.serverPort }
function Assert-Rejected {
  $rejected=$false
  try { Assert-Accepted } catch { $rejected=$true }
  if (-not $rejected) { throw "Malformed access-control evidence was accepted" }
}
Assert-Accepted
$fixture.accessControl.revision=[int64]0; Assert-Rejected; $fixture.accessControl.revision=[int64]1
$fixture.accessControl.migrationRunId=''; Assert-Rejected; $fixture.accessControl.migrationRunId=('access-control-' + ('b'*32))
$fixture.accessControl.status='d1'; Assert-Rejected; $fixture.accessControl.status='postgres'
$fixture.tables.PSObject.Properties.Remove('access_control_users'); Assert-Rejected
$fixture.tables | Add-Member -NotePropertyName access_control_users -NotePropertyValue ([int64]1)
$fixture.accessControl | Add-Member -NotePropertyName unexpected -NotePropertyValue 1; Assert-Rejected
$fixture.accessControl.PSObject.Properties.Remove('unexpected')
$fixture.PSObject.Properties.Remove('accessControl'); Assert-Rejected
Write-Output '{"status":"passed","checks":8,"productionWrites":0}'
