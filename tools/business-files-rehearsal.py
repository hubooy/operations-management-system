"""Exercise the application exporter using synthetic fixtures, never live facts."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from business_analysis.report_files import Column, Table, write_pair

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output-root", type=Path, required=True)
parser.add_argument("--rows", type=int, default=5001)
parser.add_argument("--wide-text-columns", type=int, default=0)
args = parser.parse_args()
directory = args.output_root.resolve()
if not directory.is_relative_to(ROOT / ".runtime") or not 101 <= args.rows <= 250000 or not 0 <= args.wide_text_columns <= 16:
    parser.error("Synthetic outputs must be within this worktree's .runtime; rows within 101..250000")
directory.mkdir(parents=True, exist_ok=True)
columns = (Column("keyword", "关键词"), Column("impressions", "曝光次数", "integer", True),
    Column("clicks", "点击次数", "integer", True), Column("ctr", "点击率", "ratio", ratio_of=(2, 1)))
def records():
    for i in range(args.rows):
        yield [f"词{i:06d}", 100, None if i % 7 == 0 else i % 10, None if i % 7 == 0 else (i % 10)/100,
            *[hashlib.sha256(f"synthetic-{i}-{j}".encode()).hexdigest() for j in range(args.wide_text_columns)]]
tables = [Table("overview", "推广诊断", "合成演练。曝光和点击使用同一来源，缺失点击不解释为零。", columns,
    [["商用设备", 200, 10, .05], ["厨房用品", 400, 12, .03], ["待补数据", None, None, None]], 3),
    Table("raw", "完整搜索词", "全部合成搜索词。搜索、排序、缺失筛选和 CSV 导出作用于全表。", columns+tuple(Column(f"synthetic{j}", f"合成文本{j+1}") for j in range(args.wide_text_columns)), records(), args.rows),
    Table("literal", "原文与精度", "文本作为数据；超过 Excel 精度的整数按文本保留。", (Column("a", "来源文本"), Column("b", "整数标识", "integer")),
        [["=1+1", 9999999999999999], ['</script><script>window.attacked=true</script>', 12]], 2)]
started = time.monotonic()
with (directory / "report.xlsx").open("wb") as xlsx, (directory / "report.html").open("wb") as html:
    manifest = write_pair(xlsx, html, title="经营分析导出演练", metadata={"synthetic": True, "liveData": False, "period": "2026-09-01 至 2026-09-30"}, tables=tables)
manifest["elapsedSeconds"] = round(time.monotonic()-started, 3)
manifest["files"] = {}
for suffix in ("xlsx", "html"):
    file = directory / ("report."+suffix)
    sha = hashlib.sha256()
    with file.open("rb") as stream:
        while block := stream.read(1024*1024):
            sha.update(block)
    manifest["files"][suffix] = {"bytes": file.stat().st_size, "sha256": sha.hexdigest()}
(directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"directory": str(directory), "rows": args.rows, "files": manifest["files"], "elapsedSeconds": manifest["elapsedSeconds"]}))
