# 全量筛查接入新版经营报告运行时设计

状态：**设计，未注册新 profile、工具或调度行为**。第 33 批内部服务已作为候选提交 `3a5091be` 完成 18 项隔离 PostgreSQL 验证；仍未接公开运行时。本文基于当前 integrated runtime、持久回执、预验和 renderer 4 代码，提出下一步最小完整接入；不表示已有报告自动获得筛查能力，不授予上线或模型调用权限。

后续实现：第三十五批已完成 [固定存储](AI_BUSINESS_SCREENING_STORAGE.md)，第三十七批已完成 [五角色无损包及内部读取](AI_BUSINESS_SCREENING_PACKAGES.md)，第三十八批已完成 [完整 graph 容量预览](AI_BUSINESS_SCREENING_PREFLIGHT.md)。本文保留此前完整方案；实际包的固定字段、源绑定、100记录分页与实测容量以实现说明和代码为准。完整预览已证实55分区低候选样例报告204916字节，超过192 KiB；不得用前期仅工具帧探针宣称整图准入通过。运行时准入、job回执和文件接线仍未完成。

## 1. 推荐生命周期：每报告一次成功筛查，五个 Agent 各自阅读

筛查是确定性事实计算，不是专业 Agent 的自由任务。推荐在任何 Agent/模型启动前，对一个固定报告执行一次完整 prepare，将核验后的覆盖和候选页作为该报告的不可变快照持久保存。三个专业 Agent、独立复核和整合读取同一数学结果，但各自保留自己的读取回执。

“一次”指一个报告版本只发布一份成功结果：进程崩溃、准备失败或并发竞争可能重新做确定性计算；不能把在内存中执行一次当成持久 exactly-once 保证。报告参数、证据、规则、容量策略或映射计划变动必须新建版本。不得把旧任务的已读状态复制给新报告。

不建议每个 job、每次工具分页或每次 receipt 检查都调用第 33 批 `prepare_for_report`。它会按 descriptor 完整聚合所有事实；五 job 各准备会至少重复五份工作，多次恢复/回执验证还会放大。现 `business_integrated_reuse.CompletedReuse` 只在一个 owner/report/调用阶段内共享完成 JSON，不能替代跨 job/进程的持久筛查快照，也不应扩大成全局活 SQLite 缓存。

文件创建/恢复/发布有独立的正确性门禁。建议每次文件构建 attempt 在准备阶段至多完整重算一次筛查，与原固定筛查 resultDigest 对账；该计算可在同一 attempt 的后续内容校验中复用。下载单个不可变 chunk 只做当前权限与完整固定引用的轻核验，不重新扫描全量事实。

## 2. 当前代码决定的约束

| 当前位置 | 对新接入的直接影响 |
| --- | --- |
| `workflows.create` | 当前 profile 在创建事务中完成 preflight 并创建 node；新筛查尚需真实 reportId，不能把全量扫描硬塞进该长事务 |
| `AiReportRun` 与固定 workflow input | report 是追加/不可变记录，不能创建后回填 snapshot 中的筛查 digest，不能偷偷修改 input |
| `workflows.advance_workflow` / `business_parallel.workflow_step` | 当前会从 queued/running 工作流派发专业 job；新 profile 必须在此之前有专用 readiness 门禁 |
| `business_integrated_receipts` | 每 job 独立核最多 40 个有审计 dispatch；目录和预算逐页完整，mapped 当前只要求读一页。这份合同须冻结，不直接升级其字段含义 |
| `business_integrated_preflight` | 使用实际工具帧、转义探针、192 KiB transcript、24 KiB 节点输入与模型 token/tool/round 配额；新候选页同样不能靠截旧消息准入 |
| 第 33 批 `VerifiedScreening` | 只是当前进程内部对象；每次使用重验权限/绑定。其纯 `prepared_unpublished` 不能直接当持久发布依据 |
| renderer 4 | 当前绑定报告内容、映射/预算，完整 manifest 字段严格校验；筛查引用不能无版本地塞进旧 manifest |

## 3. 先固定 intent，再原子发布成功快照

第四十批已经实现本文的固定意图与真实发布绑定，详见 [实现及升级恢复](AI_BUSINESS_SCREENING_INTENT.md)。扫描前调度门禁和模型准入尚未接入；创建API继续不接受新profile。

