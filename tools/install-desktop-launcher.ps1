[CmdletBinding()]
param(
  [string]$ProjectRoot = 'D:\运营管理系统',
  [string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA 'TERUISI\Launcher'),
  [switch]$NoShortcuts
)

$ErrorActionPreference = 'Stop'
$controller = Join-Path (Resolve-Path -LiteralPath $ProjectRoot).Path 'tools\operations-system-control.ps1'
if (-not (Test-Path -LiteralPath $controller -PathType Leaf)) { throw '未找到现有系统总控。' }
$powerShellPath = (Get-Command pwsh.exe -ErrorAction Stop).Source
$chromePath = @(
  (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
  (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
  (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $chromePath) { throw '未找到 Google Chrome。' }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw '未找到 Windows .NET Framework C# 编译器。' }
$source = Join-Path $PSScriptRoot 'desktop-launcher\Launcher.cs'
New-Item -ItemType Directory -Path $InstallDirectory -Force | Out-Null
$InstallDirectory = (Resolve-Path -LiteralPath $InstallDirectory).Path
$executable = Join-Path $InstallDirectory '运营管理系统.exe'
$iconPath = Join-Path $InstallDirectory 'launcher.ico'

# Draw a code-native icon; no downloaded assets or extra runtime dependency.
Add-Type -AssemblyName System.Drawing
$bitmap = New-Object System.Drawing.Bitmap(64, 64)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.Clear([System.Drawing.Color]::FromArgb(13, 130, 117))
$pen = New-Object System.Drawing.Pen([System.Drawing.Color]::White, 4)
$graphics.DrawRectangle($pen, 13, 15, 38, 27)
$graphics.DrawLine($pen, 32, 42, 32, 51)
$graphics.DrawLine($pen, 22, 52, 42, 52)
$graphics.FillEllipse([System.Drawing.Brushes]::White, 27, 24, 10, 10)
$iconHandle = $bitmap.GetHicon()
$icon = [System.Drawing.Icon]::FromHandle($iconHandle)
$iconStream = [System.IO.File]::Create($iconPath)
try { $icon.Save($iconStream) } finally {
  $iconStream.Dispose(); $icon.Dispose(); $pen.Dispose(); $graphics.Dispose(); $bitmap.Dispose()
}

& $compiler /nologo /target:winexe /platform:anycpu /optimize+ "/win32icon:$iconPath" "/out:$executable" `
  /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Net.Http.dll `
  /reference:System.Runtime.Serialization.dll $source
if ($LASTEXITCODE -ne 0) { throw '桌面应用编译失败。' }
$configuration = [ordered]@{ ControllerPath=$controller; PowerShellPath=$powerShellPath; ChromePath=$chromePath }
$configuration | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $InstallDirectory 'launcher.json') -Encoding UTF8

$createdLinks = @()
if (-not $NoShortcuts) {
  $shellObject = New-Object -ComObject WScript.Shell
  $destinations = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
  foreach ($destination in $destinations) {
    $linkPath = Join-Path $destination '运营管理系统.lnk'
    if (Test-Path -LiteralPath $linkPath) {
      $previous = $shellObject.CreateShortcut($linkPath)
      if ($previous.TargetPath -ne $executable) { throw "已存在其他同名快捷方式，请先确认：$linkPath" }
    }
    $shortcut = $shellObject.CreateShortcut($linkPath)
    $shortcut.TargetPath = $executable
    $shortcut.WorkingDirectory = $InstallDirectory
    $shortcut.IconLocation = "$executable,0"
    $shortcut.Description = '一键打开本地运营管理系统；未启动时自动通过系统总控启动。'
    $shortcut.Hotkey = 'CTRL+ALT+O'
    $shortcut.Save()
    $readback = $shellObject.CreateShortcut($linkPath)
    if ($readback.TargetPath -ne $executable -or $readback.WorkingDirectory -ne $InstallDirectory) {
      throw "快捷方式回读不一致：$linkPath"
    }
    $createdLinks += $linkPath
  }
}
[pscustomobject]@{Executable=$executable; Shortcuts=$createdLinks; Controller=$controller} | ConvertTo-Json
