"""Internal successful screening publication; no runtime or public endpoint."""
import json

from django.db.models import Count, Sum
from business_analysis import screening_storage as contract
from business_analysis.contracts import AnalysisContractError
from . import business_diagnostic_screening as screening, models as m
from .control_models import AiMutationAudit
from .policy import AiError, authorize_owner, canonical, current_principal, digest, identifier, mutation, revision, uid


def _call(function, *args):
    try:
        return function(*args)
    except (AnalysisContractError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as error:
        raise AiError("筛查持久结果未通过完整核验", "conflict", 409) from error


def _reference(row):
    return {"schemaVersion":"business-screening-storage-reference-v1", "id":row.id, "reportId":row.report_id,
        "bindingDigest":row.binding_digest, "selectionPlanDigest":row.selection_plan_digest,
        "resultDigest":row.service_result_digest, "contentRootDigest":row.content_root_digest,
        "manifestDigest":row.manifest_digest}


def _record(page):
    return {"sequence":page.sequence, "kind":page.kind, "partitionKey":page.partition_key,
        "offset":page.offset, "returned":page.returned, "total":page.total, "nextOffset":page.next_offset,
        "payloadJson":page.payload_json, "payloadDigest":page.payload_digest}


def _loaded(run_id, principal):
    current_principal(principal, admin=True)
    row = m.AiBusinessScreeningRun.objects.filter(pk=identifier(run_id)).first()
    if row is None: raise AiError("固定筛查结果不存在", "not_found", 404)
    authorize_owner(row, principal)
    binding, manifest = _call(contract.manifest, row.binding_json, row.manifest_json)
    actual = screening._load(row.report_id, principal)
    plan = screening._describe(actual)["plan"]
    from . import business_screening_runtime, business_promotion_runtime_contract as promotion
    if binding["executionProfile"] in (business_screening_runtime.PROFILE, promotion.PROFILE):
        report = m.AiReportRun.objects.get(pk=row.report_id)
        if binding["executionProfile"] == business_screening_runtime.PROFILE:
            _, snapshot, _, _, _, _ = business_screening_runtime.bound(report,principal)
        else:
            snapshot = json.loads(report.snapshot_json)
            if canonical(actual[0]) != row.binding_json:
                raise AiError("词货筛查记录的报告绑定不一致", "conflict", 409)
        intent = snapshot["screeningIntent"]
        if (row.id != intent["id"] or row.selection_plan_digest != intent["selectionPlanDigest"]
                or row.algorithm_version != intent["algorithmVersion"]
                or row.selection_policy != intent["selectionPolicy"]
                or row.capacity_profile != intent["capacityPolicy"]):
            raise AiError("固定筛查记录不属于报告的预分配意图", "conflict", 409)
    if (canonical(actual[0]) != row.binding_json or row.binding_digest != digest(row.binding_json)
            or row.manifest_digest != digest(row.manifest_json) or row.owner_email != binding["ownerEmail"]
            or row.scope_json != canonical(binding["scope"]) or row.evidence_id != binding["evidenceRunId"]
            or row.report_id != binding["reportId"] or row.storage_schema != contract.SCHEMA
            or row.capacity_profile != contract.CAPACITY_PROFILE or not plan["canScreen"]
            or plan["planDigest"] != row.selection_plan_digest
            or any(getattr(row, field) != manifest[key] for field,key in (
                ("binding_digest","bindingDigest"), ("selection_plan_digest","selectionPlanDigest"),
                ("pure_result_digest","pureResultDigest"), ("service_result_digest","serviceResultDigest"),
                ("content_root_digest","contentRootDigest"), ("algorithm_version","algorithmVersion"),
                ("selection_policy","selectionPolicy"), ("page_count","pageCount")))):
        raise AiError("固定筛查来源或协议已变化", "conflict", 409)
    if not 1 <= row.stored_bytes <= contract.MAX_RUN_BYTES:
        raise AiError("固定筛查容量记录无效", "conflict", 409)
    return row, binding, manifest


def _all(row):
    pages = list(m.AiBusinessScreeningPage.objects.filter(run=row).order_by("sequence")[:contract.MAX_PAGES+1])
    return {"bindingJson":row.binding_json, "manifestJson":row.manifest_json,
        "pages":[_record(page) for page in pages], "storedBytes":row.stored_bytes}


def _quota(owner, added):
    rows = m.AiBusinessScreeningRun.objects
    for query, byte_limit, count_limit in ((rows.all(),contract.GLOBAL_BYTES,contract.GLOBAL_ROWS),
            (rows.filter(owner_email=owner),contract.OWNER_BYTES,contract.OWNER_ROWS)):
        used = query.aggregate(bytes=Sum("stored_bytes"), count=Count("id"))
        if (used["bytes"] or 0)+added > byte_limit or used["count"]+1 > count_limit:
            raise AiError("固定筛查存储额度不足，历史结果仍计入额度", "rate_limited", 429)


def publish(verified, principal, *, before_write=None):
    """Only accept this process's completed object; atomically publish all pages."""
    current_principal(principal, admin=True, write=True)
    binding, value = screening._verified(verified, principal)
    bundle = _call(contract.materialize, value)
    fixed = _call(contract.validate, bundle)
    with mutation(principal):
        # Internal lifecycle CAS runs after materialization, while holding the
        # same write lock as publication. Never expose this callback in an API.
        if before_write is not None:
            before_write()
        screening._revalidate(binding, principal)
        # The fixed selector itself is metadata-only, but its current version
        # must still agree with the prepared result at publication time.
        actual_plan = screening._describe(screening._load(binding["reportId"],principal))["plan"]
        if not actual_plan["canScreen"] or actual_plan["planDigest"] != fixed["selectionPlanDigest"]:
            raise AiError("筛查计划已变化，不能发布旧结果", "conflict", 409)
        old = m.AiBusinessScreeningRun.objects.filter(report_id=binding["reportId"],
            binding_digest=fixed["bindingDigest"], selection_plan_digest=fixed["selectionPlanDigest"],
            storage_schema=contract.SCHEMA).first()
        if old is not None:
            saved, _, _ = _loaded(old.id, principal)
            saved_bundle = _all(saved)
            _call(contract.validate, saved_bundle)
            if canonical(saved_bundle) != canonical(bundle):
                raise AiError("同一固定筛查已有不同结果，禁止覆盖", "conflict", 409)
            screening._revalidate(binding, principal)
            return {"reference":_reference(saved), "replayed":True}
        _quota(principal.email.lower(),bundle["storedBytes"])
        from . import business_screening_runtime, business_promotion_runtime_contract as promotion
        screen_id = uid("screening")
        if binding["executionProfile"] in (business_screening_runtime.PROFILE, promotion.PROFILE):
            report = m.AiReportRun.objects.get(pk=binding["reportId"])
            if binding["executionProfile"] == business_screening_runtime.PROFILE:
                _, snapshot, _, _, _, _ = business_screening_runtime.bound(report,principal)
            else:
                loaded = screening._load(report.id, principal)
                snapshot = json.loads(report.snapshot_json)
                if canonical(loaded[0]) != canonical(binding):
                    raise AiError("词货筛查发布的报告绑定不一致", "conflict", 409)
            intent = snapshot["screeningIntent"]
            if (intent["selectionPlanDigest"] != fixed["selectionPlanDigest"]
                    or intent["algorithmVersion"] != fixed["algorithmVersion"]
                    or intent["selectionPolicy"] != fixed["selectionPolicy"]):
                raise AiError("筛查结果不属于预分配的固定意图", "conflict", 409)
            screen_id = intent["id"]
        row = m.AiBusinessScreeningRun.objects.create(id=screen_id,report_id=binding["reportId"],
            evidence_id=binding["evidenceRunId"], owner_email=principal.email.lower(),scope_json=canonical(principal.scope),
            binding_json=bundle["bindingJson"],binding_digest=fixed["bindingDigest"],
            manifest_json=bundle["manifestJson"],manifest_digest=digest(bundle["manifestJson"]),
            selection_plan_digest=fixed["selectionPlanDigest"],pure_result_digest=fixed["pureResultDigest"],
            service_result_digest=fixed["serviceResultDigest"],content_root_digest=fixed["contentRootDigest"],
            algorithm_version=fixed["algorithmVersion"],selection_policy=fixed["selectionPolicy"],
            storage_schema=contract.SCHEMA,capacity_profile=contract.CAPACITY_PROFILE,
            page_count=fixed["pageCount"],stored_bytes=bundle["storedBytes"])
        m.AiBusinessScreeningPage.objects.bulk_create([m.AiBusinessScreeningPage(id=uid("screen-page"),run=row,
            sequence=p["sequence"],kind=p["kind"],partition_key=p["partitionKey"],offset=p["offset"],returned=p["returned"],
            total=p["total"],next_offset=p["nextOffset"],payload_json=p["payloadJson"],payload_digest=p["payloadDigest"])
            for p in bundle["pages"]],batch_size=50)
        screening._revalidate(binding, principal)
        AiMutationAudit.objects.create(request_id=uid("screen-audit"),actor_email=principal.email.lower(),actor_role=principal.role,
            action="business_screening_published",scope_digest=digest([principal.scope,row.id]),
            response_digest=digest(_reference(row)),revision=int(revision())+1)
    return {"reference":_reference(row), "replayed":False}


def describe(run_id, principal):
    row, binding, manifest = _loaded(run_id,principal)
    value = {"reference":_reference(row),"manifest":manifest,"storedBytes":row.stored_bytes}
    screening._revalidate(binding,principal)
    return value


def _read(run_id, principal, kind, partition_key, offset):
    if type(offset) is not int or not 0 <= offset <= contract.MAX_PAGES*contract.PAGE_SIZE:
        raise AiError("筛查固定页偏移无效")
    row, binding, manifest = _loaded(run_id,principal)
    group = next((g for g in manifest["groups"] if (g["kind"],g["partitionKey"]) == (kind,partition_key)),None)
    if group is None: raise AiError("筛查固定分区不存在", "not_found", 404)
    page = m.AiBusinessScreeningPage.objects.filter(run=row,kind=kind,partition_key=partition_key,offset=offset).first()
    if page is None:
        raise AiError("筛查固定页不存在，请使用返回的下一偏移", "not_found", 404)
    payload = _call(contract.validate_page,_record(page),manifest,binding)
    screening._revalidate(binding,principal)
    return payload


def read_coverage(run_id, principal, *, offset=0):
    return _read(run_id,principal,"coverage","",offset)


def read_candidates(run_id, principal, partition_key, *, offset=0):
    identifier(partition_key,"partitionKey")
    return _read(run_id,principal,"candidates",partition_key,offset)
