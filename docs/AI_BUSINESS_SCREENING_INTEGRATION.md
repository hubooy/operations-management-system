# 封存证据全量筛查的内部服务接入设计

状态：**内部接入设计，第 33 批正在独立验收，未接工具或报告运行时**。第 31 批已有 `business_analysis/diagnostic_screening.py` 的未发布纯计算结果；第 33 批增加固定范围纯计划与 owning service 候选实现。不改旧报告、不开放模型调用、不把候选页当全部明细。2026-09-17 本计划子任务未运行 PostgreSQL 或正式服务。

## 1. 第一片服务只从固定报告进入

建议新增 `backend/ai_assistant/business_diagnostic_screening.py` 与 `test_business_diagnostic_screening.py`，复用现有表迭代器。不要改 `business_reports.graph/content`、`workflows`、旧工具定义、路由、迁移或文件协议。

建议内部 API：

```python
describe_for_report(report_id, principal) -> PlanPreview
prepare_for_report(report_id, principal) -> VerifiedScreening
coverage_page(verified, principal, *, offset=0) -> dict
candidate_page(verified, principal, partition_key, *, offset=0) -> dict
```

第一片仅接受真实已存在报告 ID；即使报告尚未运行模型，也必须已固定 sealed v2 证据。映射计划从该报告 snapshot 读取，可不存在，不能从请求参数临时替换。这样纯模块所需 reportId 有真实绑定，不伪造“临时报告”。未来若需要创建报告前的筛查预览，另设明确的证据级协议；不能复用 reportId 字段填写任意字符串。

`describe_for_report` 只验证当前权限、固定参数、封存目录和 checkpoint 并枚举完整计划，**不声称已扫描事实**。`prepare_for_report` 自行重新 describe；不接受客户端回传的 descriptor、total、planDigest 或 preview 作为授权。预算是否存在不改变七条规则，也不重新计算预算情景；但固定预算引用存在时保留既有轻绑定门禁，失参不能降级为无预算。

`VerifiedScreening` 是服务内部创建的不可变 canonical JSON 容器，返回副本；包含 report snapshot/workflow input 摘要、规范 owner/scope 绑定、证据五字段、完整 catalog/sourceCount、requested scope、映射及算法、筛查策略/计划/结果摘要。它不接受 HTTP JSON 反序列化恢复，没有跨进程缓存或持久身份。分页只接受此内部对象；每次仍核验当前 owner/权限和原报告绑定，不把对象类型或自报 SHA 当跨请求授权。

## 2. 权限与固定绑定顺序

1. 按 ID 重载 `AiReportRun.select_related('workflow')`；`current_principal(admin=True)`，同时 `authorize_owner(report)` 与 `authorize_owner(workflow)`，两者 owner/scope 必须精确相同。
2. 解析规范 snapshot，要求 sealed v2 支持的真实 profile。集成报告复用 `business_integrated.bound`；普通 v2/固定预算 v2 复用 `business_reports.bound_reference`，另核实际 workflow.input_json 与完整重建 reference 的 canonical 字节相等。不要仅校验 evidenceRunId。
3. 通过 `business_evidence.get_run` 取得证据，构造 `business_sealed.Reader`。Reader 在入口核 seal/catalog；`sources` 为完整深拷贝目录。`info` 是固定检查点元信息，尚未重新读完事实。
4. 请求维度和窗口来自已固定 evidence header 的 `analysisRequest`，调用现有严格 validator，并绑定其 canonical digest。若缺失，不默认填五维或三个窗口：preview 返回 `missing_fixed_analysis_request`，执行拒绝，旧报告照常仍可由旧流程处理。
5. 有 mappingPlan 时必须从固定 snapshot 取得，并用完整 Reader.sources 调用 `mapping_plan._checked_plan`，核 mappingPlanDigest；基期只接受 `validate_baseline_pair` 已验证的同 master 对。无映射选择不推断 ERP→SKU/SPU。
6. 每个来源 context 开始/结束及整个 prepare 末尾重新读取 owner/权限、报告 snapshot/workflow input、evidence version/plan/seal/catalog；最终检查成功后才能生成 `VerifiedScreening`。不能只 `authorize_owner` 一个开始时保留的 ORM 对象。

报告内容、标题或自然语言问题不参与猜测表选择。一个 sourceKey 的 query/sourceRef/evidenceDigest 改变，即使 mappingPlanDigest 因只绑定选择键而没变，也必须使完整筛查绑定失效。

## 3. 完整枚举：范围项、执行表、规则分区分别记录

计划至少保存三个有序清单：

- `requestedCoverage`：每个固定来源族×请求维度×请求窗口，以及用户固定关联对应的 ERP 映射维度；有请求就有记录，包含无来源、无关联、规则未实现等原因。
- `descriptors`：当前纯 scanner 能真正执行的完整表和 ruleIds；不能给纯模块空 ruleIds 或用虚构表填补无能力项。
- `partitions`：descriptor×ruleId，当前实现最多 64 个；一个表可有多个规则，共享一次完整迭代。

