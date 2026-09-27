# 封存证据派生结果复用设计

状态：**第二十九批候选已接入单次 content 校验复用，未生产采用**。2026-09-17；跨请求、跨 Agent tick 和持久派生缓存仍是后续设计。本批没有新增迁移，不授予服务启停或上线资格；实际验证见候选证据。

## 建议：下一批先做同一次校验内的结果复用

候选由 `ContentReuse` 提供可信报告范围，显式传递 `CompletedReuse`，只在一个报告、一次校验阶段内复用**已完整计算成功的规范 JSON**。不保留活 SQLite、游标或生成器，不跨 HTTP 请求、Agent tick、文件 attempt、进程或 owner 共享。不新增数据库表或迁移。每次使用仍实时核验 principal 和固定报告绑定，每个 Agent 的回执仍分别验证。4 MiB 容量是规范 JSON、键与摘要的计费量，不是 Python 进程总内存或 RSS 上限。

首批解决重复的固定预算计算与相同分析页重算；不宣称已经解决完整导出每个维度的重复扫描，也不把任务级缓存、跨请求分页加速包含在本批。

## 一、当前代码能证实的重复代价

| 当前位置 | 实际行为 | 可复用的部分 |
| --- | --- | --- |
| `business_integrated_receipts._trusted/progress` | 每次 proof 调用 `prepare_for_report(resolve_budget=True)`；有预算就经 store.load 完整 resolve。随后对每条成功 analysis 调用 analysis_from 重算，包括以前 tick 已验证的页 | 同一次上层校验中，完全相同固定预算及精确分析页 |
| `business_reports.content` | 对五个已完成 job 逐个执行 execution_surface、validate_complete，之后 diagnosis.validate 和 for_report | 五份独立 proof 中重复的数学结果；不能共享 job 是否已读完的判断 |
| `business_integrated_tools.analysis_from` | native 每次 build_table 都消费完整来源；mapped 每次进入 mapped_analysis.table，重新执行显式关联、聚合和比较 | 已完成的相同 mode/selector/dimension/offset 结果页 |
| `business_budget.resolve` | 同一次 resolve 已按 sourceKey/dimension 分组，每组只扫描一次；多次 load 会重新扫描 | 不应再优化它已合并的 target 内循环，而应减少外层重复 load |
| `business_mapped_claims.resolve` | 每个引用独立打开映射表，即使只是同一行的 value 和 difference | 后续可增加精确行复用；首批不改变 claims 协议或取行方式 |
| `business_export.package` / `business_mapped_export.append` | 原始表、不同维度、SKU/SPU、不同基期分别执行完整扫描并落 TableSpool | 首批不缓存完整表；避免把 256 MiB identity 和 128 MiB derived 的活对象长期叠加 |
| `business_files.binding` / `business_volume_files.build` | 创建、恢复、发布使用完整内容验证；chunk 采用轻量固定绑定。当前 integrated+budget 分支已复用局部 content，不再重复调用两次 | 不取消这些阶段门禁；不同阶段明确使用新的复用上下文 |

计算举例（调用计数推导，不是实测耗时）：若五个 job 的 proof 都加载同一预算，而 commerce/review/report 都保存了同一个 mapped 页，那么单次 content 内可把这五次预算重算降为一次；末尾 for_report 使用同一结果后也不新增一次。三个相同 mapped 页可降为一次数学计算；五个 job 的工具 ledger、摘要、审计、顺序、必读条件仍各验证一次。若参数/offset 不同，或容量不足发生淘汰，不承诺这个命中率。

当前限额：PreparedBudget.result_json ≤2 MiB；集成工具 data ≤38,000 UTF-8 字节；每 job 最多40派发；原生分区 SQLite 上限256 MiB/25万组；映射派生128 MiB与同时一个 identity128 MiB合计≤256 MiB。TableSpool另有131072页上限（实际字节取 page_size），**不是**上述映射组合额度的一部分。首批复用不新增磁盘，也不把这些既有独立额度误称为全进程统一额度。

## 二、最小内部接口与接线

以下均为建议 API，不是已有实现。

```python
with DerivedReuse(report, principal, phase="report-content") as reuse:
    # 每个 job 独立扫描 ledger；只在数学求值处查 memo。
    validate_complete(job, snapshot, principal, reuse=reuse)
    budget = reuse.fixed_budget(report, principal)  # 可选预算，返回新副本

# 内部方法；每次会实时 bound，不能传入 client 自报的可信摘要。
reuse.analysis_page(report, principal, arguments, compute)
```

`analysis_page` 的 compute 回调只由内部工具适配层传入，不能由模型、请求体或外部插件指定。建议新增 `ai_assistant/business_derived_reuse.py` 和独立测试文件；核心变动限于：

