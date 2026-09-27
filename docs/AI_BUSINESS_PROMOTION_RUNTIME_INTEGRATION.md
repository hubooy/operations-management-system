# 关键词深诊断接入多 Agent 与工程文件：最小版本化路径

2026-09-18，候选分支实施方案。第一片已增加内部AI reader GET，尚未注册模型工具、新profile或文件；不代表多Agent词货诊断完整接通。旧报告、迁移和renderer保持原样，尚未生产采用。

## 第一片：内部reader接口（候选，待主线程PG验收）

精确路径`GET /api/ai/reports/<reportId>/promotion-keyword-sku`，由[views](../backend/ai_assistant/views.py)的精确allowlist与三段路径分派进入[严格参数适配](../backend/ai_assistant/business_promotion_runtime_tools.py)。沿用签名身份、当前AI reader/authority、实时账号及报告所属权限。底层只调用[既有owning page/read_row](../backend/ai_assistant/business_promotion_keyword_sku.py)，因此仅适用于其已支持的真实固定封存v2报告（含已支持integrated/screening），不会把纯计算DTO变为授权证明。

- 页模式：必须`sourceKey/view`，可选`baselineKey/offset/limit`；offset为规范非负整数字符串，limit如提供只能为`20`。
- 行模式：`rowIndex/rowId`必须同时存在；可选baselineKey，禁止offset/limit以及混合模式。行索引与SHA行身份由owning完整重验。
- 未知/重复参数拒绝；view仅支持现有`keyword_sku/keyword_sku_context`，来源与基期由owning固定目录验证。
- 原样返回owning封套、摘要和authority边界，完整UTF-8响应最多38000字节；no-store与X-AI-Revision沿用views。GET不写业务事实、不调模型、不建立读取回执，也不等于后续Agent已阅读。
- 错误不回传成功authority；容量不足整行拒绝，撤权或绑定变化不得返回旧结果。

新增[真实报告路由测试](../backend/ai_assistant/test_business_promotion_runtime_tools.py)，标签`ai_assistant.test_business_promotion_runtime_tools`；包括签名、完整封套/基期、行引用、参数、身份/角色/方法、错误来源、容量和迟到撤权。另须运行既有`ai_assistant.test_business_screening_routes`核验旧路径。这里没有执行生产或实际模型，测试结果由主线程补录。

## 已有能力和准确缺口

现有 `business_analysis/results.py` 已有 `keyword` 与 `searchTerm` 原生维度；因此不是“系统完全没有关键词分析”。缺口是新增的关键词×明确推广 SKU、以及计划/单元上下文，尚未进入当前多 Agent 的可调用工具、数值引用核验和报告文件目录。

| 层 | 已有实现 | 尚未连接的部分 |
| --- | --- | --- |
| 纯计算 | `backend/business_analysis/promotion_keyword_sku.py` 的 `keyword_sku`、`keyword_sku_context`；`promotion_views.py` 的计划/单元视图 | 不具备来源权限，不可直接给模型当已核验证据 |
| owning 服务 | `backend/ai_assistant/business_promotion_keyword_sku.py`、`business_promotion_views.py` 的 `table/page/read_row` | 已固定真实报告/封存来源，完整遍历，context 正常退出后再次复验；未注册工具/文件 |
| 当前 runtime | `business_screening_runtime_contract.py`：profile `business-agent-screening-reference-v1`、surface `business_agent_screening_v1`、三个 v1 工具 | `business_screening_tools.analysis_from` 只分发 native/mapped，前者只接受原 `results.VIEWS` |
| 模型引用 | `business_screening_diagnosis.py` 的 `_reference` 与后续解析；`business_screening_execution.py`、`business_screening_receipts.py` | 新视图不能凭同名字符串混入旧引用，尚无新绑定、读页回执与服务端数值解析 |
| 工具入口 | `lib/ai/tool-registry.ts`、`tool-registry-contract.ts`、`django-edge.ts`；`backend/ai_assistant/views.py` | surface、严格参数结构、签名目录摘要、运行身份必须一起注册 |
| 文件 | `business_export.py`、`business_screening_export.py`、`business_files.py`、`business_volume_files.py` | 当前新增单文件 renderer 5、多卷 6，兼容历史 1—4；未包含上述新表及来源身份缺口 |

