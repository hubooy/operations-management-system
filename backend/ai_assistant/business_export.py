"""Prepare paired files from the exact sealed report snapshot, without source I/O."""
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import sqlite3
from tempfile import TemporaryDirectory

from business_analysis.contracts import AnalysisContractError
from business_analysis.report_files import Column, Table, MAX_COLUMNS, MAX_FILE_BYTES, write_pair
from business_analysis.results import VIEWS
from . import business_evidence, business_reports
from .business_sealed import Reader
from .business_sealed_source_tables import append as append_sealed_source_tables
from .policy import AiError, authorize_owner, canonical, digest

DIMENSION_NAMES = {"shop": "店铺", "category": "品类", "spu": "SPU", "sku": "SKU", "keyword": "关键词", "searchTerm": "搜索词", "daily": "逐日", "brand": "品牌"}
LABELS = {"rowId": "来源行ID", "id": "分析行ID", "rowIndex": "分析行位置", "sourceKey": "来源键", "baselineKey": "基期来源键",
    "platform": "平台", "shopName": "店铺", "category": "品类", "spuId": "SPU", "skuId": "SKU", "date": "日期",
    "keyword": "关键词", "searchTerm": "搜索词", "brand": "品牌", "onlineSpecCode": "网店规格编码", "merchantCode": "商家编码",
    "value": "值", "presentRows": "有值行数", "missingRows": "缺失行数", "currentRowCount": "本期行数", "baselineRowCount": "基期行数",
    "current": "本期", "baseline": "基期", "difference": "差额", "changeRate": "变化率", "percentagePoints": "百分点变化", "status": "状态",
    "spendCents": "推广费用（分）", "reportedGmvCents": "平台归因金额（分）", "netSalesCents": "ERP净销售（分）",
    "positiveSalesCents": "正向销售（分）", "refundCents": "退款（分）", "costCents": "源成本（分）", "grossProfitCents": "毛利（分）",
    "reportedGrossProfitCents": "源报告毛利（分）", "feeCents": "分摊费用（分）", "impressions": "曝光次数", "clicks": "点击次数",
    "reportedOrderLines": "平台订单口径", "paymentCents": "支付金额（分）", "ctr": "点击率", "cpcCents": "点击成本（分）", "roas": "归因产出比",
    "metrics": "指标", "baselineMetrics": "基期指标", "comparisons": "比较", "ratios": "比率", "entity": "身份", "dimensions": "来源维度",
    "dimensionMissing": "维度缺失", "sampleComparisons": "市场样本比较", "object": "调整对象", "change": "具体动作", "prerequisites": "执行前提",
    "successMetric": "观察指标", "observationDays": "观察天数", "rollback": "回退条件", "priority": "优先级", "ownerRole": "责任角色", "budgetImpact": "预算影响",
    "totalBudgetCents": "预算总上限（分）", "reserveCents": "预留预算（分）", "reservedCents": "预留预算（分）", "allocatedCents": "已分配预算（分）", "unallocatedCents": "未分配余额（分）",
    "budgetCents": "对象预算（分）", "minBudgetCents": "对象最低预算（分）", "maxBudgetCents": "对象最高预算（分）", "horizonDays": "规划天数", "weight": "分配权重",
    "scenario": "情景", "name": "名称", "cpcFactorBps": "点击成本乘数（10000=不变）", "orderRateFactorBps": "订单效率乘数（10000=不变）", "orderValueFactorBps": "订单金额乘数（10000=不变）",
    "contributionMarginBps": "假设贡献率（基点）", "minimumClicks": "样本点击门槛", "minimumOrderLines": "样本订单口径门槛", "reviewAfterSpendBps": "复盘花费占预算（基点）",
    "projectedClicks": "情景点击次数", "projectedOrderLines": "情景订单口径", "projectedAttributedGmvCents": "情景归因金额（分）", "knownAttributedGmvCents": "已知对象情景归因金额（分）",
    "assumedContributionAfterAdCents": "假设贡献扣推广余额（分，非利润）", "projectedRoas": "情景归因产出比", "breakEvenRoas": "假设贡献收支平衡产出比",
    "equivalentBaselineSpendCents": "等规划天数基期花费（分）", "budgetChangeCents": "预算较等天数花费差额（分）", "reviewAfterSpendCents": "提前复盘花费（分）",
    "minimumRoasBps": "最低归因产出比（10000=1倍）", "unavailableTargets": "不可测算对象数", "targetCount": "预算对象数", "mixedReportingBases": "包含不同报告口径",
    "byReportingBasis": "各报告口径分别汇总", "rollbackRule": "复盘与回退条件", "limitations": "限制说明", "missingMetrics": "缺失指标", "days": "基期天数", "datesPresent": "日期覆盖存在"}


