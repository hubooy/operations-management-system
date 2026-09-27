# 完整筛查报告的容量预检

第三十八批候选固定了下一版本的五 Agent 报告合同及内部容量测算。它接收第三十七批的完整角色阅读包，**不注册新报告入口，不派发模型，也不授予运行或已读资格**。所有结果固定包含 `previewOnly=true`、`runtimeAdmissionGranted=false`、`modelDispatched=false`。

实现位于 `backend/ai_assistant/business_screening_runtime_contract.py`、`business_screening_preflight.py` 与 `test_business_screening_preflight.py`。验收摘要见 [候选证据](evidence/ai-business-screening-preflight-candidate.json)。

## 固定协议与报告内容

未来 profile 为 `business-agent-screening-reference-v1`，surface 为 `business_agent_screening_v1`。三项独立新工具名为 `get_business_screening_package_v1`、`get_business_screening_analysis_table_v1` 和 `get_business_screening_budget_v1`。这些名称尚未因本模块导入而加入旧目录、权限、路由或工作流。

固定图仍是商品、推广、市场/B端三个专业节点并行，随后独立复核、整合报告、人工复核。专业节点输出各不超过 2,000 UTF-8 字节，复核 1,500 字节，报告 8,000 字节。没有额外辅助 Agent 生命周期。

专业结果保留 `summary/findings`；每项有 `id/kind/title/explanation/references`，行动另外包含完整 `action`。行动字段为 `object/change/prerequisites/successMetric/observationDays/rollback/priority/ownerRole/budgetImpact`。独立复核保留 `approved/conflicts/limitations`；整合输出 `sections+diagnosis`，采用原五个业务章节，最多 12 条 findings、32 个引用。

候选结构化引用只有 `{candidateId,metric,field}`，其中 field 为 `value/baseline/difference`，**引用中不填写数字 value 或 number**；真实数值由独立候选解析器确定。可选原生/映射明细核查继续使用精确 source/pair、基期、维度、行号、行 ID、指标及字段引用。新图不继承旧集成协议的“必须再读一页映射明细”要求。

每个角色必须读取自己的全部角色包页，保留所有来源、覆盖、缺口，以及各规则分区的匹配、保留和遗漏计数。不能以候选、排名或部分明细代表模型已读全部事实。推广、独立复核、报告节点有固定预算时须完整读取预算；其他角色若主动开始读取预算，未来运行时也须完成。

## 输入、固定意图与真实性边界

内部 API 为：

```python
measure(role_pages, reference, *, model, entries, budget_pages, guidance="")
```

`role_pages` 必须提供五个角色的完整确定分页；逐角色执行真实 `decode_pages`，拒绝缺页、重复页、错角色、摘要变化和非完整前缀。五角色的 binding、authority、来源、来源元数据、覆盖、表来源绑定必须逐值一致。额外核对固定报告、证据、持久 manifest、结果摘要与每角色 packageDigest。

`reference` 是容量预览封套：`schemaVersion=business-screening-preflight-reference-v1`、真实 `workflowInput`、持久 `screeningReference` 和五角色 `packageDigests`。不是创建请求，也不是授权凭证。真实来源 input 的 SHA 必须等于包中 binding.workflowInputDigest。

旧报告可以参加预览。函数保留其原 input，另构造 `proposedWorkflowInput`，补齐实际报告 ID 和下述固定意图，不修改原报告或工作流。如果原 input 已有意图，则必须精确相等，不允许替换：

```text
schemaVersion = business-screening-intent-v1
id = 精确预分配的持久筛查 ID
selectionPlanDigest = 完整固定范围摘要
selectionPolicy = screen-selection-v1
algorithmVersion = diagnostic-signs-v1
capacityPolicy = screening-storage-v1
packagePolicy = screening-role-package-policy-v1
```

`intent(screening_id, selection_plan_digest)` 返回上述七键对象，`INTENT_FIELDS` 固定字段集合。预览返回原 input 与 proposed input 的摘要，以及两者是否已相等。未来正式准入必须要求实际报告是新 profile、实际 input 等于 proposed input，并复验 ready 记录；不能直接把旧报告预览结果当创建或运行授权。

`budget_pages` 输入既有 `business-budget-page-v1` 完整原页。核对预算参数引用、报告/账号/范围/证据五字段、页 SHA、严格整数分页、全部行号及终页。预算缺失不能降级为无预算；没有固定预算引用时不能塞入预算页。此检查不能替代 owning 服务对真实预算参数和事实的重新计算。

公开 JSON 和自报 SHA 仍不提供事实或账号 authority。模型配置及工具目录由调用方提供，本函数不查询配置、不连接数据库，也不检查实际 job 身份。

## 实际测量方法

容量按完整原文测量，不裁剪候选、缺口、预算对象或依赖，不用更短占位页假装真实工具响应：

