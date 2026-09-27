# 日经营来源与月度财报并列的 v3 纯计划

2026-09-24。本片只新增 `business_analysis.evidence_v3` 纯目录、测试与此文档。没有公共路由、模型工具、Django ORM、数据库迁移、证据持久化或报告能力；`reportGenerationSupported=false` 是固定计划字段。旧 `business-evidence-v1/v2`、已封存任务与文件字节未改变。

`build_catalog(trusted_sources, analysis_request=...)` 需要至少一个日来源和一个 `finance` 来源，总数最多48。日来源实际调用现有 v2 纯目录合同验证：sales/netshop/market 的精确查询、来源组合、同一原始日区间、`current/previous/yearAgo` 窗口完整性均保持现有含义。finance 查询严格为：

```json
{
  "months": ["2026-08", "2026-09"],
  "scope": {
    "scope_key": "精确账本身份",
    "scope_type": "shop",
    "scope_name": "精确账本名称",
    "group_name": "精确账本分组"
  },
  "analysisPeriod": {"startDate": "2026-08-20", "endDate": "2026-09-18"}
}
```

财务月份必须连续、按升序、1—24个月，显式包含日区间涉及的所有月份；`analysisPeriod` 与日来源**原始当期**起止日逐字一致。其余月份可供未来独立月度比较，但不取得日 `window` 身份。`scope` 四字段逐值匹配，不猜平台、组、店铺别名；其他字段、不可编码控制字符或单查询超过4096 UTF-8字节均拒绝。需要验证实际已发布的月份、跨组同名店的账本身份、资料完整性与费率口径，不能从目录 DTO 推断。

finance 目录项标记 `temporalRole=monthly_context` 并用既有财务纯合同给出 `periodAlignment`。分析日区间恰好等于所选完整自然月首末日才为 `exact_full_months`；例如近30天跨两月时为 `different_or_partial_months`。这是日期关系，**不是**当前店铺已经导入财报的证明。缺月须在未来 owning 证据中标记缺口，不把它算作0。计划 `financePolicy` 固定禁止按日分摊月账、累加源比率或合计与明细、反推 SKU 利润，以及未经核验将财报金额与ERP/店铺/市场相加或把同名店视为已匹配。源账本原比率与 `source_row_count` 的限制见 `AI_BUSINESS_FINANCE_EVIDENCE_CONTRACT.md`。

v3 顶层为 `business-evidence-v3`，目录页为 `business-evidence-directory-page-v3`；`collector.version=2` 仅是未来协议标识，此片并未注册采集器。每条来源查询不超过4096字节，全部查询不超过128 KiB，header不超过16,000字节，目录页不超过38,000字节、单页最多20项，最多48项。64 MiB/2,000页仅是未来共享事实配额，目录数量不提高它。header、目录、页均由规范 JSON 摘要固定，验证时从独立可信来源重建全部项；缺页、重排、自洽伪摘要和额外字段拒绝。`sourceAuthorityVerified`、`persistentEvidenceVerified`、`businessCoverageVerified`、`modelAnalysisCompleted` 与 `reportGenerationSupported` 均固定为 false。

后续持久化需另行解决：当前 `ai_assistant.0019_business_source_directory` 的 PostgreSQL 约束和守卫仅允许三类日来源，且 v2 `PageReconciler`、`Reader` 和筛查数据库守卫只懂日页。新增 v3 必须使用独立查询与财务检查点验证，并让历史 v1/v2 读法保持原样；任何数据库迁移需保留旧备份恢复。财务来源应在 `finance_reader` 内使用真实账号、完整 revision 与 month→batch 前后复验，持久证据要证明真实页链及完成状态。此次纯目录不能作为已授权、已收集、已封存或五 Agent 已读的结果。

测试：`PYTHONPATH=backend python -m unittest business_analysis.test_evidence_v3`，覆盖旧日来源规则、24月与跨月边界、scope隔离、重复来源、容量、目录全页以及伪造授权标志。真实店铺财报覆盖和日经营对账未在本片验证。
