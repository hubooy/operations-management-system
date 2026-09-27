// This fixed program is bundled into the immutable helper. It accepts only
// non-secret binding metadata on stdin; setup collects secrets in a local form.
// Standard pipes remain available without a console. Console.*Encoding setters
// call console APIs and can fail with an invalid handle in a background process.
export const jackyunPowerShellUtf8Pipes = String.raw`
$utf8 = New-Object Text.UTF8Encoding($false,$true)
$pipeReader = New-Object IO.StreamReader([Console]::OpenStandardInput(),$utf8,$false)
$pipeWriter = New-Object IO.StreamWriter([Console]::OpenStandardOutput(),$utf8)
$pipeWriter.AutoFlush = $true
`;

export const jackyunDpapiProgram = String.raw`
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$stage = 'initialize'
$pipeWriter = $null
$lookupDiagnostic = $null
trap {
  $failure = @{ok=$false;status='failed';stage=$script:stage;exceptionType=$_.Exception.GetType().FullName;errorId=$_.FullyQualifiedErrorId}
  if ($script:stage -eq 'missing' -and $null -ne $script:lookupDiagnostic) { $failure.lookup = $script:lookupDiagnostic }
  $diagnostic = $failure | ConvertTo-Json -Compress
  if ($null -ne $script:pipeWriter) { $script:pipeWriter.WriteLine($diagnostic) } else { $diagnostic }
  exit 1
}
${jackyunPowerShellUtf8Pipes}
Add-Type -AssemblyName System.Security
$stage = 'binding_input'
$request = $pipeReader.ReadToEnd() | ConvertFrom-Json
$stage = 'binding_fields'
if ($request.action -notin @('setup','status','read')) { throw 'Invalid action' }
if ($request.tenantId -notmatch '^[0-9]{4,12}$') { throw 'Invalid tenant binding' }
foreach ($value in @($request.vaultRoot, $request.profileDirectory)) {
  if (-not [IO.Path]::IsPathRooted($value) -or $value.StartsWith('\\')) { throw 'Invalid local binding' }
}
$stage = 'binding_paths'
$vaultRoot = [IO.Path]::GetFullPath($request.vaultRoot)
$profile = [IO.Path]::GetFullPath($request.profileDirectory).TrimEnd('\').ToLowerInvariant()
$binding = 'TERUISI-JACKYUN:v1:' + $request.tenantId + ':' + $profile
$entropy = $utf8.GetBytes($binding)
$hasher = [Security.Cryptography.SHA256]::Create()
try { $key = ([BitConverter]::ToString($hasher.ComputeHash($entropy))).Replace('-','').ToLowerInvariant() }
finally { $hasher.Dispose() }
$vaultFile = Join-Path $vaultRoot ($key + '.json')
$stage = 'binding_identity'
$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$allowedSids = @($userSid.Value, 'S-1-5-18', 'S-1-5-32-544')
function Assert-LocalPath([string]$Target) {
  $current = [IO.Path]::GetFullPath($Target)
  while ($current) {
    if (Test-Path -LiteralPath $current) {
      if ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { $script:stage = 'path_integrity'; throw 'Reparse path denied' }
    }
    $parent = [IO.Directory]::GetParent($current)
    if ($null -eq $parent) { break }
    $current = $parent.FullName
  }
}
function Set-PrivateDirectory([string]$Target) {
  $acl = New-Object Security.AccessControl.DirectorySecurity
  $acl.SetOwner($script:userSid)
  $acl.SetAccessRuleProtection($true, $false)
  foreach ($sid in $script:allowedSids) {
    $identity = New-Object Security.Principal.SecurityIdentifier($sid)
    $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
    $acl.AddAccessRule($rule)
  }
  Set-Acl -LiteralPath $Target -AclObject $acl
}
function Assert-Private([string]$Target) {
  Assert-LocalPath $Target
  $acl = Get-Acl -LiteralPath $Target
  if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $script:userSid.Value) { throw 'Credential owner mismatch' }
  foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
    if ($rule.AccessControlType -ne 'Allow' -or $rule.IdentityReference.Value -notin $script:allowedSids) { throw 'Credential ACL mismatch' }
  }
}
function Read-Credential {
  Assert-Private $script:vaultRoot
  if (-not (Get-Acl -LiteralPath $script:vaultRoot).AreAccessRulesProtected) { throw 'Credential directory inheritance is not protected' }
  Assert-Private $script:vaultFile
  $file = Get-Item -LiteralPath $script:vaultFile
  if ($file.PSIsContainer -or $file.Length -gt 32768) { throw 'Invalid credential file' }
  $payload = [IO.File]::ReadAllText($script:vaultFile,$script:utf8) | ConvertFrom-Json
  if ($payload.version -ne 1 -or $payload.binding -ne $script:key) { throw 'Credential binding mismatch' }
  $bytes = [Security.Cryptography.ProtectedData]::Unprotect([Convert]::FromBase64String($payload.ciphertext),$script:entropy,[Security.Cryptography.DataProtectionScope]::CurrentUser)
  try {
    $plain = $script:utf8.GetString($bytes) | ConvertFrom-Json
    if ($plain.tenantId -ne $script:request.tenantId -or $plain.profile -ne $script:profile -or
        [string]::IsNullOrWhiteSpace($plain.username) -or [string]::IsNullOrEmpty($plain.password)) { throw 'Credential identity mismatch' }
    return $plain
  } finally { [Array]::Clear($bytes,0,$bytes.Length) }
}
$stage = 'binding_local_path'
Assert-LocalPath $vaultRoot
Assert-LocalPath $vaultFile
if ($request.action -eq 'setup') {
  $stage = 'setup_acl'
  $mutex = New-Object Threading.Mutex($false,('Local\TERUISI-JACKYUN-VAULT-' + $key))
  $locked = $false
  try {
    $locked = $mutex.WaitOne(0)
    if (-not $locked) { throw 'Credential setup already open' }
    if (-not (Test-Path -LiteralPath $vaultRoot)) {
      [IO.Directory]::CreateDirectory($vaultRoot) | Out-Null
      Set-PrivateDirectory $vaultRoot
    }
    Assert-Private $vaultRoot
    if (Test-Path -LiteralPath $vaultFile) { Assert-Private $vaultFile }
    $stage = 'setup_form'
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $form = New-Object Windows.Forms.Form
    $form.Text = '吉客云登录凭据 · Windows DPAPI'
    $form.ClientSize = New-Object Drawing.Size(480,260)
    $form.StartPosition = 'CenterScreen'
    $form.FormBorderStyle = 'FixedDialog'
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false
    $form.TopMost = $true
    $form.Font = New-Object Drawing.Font('Microsoft YaHei UI',10)
    $note = New-Object Windows.Forms.Label
    $note.Text = '吉客号：' + $request.tenantId + '。凭据仅由当前 Windows 用户解密。'
    $note.SetBounds(20,20,440,45)
    $form.Controls.Add($note)
    $accountLabel = New-Object Windows.Forms.Label
    $accountLabel.Text = '手机号或工号'
    $accountLabel.SetBounds(20,76,115,26)
    $form.Controls.Add($accountLabel)
    $account = New-Object Windows.Forms.TextBox
    $account.SetBounds(140,72,310,28)
    $account.MaxLength = 256
    $form.Controls.Add($account)
    $passwordLabel = New-Object Windows.Forms.Label
    $passwordLabel.Text = '密码'
    $passwordLabel.SetBounds(20,118,115,26)
    $form.Controls.Add($passwordLabel)
    $password = New-Object Windows.Forms.TextBox
    $password.SetBounds(140,114,310,28)
    $password.UseSystemPasswordChar = $true
    $password.MaxLength = 1024
    $form.Controls.Add($password)
    $errorLabel = New-Object Windows.Forms.Label
    $errorLabel.SetBounds(20,155,430,28)
    $errorLabel.ForeColor = [Drawing.Color]::DarkRed
    $form.Controls.Add($errorLabel)
    $save = New-Object Windows.Forms.Button
    $save.Text = '加密保存'
    $save.SetBounds(235,200,100,34)
    $save.Add_Click({
      if ([string]::IsNullOrWhiteSpace($account.Text) -or [string]::IsNullOrEmpty($password.Text)) {
        $errorLabel.Text = '请填写手机号或工号和密码。'
      } else { $form.DialogResult = [Windows.Forms.DialogResult]::OK; $form.Close() }
    })
    $form.Controls.Add($save)
    $cancel = New-Object Windows.Forms.Button
    $cancel.Text = '取消'
    $cancel.SetBounds(350,200,100,34)
    $cancel.DialogResult = [Windows.Forms.DialogResult]::Cancel
    $form.Controls.Add($cancel)
    $form.AcceptButton = $save
    $form.CancelButton = $cancel
    if ($form.ShowDialog() -ne [Windows.Forms.DialogResult]::OK) {
      $account.Clear(); $password.Clear(); $form.Dispose()
      $pipeWriter.WriteLine((@{ok=$false;status='cancelled'} | ConvertTo-Json -Compress))
      exit 0
    }
    $plainBytes = $null
    $temporary = Join-Path $vaultRoot ($key + '.tmp-' + [Guid]::NewGuid().ToString('N'))
    try {
      $stage = 'setup_encrypt'
      $plainJson = @{tenantId=$request.tenantId;profile=$profile;username=$account.Text.Trim();password=$password.Text} | ConvertTo-Json -Compress
      $plainBytes = $utf8.GetBytes($plainJson)
      $cipher = [Security.Cryptography.ProtectedData]::Protect($plainBytes,$entropy,[Security.Cryptography.DataProtectionScope]::CurrentUser)
      $payload = @{version=1;binding=$key;ciphertext=[Convert]::ToBase64String($cipher);updatedAt=[DateTimeOffset]::UtcNow.ToString('o')} | ConvertTo-Json -Compress
      $stream = [IO.File]::Open($temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
      try { $outBytes=$utf8.GetBytes($payload); $stream.Write($outBytes,0,$outBytes.Length); $stream.Flush($true) }
      finally { $stream.Dispose() }
      Assert-Private $temporary
      Assert-LocalPath $vaultFile
      if (Test-Path -LiteralPath $vaultFile) {
        Assert-Private $vaultFile
        [IO.File]::Replace($temporary,$vaultFile,$null)
      } else { [IO.File]::Move($temporary,$vaultFile) }
      $stage = 'setup_verify'
      $verified = Read-Credential
      $verified.username = ''; $verified.password = ''
      $pipeWriter.WriteLine((@{ok=$true;status='stored';ready=$true} | ConvertTo-Json -Compress))
    } finally {
      if ($null -ne $plainBytes) { [Array]::Clear($plainBytes,0,$plainBytes.Length) }
      $plainJson=$null; $account.Clear(); $password.Clear(); $form.Dispose()
      if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
  } finally { if ($locked) { $mutex.ReleaseMutex() }; $mutex.Dispose() }
  exit 0
}
$stage = 'binding_vault_lookup'
if (-not (Test-Path -LiteralPath $vaultFile)) {
  if ($request.action -eq 'status') { $pipeWriter.WriteLine((@{ok=$true;ready=$false;status='missing'} | ConvertTo-Json -Compress)); exit 0 }
  $stage = 'missing'
  # Diagnostic only: neither alternate lookup may authorize reading a file
  # that the original provider check rejected. Never emit paths or bindings.
  $lookupDiagnostic = @{provider=$false;file=[IO.File]::Exists($vaultFile);directory=[IO.Directory]::Exists($vaultRoot)}
  throw 'Credential missing'
}
$stage = 'read'
$credential = Read-Credential
try {
  if ($request.action -eq 'status') { $pipeWriter.WriteLine((@{ok=$true;ready=$true;status='ready'} | ConvertTo-Json -Compress)) }
  else { $pipeWriter.WriteLine((@{username=$credential.username;password=$credential.password} | ConvertTo-Json -Compress)) }
} finally { $credential.username=''; $credential.password='' }
`;
