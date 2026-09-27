"""Owner-bound, append-only source pages; no model or caller-supplied facts.

Collection advances one bounded page per request. Reads occur outside the AI
mutation lock; version CAS commits the page and checkpoint together.
"""
import json
from django.conf import settings
from django.db.models import Sum, JSONField
from django.db.models.functions import Cast
from django.db.models.fields.json import KeyTextTransform
from django.utils import timezone
from business_analysis.contracts import PageReconciler, AnalysisContractError, comparison_periods
from . import models as m, transport, business_evidence_store as store
from .datasets import _result
from .policy import AiError, authorize_owner, boolean, canonical, cas, current_principal, digest, fields, identifier, integer, mutation, passive, uid

MAX_BYTES = 64 * 1024 * 1024
MAX_PAGES = 2000
TOOLS = {"sales": "get_sales_analysis_records", "netshop": "get_netshop_analysis_records", "market": "get_market_analysis_records"}

is_v2 = store.is_v2


def next_source(row):
    if is_v2(row):
        sources = store.catalog(row)
        key = m.AiBusinessEvidenceSource.objects.filter(run_id=row.id, finished=False).order_by("ordinal").values_list("source_key", flat=True).first()
        return next((s for s in sources if s["key"] == key), None)
    plan, state = json.loads(row.plan_json), json.loads(row.state_json)
    return next((s for s in plan["sources"] if not state.get(s["key"], {}).get("verifier", {}).get("finished")), None)


def all_finished(row):
    if is_v2(row):
        sources = store.catalog(row)
        return store.progress(row)["completedSources"] == len(sources)
    state = json.loads(row.state_json)
    return len(state) == len(json.loads(row.plan_json)["sources"]) and all(s["verifier"]["finished"] for s in state.values())


def principal_key(principal):
    return digest(["business-workbench", principal.email.lower()])


def listing(params, principal):
    current_principal(principal, admin=True)
    fields(params, {"page", "pageSize", "clientRequestId"})
    def number(name, default, maximum):
        value = params.get(name, str(default))
        if not isinstance(value, str) or len(value) > 5 or not value.isascii() or not value.isdigit() or value.startswith("0"):
            raise AiError("分页参数无效")
        return integer(int(value), name, hi=maximum)
    page, size = number("page", 1, 10000), number("pageSize", 10, 20)
    rows = m.AiBusinessEvidenceRun.objects.filter(owner_email=principal.email.lower(),
        scope_json=canonical(principal.scope)).exclude(plan_json__contains='"schemaVersion":"business-evidence-v3"')
    if "clientRequestId" in params:
        rows = rows.filter(client_request_id=identifier(params["clientRequestId"]))
    total = rows.count()
    items = []
    for row in rows.order_by("-created_at", "-id")[(page-1)*size:page*size]:
        plan, state = json.loads(row.plan_json), json.loads(row.state_json)
        progress = store.progress(row) if is_v2(row) else None
        items.append({"id": row.id, "clientRequestId": row.client_request_id,
            "question": plan.get("analysisRequest", {}).get("question", ""), "status": row.status, "version": row.version,
            "collection": {"status": row.collection_status if row.status == "collecting" else row.status,
                "nextAttemptAt": row.next_collect_at.isoformat(), "consecutiveFailures": row.collection_failures, "errorCode": row.collection_error_code},
            "createdAt": row.created_at.isoformat(), "storedBytes": row.stored_bytes,
            "sourceCount": progress["sourceCount"] if progress is not None else len(plan.get("sources", [])),
            "completedSources": progress["completedSources"] if progress is not None else sum(bool(s["verifier"]["finished"]) for s in state.values()),
            "rowCount": progress["rowCount"] if progress is not None else sum(s["verifier"]["rows"] for s in state.values())})
        if progress is not None:
            store.assert_current(row)
    return {"items": items, "principalKey": principal_key(principal),
        "pagination": {"page": page, "pageSize": size, "total": total, "hasMore": page*size < total}}


def detail(run_id, principal):
    row = get_run(run_id, principal)
    related = list(m.AiReportRun.objects.filter(owner_email=principal.email.lower(), scope_json=canonical(principal.scope)).annotate(
        evidence_id=KeyTextTransform("evidenceRunId", Cast("snapshot_json", JSONField())),
        report_schema=KeyTextTransform("schemaVersion", Cast("snapshot_json", JSONField())),
    ).filter(evidence_id=row.id, report_schema="business-report-v1").order_by("-created_at", "-id").values(
        "id", "workflow_id", "workflow__status", "created_at")[:11])
    return {"item": mapping(row), "principalKey": principal_key(principal),
        "reportsPagination": {"limit": 10, "hasMore": len(related) > 10},
        "reports": [{"id": r["id"], "workflowId": r["workflow_id"], "status": r["workflow__status"],
            "createdAt": r["created_at"].isoformat()} for r in related[:10]]}