`business_screening_tools.py` 等模块头部仍有早期“未注册”说明，不能据此否定现有全链路：实际目录、views 与执行入口已有 v1 接线。实施以调用点和版本门禁为准。

## 首次交付范围

只增加当前报告固定京东推广来源的两张全量表：关键词×推广 SKU、关键词×推广 SKU×计划/单元/匹配方式上下文。当前期与已有明确固定基期比较，复用已封存来源，不另取数据、不猜商品归属。计划/单元单独汇总服务可供后续同一主题核查，但不是首片上线前提，不继续扩展广告管理、竞价自动修改、搜索词归因或新平台。

深诊断沿既有五角色：promotion 给出关键词商品的费用/点击/平台归因成交变化、集中与低效现象；independent_review 重读可引用行并识别口径冲突；report 形成有前提、观察期、指标和回退条件的调整方案。不能把文件中增加两表当作诊断完成。

## 实施顺序与精确接点

### 1. 冻结新增运行合同，不改变 v1 目录

新增独立 `backend/ai_assistant/business_promotion_runtime_contract.py`，建议命名 profile `business-agent-screening-promotion-reference-v1`、独立 surface 与带版本工具名；名称实施前查重。复用五角色结构和原角色包算法，但新 profile 单独固定其工具目录、允许工具摘要、算法版本和推广表选择清单。现有 v1 三工具、参数 JSON Schema、输出结构与旧快照均不原地扩展。

在 `business_reports.py` 的工具/surface/snapshot 分派和创建入口增加精确新分支；现有 `business_screening_runtime.py`、`business_screening_creation.py`、`business_screening_permission.py`、`business_screening_admission.py`、`business_screening_execution.py`、`business_screening_receipts.py` 的严格 profile 检查须通过独立适配器或明确版本分派复用。禁止简单把旧 PROFILE 改成新字符串，或把多个 profile 都当作同一个旧 snapshot。

PostgreSQL `0024_business_screening_runtime.py` 把 profile、snapshot 字段与策略写入触发器，不能只改 Python。需要新迁移，保留前驱函数，精确允许新 profile 与新增固定字段；不修改已应用 0024。迁移编号由集成时实际最新叶子决定。

### 2. 新工具适配完整 owning 服务

新增 `backend/ai_assistant/business_promotion_runtime_tools.py`：参数绑定 runId/reportId/screeningId/sourceKey/baselineKey/view/offset，视图仅首片两个；只能选新快照中的固定推广来源及比较关系。底层调用 `business_promotion_keyword_sku.page/read_row`，不得自己从 DTO 或纯 build 授权，不在 context 正常结束前返回。

`lib/ai/tool-registry.ts` 新增独立目录条目；同步 `tool-registry-contract.ts`、`django-edge.ts` 和 backend `views.py` 新的严格只读内部路由/分派。模型 schema、Django admission 的手工 schema 核验与真实 endpoint 必须一致。继续完整响应 ≤38,000 UTF-8 字节、每页完整行前缀，不静默截断。

新 profile 的 promotion 节点必须明确知晓两种视图可用、缺字段限制以及按 nextOffset 阅读；不要求每个 Agent 把百万行全部读入上下文。完整数学遍历由 owning 服务证明，模型实际读取只证明返回给该节点的页；若必读内容装不进当前容量，preflight 必须拒绝/给缺口，不能把未读算作读完或直接提高上限。

### 3. 引用和调整建议同步接入

新增版本化诊断引用：明确类型、sourceKey、baselineKey、view、rowIndex、完整 rowId、tableBindingDigest、metric、field；与旧 native/mapped/candidate 引用分开分派。修改接点为 `business_screening_diagnosis.py`、`business_screening_content.py`、`business_screening_content_fence.py` 及新 profile 的执行/回执适配器；旧解析器行为保持。

每个被引用数值都通过 `business_promotion_keyword_sku.read_row` 重新解析，绑定同一视图、基期和报告，忽略/拒绝模型自填数值。跨报告、错基期、错算法、旧 rowId 均拒绝。promotion、复核与汇总节点不能互相借用读取回执。首片不新增候选筛查规则：原角色包 candidateId 与表绑定不变，新诊断使用显式推广行引用，不把新表塞进旧候选集合。