建议新 profile `business-agent-screening-reference-v1`、新 surface `business_agent_screening_v1`。命名为提案，实施时一次固定。可选 mapping/budget 继续显式固定；未选择时不能推断。旧 profile 的创建、恢复、graph、工具目录 SHA 和数据库 guard 语义全部保留。

首次创建的新 snapshot/reference 固定：

```json
{
  "screeningIntent": {
    "schemaVersion": "business-screening-intent-v1",
    "id": "预分配的精确筛查ID",
    "selectionPlanDigest": "完整纯计划摘要",
    "selectionPolicy": "screen-selection-v1",
    "algorithmVersion": "diagnostic-signs-v1",
    "capacityPolicy": "独立固定版本"
  }
}
```

实际 reportId、workflowId、owner/scope、证据五字段、analysisRequestDigest、mapping/budget 引用与完整算法版本仍由服务重建并固定。intent 引用的 ID 在创建时尚无 ready 记录，这是明确的准备状态；**report snapshot 和 workflow.input_json 之后都不更新**。最终结果 digest 存在该固定 ID 的不可变 ready 快照里，不回填旧父对象。

最小状态路径：

1. 创建时只做身份/固定范围/纯 plan 静态准入、模型及工具目录配置绑定，持久化 inert queued 报告及完整原请求幂等记录。返回“准备筛查”，不是“模型分析已开始”或“完整模型上下文已准入”。
2. 新 profile 的后台准备步骤用现 workflow 的短 CAS/租约取得本报告准备权；全量 prepare 在数据库事务外执行。不得持 SQL 行锁等待完整扫描，更不得使用模型任务占位来做数学筛查。
3. prepare 通过所有源 context 退出和末尾权限复验后，生成完整固定覆盖/候选页。对**实际页**执行各节点模型准入，复验当前模型配置、固定 graph/catalog 和预算引用。
4. 在短 mutation 内重新核权限、report/workflow intent、evidence/seal、租约/版本及配额，原子插入专用 screening run + 全部 immutable pages。失败不落半份“成功覆盖”。
5. scheduler 只有查到精确 intent ID 的完整 ready 结果，并通过当前配置准入，才能创建五个专业 job。模型 dispatch 路径再次有相同门禁，避免绕开 workflow_step 直接推进 job。

失败/暂停/取消发生在准备阶段时，保留原不可变请求和 intent。允许重新执行确定性准备；已知/未知外部模型回执仍按原规则禁止自动重放。模型已运行后，不能重建另一份同 ID 筛查结果替换旧阅读依据。

如果并发准备因租约超时都完成，唯一约束只能允许一份结果。竞争失败者先核实际 ready 记录的所有绑定及 resultDigest 完全相同后复用；不同结果必须冲突，不选择较新一份。ready 存在后恢复不再执行日常全量 prepare。

## 4. 持久快照是新存储能力，不能借旧表伪装

与持久化专项方案对齐，建议使用专用 run + immutable page 两表，绑定真实报告与 owner/scope，原子发布完整页集。不复用 `AiArtifacts` 的截断型小表，也不把派生候选伪装成 `AiBusinessEvidenceChunk` 事实或 renderer 文件块。

每份 ready 至少绑定：固定 intent、report snapshot/workflow input SHA、evidence/catalog/source info 摘要、mapping/selection/rules/capacity 版本、纯结果 digest、服务 authority、覆盖页和所有候选分区的完整页目录及摘要、总字节/页数。纯对象的 `authorityVerified=false` 原样保留，外层服务 authority 与它区分。

建议独立容量可从 16 MiB/run、owner 64 MiB/20 run、global 256 MiB/200 run 起评估；这是待主线程确认并实测的新额度，不挪用事实 64 MiB/2000 页或文件额度，也不承诺所有合法纯结果必能持久化。按完整实际 UTF-8 页及 header 计费，达到上限拒绝发布；数据库并发配额锁必须统一且取得新 snapshot 后计数。

DB 至少保证 owner/report/workflow/evidence 同绑定、唯一固定结果、父子页集合/坐标/摘要/实际字节与总计守恒、页不可更新及不允许孤立 ready。内部存储首片允许对旧的受支持 v2 固定报告保存筛查结果，但不改变其旧 runtime 完成标准；后续新 profile 须另行扩展绑定合同与门禁，不能把旧报告的旁路筛查当作已执行新版 profile。新 profile 的 job 在 ready 前不能进入可派发状态。数学规则真实性依可信服务和重算，不声称 SQL 只看 SHA 就证明业务计算正确。