def get_run(run_id, principal):
    current_principal(principal, admin=True)
    row = m.AiBusinessEvidenceRun.objects.filter(pk=identifier(run_id)).first()
    if not row:
        raise AiError("证据任务不存在", "not_found", 404)
    if json.loads(row.plan_json).get("schemaVersion") == "business-evidence-v3":
        raise AiError("证据任务不存在", "not_found", 404)
    return authorize_owner(row, principal)


def _restore(state):
    verifier = PageReconciler()
    if state:
        if set(state) != set(verifier.__dict__):
            raise AiError("证据检查点版本不兼容", "conflict", 409)
        verifier.__dict__.update(state)
    return verifier


def mapping(row):
    if is_v2(row):
        return _mapping_v2(row)
    state, plan = json.loads(row.state_json), json.loads(row.plan_json)
    return {"id": row.id, "status": row.status, "version": row.version, "storedBytes": row.stored_bytes,
        "collection": {"status": row.collection_status if row.status == "collecting" else row.status,
            "nextAttemptAt": row.next_collect_at.isoformat(), "consecutiveFailures": row.collection_failures, "errorCode": row.collection_error_code},
        "createdAt": row.created_at.isoformat(),
        "plan": plan, "sources": {key: {"pageCount": value["pageCount"], "sourceRef": value["verifier"]["source_ref"],
            "rowCount": value["verifier"]["rows"], "complete": value["verifier"]["finished"], "metadata": value["metadata"],
            "reconciliation": _restore(value["verifier"]).result() if value["verifier"]["finished"] else None} for key, value in state.items()},
        "consistency": "immutable_collected_source_versions_not_cross_domain_atomic_snapshot", "modelAnalysisCompleted": False,
        **({"analysisRequestMeaning": "requested_only_not_source_availability_or_dimension_coverage"}
            if "analysisRequest" in plan else {})}