缺 promotedSkuId、缺 keyword 或缺上下文身份的桶保留金额并展示缺口；不得针对其给具体商品操作。只使用明确 `promotedSkuId`，不回退 triggerSkuId/attributedSkuId/顶层 skuId/名称。两表是不同分组的同一费用，禁止相加。来源日期有记录不代表逐词逐商品完整，平台归因成交不代表 ERP 净销售、利润或因果增量。同比/环比须绑定已固定基期，零/负基期和缺日沿纯算法状态披露。

### 4. 新文件版本与旧版恢复共存

新增版本化 `business_promotion_export.py`，全量扫描两表写 spool，并附 source/baseline、算法、表摘要、身份缺口与限制；HTML/XLSX 使用同一 spool/表摘要，模型摘要不替代全量表。表过大按现有多卷容量分卷，不提高 120 表/100 万行/100 卷等现有界限，不裁字段或丢缺身份桶。

在 `business_export.package/prepare_volumes`、`business_files.binding`、`business_volume_files` 增加精确新 profile+新 renderer 分支。新多卷 renderer 建议使用下一个未占用整数（当前候选最高 6，7 仅是建议，实施时确认）；renderer 6 的旧报告不得自动多出新表。新 profile 只能使用新 renderer，新文件 run 按新版本/绑定生成独立 ID。

新文件触发器迁移必须同时覆盖父 run、manifest renderer 一致性、分卷/分块、发布 guard 和 reverse 拒绝条件；参考 `0025_business_file_opc.py` 但不能原地修改。旧 1—6 下载、暂停续跑、已存 chunk、attempt、manifest 与字节保持。正式模板的诊断卡片与附表目录必须显示新证据引用及明确缺口。

## 必须通过的验收

1. **旧版兼容**：旧工具目录/快照/策略摘要不变；旧 v1 三工具 schema 正反例不变；旧 1—6 已完成文件下载与暂停续跑不重算，固定时间下新旧实现生成旧版本字节一致。
2. **新注册与权限**：正确新 profile 可走真实目录→签名路由→owning；普通聊天、旧 profile、错 actor/scope/报告/run/source/baseline/view 被拒绝；当前撤权、过期固定证据和最终 context 撤权拒绝。
3. **真实数值**：同词多 SKU、多计划/单元/匹配方式、缺身份、负退款、空值、零基期、缺日、跨年闰日；两表各自对账到同一固定费用总额且不可二次相加。错误商品角色字段不会填补 promotedSkuId。
4. **引用与多 Agent**：真实读取回执绑定本节点；未读页、伪造 rowId/tableBindingDigest、跨基期及模型自填数字拒绝；promotion→独立复核→report→人工复核的合成端到端完整通过，冲突未解不得批准。
5. **容量**：38 KB 完整中文响应、长词、单行超限拒绝、多页连续性；preflight 使用实际工具目录和固定数据，超上下文不付费启动；超文件容量不交付部分成功。
6. **迁移与文件**：真实 PostgreSQL 最小角色正负探针、升级前后旧表摘要/权限不扩展、旧库备份恢复、新版 manifest 与父版本严格一致；新两表 HTML/XLSX 数据摘要一致，浏览器表格/搜索/CSV/移动端可用，Excel 原生验收受有效 Office 许可约束。
7. **真实诊断质量单列**：合成运行通过不代表付费模型效果通过。后续有授权时用固定真实报告核对关键词商品诊断、同比环比和行动方向；不自动调整广告、不发送外部消息。

首个可实施批次建议只做第 1—3 步的新 profile/工具/引用完整闭环及其测试；第二批接新 renderer 与文件验证。两批都通过之前，进度应写“关键词 SKU owning 已有、runtime/报告接入未完成”，不得写为五阶段已全部完成。

## 2026-09-18 内部 reader 接线

候选已增加精确 GET `/api/ai/reports/<reportId>/promotion-keyword-sku`，分页和行引用两种参数形态互斥。它只调用既有 owning `page/read_row`，保留完整报告、来源、基期、表摘要及 authority 封套，不把纯合同或请求参数当授权；没有注册模型工具、新 profile 或文件版本。

新路由7项与旧 screening 路由4项在隔离 PostgreSQL 合跑共11项通过，日志 `.runtime/ai-pg-864340d94583/tests.log`。首轮唯一失败是测试把其他管理员不可见的报告预期为403，实际正确隐藏为404；测试修正后通过，失败日志 `.runtime/ai-pg-56a58e919116/failure.log` 保留，未放宽服务。覆盖两种视图、基期、精确行引用、重复/混合参数、路径/方法/角色、容量、撤权、禁止业务事实SQL及模型调用。

