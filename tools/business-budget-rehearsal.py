"""Calculate a synthetic budget fixture; never reads operations data or providers."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from business_analysis.budget import calculate
from business_analysis.test_budget import fixture

plan, baselines = fixture()
if len(sys.argv) > 1:
    plan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
result = {**calculate(plan, baselines), "evidenceRunId": "evidence_1"}
print(json.dumps(result, ensure_ascii=False))
