import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

test("optional DingTalk dependencies are checked only by explicit receiver startup", () => {
  const source = readFileSync(new URL("../tools/django-ai.ps1", import.meta.url), "utf8");
  const start = source.slice(source.indexOf("function Start-AiStack"), source.indexOf("function Stop-AiStack"));
  const receiver = source.slice(source.indexOf("function Invoke-DingTalkReceiver"), source.indexOf("function Enable-AiStartup"));
  assert.doesNotMatch(start, /dingtalk_stream|StartDingTalk|Invoke-DingTalkReceiver/);
  assert.match(receiver, /import dingtalk_stream, websockets/);
  assert.ok(receiver.indexOf("import dingtalk_stream") < receiver.indexOf('Start-ManagedProcess $serviceName'));
  assert.match(source, /DingTalkConnection/);
  assert.match(source, /Get-DingTalkConnectionState/);
});

test("AI runtime environment wrapper invokes the caller once without callback shadowing", t => {
  const powershell = path.join(process.env.SystemRoot ?? "C:\\Windows", "System32/WindowsPowerShell/v1.0/powershell.exe");
  if (process.platform !== "win32" || !existsSync(powershell)) { t.skip("Windows PowerShell 5 is unavailable"); return; }
  const controller = fileURLToPath(new URL("../tools/django-ai.ps1", import.meta.url));
  const script = `
$ErrorActionPreference='Stop'
$sourcePath=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${Buffer.from(controller).toString("base64")}'))
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseInput([IO.File]::ReadAllText($sourcePath,[Text.Encoding]::UTF8),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Controller syntax error'}
$wrapper=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Invoke-WithAiEnvironment'},$true)
if(-not $wrapper){throw 'Missing environment wrapper'}
. ([scriptblock]::Create($wrapper.Extent.Text))
function Read-PandasConfig { return [pscustomobject]@{ image='sha256:fixture' } }
$PandasKeyPath='fixture-dpapi-file'
function Invoke-WithDjangoEnvironment($Secrets,$DatabaseUrl,$ProcessRole,$ReadOnly,$BodyBytes,$AuthorityEpoch,$CutoverId,[scriptblock]$Operation) {
  if($AuthorityEpoch -cne 'test-epoch' -or $CutoverId -cne 'test-cutover' -or $DatabaseUrl -cne 'fixture-database'){throw 'Authority transport changed'}
  $names=@('AI_SECRET_ENCRYPTION_KEY','AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST','TERUISI_DJANGO_AI_EDGE_BASE_URL','TERUISI_PANDAS_RUNNER_KEY_FILE','TERUISI_PANDAS_RUNNER_IMAGE')
  $previous=@{}; foreach($name in $names){$previous[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
  try { $env:TERUISI_PANDAS_RUNNER_KEY_FILE=''; $env:TERUISI_PANDAS_RUNNER_IMAGE=''; & $Operation } finally {foreach($name in $names){[Environment]::SetEnvironmentVariable($name,$previous[$name],'Process')}}
}
$env:AI_SECRET_ENCRYPTION_KEY='outer-sentinel'
$testSecrets=[pscustomobject]@{ModelEncryptionKey='test-only-key';ModelOriginAllowlist='https://example.com'}
$authority=[pscustomobject]@{authorityEpoch='test-epoch';cutoverId='test-cutover'}
foreach($testRole in @('ai_writer','ai_reader')) {
  $script:called=0
  Invoke-WithAiEnvironment ([pscustomobject]@{}) $testSecrets 'fixture-database' $testRole ($testRole -ceq 'ai_reader') 1048576 $authority {
    $script:called++
    $expected=if($testRole -ceq 'ai_writer'){'test-only-key'}else{''}
    if([string]$env:AI_SECRET_ENCRYPTION_KEY -cne $expected -or $env:AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST -cne 'https://example.com' -or $env:TERUISI_DJANGO_AI_EDGE_BASE_URL -cne 'http://127.0.0.1:3000'){throw 'AI environment mismatch'}
    $expectedPandas=if($testRole -ceq 'ai_writer'){'fixture-dpapi-file'}else{''}
    if([string]$env:TERUISI_PANDAS_RUNNER_KEY_FILE -cne $expectedPandas){throw 'Pandas credential leaked to reader or missing in writer'}
  }
  if($script:called -ne 1 -or $env:AI_SECRET_ENCRYPTION_KEY -cne 'outer-sentinel'){throw 'Callback count or restoration failed'}
}
Write-Output 'AI writer and reader callbacks each executed once'
`;
  const result = spawnSync(powershell, ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", Buffer.from(script, "utf16le").toString("base64")], { encoding: "utf8", windowsHide: true, timeout: 10000 });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /AI writer and reader callbacks each executed once/);
});
