# 词货多 Agent 报告的数据库迁移边界

2026-09-24，整合分支候选。前期只读审查时，新 profile 尚无持久形状；随后 `business_promotion_creation_contract.prepare_candidate` 已从真实封存根冻结 `snapshot`、`workflowInput`、五角色图与工具目录顺序，并通过隔离 PostgreSQL 形状测试。基于这份形状新增候选 `ai_assistant.0026_business_promotion_profile`，只覆盖报告、工作流和筛查发布约束。迁移已有 4 项隔离 PostgreSQL 测试通过，独立升级/备份恢复仍待验收；未正式采用。

旧 `business_promotion_runtime.prepare_candidate` 仍只返回 `registered=false`、`readiness=requires_new_persistent_profile` 的旧报告派生候选，不具备写入或派发权限；它不能代替新的报告创建入口。

## 先冻结可持久的完整写入协议

新创建入口须在同一原子事务内写入新的报告、工作流、预分配的 `screeningIntent`、固定的 `promotionSelector` 和现有预算/商品映射引用。创建前从真实已封存来源目录选定一个京东推广当前期 sourceKey、可选同店铺、同原日期范围的 `previous` 或 `yearAgo` baselineKey；拒绝猜测基期。持久报告应拥有自己的新 `executionProfile`，且明确列出 schemaVersion、全部允许字段、不可变 selector 的精确形状、两个固定视图、算法版本和 contextDigest。工作流 input 应精确说明如何复制或引用上述选择，必须绑定 reportId、证据 seal、筛查意图以及同一 owner/scope。新 profile 的允许工具目录、固定图和模型入场协议须与持久内容一致。

当前已冻结的持久字段由 `business_promotion_creation_contract` 给出。迁移从 `0024` 及其 `0023` 前驱精确派生旧函数，仅在独立新 profile 分支核对：报告封存 v2、`screeningIntent`、真实目录行重建的双 catalogDigest、当前京东推广来源/可选同店同期间基期、两视图、词货算法与 contextDigest，及与 workflow input 的 promotionRef 精确一致。新 workflow 保留既有“先工作流、后报告”的同事务顺序，由原 DEFERRABLE 提交时拒绝孤儿。

## 0026 报告与筛查 guard 的最小实现

从已经应用的 `0024_business_screening_runtime.py` 冻结常量派生，使用精确替换并断言每个锚点只出现一次；不修改旧迁移。旧 profile 继续调用其原有 guard 逻辑，新 profile 进入独立的严格式分支。必须一并覆盖：

1. `ai_business_screening_report_guard`：报告 snapshot 字段集合、JSON 原始类型和重复键、owner/scope、真实封存来源目录、selector 当前期与基期的同源关系、算法/contextDigest、预分配 screeningIntent、workflow input 与报告精确一致。不能只检查字符串属于新白名单。
2. `ai_business_screening_workflow_guard`：提交时拒绝带 selector 或筛查意图却没有对应新报告的孤儿工作流；旧报告不能混入新字段。新报告对应的图、工具策略和持久 input 需要由 owning 创建入口及 DB 可证明的字段共同绑定。
3. `ai_screen_initial_guard`：筛查发布必须属于该报告预分配 ID 和选择计划，不能复用另一报告或旧 profile 的 screening 结果。预算及映射字段仍沿原 guard 的权威引用和精确 SQL 复验。
4. 已存在的报告/工作流/筛查行不可改写。新 profile 的逆迁移只要发现任意新报告、工作流或预分配意图，即使尚未发布筛查，也必须拒绝；空逆迁移要恢复 0024 前驱函数体及触发器组合，并用 `pg_proc.prosrc` 和触发器回查实证。

只有数据库能从已保存的真实 seal、目录和选择计划验证的字段才进入 SQL；模型陈述、请求中的摘要、前端选择、候选 `authorityVerified=false` 均不能作为授权。

## renderer 7 的独立门槛

`promotionSelector` 应固定在新报告 snapshot 中，renderer 7 只接受该 profile，因而首版无需扩展 `AiBusinessFileRun` 字段或复用受约束为 `'null'` 的 `scope_json`。在报告 guard 和 owning 绑定就绪前，不扩 `ai_business_file_bound`。可在同一充分测试的 0026 加入 renderer 7，或者留到 0027；两种方式都须从 `0025_business_file_opc.NEW_SQL` 的五个函数精确派生并保持旧 1–6 路径：