def flatten(value, prefix=()):
    """JSON-pointer keys avoid collisions between source field names and paths."""
    result = {}
    if isinstance(value, dict) and value:
        for key, child in value.items():
            result.update(flatten(child, (*prefix, key)))
    else:
        key = "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in prefix)
        result[key] = canonical(value) if isinstance(value, (list, dict)) else value
    return result


def label(key):
    parts = [p.replace("~1", "/").replace("~0", "~") for p in key.split("/")[1:]]
    return " · ".join(LABELS.get(part, part) for part in parts)


class TableSpool:
    def __enter__(self):
        self.directory = TemporaryDirectory(prefix="teruisi-report-")
        try:
            self.db = sqlite3.connect(self.directory.name + "/tables.sqlite")
            self.db.execute("PRAGMA cache_size=-2048")
            self.db.execute("PRAGMA max_page_count=131072")
            self.db.execute("CREATE TABLE rows(table_id INTEGER,row_id INTEGER,payload TEXT,PRIMARY KEY(table_id,row_id)) WITHOUT ROWID")
        except Exception:
            if hasattr(self, "db"):
                self.db.close()
            self.directory.cleanup()
            raise
        self.tables = []
        return self

    def __exit__(self, kind, error, trace):
        try:
            self.db.close()
        finally:
            self.directory.cleanup()
        if isinstance(error, sqlite3.DatabaseError):
            raise AiError("报告派生表临时空间不可用或超过容量", "payload_too_large", 413) from error

    def add(self, key, title, note, records, expected=None):
        table_id, columns, count = len(self.tables), {}, 0
        with self.db:
            for record in records:
                flat = flatten(record)
                for field, value in flat.items():
                    kinds = columns.setdefault(field, set())
                    if value is not None:
                        kinds.add("integer" if type(value) is int else "decimal" if type(value) is float else "text")
                if len(columns) > MAX_COLUMNS:
                    raise AiError("派生表列数超过容量，须按指标拆分", "payload_too_large", 413)
                self.db.execute("INSERT INTO rows VALUES (?,?,?)", (table_id, count, canonical(flat)))
                count += 1
                if count > 1000000:
                    raise AiError("派生表超过单表容量", "payload_too_large", 413)
        if expected is not None and count != expected:
            raise AiError("报告明细行数与来源核对不一致", "conflict", 409)
        fields = list(columns) or ["/状态"]
        specs = []
        for field in fields:
            kinds = columns.get(field, set())
            kind = "text" if not kinds or "text" in kinds else "decimal" if "decimal" in kinds else "integer"
            parts = field.split("/")
            is_value = parts[1:2] == ["metrics"] and (len(parts) == 3 or parts[-1] == "value")
            specs.append(Column(field, label(field), kind, total=is_value and kind == "integer"))
        # Only rows with an available deterministic ratio get a cached formula.
        ratios = {"ctr": ("clicks", "impressions"), "cpcCents": ("spendCents", "clicks"), "roas": ("reportedGmvCents", "spendCents"), "orderLineConversionRate": ("reportedOrderLines", "clicks")}
        for index, field in enumerate(fields):
            if field.startswith("/ratios/") and field.split("/")[-1] in ratios:
                metric = field.split("/")[-1]
                keys = ["/metrics/"+k+"/value" for k in ratios[metric]]
                if all(k in fields for k in keys) and specs[index].kind != "text":
                    specs[index] = Column(field, label(field), "ratio" if metric in {"ctr", "orderLineConversionRate"} else "decimal", ratio_of=tuple(fields.index(k) for k in keys))
        def rows():
            for (payload,) in self.db.execute("SELECT payload FROM rows WHERE table_id=? ORDER BY row_id", (table_id,)):
                values = json.loads(payload)
                yield [values.get(field) for field in fields]
        self.tables.append(Table(key, title, note, tuple(specs), rows(), count))


