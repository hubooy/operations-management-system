# 经营分析集成协议与候选实现

状态：**第二十七批候选已实现 integrated runtime，未生产采用**。协议、迁移 0022、只读工具、固定报告创建和多卷文件已接入候选代码，合成验收及限制见第 7 节与候选证据。真实经营数据、付费模型质量和正式部署仍须独立验收，不能将候选实现等同于上线。

目标是在同一固定封存证据下，由用户明确选择 ERP 销售与当前商品主数据的关联，供独立专业 Agent 核验；可选沿用现有推广预算。关联不自动改写商品主数据、报告历史或业务预算，不推断店铺别名、来源归属或自然语言身份。

## 1. 固定协议与兼容边界

新增执行协议 `business-agent-integrated-reference-v1`，对应独立 surface `business_agent_integrated_v1`。新协议使用独立 graph、工具条目、预检与读取证明。旧 profile、旧 graph、工具定义和 allowedSurfaces、全部旧 catalog 摘要保持不变。无关联计划的原调用继续原路径；不得仅因部署新代码自动转换历史任务。

预算仍使用原 `business-budget-reference-v1` 四字段引用和 `business-budget-binding-v1` **13 字段**绑定。参数表、唯一报告 FK、48000 字节计划、4096 字节绑定、100 目标存储上限，以及原 owner/global 字节与行数配额保持不变。新协议不向预算绑定加入 mapping 字段，也不把 ERP 关联销售作为推广归因成交或预算收益基线。

## 2. 精确 snapshot 与轻量输入

报告继续使用不可变 `ai_report_runs.snapshot_json`，无需新增关联参数表或 FK。保留现有 `schemaVersion='business-report-v1'`、`executionMode='parallel-v1'`、`evidenceProtocol='reference-v2'`、question、scope、模板等字段，以及六项封存引用：`evidenceRunId`、`evidenceVersion`、`evidencePlanDigest`、`catalogDigest`、`sealedDigest`、`sourceCount`。

新分支增加或固定以下字段：

| 字段 | 合同 |
| --- | --- |
| `executionProfile` | 精确为 `business-agent-integrated-reference-v1` |
| `reportId` | 精确等于实际报告主键 |
| `mappingPlan` | 下述完整规范计划；不截断、不存事实明细 |
| `mappingPlanDigest` | `SHA256(canonical(mappingPlan))` |
| `budgetRef` | 可选，沿用原四字段引用；缺省表示无预算，不接受 null 伪装 |

`mappingPlan` 只有三个字段：`schemaVersion='business-mapping-plan-v1'`、`algorithmVersion='exact-product-partition-v1'`、`pairs`。每对只有 `pairKey`、`salesKey`、`masterKey`。`pairKey=SHA256(canonical([algorithmVersion,salesKey,masterKey]))`；pairs 按 salesKey/masterKey 排序，每个销售来源最多出现一次。规范 JSON 使用 UTF-8、排序字段、紧凑分隔符，不转义中文，禁止 NaN。

完整目录最多 48 来源，计划 1—47 对且最多 16000 UTF-8 字节。来源键按现有有界标识符合同；sales 必须是 ERP 销售域，master 必须是同一精确平台/店铺的 netshop/master/current。多个销售窗口或渠道可以显式复用同一个当前 master，不得复制一条销售事实到多个候选 SKU。

**mappingPlanDigest 只固定选择，不单独绑定来源查询或事实。** 同一组来源键保持不变而合法渠道、日期变化，计划摘要可能不变；`catalogDigest`、证据计划摘要和封存摘要必须一起核验。关联返回的 binding 还须核对两端 `queryDigest` 和 `sourceRef`，不能把服务响应自报的摘要当权威。

报告 snapshot 整体仍限 32768 UTF-8 字节。工作流输入仍限 8000 字节，不能放入完整 mappingPlan。在原 `workflow_reference` 上仅加入：

```json
{
  "reportId": "固定报告ID",
  "mappingRef": {
    "schemaVersion": "business-mapping-reference-v1",
    "planDigest": "完整计划摘要",
    "pairCount": 1
  }
}
```

有预算时再加入原 `budgetRef`。这里的 mappingRef 是轻量导航，不是独立授权凭证；运行时必须通过当前用户可访问的实际报告读取完整计划。禁止内嵌 budgetPlan。无预算时 budgetRef 必须不存在、`budget_plan_id` 必须为 NULL；有预算时二者必须同时存在且精确一致。

## 3. 最小 0022 数据库变更

新增迁移，不修改已经采用的 0021。不增加表、不扩权限、不放开报告 UPDATE/DELETE，AI 自有表仍为 63 张。

新增 integrated 报告 `BEFORE INSERT` guard，直接 writer 写入也必须满足：