1. `business_reports.content`：仅 integrated 分支建立一次显式 scope，传给五个 proof。最后预算读取使用同一 scope 的固定结果。旧 profile/旧返回字节保持原分支。
2. `business_integrated_receipts.progress/validate_complete`：增加可选内部 reuse；未传则只建立该次 proof 的短 scope。每条 dispatch/result 的加载、摘要、provider所属job、序号、unknown、audit和内容比对保持原样；不能缓存整个 proof。
3. `business_integrated_tools.prepare_for_report/analysis_from`：新增可选内部 reuse 接入点，把当前完整原生或映射计算抽为不带 memo 的私有实现，避免 memo miss 递归。目录/预算页可从同一个 PreparedBudget 组装，但完整 reference 仍来自当次 bound。
4. `business_budget_store`：公开的 load 保持完整重算默认。仅新增/整理内部“先轻量绑定、再完整计算”的 helper 供 scope 调用；缓存命中仍走 binding_for_report，不能直接返回未核验参数对象。create/prepare/insert/preflight继续走原完整重验；首批不缓存事务前准备对象。
5. `business_files` / `business_export`：首批无需扩散 memo 到完整文件阶段；content 自身的短 scope 即可获益。不要把一个 scope 包住整个 build、_verify_staged 和最终 ready 发布。

不改工具目录、surface、graph、snapshot、预算/关联算法、页响应字段、数据库模型、guard、备份清单或既有renderer。工具返回值、报告内容和可确定的文件字节必须保持一致。

## 三、键必须绑定真实身份与算法

scope 固定：当前 owner.email.lower、canonical(scope)、角色/权限指纹、reportId、canonical(snapshot)摘要、workflowId、阶段。不得使用 email 一项作为权限许可；命中时仍执行 current_principal(admin=True) 与 integrated.bound。

每个条目键至少包含：

- 封存五项绑定：evidenceRunId、evidenceVersion、evidencePlanDigest、catalogDigest、sealedDigest，加 sourceCount。
- 完整 mappingPlanDigest；mapping algorithm 与 mapped table algorithm 常量；非映射 native 也要绑定该报告 snapshot，以免跨报告共享同页。
- 预算：budgetRef 全四字段、规范参数 planDigest、calculatorVersion。无预算与预算缺失不是同一种状态。
- 分析：mode；sourceKey/baselineKey 或 pairKey/baselinePairKey；dimension；严格整数 offset、固定 LIMIT；整个规范 arguments。缺省offset可在通过字段验证后规范为0，未知字段不能为了命中被删除。
- 来自真实目录/Reader.info 的双方 queryDigest、sourceRef、evidenceDigest、metadata摘要。不能信任 result 自报的 sourceDigest。
- 内部 reuse schema/算法签名。native 当前没有独立算法版本常量：首批应添加独立**内部**实现版本戳，只用于内存键，不回写旧结果；进程版本切换也不会复用旧 scope。

缓存值只能由当前代码完整重新计算产生。不能把数据库里“ok+recorded”的工具返回页直接装入缓存后用于验证自身，否则会把重新签名的篡改变成真值。

## 四、成功边界、权限与并发

miss 的顺序：实时绑定 → 完整计算（所有 Reader 页链/控制总额和映射 groupsDigest/resultDigest/rowCount/totals核验）→ 退出所有 source/context，晚失权检查通过 → 再次实时绑定 → 规范序列化及容量检查 → 标记 complete 后存入 → 返回独立解码副本。

hit 的顺序：实时 principal + report/workflow owner/scope + immutable snapshot/input + evidence seal/catalog + 固定可选预算参数绑定 → 键精确一致 → 核内部 bytes/digest → 返回新副本。scope 的正常退出再复验绑定；调用方必须在退出之后才发布结果，不能在 with 内直接对外提交成功。

不缓存 authorize_owner、current_principal、bound 的成功结果，不缓存异常、未知派发、未读完页面、失败审计或未退出 context 的临时结果。未知工具结果仍直接 tool_dispatch_unknown，不触发 compute 来“补出”回执；失败回执不计覆盖。

同 scope 内不同 job 可以复用纯数学结果，因为它们已被限定同一 owner/报告/阶段；每个 job 仍须拥有其本人完整目录、条件预算与映射工具回执，不能借别人的读取证明。scope 不得跨线程/异步任务传递；绑定创建线程并拒绝越界。并行三个专业 Agent 各建各的 scope，首批不 single-flight、不跨 job tick 共享，不新增长等待锁。

## 五、容量与清理（建议策略，非现有限额）