@contextmanager
def package(report, principal, *, draft, checkpoint=None, renderer_version=1):
    authorize_owner(report, principal)
    snapshot = json.loads(report.snapshot_json)
    if snapshot.get("schemaVersion") != business_reports.SCHEMA:
        raise AiError("此构建器仅支持经营分析报告")
    v2 = business_reports.is_v2_snapshot(snapshot)
    if v2 and renderer_version not in (4, 6):
        raise AiError("v2证据只能使用内部多卷构建器，旧双文件渲染器不支持", "conflict", 409)
    if not draft and report.workflow.status != "completed":
        raise AiError("正式报告须先通过人工复核", "conflict", 409)
    value = business_reports.content(report, principal) if draft else business_reports.validate_review(report, principal)
    evidence = business_evidence.get_run(snapshot["evidenceRunId"], principal)
    if evidence.status != "sealed" or evidence.version != snapshot["evidenceVersion"] or digest(evidence.plan_json) != snapshot["evidencePlanDigest"]:
        raise AiError("报告证据快照不一致", "conflict", 409)
    reader = Reader(evidence, principal)
    sources = reader.sources
    source_by_key = {s["key"]: s for s in sources}
    info = {source["key"]: reader.info(source["key"]) for source in sources}
    def pages(key):
        return reader.pages(key, checkpoint=checkpoint)
    expected = {key: entry["expected"] for key, entry in info.items()}
    metadata = {"reportId": report.id, "evidenceRunId": evidence.id, "evidenceVersion": evidence.version,
        "planDigest": snapshot["evidencePlanDigest"], "scope": snapshot["scope"], "question": snapshot["question"],
        "status": "待复核草稿" if draft else "已通过人工复核", "schemaVersion": "business-files-v1",
        "limitations": ["金额字段保留原分单位；缺失不补零", "市场数据是TOP样本区间，不代表全行业或份额", "当前商品主数据不是历史映射", "建议不自动执行"]}
    if v2:
        metadata.update(schemaVersion="business-files-v2", rendererVersion=renderer_version,
            catalogDigest=snapshot["catalogDigest"], sealedDigest=snapshot["sealedDigest"], sourceCount=len(sources))
    screening = snapshot.get("executionProfile") == "business-agent-screening-reference-v1"
    if screening:
        from . import business_screening_export
        metadata.update(business_screening_export.metadata(report, principal))
    if business_reports.integrated.is_snapshot(snapshot) or (screening and "mappingPlan" in snapshot):
        from business_analysis import mapped_results
        if not screening:
            business_reports.integrated.bound(report, principal)
        metadata.update(mappingPlanDigest=snapshot["mappingPlanDigest"],
            mappingAlgorithmVersion=snapshot["mappingPlan"]["algorithmVersion"], mappedTableAlgorithmVersion=mapped_results.ALGORITHM_VERSION)
    if v2 and "budgetRef" in snapshot:
        from . import business_budget, business_budget_store
        fixed = business_budget_store.binding_for_report(report, principal)
        resolved = business_budget.for_report(report, principal)
        if (snapshot["budgetRef"] != fixed.reference or resolved is None
                or resolved["planDigest"] != fixed.reference["planDigest"]
                or canonical(value.get("budget")) != canonical(resolved)):
            raise AiError("报告预算内容与固定参数重算不一致", "conflict", 409)
        metadata.update(budgetRef=fixed.reference, budgetPlanDigest=resolved["planDigest"],
                        budgetBindingDigest=fixed.reference["bindingDigest"])
    with TableSpool() as spool:
        overview = ({"项目": key, "内容": canonical(item) if isinstance(item, (dict, list)) else item} for key, item in metadata.items())
        if screening and renderer_version == 6:
            spool.add("business-summary", "经营摘要", "先阅读经营判断和调整规划，再结合完整来源、明细及缺口复核。", [
                {"项目":"分析问题", "内容":metadata["question"]},
                {"项目":"总体判断", "内容":value["diagnosis"]["summary"]},
                {"项目":"复核状态", "内容":metadata["status"]},
                *({"项目":"口径说明", "内容":item} for item in value["screening"]["limitations"]),
            ])
        else:
            spool.add("overview", "报告范围", "固定来源、期间与报告版本。", overview)
        spool.add("diagnosis", "深度诊断", "解释与因果仍需人工判断；以下文字来自已持久化的专业分析与复核。", ({"章节": section["title"], "正文": section["body"][start:start+300]} for section in value["sections"] for start in range(0, len(section["body"]), 300)))
        findings = value["diagnosis"]["findings"]
        spool.add("actions", "调整规划", "每条动作保留前提、观察期、责任角色与回退条件。", ({"结论ID": f["id"], "类型": f["kind"], "标题": f["title"], "解释": f["explanation"], **f.get("action", {})} for f in findings))
        citations = ({"结论ID": f["id"], **fact} for f in findings for fact in f["facts"])
        if screening:
            citations = business_screening_export._chunks(citations)
        spool.add("citations", "结论证据", "数值由服务端重新核验；不代表文字中的因果关系已自动证明。", citations)
        spool.add("sources", "来源与核对", "明细封存时的水位与逐页核对结果。", ({"sourceKey": s["key"], "来源": s["domain"], "查询范围": canonical(s["query"]), "核对": canonical(expected[s["key"]]), "覆盖与口径": canonical(info[s["key"]]["metadata"])} for s in sources))
        if screening and renderer_version == 6:
            spool.add("overview", "报告范围与版本", "固定来源、期间与报告版本；用于核对和追溯。", overview)
        if value.get("budget"):
            budget = value["budget"]
            budget_note = "；".join(budget["limitations"])
            spool.add("budget-limits", "预算上限与预留", "固定输入及总额核对；本文件参数为该报告版本快照。", [{**budget["allocation"], **{k: v for k, v in budget["plan"].items() if k not in {"targets", "scenarios"}}, "planDigest": budget["planDigest"]}])
            spool.add("budget-targets", "预算对象与约束", "对象权重、上下限和责任角色均来自固定输入。", budget["plan"]["targets"])
            spool.add("budget-assumptions", "预算情景假设", budget_note, budget["plan"]["scenarios"])
            spool.add("budget-summary", "预算情景汇总", "缺失对象时完整预测为空，仅展示已知对象合计；不是店铺净利润。", ({"scenario": s["assumptions"]["name"], **s["summary"]} for s in budget["scenarios"]))
            for index, scenario in enumerate(budget["scenarios"]):
                spool.add("budget-scenario-"+str(index), "情景_"+scenario["assumptions"]["name"], budget_note, scenario["rows"])
        append_sealed_source_tables(spool, sources, expected, pages, source_by_key, VIEWS, DIMENSION_NAMES)
        if business_reports.integrated.is_snapshot(snapshot):
            from .business_mapped_export import append
            append(spool, report, principal, checkpoint=checkpoint)
        if screening:
            if "mappingPlan" in snapshot:
                from .business_mapped_export import _append_bound
                from .business_screening_runtime import bound
                actual, fixed, _, sealed, catalog, _ = bound(report, principal)
                _append_bound(spool, fixed, sealed, catalog, principal, checkpoint=checkpoint)
                bound(actual, principal)
            business_screening_export.append(spool, report, principal, metadata, read_proofs=value["screening"]["readProofs"], checkpoint=checkpoint)
        calculator = None
        if renderer_version >= 2 and value.get("budget"):
            from business_analysis.budget_offline import payload
            calculator = payload(value["budget"], report.id)
            if renderer_version >= 3:
                calculator["excelEnabled"] = True
        yield metadata, spool.tables, calculator