- 单文件分片仍只允许 1/2/3/5；多卷分片和完整性检查只增加 7，父 run 的版本、attempt、status、stored_bytes 与分片链保持原规则。
- compact manifest 的 `rendererVersion` 必须为精确整数 7 且等于父 run。7 的父 run 必须指向精确新 profile 及其不可变 selector；不能由旧报告用 `deliveryMode=volumes` 自选 7。
- 新卷的完整 manifest 另绑定两种视图的 material manifest digest、每表 rowCount/NDJSON SHA、tableBindingDigest、source/baseline 和费用守恒证明；现有 `business-file-delivery-v2` compact 根字段集合保持，旧 4/6 manifest 不增加字段。
- 任意 7 文件任务（queued、paused、ready、cancelled）存在时，逆迁移拒绝；旧 1–6 文件的 manifest、attempt、历史 chunk、下载字节和暂停续跑行为必须保留。

## 分批实证与接受标准

第一批先实现真正的新报告创建、owning 复验和固定图/目录，不运行模型。随后编写 `0026` 与真实 PostgreSQL 测试：空库升级及逆迁移；旧 1–6 报告/文件行与摘要不变；新 profile 正确创建和筛查发布；错 actor、owner/scope、source、baseline、selector、contextDigest、意图 ID、孤儿工作流及重复 JSON 键原子拒绝；任意新报告存在时逆迁移拒绝。检查迁移前后数据库角色权限及独立 dump/restore，不能用纯 SQL 文本测试代替实际触发器验收。

第二批接 renderer 7 的 HTML/XLSX 全量两表、发布/恢复/下载及 0025 五函数扩展，按同样方式验证旧版本字节兼容和新版本分片完整性。词货材料本身不等于已交付文件；两张表是同一推广事实的不同分组，费用不可相加。正式业务模型效果及生产采用分别验收。

## 2026-09-24 候选持久结构已冻结（尚无创建或迁移）

新增只读 `business_promotion_creation_contract.prepare_candidate`：输入真实已封存 v2 证据 ID、当前无范围管理员及精确请求 `reportId`、`screeningId`、`question`、`sourceKey`，可显式给 `baselineKey`、`mappingPairs`、`budgetPlan`。它先用原 `business_screening_runtime.prepare` 完整准备筛查意图、映射与预算引用，再从真实持久来源目录固定一个京东推广当前期来源、可选同店同范围基期及两种词货视图。返回前重新核验旧准备、证据版本和账号；`revalidate_candidate` 另会复查四工具目录及全部候选 JSON。两者都不插入行。

候选根结构 `business-promotion-creation-candidate-v1` 包含真实 `ownerEmail`、`scopeJson`、证据 ID/版本、`snapshot`、`workflowInput`、六节点 `graph`、按顺序的四工具 `allowedTools` 与当前工具目录摘要；各 JSON 的规范 UTF-8 字节数与 SHA-256 单独记录。`authorityVerified=false`、`registered=false` 是候选根状态，不能写入正式报告快照作为数据库授权。

拟议 `AiReportRun.snapshot_json` 保留旧筛查的全部字段、不可变 `screeningIntent`、映射与预算绑定，仅将 `executionProfile` 设为 `business-agent-screening-promotion-reference-v1` 并增加 `promotionSelector`、`contextDigest`、`promotionCatalogDigest`、`promotionAlgorithmVersion`。原 `sealedDigest` 与 `catalogDigest` 必须与当前封存证据一致。证据 `catalogDigest` 使用 v2 目录 schema 包装后的摘要；新 `promotionCatalogDigest` 是词货纯合同对规范目录条目的摘要，两种哈希口径不能直接比较或混用。`promotionSelector` 精确为 `{sourceKey, views}` 或 `{sourceKey, baselineKey, views}`，`views` 固定顺序为 `keyword_sku`、`keyword_sku_context`。

拟议 `AiWorkflowRuns.input_json` 保留旧 `reference-v2` 全部字段、`screeningIntent`、可选 `mappingRef`/`budgetRef`，另增加唯一 `promotionRef`，字段精确为 `schemaVersion`、`promotionSelector`、`contextDigest`、`sealedDigest`、`catalogDigest`、`promotionCatalogDigest`、`promotionAlgorithmVersion`，与报告对应字段逐项一致。`graph_json` 使用新 profile 的五 Agent 加人审固定图；`allowed_tools_json` 是新 surface 的四工具固定顺序；`tool_policy_digest` 来自完整当前中央目录。旧报告字段、旧图和旧文件语义均未改。

这仍只是写入协议的候选形状。当前 PostgreSQL `0024/0025` 保护不会接受上述新 profile，Python 创建/派发/读取回执也未注册。接下来的 `0026` 应只接受这份精确 shape，并在实际事务中重新绑定用户、来源目录、图和工具策略；任何候选摘要或 `registered=false` 对象都不能直接充当授权。真实隔离 PostgreSQL shape/负向测试由整合主线程运行，之后才能决定迁移实现。

