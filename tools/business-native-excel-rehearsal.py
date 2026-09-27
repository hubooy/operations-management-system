"""Native Excel QA over explicitly synthetic .runtime copies; never active workbooks.

Windows only. Supervises one hidden STA helper and kills only its independently
verified Excel PID+image+creation time on timeout. Original files are never opened.
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, subprocess, sys, time, uuid, zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'.runtime'
POWERSHELL=Path(os.environ.get('WINDIR',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
ALLOWED={'AND','COUNT','COUNTIF','COUNTIFS','GCD','IF','INDEX','INT','ISBLANK','ISNUMBER','MAX','MIN','MOD','NOT','OR','PRODUCT','QUOTIENT','ROUND','SUM','SUMIF','SUMPRODUCT'}

def canonical(value): return json.dumps(value,ensure_ascii=True,separators=(',',':'))
def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as stream:
  while block:=stream.read(1024*1024):h.update(block)
 return h.hexdigest()
def bounded(path):
 p=Path(path).resolve(strict=True)
 if not p.is_relative_to(RUNTIME.resolve()) or not p.is_file():raise ValueError('Only an existing .runtime synthetic file is allowed')
 return p

def inspect(path):
 """Reject executable/external package parts before any Excel process exists."""
 with zipfile.ZipFile(path) as z:
  infos=z.infolist()
  if len(infos)>10000 or sum(i.file_size for i in infos)>256*1024*1024:raise ValueError('Uncompressed package capacity')
  names=[i.filename for i in infos]
  if len(names)!=len(set(names)):raise ValueError('Duplicate ZIP parts')
  for name in names:
   lower=name.lower()
   if name.startswith('/') or '..' in Path(name).parts or any(x in lower for x in ('vbaproject','macrosheet','externallink','connections','querytable','embedding','customui','activex','vml')):raise ValueError('Active/external package part: '+name)
   if name.endswith('.rels'):
    for rel in ET.fromstring(z.read(name)):
     if rel.attrib.get('TargetMode')=='External':raise ValueError('External relationship')
  workbook=ET.fromstring(z.read('xl/workbook.xml'))
  sheets=workbook.findall('s:sheets/s:sheet',NS)
  if not 1<=len(sheets)<=131:raise ValueError('Sheet capacity')
  rels={r.attrib['Id']:r.attrib['Target'] for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
  shared=[]
  if 'xl/sharedStrings.xml' in names:
   shared=[''.join(t.text or '' for t in si.findall('.//s:t',NS)) for si in ET.fromstring(z.read('xl/sharedStrings.xml'))]
  result=[]
  for s in sheets:
   target=rels[s.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
   name=target.lstrip('/') if target.startswith('/') else 'xl/'+target
   if '..' in name.split('/'):raise ValueError('Unsafe worksheet relationship')
   sheet=ET.fromstring(z.read(name));cells=[];formula_count=0;maxrow=maxcol=0
   for c in sheet.findall('.//s:sheetData/s:row/s:c',NS):
    address=c.attrib['r'];match=re.fullmatch(r'([A-Z]+)([1-9][0-9]*)',address)
    if not match:raise ValueError('Cell coordinate')
    col=0
    for ch in match[1]:col=col*26+ord(ch)-64
    maxrow=max(maxrow,int(match[2]));maxcol=max(maxcol,col)
    f=c.find('s:f',NS);v=c.find('s:v',NS);kind=c.attrib.get('t','n')
    if f is not None:
     formula_count+=1;formula=f.text or ''
     # No named/external/DDE formulas; only the existing arithmetic vocabulary.
     if any(x in formula for x in ('[',']','|')) or set(re.findall(r'([A-Z][A-Z0-9_.]*)\s*\(',formula))-ALLOWED:raise ValueError('Unsupported formula')
    if kind=='inlineStr':value=''.join(t.text or '' for t in c.findall('.//s:t',NS))
    elif kind=='s':value=shared[int(v.text)]
    elif kind=='b':value=bool(int(v.text))
    elif kind=='e':raise ValueError('Cached Excel error')
    elif v is None or v.text is None:value=None
    elif kind=='str':value=v.text
    else:
     number=float(v.text);value=int(number) if number.is_integer() else number
    cells.append({'address':address,'value':value,'formula':f.text if f is not None else None})
   result.append({'name':s.attrib['name'],'maxRow':maxrow,'maxColumn':maxcol,'formulaCount':formula_count,'cells':cells})
  return result

def check(sheet,address,expected,label=None):return {'sheet':sheet,'address':address,'expected':expected,'label':label or address}
def budget_checks(case,sheets,modern):
 checks=[check('原报告固定数值','A4',45000)]
 if case.get('invalid'):
  return checks+[check(sheets[0],'E10','请检查参数及最低预算合计'),check(sheets[0],'E5',None),check(sheets[2],'P5','参数无效')]
 checks += [check(sheets[0],'E10','参数有效'),check(sheets[0],'E5',case['allocation']['allocatedCents']/100),check(sheets[0],'E6',case['allocation']['unallocatedCents']/100)]
 for i,row in enumerate(case['rows']):
  high=case.get('precision',False) and i==0;bp=i in case.get('baselinePrecisionTargets',[]) or (modern and case.get('normUnsupported',False) and row['equivalentBaselineSpendCents'] is not None and abs(row['equivalentBaselineSpendCents'])>99999999999999)
  for col,key in {'B':'budgetCents','J':'projectedAttributedGmvCents','K':'projectedRoas','L':'assumedContributionAfterAdCents','M':'reviewAfterSpendCents','N':'equivalentBaselineSpendCents','O':'budgetChangeCents'}.items():
   value=None if (high and col in ('J','K','L')) or (bp and col in ('N','O')) else row[key]
   checks.append(check(sheets[2],col+str(i+5),value,key))
  status=('需高精度复算' if high else {'unavailable':'不可测算','low_sample_scenario':'低样本假设','assumption_scenario':'假设情景'}[row['status']])+('；基期对比需高精度复算' if bp else '')
  checks += [check(sheets[2],'P'+str(i+5),status),check(sheets[0],'H'+str(i+27),status)]
  if modern:checks.append(check(sheets[2],'U'+str(i+5),'基期对比需高精度复算' if bp else '基期对比不可测算' if row['equivalentBaselineSpendCents'] is None else '基期对比可测算'))
 for addr,key in [('E7','projectedAttributedGmvCents'),('E8','assumedContributionAfterAdCents')]:
  value=case['summary'][key];checks.append(check(sheets[0],addr,'不可合计或不可测算' if case.get('precision') or value is None else value/100))
 return checks

def cleanup_owned(receipt):
 if not receipt.exists():return {'ownershipAvailable':False,'terminated':False}
 owned=json.loads(receipt.read_text(encoding='utf-8-sig'))
 if owned.get('verified') is not True:raise RuntimeError('Ownership not proven; no Excel termination permitted')
 image=owned['path'].replace("'","''");pid=int(owned['pid']);ticks=int(owned['startTicks'])
 script=f"$p=Get-Process -Id {pid} -ErrorAction SilentlyContinue; if(-not $p){{ @{{running=$false;terminated=$false}}|ConvertTo-Json -Compress }}else{{ $handle=$p.Handle; if($p.Path -ine '{image}' -or $p.StartTime.ToUniversalTime().Ticks -ne {ticks}){{@{{running=$true;terminated=$false;identityMismatch=$true}}|ConvertTo-Json -Compress}}else{{$p.Kill();$p.WaitForExit(5000)|Out-Null;@{{running=(-not $p.HasExited);terminated=$true;pid={pid};identityVerified=$true}}|ConvertTo-Json -Compress}} }}"
 result=subprocess.run([str(POWERSHELL),'-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=15)
 if result.returncode:raise RuntimeError('Exact process cleanup failed: '+result.stderr)
 value=json.loads(result.stdout)
 if value.get('running'):raise RuntimeError('Private process absent/identity not safely resolved')
 return value

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--formal',default=str(RUNTIME/'screening-formal-files'));parser.add_argument('--budget',action='append');parser.add_argument('--timeout',type=int,default=600);parser.add_argument('--case-timeout',type=int,default=90);parser.add_argument('--only');args=parser.parse_args()
 if os.name!='nt':raise RuntimeError('Native Windows Excel only')
 out=RUNTIME/('native-excel-'+uuid.uuid4().hex[:12]);out.mkdir();copies=out/'copies';copies.mkdir()
 budgets=args.budget or [str(RUNTIME/'excel-independent-review'),str(RUNTIME/'excel-baseline-precision-review')]
 paths={};tests=[]
 def own_copy(path):
  source=bounded(path)
  if source not in paths:
   meta=inspect(source);destination=copies/(str(len(paths))+'-'+source.name);shutil.copyfile(source,destination)
   if sha(source)!=sha(destination):raise ValueError('Copy checksum')
   destination.chmod(0o444)
   paths[source]={'source':str(source),'copy':str(destination),'sha256':sha(source),'sheets':meta}
  return paths[source]
 for directory in budgets:
  expected=json.loads(bounded(Path(directory)/'expected.json').read_text(encoding='utf8'))
  for case in expected['cases']:
   item=own_copy(Path(directory)/case['file']);sheets=expected['sheets']
   modern=any(c['address']=='U5' for s in item['sheets'] if s['name']==sheets[2] for c in s['cells'])
   tests.append({'name':Path(directory).name+'/'+case['name'],'file':item['copy'],'sheets':item['sheets'],'edits':[{'sheet':sheets[i],'address':a,'value':v} for i,a,v in case['edits']],'checks':budget_checks(case,sheets,modern)})
 formal=Path(args.formal).resolve(strict=True)
 if not formal.is_relative_to(RUNTIME.resolve()):raise ValueError('Formal input must be synthetic runtime')
 for file in sorted(formal.glob('*.xlsx')):
  item=own_copy(file)
  # Every constant and cached formula result must agree with native Value2.
  checks=[check(s['name'],c['address'],c['value']) for s in item['sheets'] for c in s['cells'] if c['value'] is not None]
  tests.append({'name':'formal/'+file.name,'file':item['copy'],'sheets':item['sheets'],'edits':[],'checks':checks})
 if args.only:tests=[t for t in tests if re.fullmatch(args.only,t['name'])]
 if not tests:raise ValueError('No synthetic cases')
 for test in tests:test['sheets']=[{k:v for k,v in s.items() if k!='cells'} for s in test['sheets']]
 job={'directory':str(out),'tests':tests,'expectedExcelPath':r'C:\Program Files\Microsoft Office\Root\Office16\EXCEL.EXE'}
 (out/'job.json').write_text(canonical(job),encoding='utf8')
 helper=ROOT/'tools/business-native-excel-helper.ps1'
 command=[str(POWERSHELL),'-NoProfile','-NonInteractive','-STA','-ExecutionPolicy','Bypass','-File',str(helper),'-Job',str(out/'job.json')]
 print(canonical({'stage':'prepared','output':str(out),'cases':len(tests),'files':len(paths)}),flush=True)
 start=time.monotonic();log=(out/'helper.log').open('wb');process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
 receipt=out/'ownership.json';events=out/'events.jsonl';last_event='';forced=False;supervisor_errors=[]
 try:
  while process.poll() is None:
   if events.exists():
    raw=events.read_text(encoding='utf-8-sig');latest=raw.splitlines()[-1] if raw.strip() else ''
    if latest!=last_event:last_event=latest;print(latest,flush=True)
   idle=time.time()-(events.stat().st_mtime if events.exists() else time.time()-int(time.monotonic()-start))
   if time.monotonic()-start>args.timeout or idle>args.case_timeout:
    stopped=cleanup_owned(receipt)
    (out/'watchdog.json').write_text(canonical(stopped),encoding='utf8')
    process.kill();forced=True;raise TimeoutError('Bounded native Excel QA timed out')
   time.sleep(0.25)
 except Exception as error:
  supervisor_errors.append({'stage':'supervision','error':str(error)})
 finally:
  if process.poll() is None:process.kill()
  try:process.wait(timeout=15)
  except Exception as error:supervisor_errors.append({'stage':'helper_cleanup','error':str(error)})
  log.close()
  try:final_cleanup=cleanup_owned(receipt)
  except Exception as error:
   final_cleanup={'confirmed':False,'error':str(error)}
   supervisor_errors.append({'stage':'excel_cleanup','error':str(error)})
  forced=forced or final_cleanup.get('terminated',False)
  (out/"process-cleanup.json").write_text(canonical(final_cleanup),encoding="utf8")
  originals=[{'source':v['source'],'sha256':v['sha256'],'unchanged':sha(Path(v['source']))==v['sha256'],'copyUnchanged':sha(Path(v['copy']))==v['sha256']} for v in paths.values()]
  (out/'source-hashes.json').write_text(canonical(originals),encoding='utf8')
 result_path=out/'result.json'
 result=json.loads(result_path.read_text(encoding='utf-8-sig')) if result_path.exists() else {'passed':False,'error':'helper did not produce result'}
 result.update({'syntheticOnly':True,'originalsUnchanged':all(v['unchanged'] and v['copyUnchanged'] for v in originals),'outputDirectory':str(out),'helperExit':process.returncode,'forcedTermination':forced,'caseCount':len(tests),'supervisorCleanup':final_cleanup,'supervisorErrors':supervisor_errors})
 result['passed']=result.get('passed') is True and result['originalsUnchanged'] and process.returncode==0 and not supervisor_errors
 (out/'evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
 print(canonical({k:result.get(k) for k in ('passed','comparedValues','excelErrorCells','privateProcessExited','originalsUnchanged','outputDirectory','helperExit','forcedTermination','caseCount')}),flush=True)
 return 0 if result['passed'] else 1
if __name__=='__main__':sys.exit(main())