下一步仍须实现本文第1—3步的新 profile、中央工具、节点独立读取回执和数值引用复验；内部 reader 存在不代表 Agent 已能调用。之后再进入新 renderer 和双格式文件。

## 2026-09-23 中央工具候选接线

在最新主线整合分支新增独立 `business_agent_screening_promotion_v1` surface 及四个固定工具，保留原五角色所需的角色包、普通分析、预算与新增关键词×明确推广SKU读取职责。账号必须是无范围管理员；普通聊天和旧筛查 surface 的工具目录与历史固定摘要保留。词货读页与精确行引用调用上述 owning reader，页/行参数互斥，完整语义 JSON 限 38 KB；原始 HTTP 允许最多 48 KB 以容纳序列化空白，超过任一界限都拒绝而不截断。

新工具及旧目录相关 Node 48 项通过，日志 `.runtime/promotion-tools-node-final.log`；静态 lint 通过。本片只注册中央工具及签名读取桥；Python 新 profile 的创建、调度、节点读取证明、数值引用、诊断和新文件版本仍未接入。任何固定为旧 profile 的报告仍按原协议解释。

## 2026-09-23 新 profile 纯合同（候选）

`backend/ai_assistant/business_promotion_runtime_contract.py` 现在提供独立五角色候选图、`freeze_snapshot`、`checked_snapshot` 和 `scoped_row_reference`。新图从旧筛查图的两个已固定摘要构造；旧图或预算版图发生任何变更时，新图生成会拒绝，避免新 profile 静默继承旧协议变动。五个 Agent 的职责、依赖顺序、输出字节上限和人工复核节点均保留；新图仅将工具名换成四个新 surface 工具，并规定词货数值引用只能由推广、独立复核、报告三个角色使用。

纯快照要求一个明确的京东推广当前期来源、可选的同店铺同数据集且同原日期区间的环比或同比基期。它固定 `promotionSelector`（含两种视图）、`contextDigest`、`sealedDigest`、来源目录摘要和词货算法版本；两种视图是同一费用的不同分组，不得相加。复核从调用方提供的完整目录和上下文重新计算，拒绝错报告、证据、筛查任务、店铺、基期、视图、算法及额外字段。词货引用还绑定角色、选定视图和完整行身份；模型自填数值不能进入引用合同。

上述纯对象仍标记 `authorityVerified=false`、`registered=false`。传入目录与上下文本身不获得授权；后续 owning adapter 必须从持久报告和封存证据加载并最终复验真实身份。此批没有改旧 screening-v1 合同、已发布迁移、任务派发、读取回执或 renderer，也未运行模型。`python -m unittest ai_assistant.test_business_promotion_runtime_contract -v` 共 11 项通过（含旧图摘要、预算与无预算、五角色并行依赖、错误来源/基期/角色及伪造字段负向）。

## 2026-09-24 真实封存根的只读候选适配

新增 `business_promotion_runtime.prepare_candidate(reportId, screeningId, selector, principal)` 和 `checked_candidate`。仅接受当前无范围管理员，从持久报告、工作流输入、已封存证据目录和已发布筛查结果重载；完整验证筛查所有持久页，再从真实目录运行 `freeze_snapshot/checked_snapshot` 与五角色图。显式 `sourceKey` 和可选 `baselineKey` 只是候选选择约束，必须在同一份报告的封存目录中对应京东推广当前期及同店铺同数据集基期。返回前重新加载并比较完整根，账号撤权、报告快照、来源或筛查根变化均不得返回旧候选。

返回仅含原报告身份摘要、新 profile 的拟议快照与五角色图，明确 `authorityVerified=false`、`registered=false` 和 `readiness=requires_new_persistent_profile`。旧报告的原 `executionProfile`、快照、工作流输入与筛查页不修改；此候选不能用于 Agent 派发、读取证明或正式报告。原报告没有持久 `promotionSelector`，因此当前显式选择尚不能充当可恢复的新报告身份。下一步必须通过新的报告创建和数据库 guard 将该选择持久化，才可连接调度与工具回执；不得将此候选直接标记为 ready。