上述候选结构的 `PromotionCreationPgTests` 3项隔离 PostgreSQL 测试通过（10.727秒），日志 `.runtime/ai-pg-ed15939d72c8/tests.log`。验证真实封存来源、无预算/有预算与显式商品关联、角色和工具目录绑定、零持久写、错身份/基期/目录变化/伪造候选拒绝。仍未发布 `0026`，也未创建新 profile 报告或调用模型。

## 2026-09-24 内部原子创建路径候选（待 0026 后真实 PostgreSQL 验收）

新增未注册的 `business_promotion_creation.create`。调用方只提交新请求 ID、已封存 v2 证据 ID、问题与明确推广来源及可选基期、商品关联、预算；报告 ID 和筛查意图 ID 由服务生成。事务外重验真实证据、当前管理员、模型与四工具目录并构造上述持久 shape；事务内持有现有 AI mutation 版本锁，复核身份、模型摘要、来源目录、筛查准备与容量，然后同一事务写入可选预算、工作流、六节点、报告与创建事件。沿用旧筛查创建的预算胶囊、额度与元数据复验，预算事实扫描和中央目录网络读取留在事务外。失败由数据库事务整体回滚，重复请求须核对既有报告的来源、固定图、节点、预算和工具目录后才返回原 ID；不会重放模型调用。

此模块没有公共路由、调度注册或新的数据库放行逻辑。当前 `0025` 的报告触发器仍会拒绝新 profile，任何尝试中的预算、工作流、节点和事件随事务回滚；不能据 Python 候选路径称新报告可创建。测试标签 `ai_assistant.test_business_promotion_creation` 的真实数据库成功/回滚用例须在 0026 安装到隔离 PostgreSQL 后运行；当前仅可执行静态及纯输入检查。SQLite 不能等同检验本系统 PostgreSQL 权限与触发器，不能用 SQLite 结果代替该验收。

## 2026-09-24 0026 隔离 PostgreSQL 触发器验收

`0026_business_promotion_profile` 从 0024 冻结的报告/工作流/筛查 SQL 精确派生，新增独立新 profile 报告触发器。对最多 48 个真实来源行重建规范目录：`catalogDigest` 核验目录 schema 包装后的 SHA，`promotionCatalogDigest` 核验条目数组 SHA；从所选京东推广当前期和可选同店同期间基期重算 `contextDigest`。新报告还必须匹配精确四工具顺序、当前固定五角色图摘要、非 dry run 工作流、筛查意图和 promotionRef。旧 profile 分支与 0025 的文件 1–6 守卫不变；本迁移不允许 renderer 7，也不注册模型或 Agent 派发。

保留了三轮失败日志：首次 `.runtime/ai-pg-a1ac4eca1fd4/failure.log` 为 PL/pgSQL 内层 `CASE WHEN ... THEN` 被 IF 表达式解析为条件终点，改为互斥的预算/无预算图 SHA 条件；第二次 `.runtime/ai-pg-09c0d25255f0/failure.log` 为 JSON 提取与文本拼接优先级及逆迁移前驱函数已有 `CREATE OR REPLACE`，分别加括号、按已冻结前缀恢复；第三次 `.runtime/ai-pg-f99cf757099c/failure.log` 为原工作流 `dry_run` 是 bigint，改用精确整数零核验。没有放宽来源、图、工具或旧版本检查。

修复后 `ai_assistant.test_business_promotion_profile_migration.PromotionProfileMigrationTests` 隔离 PostgreSQL 4 项通过，日志 `.runtime/ai-pg-69a46af29b48/tests.log`。覆盖真实封存新报告、预算与商品映射组合、错误身份/来源/selector/图/工具/上下文原子拒绝、工作流孤儿、空逆迁移恢复旧函数、新报告行拒绝逆迁移及旧文件函数体未变。此结果只证明候选迁移和合成范围，不代表新 Agent 运行、renderer 7 或正式报告已验收。

同一候选另完成从 0025 到 0026 的独立升级/恢复演练，证据 `.runtime/ai-pg-216c0adcdbf4/business-promotion-profile-upgrade.json`。升级前 65 张 AI 表摘要保留，旧 renderer 1–6 合成文件的 manifest、分块和完整字节逐项不变，五个文件 guard 函数体与 reader/writer 表权限不变；0025 旧备份和 0026 新备份分别恢复到独立数据库。恢复后的新 profile 报告及 owning 来源页与源库一致。空逆迁移、再升级通过；存在新报告时逆迁移正确拒绝。演练没有模型调用或 renderer 7 文件，且只使用隔离合成数据。
