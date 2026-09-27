# 市场 v2 材料已准入报告根：0053 隔离候选

0044 的市场 v2 停放报告、工作流不可更新，且禁止任何 Agent 任务；0045 的三张市场材料仅由独立证明角色写入不可变侧表。直接把第五工具加入旧词货四工具常量或把停放流程改为运行态，会绕过版本边界，也会被数据库拒绝。

0053 采用**新报告与新工作流行**，版本为 `business-agent-screening-promotion-market-admitted-v2`。新行的 `marketAdmission` 只引用已停放报告的精确 ID、0045 的选择摘要和市场材料清单摘要；数据库逐项复核同账号、无范围管理员、原报告与封存证据、原来源报告摘要、价格带/双观察日选择、0045 材料字节及图版本。原停放报告和材料行完全不更新。新工作流冻结原 v2 六节点图及五个**提议工具名称**，实际 `allowedTools=[]`、`model_id=''`、`status=paused`、`agentDispatchSupported=false`。该切片只表明三张材料已准入，绝不表明模型或 Agent 已读。

数据库触发器保持新报告和工作流不可变，要求一个工作流在事务提交时恰有一个对应报告；拒绝该版本的节点、Agent job、第五工具派发和工具结果。即使应用误把流程标记为 running 或补上模型 ID、第五工具，数据库也拒绝。旧 v1 四工具与 renderer7/9 不变；通用派发/结果表结构不变。没有公开路由、工具注册、模型调用、文件生成或生产迁移。

后续独立版本才能开放执行：先固定第五工具真实 transport 目录、模型策略、角色和数值引用协议，再版本化启用节点/job/provider/dispatch/result，并持久重放同一 job 的模型调用与逐页/逐行市场结果。市场 TOP 样本不能归属本店、ERP 或 B 端销售；价格带汇总与成员不可相加。当前 0053 的 `agentDispatchSupported=false` 不能被界面或报告写成“五 Agent 已完成”。

隔离 PostgreSQL 目标测试：`ai_assistant.test_business_market_v2_admitted_paused` 在 2026-09-25 运行 **3/3 通过**，证据为隔离工作树 `.runtime/ai-pg-86461140037a/tests.log`。它验证新行与旧停放行并存、0045 同源材料、幂等、缺材料/错账号拒绝及节点/job/运行状态数据库拒绝。既有 `ai_assistant.test_business_market_v2_material_admission`、`ai_assistant.test_business_market_v2_parked_creation`，以及更广的 NULL/错 selector 直接插入、升级/备份恢复与空逆迁移，仍由整合阶段另行验收；这 3 项不能替代这些门禁。候选不授予生产迁移。

整合分支另在 `.runtime/ai-pg-7a6b5b162f07/tests.log` 验证目录门禁能识别 job 禁令触发器被关闭；0052→0053 完整隔离升级/前后备份恢复/空逆迁移重做通过 `.runtime/ai-pg-f8faa8ebc693/business-market-v2-admitted-paused-upgrade-evidence.json`。79 张旧 AI 表、renderer1—7 文件字节、0044/45 停放与材料原行、所有旧 AI 函数的 OID/正文/ACL 保持，新七个函数和七个触发器的绑定与关闭状态回读通过。旧 parked、材料与新 admitted 三组同库 18 项回归 `.runtime/ai-pg-edd8cc7dda38/tests.log` 通过；这仍不证明第五工具或 Agent 已实际运行。