reader/writer 权限、health/schema/table manifest、备份/恢复、升级前后旧行摘要、含新数据逆迁移拒绝必须一并交付。迁移编号由当时实际末迁移决定，本文不抢占编号。

## 5. 先固定角色包，再建立每 Agent 独立证明

直接暴露第 33 批按 partition 的候选分页会让 55 个分区消耗几十次调用，不能作为新版运行时默认路径。建议新 surface 只增三个新工具：

- `get_business_screening_package_v1`：完整来源/覆盖加本角色必读候选的确定性跨分区记录流。
- `get_business_screening_analysis_table_v1`：可选原生/映射明细核查，严格沿用 native/mapped 互斥参数和 owning resolver。
- `get_business_screening_budget_v1`：已有固定预算的完整分页；无预算必须拒绝，不能返回假空成功。

旧工具 entry、allowedSurfaces 和说明全部不动。新工具共同固定 runId/reportId；package 另带 `role` 和严格整数 `offset`（默认 0），**运行时必须在 dispatch 前、回执核验时把 role 与实际 job.workflow_node_key 精确核对**。principal 只证明登录身份，不证明当前专业节点。不能让模型选择 market_b2b 空候选包代替复核包。HTTP reader 若允许报告所有者检查任意角色，只代表读取权限，不生成 Agent 完成证明。

### 5.1 固定包协议（下一版纯合同，当前未实现）

角色包是有损摘要之外的另一种传输表示：确定性省去重复字段名和常量，但能够逐项还原已保留的所有数据。建议固定 `business-screening-role-package-v1` 与 `screening-role-package-policy-v1`，包含以下记录类型和固定顺序：

1. 完整来源及其 source info、来源 family。
2. 全部 requestedCoverage、实际执行表的覆盖/日期/原行摘要、全部规则分区的 eligible/ineligible/matched/retained/omitted。**所有角色均拿到全部分区，不因职责省略市场 unsupported、缺映射或缺基期。**
3. 本角色固定必读分区的全部 retained candidates，按固定 partition 顺序、该分区稳定排名排列；保留原 candidateId、rowId/rowIndex、指标原值、理由和引用。

用显式 `recordSchemas` 规定 coverage 等记录的列序；重复的 ruleId/tableKey/meaning 可放入固定字典，候选引用其索引。禁止截断文本、删字段、四舍五入或裁候选来缩小包。纯合同测试必须把这种表示解回原记录并深度精确相等；未知 schema/类型/索引/字段/额外值拒绝。第一版不采用任意递归压缩或需要执行代码的解包方式。

每页最多 **100 个有界记录**、实际 UTF-8 不超过 **38,000 字节**（这是新包合同，不改变旧表工具每页 20 行）。取完整前缀，`nextOffset` 按实际返回记录数，超宽单记录拒绝；不切半行、不把多个分区偷偷当一个源。页头固定 role、report/evidence/intent/result/selection/包策略摘要、packageDigest、总记录数及当前 offset；第 0 页有有界 schema/常量目录。首部和单记录都须先测字节，无法装入时明确容量失败。schema/常量目录不得绕过最大页或内存限制。

packageDigest 由非自引用的完整规范记录流及固定策略/绑定计算；每页自身摘要由独立清单记录，不在本页哈希里包含自身摘要。翻页不能用页号代替记录 offset，也不能混用旧分区 offset。

第 34 批拟议存储仍可保留完整分区页，详见 [存储设计](AI_BUSINESS_SCREENING_STORAGE_DESIGN.md)。角色包应从已经 ready 的不可变页生成，**不得为了读取一个包再次扫描事实**。下一运行时版本可在一次准入准备中从有界持久数据确定性构造五份包，并在本次请求内复用；若后续选择持久化角色包，须显式升级 page kind/manifest 和权限迁移，不能将其伪装成旧候选页。尚未持久化的包不能仅信任跨进程内存缓存；恢复可从同一 ready 记录重建并复核完全相同的包摘要。

### 5.2 角色必读集合与 proof