来源族按精确 domain + query（只去 window）分组；query 原始 startDate/endDate 保持不变，真实各窗口日期由 `comparison_periods` 计算。平台、店铺、dataset/channel、市场类目/scope/rankingDimension/priceBandFilter 均属于身份。来源族某窗口应有唯一来源；缺失写入 coverage，重复或歧义拒绝，不选择其中之一。

枚举规则建议固定为 `screen-selection-v1`，独立于自然语言：

| 来源 | 请求维度内的执行能力 | 单期与比较规则 |
| --- | --- | --- |
| promotion | shop/category/spu/sku/keyword；字段真实缺失由行级 ineligible 披露 | 各已请求窗口执行 `promotion_spend_without_reported_gmv`；本期对每个已请求基期执行 `promotion_spend_up_gmv_down` |
| sku/spu/b2b 原生销售 | 同上；原生来源与对应 dataset 身份保留 | 只有 `product_traffic_up_payment_down` 比较规则；各本期→有效基期表执行一次 |
| sales 原生 ERP | shop | 各已请求窗口执行退款存在、负毛利两条单期规则；本期→有效基期执行退款增加且正销不增、费用增加且净销下降两条规则 |
| 显式 sales/master | 仅请求中的 sku/spu | 与 ERP 原生同规则，但必须固定 pair；同一当前 master 比较，不声明历史真实归属 |
| master | 不单独筛查经营金额 | 作为映射依赖完整核验；coverage 标记 identity_dependency，不把主数据字段当经营指标 |
| market | 首片无规则 | coverage 标记 `unsupported_market_rules`，不借推广/商品规则处理样本；没有执行市场事实扫描就明确标为未扫描 |

ERP 原生的 category/keyword、未选映射的 SKU/SPU，以及映射中非 sku/spu 的请求均记录能力缺口；不能把 native ERP 的空 SKU 分组冒充已识别商品。SPU/SKU 原生表和 ERP 映射表分别保留。B 端必须有真实 b2b 来源，普通商用设备不补 B 端身份。

单期和比较应拆成 descriptor：例如本期单期表只执行单期规则，本期→previous 表只执行比较规则。这样两个基期不会让本期退款候选重复入榜，也不会为了“省分区”删掉 requested previous/yearAgo 的单期核查。历史窗口候选标明历史窗口，不自动当成本期待执行动作。

每个窗口/维度只在实际存在的规则下产生 descriptor。只有 current 且产品类没有基期时，coverage 为 `baseline_not_requested`，不生成伪比较或填零；若请求了基期但没有精确来源，则为 `missing_baseline_source`。若所有项都无可执行规则，preview 展示完整缺口但 `canScreen=false`，执行返回明确错误；不能调用纯模块的空计划并宣称完成。

额外未请求的窗口保留在完整目录绑定中，coverage 显式记 `outside_fixed_request`，不临时扩大历史范围。当前纯分析请求最多五维且 current 必含；daily/searchTerm/brand 不从导出的八维列表擅自加入。以后增加维度需新选择策略，而非偷偷扩大旧报告的固定含义。

## 4. 真实完整流：复用导出底座，不循环分析页

当前 `business_export.package` 在 `stream_table` context 内消费全部 rows；`business_mapped_export.append` 在 mapped table context 内消费 `table.scan()`。新服务复用底层迭代方式，不调用整个导出器：导出器包含内容/人工复核、TableSpool 和八维全导出，既不必要，也会引入错误依赖。

原生 opener：

```python
expected = reader.info(source_key)['expected']
with stream_table(
    reader.pages(source_key), dimension, expected,
    **baseline_arguments_from_the_same_reader,
) as (header, rows):
    yield header, rows
```

关联 opener：

```python
with business_mapped_analysis.table(
    evidence.id, fixed_mapping_plan, pair_key, dimension, principal,
    baseline_pair_key=baseline_pair_key,
) as table:
    yield table.header(), table.scan()
```

来源 descriptor 的 key/domain/query 来自 Reader.sources，sourceRef/evidenceDigest 来自 Reader.info.expected；映射 master 也同样构造。不要从 model/tool page 的 header 抽可信描述，不再手写关联 sourceRef 公式。

纯 scanner 的 `prepare` 已负责逐个进入 opener，完整耗尽生成器并在 context 正常退出后才返回；服务在其返回后继续做最后权限/固定绑定复验。Reader.pages 在尾部核真实页数、字节、PageReconciler 与 checkpoint 一致，stream_table 负责聚合守恒和实际输出行数，mapped_table 负责完整关联与再聚合守恒。这些完成前任何 expected rowCount 都只是待核对记录，不能因与用户 total 相等而成功。

