# 市场 v2 新 surface 五项只读工具目录候选

当前独立市场 surface `business_agent_screening_promotion_market_v2` 在 Worker 显式 `AI_MARKET_V2_AGENT_RUNTIME_ENABLED=true` 且 Django 显式 `TERUISI_DJANGO_AI_MARKET_V2_AGENT_RUNTIME_ENABLED=true` 时，才通过中央签名 edge 组合**五个可调用的只读候选条目**。默认两端均关闭。旧静态 `aiToolRegistry`、词货 v1 四条 entry 的内容、`allowedSurfaces`、顺序和目录摘要不变。

前四项使用独立名称 `get_business_market_v2_screening_package`、`get_business_market_v2_screening_analysis`、`get_business_market_v2_screening_budget`、`get_business_market_v2_keyword_sku`。它们从 0053 admitted-paused 报告经 0044 parked 与 0045/0056 精确材料声明，回追同账号原词货 v1 报告，再调用现有**无 Agent job 依赖的拥有方只读**角色包、分析表、预算与推广 SKU 视图。每项校验 admitted 报告 ID、原证据 run/筛查 ID或固定推广来源、角色、模式、偏移和结果容量；预算原报告没有固定预算时返回 `unavailable_no_fixed_budget`、`payload=null`，不填零。第五项继续使用已独立有界的 `get_business_promotion_market_v2` 三表市场候选。

四个别名与第五项只在新 surface 列出，首三项最大 40,000 字符、第四和第五 38,000 字符，均为只读、12 秒、最多 8 次调用。Node handler 和 Django reader route 分别复核精确模式、角色和签名请求 ID；宽结果与超时整次拒绝、不截断。模型传入的 `role` 和请求 ID 仅是**候选声明**，不是实际 Agent 身份。每个结果必须保持 `persistedRead=false`、`sameJobProviderPersisted=false`、`registeredAgentTool=false`；中央目录可调用只表示受开关控制的预览入口存在，**不代表已有工作流、真实模型调用或 Agent 本人已读**。0053 数据库仍硬拒节点、job、provider/tool 派发和结果。

后续执行快照迁移需要以这五项**同 surface 的真实中央目录**固定五项顺序和策略摘要，再独立版本化开放工作流状态。真正的同 job/provider 回执、人审、市场数字引用、renderer 与业务验收仍是后续工作。不得把旧 v1 dispatch handler直接挂新 surface：它只接受原 v1 running job，否则跨报告授权失败。

此文件记录候选设计与验证结果；没有生产迁移、部署或付费模型调用。

2026-09-25 独立候选：新五工具/签名 edge Node 测试 4/4、前版第五工具 Node 回归 4/4、旧 v1 四工具 Node 回归 8/8，合计 **16/16**；变更 TS 文件 ESLint 和 Python 静态编译通过。随后由整合分支执行隔离 PostgreSQL 与路由验收；真实大表 12 秒/容量、原生业务数值与真实 Agent 回执均未验收。

整合验收：新增目录与第五工具 Node 8 项、旧词货目录/工具 Node 16 项、Django 路由 2 项、ESLint 与本地构建通过。隔离真实 reader 角色三项 `.runtime/ai-pg-759ad2f0271c/tests.log`（85.918 秒）通过；另将未发布负例的错误原因收紧为“固定筛查结果不存在”后，单项 `.runtime/ai-pg-81e89178091e/tests.log`（27.115 秒）通过。首轮目标库 `.runtime/ai-pg-46c8e00d0df5/failure.log` 曾发现原词货 v1 报告被误送进旧 screening-profile reader；现改用真实已发布筛查包、推广报告拥有方分析、固定预算和原词货明细各自的既有只读路径。未发布包明确拒绝，未伪造 ready/job。默认开关未启用，无生产写入。

独立审查确认无默认开启、跨报告授权或伪造 `persistedRead` 的直接缺陷。前四项仍无同 job/provider 持久回执；12 秒截止无法中断已进入旧同步拥有方读取的慢查询，真实大表及有界取消留给下一阶段验收。

后续默认关闭的分析页/词货预览把 12 秒合作式截止传入封存逐页读取、分组临时 SQLite 和词货表遍历；超时保留原异常并清理临时文件。纯分组/结果 13 项、隔离真实 reader 三项 `.runtime/ai-pg-ab21c5667557/tests.log`、旧词货分析三项 `.runtime/ai-pg-41fa541bd9b3/tests.log` 与旧词货 HTTP 七项 `.runtime/ai-pg-dbd878f38070/tests.log` 通过。包重建与固定预算仍走旧同步路径，数据库单条查询、下游 I/O 和真实 38–40K/12 秒大表耗时尚无强制中断证明；不能因此启用执行 profile。