| 节点 | 包中必须本人读完的候选 | 其他必读/可查 |
| --- | --- | --- |
| commerce | 全部 ERP 与原生商品分区 | 全局完整覆盖；可查原生/映射明细 |
| promotion | 全部推广分区 | 全局完整覆盖；有预算时本人读完预算 |
| market_b2b | 全部真实 B 端分区 | 全局完整覆盖（含市场规则缺口）；可查市场明细 |
| independent_review | 全部分区 | 全局完整覆盖；有预算时本人读完预算 |
| report | 全部分区 | 同复核，但不能借用其已读证明 |

角色集合由可信 domain/dataset、固定规则和 role policy 推导，不能依赖模型自报。零 retained 的分区仍有完整计数记录，但不额外强制一次空候选工具调用。本角色无任何候选仍须读完全局覆盖。跨分区/规则的金额不得相加为损失总额。

新 `business_screening_receipts.py` 按实际 job 验证成功审计 dispatch/result：包 role、同 report/run/intent/resultDigest、packageDigest、页 offset 连续、真实 nextOffset、全部页固定 payload/摘要。必须从 0 读到结束，解包后同时满足全部覆盖记录和固定必读分区候选计数；重复/缺页/偷换角色/跨 job 借证明均拒绝。一次工具页含多个分区不减少应核的分区数量。

候选自身带完整可信 current/baseline 数值证明，新 profile 不再机械要求“每个映射至少读一次第一页”来重复获取相同证明；原 integrated profile 的该要求不变。额外自由明细核查仍需实时额度；其没有被调用时不能声称某个任意商品已做专门深查。新的说明和诊断 resolver 必须明确区分“已扫描全部行的固定规则”和“模型实际阅读了全部保留候选”。

proof 不来自模型自报、其他 job 文本或纯 prepared。known provider result 已持久化而证明不完整时，拒绝 finalize 并保留结果，禁止回滚回执后重试模型来制造完成。最终字段区分 executedTablesComplete、requestedCoveragePlanned、requestedTablesExecutedComplete、requestedSourceDateCoverageComplete、entityDailyCoverageVerified=false、matched/retained/omitted 和当前 job 的实际已读包页。读完 retained 不等于读完所有 matched 或全部事实明细。

## 6. 实测准入边界：角色包解决调用爆炸，不保证所有报告能进模型

第 33 批 64 分区 × 每分区 16 候选仅是数学容量，不能转为模型承诺。固定候选策略不变；超出实际模型准入时拒绝模型启动，不能现场降低 K、删除某些分区/窗口/维度、丢弃旧工具消息或把 omitted 隐藏起来。

### 6.1 已做纯合成传输探针

探针文件 `.runtime/screening-runtime-design/probe.py`，证据 `.runtime/screening-runtime-design/evidence.json`。它使用实际 pure plan/scanner 和实际 `provider.tool_frames` 函数，仅构造合成输入，不访问数据库、不调用模型、不授予来源 authority。覆盖 7 来源、55 分区、40 张表；完整保留所有来源/覆盖/分区记录，再按 5.1 的显式列与常量字典传输候选，逐项还原核验通过。call ID 按实际预验的 160 字符计；两个 provider 协议均测实际 UTF-8 与 JSON 转义。

| 每个非 shop 表合成行数 | 角色 | 必读保留候选 | 包页 | 最大实际页字节 | 最大工具帧字节 | 串行读包加 final 轮数 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2 | commerce | 50 | 4 | 37,960 | 142,533 | 5 |
| 2 | promotion | 31 | 3 | 37,773 | 125,301 | 4 |
| 2 | market_b2b | 0 | 3 | 37,935 | 97,829 | 4 |
| 2 | independent_review | 81 | 4 | 37,847 | 170,077 | 5 |
| 2 | report | 81 | 4 | 37,835 | 170,017 | 5 |
| 32 | commerce | 330 | 8 | 37,763 | 355,962 | 9 |
| 32 | promotion | 323 | 8 | 37,990 | 323,839 | 9 |
| 32 | market_b2b | 0 | 3 | 37,846 | 98,265 | 4 |
| 32 | independent_review | 653 | 13 | 37,984 | 581,837 | 14 |
| 32 | report | 653 | 13 | 37,984 | 581,669 | 14 |

**已证实的范围：** 55 分区不必变成 55 次调用；小候选样例的完整阅读传输可以压到 4 页、5 轮，落在 6 轮的必要条件内。压力例仍超 192 KiB/6 轮，新 profile 必须拒绝。所有原 retained 候选均保留，没有靠减少候选得到这些数值。