一次 descriptor 不调用 `build_table(offset=...)` 或 `table.page` 循环翻页；否则每个分页请求可能重新扫描完整事实。多个维度/基期仍分别执行完整聚合，首片诚实保留这种成本；不提前预扫一次“验数据”后再额外扫同表，也不把现有有界结果页复用当完整结果表缓存。只保留一个活源 context，映射继续串行使用其原 identity/derived 临时空间上限；不增加导出 TableSpool。

## 5. 容量预检：64 分区不是 48 来源的同义额度

当前纯限制包括：256 descriptors、64 个表×规则分区、每分区 16 条候选、单表 25 万行、累计 200 万次结果行访问，以及候选/描述/覆盖字节限制。不能因证据允许 48 来源就承诺全部来源×全部维度×全部比较可筛查。

三窗口完整、五个请求维度下，按第 3 节枚举：

| 一个精确来源族/维度组合 | 分区数 |
| --- | ---: |
| promotion，一个维度 | 三个窗口的单期各 1 + 两个比较各 1 = 5 |
| 原生 sku/spu/b2b，一个维度 | 两个比较各 1 = 2 |
| ERP 原生 shop | 三个窗口的单期各 2 + 两个比较各 2 = 10 |
| ERP 映射 sku 或 spu，一个维度 | 同上 = 10 |

具体例子：一店推广三来源 + ERP 同渠道三来源 + 一个当前 master，共 7 来源。五维推广 25 分区 + ERP shop 10 + 映射 sku/spu 20 = **55 分区，40 descriptors**，分区计数可准入。再加入同店 SKU 日数据三来源，五维×两比较增加 10 分区，合计 **10 来源、65 分区、50 descriptors，必须拒绝**。两个上述七来源店铺组合为 110 分区，也不能整体运行。以上是计数推导，不是耗时或内存实测。

preview 在读任何完整事实前完成以下检查：

1. 规范请求、完整目录、映射选择的合法性与每项 capability reason；返回完整计划或完整缺口，不截断。
2. 实际规范 descriptor 字节、表数、精确 partitionCount、候选最大数量与现有硬上限。超过任何已知上限返回 `canScreen=false` 及实际/上限值。
3. 根据 sealed checkpoint 的 rowCount 计算保守 `rowVisitsUpperBound`（比较表最多两侧原始行数之和），明确它不是实际聚合行数。上界超过 200 万不代表实际一定超限，可标 `rowCapacityKnown=false` 后有界尝试；不能把上界当已知结果数宣称不可能或成功。实际扫描严格计量，超限整次失败。
4. 描述/query/header/candidate 单项字节仍在执行时检查，无法提前知道的实体宽度和聚合行数不得伪报已通过。preview 的 `canScreen` 仅表示可尝试，不等于完整事实有效。

首片不自动拆批；否则每批都标 complete 容易伪装成一个完整综合报告。未来若增加复合批次协议，须固定全部子计划及一个总覆盖摘要，全部成功后才发布总结果，并核总资源和每 Agent 阅读准入。那是独立设计，不能用隐藏拆批绕过当前 64 分区。

## 6. 服务结果和有界候选分页

建议服务封套 `business-diagnostic-screening-v1` 保留纯 result（仍写 prepared_unpublished），另附只在本次授权服务调用完成后生成的 `authority`：固定 report/workflow/evidence/catalog/analysisRequest/mapping/selectionPolicy 摘要、`completeSourceTraversalForExecutedTables=true`、执行/未执行范围清单。不要修改纯对象的 authorityVerified 为 true 再冒充其内部已验证权限；服务负责的证明与纯计算证明分层。

服务分别返回 `requestedCoveragePlanned`、`requestedTablesExecutedComplete` 和 `requestedSourceDateCoverageComplete`，不提供含义混合的 `requestedCoverageComplete`。market 未实现、无映射等请求缺口会使前两项 false；合法空表或缺日可以已完整扫描，但日期覆盖仍为 false。`executedTablesComplete=true` 只表示实际执行表完成，不得翻译为“全部请求均已分析”。`entityDailyCoverageVerified=false` 明确源级日期覆盖不证明每实体逐日完整；单期 partial 只代表观察记录、闰日同比天数差异均保留。

当前纯结果候选总量最多 1024 条，但单个响应不能返回所有候选。内部 `coverage_page` 和 `candidate_page` 建议固定最多 20 项、实际 UTF-8 ≤38,000 字节，保留完整记录前缀；offset 从 0 严格递增，nextOffset 按实际返回数，不能按固定 20 跳行。单条无法容纳时 413，不截文字/数值。分页附相同 verified resultDigest、planDigest、bindingDigest、分区身份和 matched/retained/omitted 总数。