def build(report, principal, xlsx_file, html_file, *, draft=False, checkpoint=None, renderer_version=1):
    try:
        if type(renderer_version) is not int or renderer_version not in (1, 2, 3, 5):
            raise AnalysisContractError("报告渲染版本不受支持")
        with package(report, principal, draft=draft, checkpoint=checkpoint, renderer_version=renderer_version) as (metadata, tables, calculator):
            return write_pair(xlsx_file, html_file, title="深度经营分析 · "+metadata["scope"]["shop"], metadata=metadata, tables=tables, checkpoint=checkpoint,
                offline_budget=calculator, excel_budget=calculator if renderer_version >= 3 else None,
                xlsx_opc_version=2 if renderer_version == 5 else 1)
    except AnalysisContractError as error:
        raise AiError(str(error), "conflict", 409) from error


@dataclass(frozen=True)
class PreparedVolumes:
    """Internal one-shot package, usable only inside prepare_volumes' context."""
    metadata: dict
    tables: tuple
    plan: dict
    calculator: object
    report_id: str
    evidence_digest: str
    policy: dict
    renderer_version: int = 4
    active: bool = True
    consumed: bool = False
    _binding_digest: str = field(init=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "_binding_digest", _prepared_binding(self))


def _prepared_binding(prepared):
    """Bind passive preparation fields, including column semantics, not rows."""
    from business_analysis.volume_files import _snapshot_json
    try:
        if type(prepared.tables) is not tuple or len(prepared.tables) > 12000:
            raise AnalysisContractError("完整派生表数量超限")
        table_digests = []
        for table in prepared.tables:
            if type(table) is not Table or len(table.columns) > MAX_COLUMNS:
                raise AnalysisContractError("派生表或列数无效")
            descriptor = {"key": table.key, "title": table.title, "note": table.note, "rowCount": table.row_count,
                "columns": [{"key": c.key, "label": c.label, "kind": c.kind, "total": c.total, "ratioOf": c.ratio_of}
                    for c in table.columns]}
            table_digests.append(digest(_snapshot_json(descriptor, "多卷单表绑定")))
        value = {"metadata": prepared.metadata, "plan": prepared.plan, "calculator": prepared.calculator,
            "reportId": prepared.report_id, "evidenceDigest": prepared.evidence_digest, "policy": prepared.policy,
            "tables": table_digests, "rendererVersion": prepared.renderer_version}
        # Prepared input comes from <=48 source identities and the unchanged
        # bounded spool. Cap passive aliases before hashing mutated structures.
        return digest({key: _snapshot_json(item, "多卷准备绑定") for key, item in value.items()})
    except (AnalysisContractError, TypeError, ValueError, RecursionError, AttributeError) as error:
        raise AiError("多卷准备绑定超限或结构无效", "conflict", 409) from error


