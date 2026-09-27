[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("setup", "read", "status")]
  [string]$Action,

  [Alias("store-key")]
  [ValidatePattern("^[a-z0-9][a-z0-9-]*$")]
  [string]$StoreKey = "tmall-yijiu"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.Security
$utf8NoBom = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

$vaultRoot = Join-Path (Split-Path -Parent $PSScriptRoot) ".runtime\tmall-credentials"
$vaultFile = Join-Path $vaultRoot "$StoreKey.json"
$entropy = [Text.Encoding]::UTF8.GetBytes("TERUISI-TMALL:$StoreKey:v1")
$registryFile = Join-Path (Split-Path -Parent $PSScriptRoot) "config\tmall-store-accounts.json"

if (-not (Test-Path -LiteralPath $registryFile)) { throw "The controlled Tmall store registry is missing" }
$registry = Get-Content -LiteralPath $registryFile -Raw -Encoding utf8 | ConvertFrom-Json
$registeredStores = @($registry.stores | Where-Object { $_.storeKey -ceq $StoreKey })
if ($registeredStores.Count -ne 1) { throw "The Tmall store key is not uniquely registered" }
if ($registeredStores[0].loginMode -ne "windows_dpapi_credentials") {
  throw "The registered Tmall store does not allow Windows DPAPI credentials"
}

function Protect-PlainText([string]$PlainText) {
  $plainBytes = [Text.Encoding]::UTF8.GetBytes($PlainText)
  try {
    $cipherBytes = [Security.Cryptography.ProtectedData]::Protect(
      $plainBytes,
      $script:entropy,
      [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    return [Convert]::ToBase64String($cipherBytes)
  }
  finally {
    if ($null -ne $plainBytes) { [Array]::Clear($plainBytes, 0, $plainBytes.Length) }
  }
}

function Unprotect-PlainText([string]$CipherText) {
  $cipherBytes = [Convert]::FromBase64String($CipherText)
  $plainBytes = [Security.Cryptography.ProtectedData]::Unprotect(
    $cipherBytes,
    $script:entropy,
    [Security.Cryptography.DataProtectionScope]::CurrentUser
  )
  try { return [Text.Encoding]::UTF8.GetString($plainBytes) }
  finally { [Array]::Clear($plainBytes, 0, $plainBytes.Length) }
}

if ($Action -eq "setup") {
  $username = Read-Host "Enter the Tmall account for this store"
  if ([string]::IsNullOrWhiteSpace($username)) { throw "Account must not be empty" }
  $password = Read-Host "Enter the Tmall password (input is hidden)" -AsSecureString
  if ($password.Length -le 0) { throw "Password must not be empty" }

  $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($password)
  $passwordPlain = $null
  try {
    $passwordPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
    $payload = [ordered]@{
      version = 1
      storeKey = $StoreKey
      username = Protect-PlainText $username
      password = Protect-PlainText $passwordPlain
      updatedAt = [DateTimeOffset]::Now.ToString("o")
    }
  }
  finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
    $passwordPlain = $null
  }
  New-Item -ItemType Directory -Path $vaultRoot -Force | Out-Null
  $temporaryFile = "$vaultFile.tmp-$PID"
  try {
    $payload | ConvertTo-Json -Compress | Set-Content -LiteralPath $temporaryFile -Encoding utf8
    Move-Item -LiteralPath $temporaryFile -Destination $vaultFile -Force
  }
  finally {
    if (Test-Path -LiteralPath $temporaryFile) { Remove-Item -LiteralPath $temporaryFile -Force }
  }
  [pscustomobject]@{ ok = $true; status = "stored"; storeKey = $StoreKey } | ConvertTo-Json -Compress
  exit 0
}

if ($Action -eq "status") {
  [pscustomobject]@{ ok = $true; storeKey = $StoreKey; ready = (Test-Path -LiteralPath $vaultFile) } | ConvertTo-Json -Compress
  exit 0
}

if (-not (Test-Path -LiteralPath $vaultFile)) { throw "The encrypted credential is not configured for this store" }
$payload = Get-Content -LiteralPath $vaultFile -Raw -Encoding utf8 | ConvertFrom-Json
if ($payload.version -ne 1 -or $payload.storeKey -ne $StoreKey) { throw "The encrypted credential identity or format is invalid" }
$usernamePlain = Unprotect-PlainText ([string]$payload.username)
$passwordPlain = Unprotect-PlainText ([string]$payload.password)
try {
  if ([string]::IsNullOrWhiteSpace($usernamePlain) -or [string]::IsNullOrEmpty($passwordPlain)) {
    throw "The encrypted credential content is invalid"
  }
  [pscustomobject]@{ username = $usernamePlain; password = $passwordPlain } | ConvertTo-Json -Compress
}
finally {
  $usernamePlain = $null
  $passwordPlain = $null
}