**仍存在的明确能力缺口：** 170,077 字节只剩 26,531 字节 transcript 空间；探针没有计完整节点指令、reference/额外 response wrapper、下游依赖、预算页和模型 token。当前 integrated preflight 还固定预留 `2*38000+4096=80096` 字节明细空间，原样复用必定失败。当前复核/整合依赖输出限额分别合计 6,000/7,500 字节，`<` 的 HTML 安全替换再外层编码也可能超过上述剩余空间。因此此探针**不能宣称典型 7 来源报告的现有完整 graph 已通过模型准入**，也不能据此开放入口。

第一版角色包合同实施后须再做完整 graph 双转义探针和真实模型配置 token 预验。新 profile 可取消强制重复明细页预留，保留实际候选引用校验及可选明细的实时硬门禁；但仅这一变化尚不证明依赖最坏情况可容纳。若仍不足，先做更紧凑且可精确还原的覆盖表示，或另行固定更小的专业输出/依赖合同并验证不丢必需结论；不得在运行时裁掉已产生的专业结果。未达标就明确返回模型容量不足。带预算情形必须另外计全部实际预算页，不能沿用无预算结论。

### 6.2 新预验与实际工作量

逐节点 preflight 必须取真实角色包、必需预算页、固定新 graph、完整 reference，按实际 `provider.tool_frames` 加最坏 call ID 与依赖双转义探针计算。分别核 maxCallsPerRequest（可先固定 8）、总 dispatch/round/message 上限、192 KiB transcript、24 KiB node input 和模型 token；fit_context 不得丢消息。可选额外工具没有提前获得无限配额，运行中超过剩余额度必须停止且保留已知回执。

原始 55 分区页接口继续作为内部验证入口，不用于新模型默认分页。若在有界无损表示下，某些报告仍有几百/上千条保留候选，则需要另一版明确分组阅读/子任务汇总协议；各子任务固定分区与独立回执，复核拿完整分组结论/遗漏/引用。当前 5 Agent 不自动等于无限上下文。

一份 7 来源、55 分区计划有 40 个 descriptor，原生每维度/比较分别全量聚合，映射含 master 核验。“每报告准备一次”不等于事实只读 7 次。ready 后包构造读取有界持久页，翻页权限/封存轻验不扫事实；文件 attempt 按第 7 节单独复算。记录真实次数和临时空间，不能凭设计承诺固定耗时。

## 7. 诊断引用与同证据文件交付

第三十九批已实现 [内部候选数值解析](AI_BUSINESS_SCREENING_CLAIMS.md)，固定候选的本期/基期/差额可以从实际角色包核验。尚未接入下述实际job证明、诊断输出及文件，因此不能据此允许旧报告自动获得新引用能力。

新诊断协议可接受固定 candidateId，服务解析至 ready 快照中的规则、原 rowId/rowIndex、source/pair、current/baseline 精确数值。模型不能修改这些字段来“解释”另一条候选。引用校验应核规则条件与原始数值证明；额外自由观察仍走既有原生/映射 owning resolver，并标为额外观察，不扩张筛查覆盖。

保留人工作为因果判断和正式发布门禁。`prepared_unpublished`、成功 run 存在、某 Agent 已读完页都不等于人已复核；三者也不能直接允许正式文件。新版 content 首先核所有必需 job 的新 proof，再生成结构化诊断和覆盖说明，最后沿原人审流程。

文件需要：

1. 固定同一 report/evidence/catalog/mapping/budget/screening intent 与 ready resultDigest；新文件 binding 包含这些摘要，变动则新建文件版本。
2. 每次 attempt 准备阶段完整重算一次筛查，与 ready 结果（含行/候选/覆盖摘要）精确相等；随后继续导出完整事实/原生/映射表。不要把候选表顶替原有完整明细。
3. 附独立“筛查范围与缺口”“规则全量计数”“保留排名候选”表；明确 omitted 和日期限制，不能把保留候选标题写成“全部异常”。不可把跨规则/维度的金额相加成总损失。
4. 使用严格新 full manifest 版本或独立固定扩展协议，显式绑定 screeningPlanDigest/resultDigest/algorithmVersion。旧 `business-volume-files-v1` 的字段/验证保持原样；compact `business-file-delivery-v2` 可继续使用现有文件字节清单，是否改版由新 full manifest 接线测试决定。
5. 创建/恢复/发布继续完整 content、读取 proof 和人工复核资格检查；同 attempt 内可复用已完成数学结果，不能跨失权/版本变化复用。ready 后下载每块只轻验权限/绑定/块与整文件摘要，不重算筛查。