- 使用实际 `provider.tool_frames`，分别测量 OpenAI 兼容与 Anthropic 帧，call ID 为 160 字符。
- package 调用包含 `runId/reportId/screeningId/role/offset`，role 固定本人角色；预算调用包含 `runId/reportId/screeningId/offset`。
- 预算工具的新封套、固定引用和 pageDigest 同样计入响应与帧字节。
- 将固定完整节点指令、proposed input、依赖输出、系统提示词、额外 guidance、工具目录与模型输出 token 预留全部纳入相应测量。系统、目录计入 `fit_context` 的 token 估算，不混称为 transcript 字节。
- 依赖用引号和 `<` 两种最坏探针，覆盖节点 JSON 转义、运行时 HTML 安全替换和外层帧再次编码。人工复核节点也检查输入容量。
- 节点输入上限 24 KiB；完整 transcript 上限 192 KiB；每工具最多 8 次，总调用最多 40 次、工具轮次最多 20，并同时受实际模型更小配置限制及 64 帧边界限制。
- 调用真实 `fit_context`，任何删除消息或改变消息内容的结果均拒绝。Token 为现有 UTF-8 保守估算，不是供应商 tokenizer 或实际计费。

不强制预留可选明细调用，返回 `optionalAnalysisReserved=false`、`analysisPagesRequired=0` 和剩余 transcript 字节、工具调用次数、轮次。**剩余量不是任意明细页的许可**；未来实际调用仍须重新执行实时硬限和授权。

格式、完整性、绑定错误抛出 `AiError`；容量不足保留各节点的完整测量及失败原因，返回 `fits=false`。不能通过删源、删分区、少读候选或放宽原上限取得通过。

## 验收结果

11 项纯测试通过，最终留档复测用时 28.770 秒，完整日志为 `.runtime/batch38-screening-preflight-tests.log`。测试使用实际纯计划器、扫描器、持久序列化、角色包编解码、预算计算和 provider 帧函数；仅模拟禁止调用的模型边界与特定 `fit_context` 错误响应。使用 `django.setup()` 加标准 `unittest`，未创建或连接测试数据库，未调用模型。

覆盖完整五角色、预算缺页/错绑、跨角色/manifest/input/摘要变化、严格数字类型、旧 input 不变、新意图相等、两种协议、160 字符调用 ID、调用/轮次/token 限制、最坏依赖转义、人工输入容量、禁止上下文删除、过量候选零裁剪和输入容器边界。

最终完整指令和参数下的合成测量如下，单位均为实际 UTF-8 字节，transcript 取两种协议及两种转义探针的最大值。模型配置为 128,000 上下文 token、2,048 最大输出 token、40 次工具调用、20 轮；目录为测试中的三项明确工具定义，不代表尚未注册的未来生产目录 SHA。

| 样例 | 商品页数/字节 | 推广页数/字节 | 市场/B端页数/字节 | 独立复核页数/字节 | 报告页数/字节 | 完整预览 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 小型完整样例 | 1 / 43,226 | 1 / 40,681 | 1 / 40,118 | 1 / 86,551 | 1 / 97,980 | 六节点通过 |
| 7 来源、55 分区、每表 2 行样例 | 4 / 134,554 | 3 / 115,995 | 3 / 98,595 | 4 / 193,559 | 4 / 204,916 | 拒绝：报告超 196,608 字节 |

每表 32 行的 55 分区样例同样拒绝，并触发调用次数、transcript 和模型 token 容量限制。候选与所有输入摘要前后不变。第三十七批“工具传输页可以放下”的结果不能替代本批完整上下文预检；**55 分区当前不能保证进入模型报告流程**。

复现纯测试：在 `backend` 目录使用候选虚拟环境 Python，设置 `DJANGO_SETTINGS_MODULE=teruisi_backend.settings`，执行 `django.setup()` 后，以 `unittest.defaultTestLoader.loadTestsFromName('ai_assistant.test_business_screening_preflight')` 运行标准 `TextTestRunner`。不得为了此纯套件启动 PostgreSQL。

## 尚需接入的完整链路

1. 新 profile、独立工具 surface/TS schema、签名转发/API 与生产 reader URLConf 一次注册，保留全部旧工具和 profile 摘要。
2. 创建时固定不可变 intent、映射与可选预算，完整持久幂等；后台准备租约、同 ID ready 原子发布、失败恢复及严格身份复验。
3. scheduler 和每次模型派发都核实际新 profile/input/intent/ready、当前模型和真实 catalog，使用本预检的实际完整页面重测；超容量不得发起模型。
4. 各真实 job 分别绑定角色，完整 package/预算页持久回执、未知派发不重放、早 final 拒绝、恢复后不借用其他角色的读取证明。
5. 候选引用接入新报告诊断校验，与既有 native/mapped resolver 共同验证；专业冲突、人工复核和最终行动字段仍须完整保留。
6. HTML/XLSX 多卷交付绑定相同证据、筛查结果、规则/包版本及完整覆盖计数；创建、恢复、发布复验，下载不扩大权限。
7. 真实五 Agent 全链路、代表性规模、付费模型质量和工程文件验收。本批没有生产访问、迁移、部署、启停服务、外部消息或模型付费调用。