- 每 scope 最多64条完成条目、规范 JSON + 键 + 摘要合计≤4 MiB，单预算结果仍≤原2 MiB，单分析 data仍≤38,000字节。
- 只保存不可变 UTF-8 bytes/字符串与小型索引；读取时解码副本，避免调用方修改缓存。4 MiB是序列化载荷额度，**不是 Python RSS峰值保证**；活计算对象及临时解码开销另测。
- 有界 LRU；满时淘汰完整条目。不能缓存的合法大条目直接按原逻辑计算并返回，不缩字段/截行、不放宽旧容量、不引入新的业务拒绝。内部摘要损坏则失败关闭，不能把损坏当命中或吞掉后“自动恢复”。
- 如允许多个HTTP/文件校验并发，增加仅用于缓存预留的进程内计数：每owner最多4个可缓存scope、全进程最多16个，即规范缓存载荷预留上限16/64 MiB；超过时走不缓存的原逻辑。计数锁只保护预留/释放，不包住数据库读取、计算或模型。
- finally清空条目、释放预留；正常/异常/取消均不得残留引用。递归同键尚在building时拒绝内部重入，不把不完整值暴露出去。不得将payload、临时路径或业务明细写日志。
- 不创建临时目录或持久缓存，因此无缓存文件清理任务、无恢复权限问题；进程退出/异常重启后缓存自然丢失，下次从封存事实重算。原持久工具/provider回执和文件attempt继续由原逻辑恢复。

## 六、为何首批不做持久缓存或活表复用

持久缓存需要可验证表级摘要、owner/global额度、事务发布、并发创建、过期回收、reader/writer权限、恢复时校验算法版本与完整性，并进入备份/迁移/reverse测试。仅保存一个SHA或expiry无法证明缓存与真实封存表一致，也无法作为新授权。因此不能把磁盘JSON文件或临时SQLite路径登记成“可恢复结果”绕开这些工作。

跨分页保留 native/mapped 活表可以减少不同offset扫描，但会延长SQLite生命周期和锁/游标持有。若仍同时允许256 MiB identity与128 MiB mapped或导出spool继续增长，简单保留对象可能突破原组合档位。应后续另做显式工作区配额、只读行存储与完整完成标志；本批不承诺其收益。

完整映射导出按不同SKU/SPU和基期扫描仍存在。下一阶段可考虑一个pair的完整groups落有界一次性scratch后被SKU/SPU串行消费，但必须重构 mapped_results 当前仅接受可信 identity._Result 的边界，不能把任意字典列表当作已核验来源；它不是本批顺手可做的小缓存。

## 七、一批可验收的测试与收益记录

1. **结果不变**：开启/关闭复用，相同报告的目录/analysis/预算/diagnosis内容逐字节相等；原profile和旧工具摘要无差异；renderer1/2/3原字节及renderer4无mapping清单回归保持。
2. **可计数收益**：构造真实sealed current+previous+master+promotion、五个独立job成功回执。同一次content中记录 Reader.pages、完整budget.resolve、native build_table/mapped_table 调用。无预算及有预算各测；相同mapped参数三job从3次求值降1，预算五proof及末尾读取共6次降1。只计算这条明确调用链，不把输出构建的额外扫描混作已消除。
3. **不借证明**：commerce已读，report没读，虽命中相同页仍拒绝report完成；目录乱序/缺页/unknown/failed/未审计依然拒绝。
4. **篡改**：使用 batch27 runtime tamper 用例，工具篡改后重算全部自报摘要仍在新provider派发前拒绝。确认不得用该工具结果填memo。内部缓存载荷损坏也拒绝。
5. **绑定变化**：逐项改变owner、scope、角色、reportId、snapshot、workflow input、evidenceVersion/catalog/seal、mappingPlan/algorithm、budgetRef/calculator、source/query/offset；跨owner不得命中，失权不得退回miss后继续成功。中途禁用用户和context退出失权均零发布。
6. **生命周期与容量**：晚尾页损坏、partial generator、mapping context退出异常均没有complete条目；64条/4 MiB边界、LRU后重算、跨线程拒绝、异常清理、进程重启空缓存。配额统计不泄漏owner条目。
7. **规模记录**：5001及30001行合成、重复精确页与全不同页各一组；记录开启/关闭的源页读取次数、SQLite峰值、缓存序列化峰值、tracemalloc与RSS、耗时。性能验证用相同输入完整消费，计时不是正式服务SLA，缓存不得让未消费尾页消失。
8. **文件阶段隔离**：构建content和最终发布content使用不同scope，发布仍至少独立完整求值一次；immutable chunk只走轻绑定且不触发Reader扫描。失败attempt恢复不复用旧进程结果，不自动重放模型或未知工具。

实施前先固定 scope API 与真实扫描计数基线，再实现 bounded memo、接 integrated 分支，最后跑失败/权限/容量和完整五Agent文件回归。对外仍应称“单次校验内复用”，不称“已完成持久派生缓存”或“全量只扫描一次”。
