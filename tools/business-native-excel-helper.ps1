param([Parameter(Mandatory=$true)][string]$Job)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class NativeExcelWindow {
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd,out uint processId);
}
"@
$encoding=New-Object System.Text.UTF8Encoding($false)
$inputData=Get-Content -LiteralPath $Job -Raw -Encoding UTF8 | ConvertFrom-Json
$directory=[IO.Path]::GetFullPath($inputData.directory)
function Json-Write($Name,$Value){[IO.File]::WriteAllText((Join-Path $directory $Name),($Value|ConvertTo-Json -Depth 100 -Compress),$encoding)}
function Event($Value){[IO.File]::AppendAllText((Join-Path $directory 'events.jsonl'),($Value|ConvertTo-Json -Depth 10 -Compress)+[Environment]::NewLine,$encoding)}
function Release($Value){if($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)){[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)}}
function Fail($Text){throw [InvalidOperationException]::new($Text)}
function Cell-Read($Sheets,$SheetName,$Address){
 $sheet=$null;$range=$null
 try{$sheet=$Sheets.Item($SheetName);$range=$sheet.Range($Address);return $range.Value2}
 finally{Release $range;Release $sheet}
}
$excel=$null;$books=$null;$book=$null;$sheets=$null;$owned=$false;$identity=$null;$failures=New-Object Collections.Generic.List[object];$cases=New-Object Collections.Generic.List[object];$comparisons=0;$errorCellCount=0;$excelVersion=$null;$cleaned=$false
try{
 $preexisting=@(Get-Process -Name EXCEL -ErrorAction SilentlyContinue | ForEach-Object{$_.Id})
 $before=[DateTime]::UtcNow
 Event @{stage='creating_private_instance';existingExcelCount=$preexisting.Count}
 # CoCreateInstance only; never attach to ROT/GetActiveObject.
 $type=[Type]::GetTypeFromProgID('Excel.Application',$true)
 $excel=[Activator]::CreateInstance($type)
 $handle=[IntPtr]$excel.Hwnd;$excelPid=[uint32]0
 [void][NativeExcelWindow]::GetWindowThreadProcessId($handle,[ref]$excelPid)
 if($handle -eq [IntPtr]::Zero -or $excelPid -eq 0 -or $preexisting -contains [int]$excelPid){Fail 'Excel instance is not provably private; no properties/open/Quit permitted'}
 $process=Get-Process -Id $excelPid -ErrorAction Stop
 $image=$process.Path;$started=$process.StartTime.ToUniversalTime()
 if($image -ine $inputData.expectedExcelPath -or $started -lt $before.AddSeconds(-2)){Fail 'Excel process image or creation time mismatch'}
 $owned=$true;$identity=@{verified=$true;pid=[int]$excelPid;path=$image;startTicks=$started.Ticks;startUtc=$started.ToString('o');preexistingPids=$preexisting;hwnd=$handle.ToInt64()}
 Json-Write 'ownership.json' $identity
 Event @{stage='private_instance_verified';pid=$excelPid;path=$image}
 $excel.Visible=$false;$excel.DisplayAlerts=$false;$excel.EnableEvents=$false;$excel.ScreenUpdating=$false
 $excel.AutomationSecurity=3;$excel.AskToUpdateLinks=$false;$excel.IgnoreRemoteRequests=$true
 if($excel.Visible -or $excel.EnableEvents -or [int]$excel.AutomationSecurity -ne 3){Fail 'Security settings not applied'}
 $excelVersion=[string]$excel.Version;$books=$excel.Workbooks
 if($books.Count -ne 0){Fail 'Private instance unexpectedly contains startup workbooks'}
 # A fresh native control workbook isolates Office automation from product OOXML.
 $control=$null;$controlSheets=$null;$controlSheet=$null;$controlCell=$null
 try{
  $control=$books.Add(-4167);if($control.HasVBProject){Fail 'Unexpected native template macro'}
  $controlSheets=$control.Worksheets;$controlSheet=$controlSheets.Item(1);$controlCell=$controlSheet.Range('A1');$controlCell.Value2=17
  Release $controlCell;$controlCell=$controlSheet.Range('A2');$controlCell.Formula='=SUM(A1,3)';$excel.CalculateFullRebuild()
  if($controlCell.Value2 -ne 20){Fail 'Native control arithmetic failed'}
  if([int]$controlSheet.Evaluate('SUMPRODUCT(--ISFORMULA(A1:A2))') -ne 1){Fail 'Native formula counter control failed'}
  Release $controlCell;$controlCell=$controlSheet.Range('A3');$controlCell.Formula='=1/0';$excel.CalculateFullRebuild()
  if([int]$controlSheet.Evaluate('SUMPRODUCT(--ISERROR(A1:A3))') -ne 1){Fail 'Native error counter control failed'}
  $controlCell.ClearContents();$excel.CalculateFullRebuild()
  if([int]$controlSheet.Evaluate('SUMPRODUCT(--ISERROR(A1:A3))') -ne 0){Fail 'Native clean counter control failed'}
  $controlFile=Join-Path $directory 'native-control.xlsx';$control.SaveAs($controlFile,51)
 }finally{Release $controlCell;Release $controlSheet;Release $controlSheets;if($null -ne $control){$control.Close($false);Release $control}}
 $control=$null
 try{$control=$books.Open($controlFile,0,$true);if(-not $control.ReadOnly){Fail 'Native control open failed'};Event @{stage='native_control_opened';passed=$true}}
 finally{if($null -ne $control){$control.Close($false);Release $control}}

 foreach($test in $inputData.tests){
  $name=[string]$test.name;$caseFailed=$false;$formulaCount=0;$caseComparisons=0;$editsApplied=0
  Event @{stage='opening';case=$name}
  try{
   $copy=[IO.Path]::GetFullPath([string]$test.file)
   if(-not $copy.StartsWith((Join-Path $directory 'copies')+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){Fail 'Workbook is not a private synthetic copy'}
   $missing=[Type]::Missing
   $book=$books.Open($copy,0,$true)
   if(-not $book.ReadOnly -or $book.HasVBProject -or [IO.Path]::GetFullPath($book.FullName) -ine $copy){Fail 'Workbook readonly/normal-open/macro/path check failed'}
   $connections=$null
   try{$connections=$book.Connections;if($connections.Count -ne 0){Fail 'Workbook contains a data connection'}}finally{Release $connections}
   $sheets=$book.Worksheets
   if($sheets.Count -ne $test.sheets.Count){Fail 'Worksheet count changed on open'}
   $sheetNames=New-Object Collections.Generic.List[string]
   for($i=0;$i -lt $test.sheets.Count;$i++){
    $sheet=$null;$range=$null;$formula=$null;$errors=$null
    try{
     $meta=$test.sheets[$i];$sheet=$sheets.Item($i+1);$sheetNames.Add([string]$sheet.Name)
     if([string]$sheet.Name -cne [string]$meta.name){Fail 'Worksheet name/order changed on open'}
     $sheet.Activate();$range=$sheet.UsedRange;$rangeRows=$null;$rangeColumns=$null
     try{$rangeRows=$range.Rows;$rangeColumns=$range.Columns;$lastRow=$range.Row+$rangeRows.Count-1;$lastCol=$range.Column+$rangeColumns.Count-1}
     finally{Release $rangeRows;Release $rangeColumns}
     if($lastRow -ne $meta.maxRow -or $lastCol -ne $meta.maxColumn){Fail ('Worksheet used bounds mismatch '+$sheet.Name)}
     $count=0
     $count=[int]$sheet.Evaluate('SUMPRODUCT(--ISFORMULA('+$range.Address()+'))')
     if($count -ne $meta.formulaCount){$probe=$null;try{$probe=$sheet.Range('E5');Event @{stage='formula_mismatch_probe';sheet=$sheet.Name;formula=$probe.Formula;hasFormula=$probe.HasFormula;value=$probe.Value2}}finally{Release $probe}}
     if($count -ne $meta.formulaCount){Fail ('Formula count changed '+$sheet.Name+' actual='+$count+' expected='+$meta.formulaCount)}
     $formulaCount+=$count
    }finally{Release $errors;Release $formula;Release $range;Release $sheet}
   }
   foreach($edit in $test.edits){
    $sheet=$null;$cell=$null
    try{$sheet=$sheets.Item([string]$edit.sheet);$cell=$sheet.Range([string]$edit.address);if($null -eq $edit.value){$cell.ClearContents()|Out-Null}elseif($edit.value -is [string]){$cell.Value2=[string]$edit.value}elseif($edit.value -is [bool]){$cell.Value2=[bool]$edit.value}else{$cell.Value2=[double]$edit.value};$editsApplied++}
    finally{Release $cell;Release $sheet}
   }
   Event @{stage='calculating';case=$name;formulas=$formulaCount;edits=$editsApplied}
   $excel.CalculateFullRebuild()
   if([int]$excel.CalculationState -ne 0){Fail 'Calculation did not finish'}
   foreach($meta in $test.sheets){
    $sheet=$null;$range=$null;$errors=$null
    try{
     $sheet=$sheets.Item([string]$meta.name);$sheet.Activate();$range=$sheet.UsedRange
     $nativeErrors=[int]$sheet.Evaluate('SUMPRODUCT(--ISERROR('+$range.Address()+'))')
     $errorCellCount+=$nativeErrors
     if($nativeErrors -ne 0){
      $cells=$null;$bad=$null
      try{$cells=$sheet.Cells;for($r=1;$r -le $meta.maxRow;$r++){for($c=1;$c -le $meta.maxColumn;$c++){
       $bad=$cells.Item($r,$c)
       try{if($sheet.Evaluate('ISERROR('+$bad.Address()+')')){Event @{stage='error_cell';case=$name;sheet=$sheet.Name;address=$bad.Address();formula=$bad.Formula;value=$bad.Value2};if([string]$bad.Formula -match 'QUOTIENT\(([A-Z]+[0-9]+),([A-Z]+[0-9]+)\)'){ $nr=$Matches[1];$dr=$Matches[2];Event @{stage='integer_probe';case=$name;numerator=$sheet.Evaluate($nr+'+0');denominator=$sheet.Evaluate($dr+'+0');quotient=$sheet.Evaluate('QUOTIENT('+$nr+','+$dr+')');remainder=$sheet.Evaluate('MOD('+$nr+','+$dr+')');subtractionRemainder=$sheet.Evaluate($nr+'-QUOTIENT('+$nr+','+$dr+')*'+$dr)} }}}finally{Release $bad;$bad=$null}
      }}}finally{Release $cells}
      Fail ('Native Excel error cells '+$sheet.Name+' count='+$nativeErrors)
     }
    }finally{Release $range;Release $sheet}
   }
   foreach($check in $test.checks){
    $actual=Cell-Read $sheets ([string]$check.sheet) ([string]$check.address);$expected=$check.expected
    if($actual -is [Array]){Fail 'Expected scalar cell'}
    $equal=$false
    if($null -eq $expected){$equal=$null -eq $actual -or ($actual -is [string] -and $actual -ceq '')}
    elseif($expected -is [string]){$equal=$actual -is [string] -and $actual -ceq $expected}
    elseif($expected -is [bool]){$equal=$actual -is [bool] -and $actual -eq $expected}
    elseif($actual -is [ValueType] -and $actual -isnot [bool]){
     if([double]$expected -eq [math]::Truncate([double]$expected)){$equal=[double]$actual -eq [double]$expected}
     else{$equal=[math]::Abs([double]$actual-[double]$expected) -le 1e-12*[math]::Max(1,[math]::Abs([double]$expected))}
    }
    if(-not $equal){$caseFailed=$true;$failures.Add(@{case=$name;sheet=$check.sheet;address=$check.address;label=$check.label;expected=$expected;actual=$actual})}
    $comparisons++;$caseComparisons++;if($caseComparisons % 100 -eq 0){Event @{stage="checking";case=$name;comparisons=$caseComparisons}}
   }
   $cases.Add(@{name=$name;passed=(-not $caseFailed);sheets=$sheetNames.ToArray();formulas=$formulaCount;edits=$editsApplied;comparisons=$caseComparisons;readOnly=$true;normalLoadDefault=$true;repairRequested=$false})
   Event @{stage='case_complete';case=$name;passed=(-not $caseFailed);comparisons=$caseComparisons}
  }catch{
   $caseFailed=$true;$failures.Add(@{case=$name;error=$_.Exception.Message;details=$_.Exception.ToString();hresult=$_.Exception.HResult;line=$_.InvocationInfo.ScriptLineNumber});$cases.Add(@{name=$name;passed=$false});Event @{stage='case_failed';case=$name;error=$_.Exception.Message}
  }finally{
   Release $sheets;$sheets=$null
   if($null -ne $book){$book.Close($false);Release $book;$book=$null}
  }
 }
}catch{$failures.Add(@{stage='instance';error=$_.Exception.Message;details=$_.Exception.ToString();hresult=$_.Exception.HResult;line=$_.InvocationInfo.ScriptLineNumber});Event @{stage='failed';error=$_.Exception.Message}}
finally{
 Release $sheets
 if($null -ne $book -and $owned){try{$book.Close($false)}catch{};Release $book}
 Release $books
 if($null -ne $excel -and $owned){try{$excel.Quit()}catch{$failures.Add(@{stage='quit';error=$_.Exception.Message})}}
 Release $excel;$excel=$null
 [GC]::Collect();[GC]::WaitForPendingFinalizers();[GC]::Collect();[GC]::WaitForPendingFinalizers()
 if($owned){
  $end=[DateTime]::UtcNow.AddSeconds(10)
  do{$remaining=Get-Process -Id $identity.pid -ErrorAction SilentlyContinue;if($null -eq $remaining){$cleaned=$true;break};Start-Sleep -Milliseconds 100}while([DateTime]::UtcNow -lt $end)
  if(-not $cleaned){$failures.Add(@{stage='cleanup';error='Private Excel still running; supervisor must verify exact identity before termination'})}
 }
 Json-Write 'result.json' @{passed=($failures.Count -eq 0 -and $cleaned);excelVersion=$excelVersion;identity=$identity;cases=$cases.ToArray();failures=$failures.ToArray();comparedValues=$comparisons;excelErrorCells=$errorCellCount;privateProcessExited=$cleaned;macroSecurity=3;linksUpdated=$false;sourceFilesOpened=$false;sourceWorkbooksSaved=$false;syntheticControlSaved=$true;numericComparison='Integers exact; noninteger Excel binary values relative tolerance 1e-12'}
}
if($failures.Count){exit 1}else{exit 0}