覆盖摘要也可能超过一页；不可只返回前 20 个表就称覆盖清单完整。已知无候选分区仍保留零数与不适用计数，不用空页抹掉不支持项。

分页读取先后两次重新检查当前 principal/report/evidence 绑定；账号、权限或版本变化均拒绝。对象仅本次内部调用链复用，读取后不持有 SQLite/cursor。第一片不提供 HTTP 恢复；未来跨请求存储/缓存须绑定完整摘要、owner/scope、算法和容量策略，不能接受客户端提交的候选 JSON 作为固定结果。

## 7. 后续每 Agent 证明与旧兼容

内部服务返回核验结果不等于任何 Agent 已阅读。新报告 runtime 后续必须：

- 新 profile/surface/新工具名，旧 entry 的 schema/description/allowedSurfaces/catalog golden 全部不变；旧报告继续原校验行为。
- 创建时固定完整 screening plan/result binding 或持久恢复协议；不能恢复时换一组规则/阈值继续沿用旧回执。
- 每个 job 独立从 coverage offset 0 读至末尾；角色必读的候选分区集合由固定 policy 决定，不让模型通过不选择问题分区绕过。
- 对每个必读分区，从 offset 0 到真实末尾独立记录成功结果摘要、完整请求范围和审计；候选 retained 全读完仍明确遗漏 matched 的数量，不能称读完全量事实。
- 调用额度/模型上下文按实际覆盖与候选分页及下游依赖预验。64 分区每个各一页也可能超过当前 job 的工具额度；不放大旧限制、不静默删页，必须另行设计新 profile 的可行准入。
- 候选引用由固定 candidateId 重算/解析成现有真实原生或 mapped 引用；模型不能更改排序量、规则条件、rowIndex、entity 或 source/pair 绑定。纯数值现象不证明因果。

持久 job ledger、未知外部结果不重放、专业并行与独立复核都保持原安全原则；不得靠共享数学结果复用跨 job 的“已读”状态。

## 8. 第一片隔离验收建议

新增服务测试，不改旧 fixtures 的权限假设：真实 sealed v2 promotion/product/ERP/master，包含两基期及真实 requestedDimensions，断言精确 descriptor/分区数和 coverage；7 来源 55 分区通过计数，10 来源 65 分区在事实读取前拒绝且零模型调用。使用 actual Reader/stream_table/mapped_table，不 mock 数学计算。

覆盖：缺 analysisRequest、缺基期、错误同名 channel/shop、无映射与改 master、market unsupported、空实体/空表、最后事实页损坏、最后 context 失权、调用中 sealed/snapshot/workflow 变化。证明每个 descriptor 的来源只走一次完整流，不循环分页重扫；不同维度之间的必要重复需按实计数。

分页验证中文宽记录、完整前缀、零候选/零能力清单、权限迟到变化和旧 resultDigest 不可恢复；两个 owner 不共享结果。最后跑旧 v1/v2/integrated 报告与 catalog golden 回归，确认新模块未注册工具/路由、未改变任何旧成功门禁。

## 9. 第 33 批纯计划实际合同

新增 `business_analysis/screening_plan.py` 的 `build(analysis_request, sources, source_infos, *, mapping_plan=None, limits=None)`。sources 是完整 Reader.sources，source_infos 必须恰好包含所有来源的 `{metadata, expected, pageCount}`；可选 mapping plan 仍严格重建验证。该函数不访问数据库、不打开事实流，结果始终 `authorityVerified=false`。

返回 `business-screening-plan-v1`，包含规范 analysisRequest/digest、完整 catalogDigest/sourceProofs、mappingPlanDigest、families、requestedCoverage、全部 descriptors、可直接传 scanner 的 limits，以及 capacity/canScreen/admissionFailures/planDigest。合法但超容量的计划保留全部内容，不截断；格式不合法才抛 AnalysisContractError。`rowVisitsUpperBound` 是保守上界，超出不直接证明实际分组超限；实际扫描仍有硬门禁。

coverage 采用单期、比较、依赖、额外窗口四类固定记录；每条有稳定 coverageKey、来源族、mode、dimension、window、sourceKey/baselineKey、status/reason 和 tableKeys。单期已经计划不能掩盖比较缺口。产品仅有比较规则时，已纳入比较流的单期项标记 planned/observed_in_comparison_table；没有任何可比较来源则 unavailable。master 的历史窗口条目标为 current_master_not_historical 依赖，不伪装成历史主数据。

纯验收：18 项新增测试通过；加第 31 批 scanner 和 mapping plan 旧回归共 47 项通过。覆盖 7 来源 55 分区/40 表、10 来源 65 分区/50 表原样拒绝、48 来源 400 表全清单返回、空事实仍保留表、缺映射/缺基期/额外窗口、真实 `stream_table` → scanner 联跑及输入有界性。服务权限与数据库测试由主线程另行执行，不由这些纯测试代替。