def create(body, principal):
    if isinstance(body, dict) and body.get("schemaVersion") == "business-evidence-v2":
        return _create_v2(body, principal)
    current_principal(principal, admin=True)
    fields(body, {"clientRequestId", "sources", "collectionMode", "autoCollect", "analysisRequest", "expectedPrincipalKey"}, {"clientRequestId", "sources"})
    if "expectedPrincipalKey" in body and body["expectedPrincipalKey"] != principal_key(principal):
        raise AiError("当前账号已变化，请重新确认分析范围", "access_denied", 403)
    mode = body.get("collectionMode", "standard")
    if mode not in ("standard", "bulk"):
        raise AiError("采集模式无效")
    automatic = bool(boolean(body.get("autoCollect", False), "autoCollect"))
    if automatic and mode != "bulk":
        raise AiError("后台取数须使用 bulk 模式")
    client = identifier(body["clientRequestId"])
    sources = body["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 12:
        raise AiError("来源数量必须为 1—12")
    keys, queries = set(), set()
    for source in sources:
        fields(source, {"key", "domain", "query"}, {"key", "domain", "query"})
        key = identifier(source["key"])
        if key in keys or not isinstance(source["domain"], str) or source["domain"] not in TOOLS:
            raise AiError("来源键重复或来源域无效")
        keys.add(key)
        query = source["query"]
        allowed = ({"platform", "startDate", "endDate", "window", "category", "scope", "rankingDimension", "priceBandFilter"} if source["domain"] == "market"
            else {"platform", "shop", "startDate", "endDate", "window"} | ({"channel"} if source["domain"] == "sales" else {"dataset"}))
        fields(query, allowed, allowed - {"window"})
        for field in ("platform", "shop", "channel"):
            if field in query and (not isinstance(query[field], str) or not query[field] or query[field] != query[field].strip() or len(query[field]) > 100 or any(ord(c) < 32 for c in query[field])):
                raise AiError("必须提供精确来源身份")
        try:
            comparison_periods(query["startDate"], query["endDate"])
        except AnalysisContractError as error:
            raise AiError(str(error)) from error
        if not isinstance(query.get("window", "current"), str) or query.get("window", "current") not in {"current", "previous", "yearAgo"}:
            raise AiError("比较窗口无效")
        if source["domain"] == "netshop":
            from netshop.analysis import SOURCES
            if not isinstance(query.get("dataset"), str) or query["dataset"] not in SOURCES or query["platform"] not in SOURCES[query["dataset"]]:
                raise AiError("网店来源组合无效")
        elif source["domain"] == "market":
            from market.analysis import validate
            from market.errors import MarketApiError
            try:
                validate({"operation": "analysis_records", **query})
            except MarketApiError as error:
                raise AiError(str(error)) from error
        signature = digest({"domain": source["domain"], "query": {"window": "current", **query}})
        if signature in queries:
            raise AiError("不得重复声明同一来源查询")
        queries.add(signature)
    request = None
    if "analysisRequest" in body:
        from .business_planning import analysis_request, validate_request_sources
        request = analysis_request(body["analysisRequest"])
        validate_request_sources(request, sources)
        # Existing evidence requests preserve their original admission contract.
        # New workbench requests reserve the exact 45-character generated ID.
        passive({"evidenceRunId": "evidence-"+"0"*36, "question": request["question"], "sources": sources}, 8000)
    plan = passive({"schemaVersion": "business-evidence-v1", "sources": sources,
        **({"analysisRequest": request} if request is not None else {}),
        **({"autoCollect": True} if automatic else {}),
        **({"collector": {"version": 1, "surface": "business_collection", "pageSize": 100}} if mode == "bulk" else {})}, 16000)
    identity = digest(plan)
    with mutation(principal):
        old = m.AiBusinessEvidenceRun.objects.filter(owner_email=principal.email.lower(), client_request_id=client).first()
        if old:
            if old.request_digest != identity:
                raise AiError("请求标识对应的分析范围已变化", "conflict", 409)
            return {"item": mapping(old), "replayed": True}
        if m.AiBusinessEvidenceRun.objects.filter(owner_email=principal.email.lower(), status="collecting").count() >= 4:
            raise AiError("未完成证据任务已达到上限", "rate_limited", 429)
        if m.AiBusinessEvidenceRun.objects.count() >= 10000:
            raise AiError("证据任务存储容量已满", "rate_limited", 429)
        row = m.AiBusinessEvidenceRun.objects.create(id=uid("evidence"), owner_email=principal.email.lower(),
            client_request_id=client, request_digest=identity, plan_json=canonical(plan), collection_status="queued" if automatic else "manual")
    return {"item": mapping(row), "replayed": False}


def collect(run_id, body, principal, request_id, *, commit=None):
    fields(body, {"sourceKey", "expectedVersion"}, {"sourceKey", "expectedVersion"})
    row = get_run(run_id, principal)
    if is_v2(row):
        return _collect_v2(row, body, principal, request_id, commit=commit)
    cas(row, body["expectedVersion"])
    if row.status != "collecting":
        raise AiError("任务已结束", "conflict", 409)
    plan = json.loads(row.plan_json)
    source = next((s for s in plan["sources"] if s["key"] == body["sourceKey"]), None)
    if source is None:
        raise AiError("来源不存在", "not_found", 404)
    state = json.loads(row.state_json)
    entry = state.get(source["key"])
    verifier = _restore(entry["verifier"] if entry else {})
    if verifier.finished:
        raise AiError("来源已完整收集", "conflict", 409)
    collector = plan.get("collector")
    if collector is not None and collector != {"version": 1, "surface": "business_collection", "pageSize": 100}:
        raise AiError("采集器版本不支持", "conflict", 409)
    surface = "business_collection" if collector else "ai_agent"
    entries = transport.catalog(principal, surface)
    tool = "get_business_source_page" if collector else TOOLS[source["domain"]]
    names = {e["name"] for e in entries if e.get("risk") == "read_only" and e.get("execution", {}).get("mode") == "direct"}
    if not {tool, "get_data_freshness"} <= names:
        raise AiError("来源工具或水位查询不可用", "access_denied", 403)
    def execute(name, args):
        return _result(transport.execute_tool(name, args, principal, surface=surface, request_id=request_id, policy_digest=digest(entries)), name)
    with transport.request_budget(30):
        freshness = execute("get_data_freshness", {}) if entry is None else None
        page = execute(tool, {**source["query"], "limit": 100 if collector else 10,
            **({"domain": source["domain"]} if collector else {}), **({"cursor": verifier.expected_cursor} if verifier.expected_cursor else {})})
    try:
        filters = page["filters"]
        query = source["query"]
        expected = {key: value for key, value in query.items() if key not in {"startDate", "endDate"}}
        expected.setdefault("window", "current")
        if any(filters.get(key) != value for key, value in expected.items()) or filters.get("periods") != comparison_periods(query["startDate"], query["endDate"]):
            raise AnalysisContractError("来源未回显精确筛选范围")
        if not isinstance(page.get("sourceRevision"), str) or not 1 <= len(page["sourceRevision"]) <= 128:
            raise AnalysisContractError("来源版本无效")
        verifier.consume(page, request_cursor=verifier.expected_cursor)
        if verifier.finished:
            verifier.result()
    except (AnalysisContractError, KeyError, TypeError, ValueError) as error:
        raise AiError("来源页未通过完整性核验", "conflict", 409) from error
    encoded = canonical(passive(page, 131072))
    size = len(encoded.encode())
    with mutation(principal):
        row = get_run(run_id, principal)
        cas(row, body["expectedVersion"])
        if row.status != "collecting":
            raise AiError("任务已结束", "conflict", 409)
        state = json.loads(row.state_json)
        if sum(value["pageCount"] for value in state.values()) >= MAX_PAGES or row.stored_bytes + size > MAX_BYTES:
            raise AiError("证据容量已满；保留现有检查点，不得截断后完成", "payload_too_large", 413)
        if m.AiBusinessEvidenceSource.objects.exists():
            store.check_quota(principal, size, MAX_BYTES)
        else:
            owner_bytes = m.AiBusinessEvidenceRun.objects.filter(owner_email=principal.email.lower()).aggregate(n=Sum("stored_bytes"))["n"] or 0
            global_bytes = m.AiBusinessEvidenceRun.objects.aggregate(n=Sum("stored_bytes"))["n"] or 0
            if owner_bytes + size > MAX_BYTES * 4 or global_bytes + size > MAX_BYTES * 32:
                raise AiError("共享证据存储额度已满", "payload_too_large", 413)
        sequence = state.get(source["key"], {}).get("pageCount", 0) + 1
        m.AiBusinessEvidenceChunk.objects.create(id=uid("evidence-chunk"), run=row, source_key=source["key"], sequence=sequence,
            payload_json=encoded, payload_digest=digest(encoded))
        metadata = entry["metadata"] if entry else {"sourceRevision": page.get("sourceRevision"), "coverage": page.get("coverage"),
            "excludedOverlappingPeriodRows": page.get("excludedOverlappingPeriodRows"),
            "identityCheck": page.get("identityCheck"),
            "availableDates": page.get("availableDates"), "metricSemantics": page.get("metricSemantics"), "freshness": freshness,
            "firstCollectedAt": timezone.now().isoformat()}
        metadata["lastCollectedAt"] = timezone.now().isoformat()
        state[source["key"]] = {"pageCount": sequence, "verifier": verifier.__dict__, "metadata": metadata}
        row.state_json = canonical(passive(state, 65536))
        row.stored_bytes += size
        row.version += 1
        row.save(update_fields=["state_json", "stored_bytes", "version"])
        if commit:
            return commit({"item": mapping(row)}, 200)
    return {"item": mapping(row)}


def finish(run_id, body, principal):
    fields(body, {"expectedVersion", "action"}, {"expectedVersion", "action"})
    if not isinstance(body["action"], str) or body["action"] not in {"seal", "cancel"}:
        raise AiError("结束动作无效")
    with mutation(principal):
        row = get_run(run_id, principal)
        cas(row, body["expectedVersion"])
        if row.status != "collecting":
            raise AiError("任务已结束", "conflict", 409)
        if is_v2(row):
            row = authorize_owner(m.AiBusinessEvidenceRun.objects.select_for_update().get(pk=row.id), principal)
            cas(row, body["expectedVersion"])
            if body["action"] == "seal":
                sealed = canonical(store.seal_value(row, target_version=row.version+1))
                store.check_quota(principal, len(sealed.encode())-len(row.state_json.encode()), MAX_BYTES)
                row.state_json = sealed
            row.status = "sealed" if body["action"] == "seal" else "cancelled"
            row.version += 1
            row.save(update_fields=["status", "version", "state_json"])
            return {"item": mapping(row)}
        if body["action"] == "seal":
            state = json.loads(row.state_json)
            if len(state) != len(json.loads(row.plan_json)["sources"]) or any(not value["verifier"]["finished"] for value in state.values()):
                raise AiError("来源尚未全部核对完成", "conflict", 409)
            for value in state.values():
                _restore(value["verifier"]).result()
        row.status = "sealed" if body["action"] == "seal" else "cancelled"
        row.version += 1
        row.save(update_fields=["status", "version"])
    return {"item": mapping(row)}


def chunk(run_id, source_key, params, principal):
    get_run(run_id, principal)
    fields(params, {"sequence", "rowOffset", "rowLimit"}, {"sequence"})
    try:
        sequence = int(params["sequence"])
    except (TypeError, ValueError) as error:
        raise AiError("分块序号无效") from error
    integer(sequence, "sequence", hi=MAX_PAGES)
    record = m.AiBusinessEvidenceChunk.objects.filter(run_id=run_id, source_key=source_key, sequence=sequence).first()
    if not record:
        raise AiError("分块不存在", "not_found", 404)
    if digest(record.payload_json) != record.payload_digest:
        raise AiError("分块摘要不匹配", "conflict", 409)
    page = json.loads(record.payload_json)
    if "rowOffset" in params or "rowLimit" in params:
        try:
            offset, limit = int(params.get("rowOffset", 0)), int(params.get("rowLimit", 10))
        except (TypeError, ValueError) as error:
            raise AiError("分块行分页无效") from error
        integer(offset, "rowOffset", lo=0, hi=100)
        integer(limit, "rowLimit", hi=10)
        rows = page["items"]
        if offset > len(rows):
            raise AiError("分块行偏移超出范围")
        end = min(offset + limit, len(rows))
        return {"schemaVersion": "business-evidence-slice-v1", "sourceKey": source_key, "sequence": sequence,
            "payloadDigest": record.payload_digest, "fullPageEvidence": page["pageEvidence"],
            "sourceMetadata": {k: v for k, v in page.items() if k not in {"items", "pageEvidence", "pagination"}},
            "sourcePagination": page["pagination"], "items": rows[offset:end],
            "rowPagination": {"offset": offset, "limit": limit, "total": len(rows), "hasMore": end < len(rows), "nextOffset": end if end < len(rows) else None},
            "completeChunkInResponse": offset == 0 and end == len(rows),
            "meaning": "此响应只是不可变分块的行切片；完整页摘要不等于切片摘要，不能把切片当全量来源核对。"}
    return {"sourceKey": source_key, "sequence": sequence, "payloadDigest": record.payload_digest, "page": page}


def reconcile_products(run_id, params, principal):
    from business_analysis.identity import product_reconciliation
    row = get_run(run_id, principal)
    fields(params, {"sales", "master"}, {"sales", "master"})
    if row.status != "sealed":
        raise AiError("只有封存的完整证据可以关联", "conflict", 409)
    if is_v2(row):
        store.verify_seal(row)
        state = {key: store.source_state(row, key) for key in (params["sales"], params["master"])}
    else:
        state = json.loads(row.state_json)
    for key in (params["sales"], params["master"]):
        if key not in state or state[key]["verifier"]["rows"] > 5000:
            raise AiError("来源不存在或超过当前 5000 行关联容量", "payload_too_large", 413)
    def pages(key):
        expected = 1
        for record in m.AiBusinessEvidenceChunk.objects.filter(run=row, source_key=key).order_by("sequence").iterator(chunk_size=20):
            if record.sequence != expected or digest(record.payload_json) != record.payload_digest:
                raise AnalysisContractError("分块缺失或摘要不匹配")
            expected += 1
            yield json.loads(record.payload_json)
        if expected - 1 != state[key]["pageCount"]:
            raise AnalysisContractError("持久分块数量不一致")
    try:
        result = product_reconciliation(pages(params["sales"]), pages(params["master"]))
        for kind in ("sales", "master"):
            if result["sources"][kind] != _restore(state[params[kind]]["verifier"]).result():
                raise AnalysisContractError("关联证据与封存状态不一致")
    except (AnalysisContractError, KeyError, TypeError, ValueError) as error:
        raise AiError("持久证据未通过关联核验", "conflict", 409) from error
    if len(canonical(result).encode()) > 1500000:
        raise AiError("关联结果超过单次交付容量，须分区处理", "payload_too_large", 413)
    return result


def analysis_table(run_id, params, principal):
    from business_analysis.results import build_table
    from business_analysis.partitioned import MAX_RESULT_GROUPS
    fields(params, {"sourceKey", "dimension", "baselineKey", "offset", "limit"}, {"sourceKey", "dimension"})
    row = get_run(run_id, principal)
    if row.status != "sealed":
        raise AiError("分析表需要已封存的完整证据", "conflict", 409)
    keys = [params["sourceKey"]] + ([params["baselineKey"]] if "baselineKey" in params else [])
    if is_v2(row):
        store.verify_seal(row)
        state = {key: store.source_state(row, key) for key in keys}
    else:
        state = json.loads(row.state_json)
    if any(key not in state for key in keys):
        raise AiError("分析来源不存在", "not_found", 404)
    try:
        offset, limit = int(params.get("offset", "0")), int(params.get("limit", "20"))
        integer(offset, "offset", lo=0, hi=MAX_RESULT_GROUPS)
        integer(limit, "limit", hi=100)
    except (ValueError, TypeError) as error:
        raise AiError("分析分页参数无效") from error
    def pages(key):
        sequence = 0
        for record in m.AiBusinessEvidenceChunk.objects.filter(run=row, source_key=key).order_by("sequence").iterator(chunk_size=20):
            sequence += 1
            if sequence != record.sequence or digest(record.payload_json) != record.payload_digest:
                raise AnalysisContractError("证据块缺失或摘要变化")
            yield json.loads(record.payload_json)
        if sequence != state[key]["pageCount"]:
            raise AnalysisContractError("证据块数量变化")
    expected = {key: _restore(state[key]["verifier"]).result() for key in keys}
    try:
        table = build_table(pages(keys[0]), params["dimension"], expected[keys[0]], offset=offset, limit=limit,
            **({"baseline_pages": pages(keys[1]), "baseline_expected": expected[keys[1]]} if len(keys) == 2 else {}))
    except (AnalysisContractError, KeyError, TypeError, ValueError) as error:
        raise AiError(str(error), "conflict", 409) from error
    table.update(evidenceRunId=run_id, sourceKey=keys[0], baselineKey=keys[1] if len(keys) == 2 else None,
        pagination={"offset": offset, "limit": limit, "hasMore": offset+len(table["rows"]) < table["total"]})
    if len(canonical(table).encode()) > 1500000:
        raise AiError("分析页过大，请减小页长", "payload_too_large", 413)
    return table


def _promotion_choices(sources):
    """Exact sealed-directory choices; creation will revalidate the roots again."""
    def eligible(source):
        query = source["query"]
        return (source["domain"] == "netshop" and query.get("platform") == "京东"
            and query.get("dataset") == "promotion" and bool(query.get("shop")))

    options = []
    for source in sources:
        if not eligible(source) or source["query"].get("window") != "current":
            continue
        query = source["query"]
        baselines = [{"sourceKey": other["key"], "window": other["query"]["window"]}
            for other in sources if other["key"] != source["key"] and eligible(other)
            and other["query"].get("window") in ("previous", "yearAgo")
            and {key: value for key, value in other["query"].items() if key != "window"}
                == {key: value for key, value in query.items() if key != "window"}]
        baselines.sort(key=lambda value: (0 if value["window"] == "previous" else 1, value["sourceKey"]))
        options.append({"sourceKey": source["key"], "platform": "京东", "shop": query["shop"],
            "startDate": query["startDate"], "endDate": query["endDate"],
            "baselineChoices": baselines})
    return options


def _mapping_v2(row):
    sources = store.catalog(row)
    plan = json.loads(row.plan_json)
    result = {"id": row.id, "status": row.status, "version": row.version, "storedBytes": row.stored_bytes,
        "collection": {"status": row.collection_status if row.status == "collecting" else row.status,
            "nextAttemptAt": row.next_collect_at.isoformat(), "consecutiveFailures": row.collection_failures,
            "errorCode": row.collection_error_code}, "createdAt": row.created_at.isoformat(),
        "plan": plan, "sources": store.compact_sources(row), "progress": store.progress(row),
        "sourceDirectory": {"path": f"/api/ai/business-evidence/{row.id}/sources", "paginated": True},
        "consistency": "immutable_collected_source_versions_not_cross_domain_atomic_snapshot",
        "modelAnalysisCompleted": False, "reportGenerationSupported": True,
        "workbenchAnalysisEnabled": True, "fileGenerationSupported": True, "budgetSupported": True, "mappingSupported": True,
        "screeningSupported": True,
        **({"analysisRequestMeaning": "requested_only_not_source_availability_or_dimension_coverage"}
            if "analysisRequest" in plan else {})}
    if row.status == "sealed":
        store.verify_seal(row)
        result["seal"] = json.loads(row.state_json)
    choices = (_promotion_choices(sources) if row.status == "sealed"
        and getattr(settings, "AI_PROMOTION_AGENT_RUNTIME_ENABLED", False) is True
        and bool(plan.get("analysisRequest", {}).get("question")) else [])
    result["promotionSupported"] = bool(choices)
    result["promotionChoices"] = choices
    store.assert_current(row)
    return result


def _create_v2(body, principal):
    from business_analysis.evidence_v2 import build_catalog
    current_principal(principal, admin=True)
    fields(body, {"schemaVersion", "clientRequestId", "sources", "collectionMode", "autoCollect", "analysisRequest", "expectedPrincipalKey"},
        {"schemaVersion", "clientRequestId", "sources"})
    if "expectedPrincipalKey" in body and body["expectedPrincipalKey"] != principal_key(principal):
        raise AiError("当前账号已变化，请重新确认分析范围", "access_denied", 403)
    if body.get("collectionMode", "bulk") != "bulk":
        raise AiError("v2目录只支持bulk采集")
    automatic = bool(boolean(body.get("autoCollect", False), "autoCollect"))
    client = identifier(body["clientRequestId"])
    try:
        built = build_catalog(body["sources"], analysis_request=body.get("analysisRequest"))
    except (AnalysisContractError, ValueError, TypeError) as error:
        raise AiError(str(error)) from error
    if "analysisRequest" in body and body["analysisRequest"] is None:
        raise AiError("分析请求元信息不能为null")
    # Pure contracts do not replace the owning reader's real supported surface.
    from netshop.analysis import SOURCES
    from market.analysis import validate
    from market.errors import MarketApiError
    for source in built["entries"]:
        query = source["query"]
        if source["domain"] == "netshop" and query["platform"] not in SOURCES.get(query["dataset"], ()):
            raise AiError("网店来源组合无效")
        if source["domain"] == "market":
            try:
                validate({"operation": "analysis_records", **query})
            except MarketApiError as error:
                raise AiError(str(error)) from error
    identity = digest({"schemaVersion": "business-evidence-create-v2", "header": built["header"], "autoCollect": automatic})
    with mutation(principal):
        old = m.AiBusinessEvidenceRun.objects.filter(owner_email=principal.email.lower(), client_request_id=client).first()
        if old:
            authorize_owner(old, principal)
            if old.request_digest != identity:
                raise AiError("请求标识对应的分析范围已变化", "conflict", 409)
            return {"item": mapping(old), "replayed": True}
        if m.AiBusinessEvidenceRun.objects.filter(owner_email=principal.email.lower(), status="collecting").count() >= 4:
            raise AiError("未完成证据任务已达到上限", "rate_limited", 429)
        if m.AiBusinessEvidenceRun.objects.count() >= 10000:
            raise AiError("证据任务存储容量已满", "rate_limited", 429)
        new_bytes = len(canonical(built["header"]).encode())+2+sum(len(canonical(s["query"]).encode())+2 for s in built["entries"])
        store.check_quota(principal, new_bytes, MAX_BYTES)
        row = m.AiBusinessEvidenceRun.objects.create(id=uid("evidence"), owner_email=principal.email.lower(),
            client_request_id=client, request_digest=identity, plan_json=canonical(built["header"]),
            collection_status="queued" if automatic else "manual")
        for entry in built["entries"]:
            m.AiBusinessEvidenceSource.objects.create(id=uid("source"), run=row, source_key=entry["key"],
                ordinal=entry["ordinal"], domain=entry["domain"], query_json=canonical(entry["query"]), query_digest=entry["queryDigest"])
        return {"item": mapping(row), "replayed": False}


def _page_number(params, name, default, maximum, *, minimum=0):
    value = params.get(name, str(default))
    if type(value) is not str or not 1 <= len(value) <= 4 or not value.isascii() or not value.isdigit():
        raise AiError("目录分页参数无效")
    return integer(int(value), name, lo=minimum, hi=maximum)


def directory(run_id, params, principal):
    from business_analysis.evidence_v2 import directory_page
    row = get_run(run_id, principal)
    fields(params, {"offset", "limit"})
    if not is_v2(row):
        raise AiError("此任务没有v2来源目录", "conflict", 409)
    sources = store.catalog(row)
    offset = _page_number(params, "offset", 0, len(sources)-1)
    limit = _page_number(params, "limit", 10, 20, minimum=1)
    try:
        result = directory_page(sources, run_id=row.id, evidence_version=row.version, offset=offset, limit=limit,
            analysis_request=json.loads(row.plan_json).get("analysisRequest"))
    except AnalysisContractError as error:
        raise AiError(str(error), "conflict", 409) from error
    store.assert_current(row)
    return result


def source_detail(run_id, source_key, principal):
    row = get_run(run_id, principal)
    if not is_v2(row):
        raise AiError("此任务没有v2来源目录", "conflict", 409)
    store.catalog(row)
    source = store.source_record(row, identifier(source_key))
    value = store.checkpoint(source)
    result = {"runId": row.id, "evidenceVersion": row.version, "sourceKey": source.source_key,
        "version": source.version, "pageCount": source.page_count, "rowCount": source.row_count,
        "storedBytes": source.stored_bytes, "complete": source.finished,
        "metadata": value["metadata"] if value else None,
        "sourceRef": value["verifier"]["source_ref"] if value else None,
        "reconciliation": _restore(value["verifier"]).result() if value and source.finished else None}
    store.assert_current(row)
    return result


def _collect_v2(initial, body, principal, request_id, *, commit=None):
    from . import business_collection_continuation as continuation
    cas(initial, body["expectedVersion"])
    if initial.status != "collecting":
        raise AiError("任务已结束", "conflict", 409)
    sources = store.catalog(initial)
    source = next((s for s in sources if s["key"] == body["sourceKey"]), None)
    if source is None:
        raise AiError("来源不存在", "not_found", 404)
    initial_source = store.source_record(initial, source["key"])
    entry = store.checkpoint(initial_source)
    verifier = _restore(entry["verifier"] if entry else {})
    if verifier.finished:
        raise AiError("来源已完整收集", "conflict", 409)
    entries = transport.catalog(principal, "business_collection")
    names = {e["name"] for e in entries if e.get("risk") == "read_only" and e.get("execution", {}).get("mode") == "direct"}
    if not {"get_business_source_page", "get_data_freshness"} <= names:
        raise AiError("来源工具或水位查询不可用", "access_denied", 403)
    continued = None
    if source["domain"] in continuation.TOOLS and entry is not None:
        if continuation.TOOLS[source["domain"]] not in names:
            raise AiError("来源续读工具不可用，请等待兼容版本就绪", "access_denied", 403)
        continued = continuation.prepare(initial, source, initial_source, principal)
    def execute(name, args):
        return _result(transport.execute_tool(name, args, principal, surface="business_collection",
            request_id=request_id, policy_digest=digest(entries)), name)
    with transport.request_budget(30):
        freshness = execute("get_data_freshness", {}) if entry is None else None
        page = execute(continued.tool, continued.arguments()) if continued else execute("get_business_source_page", {
            **source["query"], "domain": source["domain"], "limit": 100,
            **({"cursor": verifier.expected_cursor} if verifier.expected_cursor else {})})
    if continued:
        continuation.check(continued, principal)
        if (not isinstance(page, dict) or page.get("sourceRevision") != continued.arguments()["expectedRevision"]
                or page.get("sourceRef") != continued.arguments()["expectedSourceRef"]):
            raise AiError("续读来源版本已变化", "conflict", 409)
    try:
        expected = {k: v for k, v in source["query"].items() if k not in {"startDate", "endDate"}}
        if any(page["filters"].get(k) != v for k, v in expected.items()) or page["filters"].get("periods") != comparison_periods(source["query"]["startDate"], source["query"]["endDate"]):
            raise AnalysisContractError("来源未回显精确筛选范围")
        if not isinstance(page.get("sourceRevision"), str) or not 1 <= len(page["sourceRevision"]) <= 128:
            raise AnalysisContractError("来源版本无效")
        verifier.consume(page, request_cursor=verifier.expected_cursor)
        if verifier.finished:
            verifier.result()
    except (AnalysisContractError, KeyError, TypeError, ValueError) as error:
        raise AiError("来源页未通过完整性核验", "conflict", 409) from error
    encoded = canonical(passive(page, 131072))
    size = len(encoded.encode())
    metadata = entry["metadata"] if entry else {"sourceRevision": page["sourceRevision"], "coverage": page.get("coverage"),
        "excludedOverlappingPeriodRows": page.get("excludedOverlappingPeriodRows"), "identityCheck": page.get("identityCheck"),
        "availableDates": page.get("availableDates"), "metricSemantics": page.get("metricSemantics"),
        "freshness": freshness, "firstCollectedAt": timezone.now().isoformat()}
    metadata["lastCollectedAt"] = timezone.now().isoformat()
    checkpoint = canonical(passive({"pageCount": initial_source.page_count+1, "verifier": verifier.__dict__, "metadata": metadata}, 32768))
    with mutation(principal):
        current_principal(principal, admin=True)
        row = authorize_owner(m.AiBusinessEvidenceRun.objects.select_for_update().get(pk=initial.id), principal)
        cas(row, body["expectedVersion"])
        if row.status != "collecting":
            raise AiError("任务已结束", "conflict", 409)
        record = store.source_record(row, source["key"], lock=True)
        if record.version != initial_source.version or record.checkpoint_json != initial_source.checkpoint_json:
            raise AiError("来源检查点已变化", "version_conflict", 409)
        if continued:
            continuation.check(continued, principal)
        if store.progress(row)["pageCount"] >= MAX_PAGES or row.stored_bytes+size > MAX_BYTES:
            raise AiError("证据容量已满；保留现有检查点，不得截断后完成", "payload_too_large", 413)
        store.check_quota(principal, size+len(checkpoint.encode())-len(record.checkpoint_json.encode()), MAX_BYTES)
        # Insert before marking finished; the DB rejects pages for finished sources.
        m.AiBusinessEvidenceChunk.objects.create(id=uid("evidence-chunk"), run=row, source_key=record.source_key,
            sequence=record.page_count+1, payload_json=encoded, payload_digest=digest(encoded))
        record.page_count += 1
        record.stored_bytes += size
        record.row_count = verifier.rows
        record.finished = verifier.finished
        record.checkpoint_json = checkpoint
        record.checkpoint_run_version = row.version+1
        record.version += 1
        record.updated_at = timezone.now()
        record.save(update_fields=["page_count", "stored_bytes", "row_count", "finished", "checkpoint_json", "checkpoint_run_version", "version", "updated_at"])
        row.stored_bytes += size
        row.version += 1
        row.save(update_fields=["stored_bytes", "version"])
        if continued:
            continuation.check_actor(continued, principal)
        if commit:
            return commit({"item": mapping(row)}, 200)
        return {"item": mapping(row)}