测试标签 `ai_assistant.test_business_promotion_runtime`；采用真实封存报告与发布筛查行，检查合法当前/基期、错来源/报告/筛查/账号、篡改候选、迟到撤权、旧字节不变及无模型/外部工具调用。3 项隔离 PostgreSQL 测试通过（22.443 秒），日志 `.runtime/ai-pg-4fa15d233537/tests.log`。首次测试夹具的来源窗口与固定分析请求不一致而被正确拒绝，修正合成目录后通过，失败日志 `.runtime/ai-pg-1fecd74e92ee/failure.log` 保留；未放宽业务门禁或执行生产。

## 2026-09-24 新持久报告的只读绑定（待隔离 PostgreSQL 验收）

新增内部 `business_promotion_runtime.bound_persisted(reportId, principal)`，用于由 `business_promotion_creation.create` 和 `0026` 创建的新 profile。它从真实报告、工作流、已封存 v2 证据与目录重建词货选择、双目录摘要、工作流输入、六节点图、四工具目录与可选预算，并检查当前无范围管理员和模型配置版本；返回前复验账号、证据版本、报告、筛查发布状态和中央目录。旧 profile、错来源/基期/身份及版本变化均拒绝。

返回 `screeningStatus=prepared_but_not_ready`、`contentReady=false` 表示报告和筛查意图已持久化，但筛查结果尚未发布。`rootBindings` 记录当前证据 ID/版本、seal、目录摘要与来源数。只有意图 ID 对应的持久筛查行经过 owning 服务及全部页链验证，才可能返回 `ready`。现有 `business_screening_store._loaded` 经旧 `business_diagnostic_screening._load`，尚不识别新 profile；发现此类行会拒绝读取，不会凭行存在宣称 ready。此时仍无模型派发或文件交付。

下一步为旧 screening `_load` 增加新 profile 精确分派时，可从本适配器同一轮核验复用可信五元组：`report` 是已验证当前账号与 workflow owner/scope 的 `AiReportRun`；`snapshot` 是规范 JSON 且逐字节等于当前封存来源重建的 `shape.snapshot`；`reference` 是 `shape.workflowInput` 且逐字节等于 `workflow.input_json`；`evidence` 是通过封存版本复验的 v2 行；`sources` 是 `evidence_store.catalog(evidence)` 的真实目录。任何调用者提供的目录、摘要或候选对象都不能取代它们。实际页读取结束和返回前还须重验这些根。

## 完整词货导出材料候选（2026-09-23）

新增 `business_promotion_export.prepare(report_id, source_key, principal, baseline_key=None)`：复用两种词货 owning context，严格跟随每页实际 nextOffset，验证页/行位置与摘要，完整采集两表。两种 context 均正常退出，再进行最终报告/当前账号复验后才返回不可变 NDJSON 页字节和独立 manifest 副本；失败、取消或迟到撤权不返回部分材料。

清单绑定报告、来源/基期、词货算法及各自 tableBindingDigest，记录全量行数、页数、UTF-8字节数、原始NDJSON SHA256和缺推广SKU/不合格身份组数。两表当前/基期花费及缺失事实行数独立对账，明确 `tableExpensesAreAdditive=false`；两表费用不得相加，平台归因成交不是ERP净销售或利润。未改变旧renderer，不写文件数据库，不生成正式HTML/XLSX。

整个材料合计最多25万行、2万页、64MiB（含manifest），调用者只能收紧上限。NDJSON保留完整行、缺身份桶、负值和空值，不截断。准备对象是内部材料而非可转让权限；未来实际文件发布/下载仍须复验固定报告授权。

静态编译通过；新增 `ai_assistant.test_business_promotion_export` 6项真实封存PG测试通过（24.147秒），日志 `.runtime/ai-pg-b7e9e0fe2330/tests.log`，包括100+词货组多页、同词不同SKU/多计划、负值/缺身份、两表费用守恒、摘要校验、容量整批拒绝、取消及最终撤权。首轮6项中5通过，1项旧合成采集器工具目录缺少网店续读，被新检查点门禁正确拒绝；夹具增加实际续读服务后通过，失败日志 `.runtime/ai-pg-43a93ac6bea3/failure.log` 保留。没有模型或生产调用，尚未注册任何文件renderer或运行profile。
