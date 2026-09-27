"""Synthetic offline calculator inputs and an actual paired HTML fixture."""
from copy import deepcopy
import json
from pathlib import Path
import random
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from business_analysis.budget import calculate
from business_analysis.budget_offline import ENGINE, payload, projection
from business_analysis.report_files import Column, Table, write_pair
from business_analysis.test_budget import fixture

directory = Path(sys.argv[1]).resolve()
directory.mkdir(parents=True, exist_ok=True)
plan, bases = fixture()
base_result = calculate(plan, bases)
with (directory / "report.xlsx").open("wb") as xlsx, (directory / "report.html").open("wb") as html:
    write_pair(xlsx, html, title="预算情景合成验收", metadata={"synthetic": True},
        tables=[Table("original", "原报告固定数值", "试算不能覆盖原结论。", (Column("gmv", "情景金额（分）", "integer"),), [[45000]], 1)],
        offline_budget=payload(base_result, "synthetic-report"))
rng, cases = random.Random(801), []
for index in range(155):
    plan, bases = fixture()
    count = 100 if index == 0 else rng.randrange(1, 15)
    target, baseline = deepcopy(plan["targets"][0]), deepcopy(bases[0])
    plan["targets"], bases = [], []
    plan["totalBudgetCents"] = rng.randrange(10000, 10000000)
    plan["reserveCents"] = rng.randrange(plan["totalBudgetCents"])
    plan["horizonDays"] = rng.randrange(7, 94)
    for i in range(count):
        identity = f"{i:064x}"
        plan["targets"].append({**target, "rowId": identity, "rowIndex": i, "weight": rng.randrange(1, 10001), "maxBudgetCents": rng.randrange(plan["totalBudgetCents"]), "minBudgetCents": 0})
        facts = {"spendCents": rng.randrange(1000, 30000), "clicks": rng.randrange(1, 4000), "reportedOrderLines": rng.randrange(1, 100), "reportedGmvCents": rng.randrange(100, 100000)}
        if index % 10 == 0: facts["clicks"] = None
        if index % 13 == 0: facts["reportedOrderLines"] = 0
        bases.append({**baseline, "rowId": identity, "metrics": facts, "datesPresent": index % 11 != 0, "source": "tmall_promotion" if i % 2 and index % 3 == 0 else "jd_promotion"})
    for scenario in plan["scenarios"]:
        for key in ("cpcFactorBps", "orderRateFactorBps", "orderValueFactorBps"): scenario[key] = rng.randrange(1000, 30001)
        scenario["contributionMarginBps"] = None if index % 4 == 0 else rng.randrange(10001)
    cases.append({"plan": plan, "baselines": bases, "expected": projection(calculate(plan, bases))})
# Large exact-cents allocation, a stable fractional tie and successive caps.
for total, weights, maxima in ((10**12, [9999, 10000], [10**12, 10**12]), (10001, [1, 1], [10000, 10000]), (10000, [2, 1], [2000, 4000])):
    plan, bases = fixture(); plan["totalBudgetCents"] = total
    for i, t in enumerate(plan["targets"]): t.update(weight=weights[i], maxBudgetCents=maxima[i])
    cases.append({"plan": plan, "baselines": bases, "expected": projection(calculate(plan, bases))})
(directory / "engine.js").write_text(ENGINE, encoding="utf-8")
(directory / "cases.json").write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
print(json.dumps({"cases": len(cases), "productionWrites": False}))