@contextmanager
def prepare_volumes(report, principal, *, draft=False, checkpoint=None,
                    max_tables=120, max_rows=1_000_000, max_volumes=100, renderer_version=4):
    """Prepare complete v2 tables and a trusted renderer-4 plan without outputs.

The existing spool limits are unchanged. Closing this context releases its
SQLite spool; render before leaving it. Nothing is persisted or published.
"""
    from business_analysis import volume_files, volume_plan
    authorize_owner(report, principal)
    if type(renderer_version) is not int or renderer_version not in (4, 6):
        raise AiError("多卷渲染版本不受支持", "conflict", 409)
    if not business_reports.is_v2_snapshot(json.loads(report.snapshot_json)):
        raise AiError("内部多卷入口须绑定v2经营报告", "conflict", 409)
    try:
        with package(report, principal, draft=draft, checkpoint=checkpoint, renderer_version=renderer_version) as (metadata, tables, calculator):
            policy = {"max_tables": max_tables, "max_rows": max_rows, "max_volumes": max_volumes}
            request = volume_files.request_for(tables, report_id=report.id,
                evidence_digest=metadata["sealedDigest"], renderer_version=renderer_version)
            plan = volume_plan.build(request, native_budget_sheets=3 if calculator is not None else 0, **policy)
            prepared = PreparedVolumes(metadata, tuple(tables), plan, calculator, report.id, metadata["sealedDigest"], policy, renderer_version)
            try:
                yield prepared
            finally:
                object.__setattr__(prepared, "active", False)
    except AnalysisContractError as error:
        raise AiError(str(error), "conflict", 409) from error


def build_volumes(prepared, outputs, *, checkpoint=None, max_file_bytes=MAX_FILE_BYTES):
    """Render caller-owned temporary stream pairs once; errors publish nothing.

`outputs` is a sequence of volume_files.VolumeStreams(xlsx=..., html=...).
Allocate exactly prepared.plan['volumeCount'] empty, readable, writable,
seekable streams. They remain caller-owned on both success and failure.
"""
    from business_analysis import volume_files
    if type(prepared) is not PreparedVolumes or not prepared.active or prepared.consumed:
        raise AiError("多卷准备包已关闭或消费；不得重放部分渲染", "conflict", 409)
    object.__setattr__(prepared, "consumed", True)
    if _prepared_binding(prepared) != prepared._binding_digest:
        raise AiError("多卷准备范围、计划或文件身份已变化", "conflict", 409)
    try:
        return volume_files.render(prepared.tables, outputs, report_id=prepared.report_id,
            evidence_digest=prepared.evidence_digest, renderer_version=prepared.renderer_version, plan=prepared.plan,
            title="深度经营分析 · "+prepared.metadata["scope"]["shop"], metadata=prepared.metadata,
            offline_budget=prepared.calculator, excel_budget=prepared.calculator,
            checkpoint=checkpoint, max_file_bytes=max_file_bytes, **prepared.policy)
    except AnalysisContractError as error:
        raise AiError(str(error), "conflict", 409) from error