第一次接入允许数据较小而只有一卷；须证明多卷拆分后覆盖/候选表不缺行、budget 原生三页仍只在第一卷，完整清单引用同一 screening ready 结果。文件字节额度与筛查的 64 分区/16 候选限制是不同容量。

## 8. 可并行实现分工与合并顺序

建议先锁定 intent、ready manifest/page 和新 runtime reference 三份纯合同，再按不重叠文件分工：

| 切片 | 独占文件建议 | 必须等到的合同 |
| --- | --- | --- |
| A：持久底座与恢复 | 新 screening models/store/migration/tests；database_contract/table_manifest/health；升级备份脚本由主线程单独负责 | 固定 intent/run/page schema、原子集合与配额方案 |
| B：新 profile/准备调度与读取证明 | 新 business_screening_runtime/preflight/receipts/tools；主线程协调 business_reports/workflows/business_parallel 的精确新分支 | A 的 ready 读取/发布接口；新版 graph 与页集合 |
| C：TS 工具与路由 | tool-registry-contract/tool-registry/django-edge/business-evidence 新条目及独立 Node 测试；路由 allowlist 指定一名集成人负责 | 新工具 schema、response 绑定与 38,000 字节合同 |
| D：诊断/文件 | 新 screening claims/export helpers 与 business_files/business_volume_files 新 profile 分支、新 full manifest 纯合同和测试 | ready 引用、proof 结构、文件 binding 字段 |

资源有限时先并行 A/C，主线程做 B 的合同/测试；A 存储门禁通过后接 B，随后 D。禁止几个 Agent 同时改 business_reports/workflows 的同一分支。每片都可保持入口关闭；只有 A+B+C+D 与端到端证明齐全，才能让工作台显式选择新报告版本。旧按钮不能静默换 profile。

## 9. 必测清单

- 创建循环：原请求一次持久化、intent 首次固定、snapshot/input 全程字节不变；ready 前零 child job、零 provider dispatch；纯 dryRun 也明确区分数学已筛查与模型未调用。
- 并发与崩溃：两个准备者同 report、失效租约、扫描后取消/失权、提交中失败、部分页/伪造 ready、配额真实双连接竞争；失败无孤立成功，重试不混入另一结果。
- 旧兼容：所有旧 profile graph、工具 entries/surface SHA、预算/映射/renderer 行为与恢复原样；新 profile 不能冒用旧参数或绕过新 readiness gate。
- 每 job 读取：后页最大退款仍进候选；漏角色包尾页/覆盖记录/必读分区候选、跨 job/role/run 借证明、乱序重复 offset、零候选分区、字节收缩后的真实 nextOffset；解包逐值相等且完整计数。
- 模型准入：55 分区低候选实际完整 graph/依赖/模型配置与有预算场景；653 候选明确拒绝；真实最宽中文页、转义和两个 provider 协议。数学计划可行但上下文/调用额度不够时零模型启动，不截候选、不自动减少范围。
- 未知回执：早 final 或已知成功模型响应后 proof 失败，持久 provider/tool 结果保留，恢复不重放外部调用。
- 权限与固定绑定：账号/role/scope/owner、workflow input、seal/catalog/source info、mapping/budget 参数或算法变化，准备/读取/恢复/文件均失败关闭。
- 文件：真实 sealed→筛查快照→五 Agent→独立复核→人审→renderer 4；至少一条引用位于原明细第二页以后；完整原生/ERP SKU/SPU/比较及筛查表均在 HTML/XLSX，金额不扇出、候选遗漏明确、预算仅第一卷，下载逐片/整文件 SHA 一致。
- 实测成本：记录每 report 准备次数、每 descriptor 源页读取次数、每 job 只读 snapshot 次数、每文件 attempt 重算次数和峰值临时空间；不能仅凭单元测试说性能已改善。

这批完成的判断是：新版报告在模型前取得固定、完整、可恢复的筛查事实；每个 Agent 独立读完合同要求的候选；同证据文件和人审完整闭合。不能把单独完成持久快照或新增一个候选工具当作整批完成。