1. 严格检查 JSON 对象/数组/标量类型、精确字段集合、重复键、计划排序、数量及 UTF-8 字节边界；缺键、JSON null、浮点版本和重复键不可利用 SQL NULL 语义通过。
2. 新 profile 必须有完整计划、计划摘要、正确 reportId；旧 profile 不允许夹带 mappingPlan/mappingPlanDigest/mappingRef 等新保留字段。新分支禁止内嵌 budgetPlan。
3. 报告、workflow 与封存 evidence 的 owner/scope 必须一致，实际证据必须为 sealed v2；复核六项封存引用、规范 owner 和 unrestricted scope。注意 snapshot.scope 是展示范围，不代替持久 scope_json 权限。
4. 根据持久可信目录逐对核对来源存在、域、current master、精确平台与店铺；核对 pairKey、唯一销售来源、完整计划摘要。重建摘要时须使用与 Python 一致的规范字节，不能用 jsonb 默认含空格/不同排序的文本替代 canonical。
5. workflow.input_json 的 reportId、六项封存引用、mappingRef 和可选 budgetRef 必须与新报告一致。该插入核验不能代替执行期间再次核验可变 workflow 状态。

同时仅替换 `ai_business_budget_report_guard()` 中的精确 profile 许可，使预算 FK 可属于原预算协议或新 integrated 协议；其他绑定和类型校验保持。原预算 initial guard、13 字段绑定、配额锁顺序和 READ COMMITTED 条件、immutable trigger、PROTECT/唯一 FK、deferred 恰一报告与反向 reportId 核验原样保留。不能使用字符串前缀或任意 profile 集合泛化放行。

新 guard 的存在与启用状态加入 health 检查；版本化迁移/备份清单登记 0022，但不改变旧版本清单的含义。逆迁移执行任何修改之前，发现 integrated 报告或其新工作流关联引用即原子拒绝；仅无依赖数据时移除新 guard 并恢复 0021 原函数定义。不得清空计划、删除报告或改写 profile 来满足回退。

## 4. 服务创建、恢复与读取闭环

创建入口显式接收关联选择，不猜测来源。先取得当前用户的完整已封存目录，使用纯 mapping_plan 重建计划；从真实 Reader 核验来源链，任何迟到不一致均拒绝。内部准备对象需防可变别名篡改，并且只允许内部创建流程选择新执行协议。

同一次 mutation 原子完成可选预算参数插入、工作流预检与创建、报告 INSERT、审计。请求摘要覆盖原始关联选择和可选预算参数；在准备前及事务内均检查原请求，未知回执只允许原 UUID/原请求恢复，不得生成新计划。历史预算重新计算或改变关联计划应创建明确的新报告，原报告不可原地更新。

`business_budget_store.binding_for_report` 仅在新 integrated 快照完整验证后允许使用同一原预算存储协议；不得简单移除 profile 检查。预算仍通过原 `resolve()` 和推广来源计算，映射与预算通过同一个不可变 reportId、封存引用和可选 FK 共同绑定。

建议独立 integrated 适配集中提供固定报告读取与核验，避免在各入口散落不同条件。以下位置都要调用：恢复排队前、每次 provider/tool 调度、专业 Agent 完成、人工复核、content、文件创建/恢复/发布。当前通用 `workflows.control(resume)` 只做 owner/CAS/retryable 等检查，新分支须补固定引用门禁；不能仅等恢复成功后下一次调度才发现缺计划。

读取证明按当前 job 独立校验：完整目录先读；关联页须匹配固定 pair、可信 binding、结果摘要和页摘要，连续分页不得缺页、重复、错序或跨 Agent 借用。失败页不计完成、unknown 不重放。哪些专业节点必须读哪些关联结果、是否引入有界聚合页，需在新 graph 与预检中精确定义后再开放，不允许以读完目录或计划代替已核验业务事实。

缺参数、错版本、owner 变化、封存或目录不一致、算法版本未知都失败关闭，禁止降级为普通无关联/无预算报告。已保存的 provider 结果即使最终验证失败也必须保留，避免恢复时自动重复付费派发。

## 5. 比较、诊断与容量语义

同比/环比必须显式选择本期与 previous/yearAgo 对；同一个当前 master、同平台/店铺/渠道及同一原始请求日期区间。比较窗口由现有日期规则计算，不把基期的实际日期重新填成另一个原始请求区间。缺基期不能视为零，缺日期、空值、零基数、退款负数、不可比范围均保留状态。

纯计划只能证明查询组合可比较，不能证明来源有事实、事实日期覆盖完整、历史 SKU 归属正确或数值可比较。实际比较仍须校验事实元数据和覆盖。当前 master 应明确标注为当前快照关联；匹配率 100% 不表示业务核验通过。

推广收益沿用原推广归因及假设情景；ERP 净销售、关联成交、市场样本和 B 端销售分别保留来源口径，不相加制造收益，不用关联结果推导保证利润或因果结论。

