"""Create-only synthetic multi-volume rehearsal; never reads business data.

Usage: python -X utf8 tools/business-volume-files-rehearsal.py NEW_OUTPUT_DIR
Uses deliberately small trusted row/table capacities to cross volume boundaries.
Only complete-manifest.json marks success; partial volume files are unpublished.
"""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import sys
import tracemalloc
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"backend"))
from business_analysis.budget import calculate
from business_analysis.budget_offline import payload
from business_analysis.report_files import Column, Table
from business_analysis.test_budget import fixture
from business_analysis.volume_files import VolumeStreams, render, request_for
from business_analysis.volume_plan import build


class SyntheticRows:
    def __init__(self, count):
        self.count, self.iterations, self.consumed = count, 0, 0

    def __iter__(self):
        self.iterations += 1
        if self.iterations != 1:
            raise AssertionError("Source must be consumed once")
        for i in range(self.count):
            self.consumed += 1
            yield [f"合成关键词{i}", i, 10**16+i]


directory=Path(sys.argv[1]).resolve()
directory.mkdir(parents=True, exist_ok=False)
tracemalloc.start()
tables=[Table(f"table-{i}",f"合成维度表{i}","仅用于多卷完整性与流式容量验收；不是业务数据。",
              (Column("keyword","关键词"),Column("spend","花费（分）","integer",True),Column("id","长整数身份","integer")),
              SyntheticRows(26001 if i==0 else i%3),26001 if i==0 else i%3) for i in range(156)]
request=request_for(tables,report_id="synthetic-volume-report",evidence_digest="a"*64,renderer_version=4)
plan=build(request,max_tables=8,max_rows=5000,native_budget_sheets=3)
p,b=fixture();budget=payload(calculate(p,b),request["reportId"])
budget["excelEnabled"]=True
with ExitStack() as stack:
    outputs=[]
    for volume in plan["volumes"]:
        index=volume["volumeIndex"]
        files=[stack.enter_context((directory/f"{request['reportId']}-volume-{index:03}-of-{plan['volumeCount']:03}.{format}").open("x+b")) for format in ("xlsx","html")]
        outputs.append(VolumeStreams(*files))
    manifest=render(tables,outputs,report_id=request["reportId"],evidence_digest=request["evidenceDigest"],renderer_version=4,
                    plan=plan,title="合成经营分析多卷验收",metadata={"synthetic":True},max_tables=8,max_rows=5000,
                    offline_budget=budget,excel_budget=budget)
    _,peak=tracemalloc.get_traced_memory()
    tracemalloc.stop()
    for volume,pair in zip(manifest["volumes"],outputs):
        with zipfile.ZipFile(pair.xlsx) as archive:
            assert archive.testzip() is None
            embedded=json.loads(archive.read("teruisi-manifest.json"))
            assert [p["rowDigest"] for p in embedded["tables"]]==[p["rowDigest"] for p in volume["tables"]]
        for format,stream in (("xlsx",pair.xlsx),("html",pair.html)):
            stream.seek(0);sha=hashlib.sha256();size=0
            for block in iter(lambda:stream.read(65536),b""):sha.update(block);size+=len(block)
            assert (size,sha.hexdigest())==(volume["files"][format]["bytes"],volume["files"][format]["sha256"])
assert all(table.rows.iterations==1 and table.rows.consumed==table.row_count for table in tables)
with (directory/"complete-manifest.json").open("x",encoding="utf-8") as target:
    json.dump(manifest,target,ensure_ascii=False,separators=(",",":"))
evidence={"syntheticOnly":True,"sourceTables":len(tables),"volumes":manifest["volumeCount"],"fragments":manifest["fragmentCount"],
          "totalRows":manifest["totalRows"],"pythonPeakTracedBytes":peak,"singlePassVerified":True,"allFileHashesVerified":True,
          "allZipMembersVerified":True,"dynamicByteSplitting":False,"manifestDigest":manifest["manifestDigest"]}
(directory/"rehearsal-evidence.json").write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(evidence,ensure_ascii=False))