47 对与 16000 字节只是计划存储上限，不承诺模型能读完全部关联明细。新映射 HTTP 响应最多 64 KiB，不能直接套用原分析工具 38000 字节的上下文预留；新工具必须按实际 UTF-8、二次转义和协议 frames 预检。完整必读页、工具调用次数/轮次、依赖输出与模型上下文任一超限时，在派发前完整拒绝；不得截断、跳过来源或虚称全量完成。事实仍为 64 MiB/2000 页，旧工具额度不扩大。

## 6. 实施与验收顺序

先落地纯合同、数据库 guard 与固定报告绑定/恢复测试，保持新 runtime 关闭；再实现独立 surface、真实预检和逐 Agent 回执证明；最后接 UI 的明确选择及完整报告/多卷交付。若文件尚未包含固定映射结果，则新 profile 文件入口应明确拒绝，不能只因继承 v2 判断而输出缺关联内容的“完整报告”。原 renderer 1/2/3 和旧 renderer 4 报告字节不变，必要的新文件协议独立版本化。

验收至少包括：

- 旧快照、13 字段预算绑定、graph、全部旧 catalog hash 及旧数据库行摘要不变；新有/无预算路径均成功。
- 直接 writer：缺字段/null/重复键/未知字段、错误摘要、重算摘要伪计划、跨 owner/report/workflow/证据、错域/跨店/历史 master、重复销售来源都拒绝；FK 与 budgetRef 同步存在且参数不可孤立或交换。
- 容量：完整 47 对、16000 与 16001 字节、snapshot 32768 和 workflow 8000 边界；恶意大对象/深嵌套失败有界；不提高事实、参数或模型额度。
- 来源绑定：相同 sourceKey 的合法渠道或日期变化使 catalogDigest 改变，即使 mappingPlanDigest 不变仍拒绝旧报告引用；旧目录缺省 window=current 保持规范兼容。
- 比较：缺基期/日期、零基数/负数/空值、日期覆盖不足、闰日、不同时期 master、原始请求区间不一致，均不伪造增长率。
- 幂等与恢复：相同请求原回执恢复，不同计划同 UUID 冲突；审计/预检/报告插入异常整事务回滚；恢复前失权、缺参数、篡改 workflow 输入拒绝；已保存模型结果不自动重放。
- Agent：缺页/乱序/重复/重算摘要/跨 job 借证明/提前 final 失败；complete、review、content、文件恢复与发布重新核验；实际工具及模型上下文不足零派发。
- 迁移：0021→0022、63 表最小权限/health/fence、旧行逐项摘要、两种新报告存在时逆迁移拒绝、完整 dump/restore 后重新读取与计算通过。迁移测试由主 Agent 串行隔离 PostgreSQL 执行。

## 7. 候选实现状态（第二十七批）

候选代码已接入独立 integrated profile、三项专用只读工具、逐 Agent 持久读取证明、模型容量预检及映射引用重算。创建报告须显式提供 `mappingPairs`；不含此字段的旧请求保持旧路径。预算可选，继续使用原参数记录及 13 字段绑定。迁移 0022 仅新增/收紧函数与触发器，自有表仍为 63 张。

商品、独立复核和整合节点各自至少读取一页已固定的映射分析；所有节点各自读取完整来源目录。预算必读节点须完整读取预算。一页映射结果仅证明该页，模型不能由此声称阅读了所有明细。完整关联分组、每对 SKU/SPU 汇总、兼容的同比/环比及来源证明由确定性文件服务全部生成，并沿用 renderer 4 分卷持久交付。

文件清单新增一组必须同时存在的 `mappingPlanDigest`、`mappingAlgorithmVersion`、`mappedTableAlgorithmVersion`，发布及下载核验其与固定报告的一致性。没有关联字段的旧清单保持原规范字节。HTML/XLSX 共用表分区，单表仍为最多 160 列；映射双方缺侧的比较表实测为 156 列。事实、模型、文件与临时盘容量均不扩大。

此节仅说明候选代码状态。模型/网络边界的合成验收不能证明真实模型经营判断质量；工作台显式选择、正式来源核对与生产采用须另行完成。

## 8. 纯计划独立复审记录

2026-09-17，只读复核 `mapping_plan.py` 与 `test_mapping_plan.py`：11 项纯测试通过，包含精确 16000 字节可用/16001 字节拒绝、47 对不丢失、有界恶意输入、比较窗口和旧缺省 window。另独立执行 6 项探针：合法渠道变更、全目录日期变更、缺基期、缺日期、循环嵌套伪 pairKey、旧缺省 current；结果符合上述合同。

其中前两项确认计划摘要本身不会绑定来源查询变化，因此第 2、4 节的目录/封存绑定是必须实施的安全条件。本记录不代表新数据库 guard、集成 runtime、模型质量或正式数据验收通过。本次未修改旧文档、迁移或作者代码，未运行 PostgreSQL、模型或正式服务。
