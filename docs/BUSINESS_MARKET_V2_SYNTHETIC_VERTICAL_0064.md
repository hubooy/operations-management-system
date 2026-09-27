# 市场 v2 合成持久纵向切片（0064 候选）

0044 工作流守卫只给 0060 第五工具例外；0060 报告/流程永久 paused 且拒绝节点与 job；0061 context 函数要求旧流程无 job；0062 回执尚不能服务新运行流程。当前 `AiModels` 没有可核的输入/输出价格，无法用“人民币费用上限”约束真实付费调用。直接把旧 0060 流程改成 running 或从合成结果签发 0062 已读证明都不成立。

0064 因此保留旧根，新增**独立 profile 的合成报告、流程及真实通用持久链**。SQL 窄函数只能由独立 NOLOGIN 角色在隔离数据库/指定本机端口调用；内部 Python 开关默认 false，且要求 Django `test` 环境。它从经 0063 SQL 证明的五 Agent 计划原子写入新 report/flow、六个固定依赖节点，以及 commerce、promotion、market_b2b、independent_review、report 各一个 paused job；每个 job 都有独立 provider dispatch/result 与 tool dispatch/result。五角色分别使用证据包、词货、市场、分析和预算工具；无固定预算时 report 复用证据包并标记预算不可用，不伪造 0 元预算。模型 ID 是不可路由的合成哨兵，`dry_run=1`、流程/job 均 paused；所有 provider/tool 回执标记 `syntheticOnly=true`、`externalProviderCalled=false`、`paidCostCents=0`，工具参数故意含 `syntheticOnly`，不能当真实工具请求重放。测试须证明六节点依赖、五组真实 FK/派发 ID、各自 provider 工具调用与结果归属、错误角色/重复执行拒绝、摘要及旧 0060 根不变，并验证模型客户端未调用。

0064 精确版本化 0044 workflow guard 的新 profile 插入例外，并对 0053 第五工具派发与结果守卫仅加入 synthetic-v4、独立 NOLOGIN 角色、隔离数据库/端口和合成结果边界的窄例外；三条旧函数 OID/ACL/owner 保留。专用报告/流程/六类子表守卫再拒普通 writer、非隔离数据库、更新/删除或从旧 profile 改挂。提交时的孤儿检查仅用固定 `SECURITY DEFINER` 读取本流程报告，证明角色仍无报告表 SELECT。旧 admitted-v2 及其他 profile 仍拒第五工具；测试以回滚夹具和普通角色负例验收。旧 v1、0044/45/53/60/61/62 记录及权限不改。合成结果包含 `persistedRead=false`、`numericCitationAllowed=false`，旧 0062 attestor 对它仍拒绝；人审和工程文件没有解锁。**这是持久链机制的隔离验证，不是付费模型分析结果。**

生产可运行版本仍须单独的模型选择与版本化价格/费用预留、用户授权及逐次 provider 调用前许可；新 profile 的真实工具输出要经拥有方重算和同 job/provider 回执，再建立独立数值单元格证明，并验证人审与 renderer。测试目标 `ai_assistant.test_business_market_v2_active_synthetic_contract` 和隔离 PG `ai_assistant.test_business_market_v2_active_synthetic`。升级门禁 `tools/ai-postgres-rehearsal.py --business-market-v2-synthetic-upgrade --upgrade-only` 从 0063 已验种子冻结旧 84 表、历史文件与函数，检查 0044 精确版本变化、新守卫/角色及前后独立备份恢复和空回退重装；合成持久链只由独立 PG 目标验证。未调用付费模型或部署生产。

整合隔离验收：五角色真实通用持久链 PG 三项 `.runtime/ai-pg-722aed197438/tests.log`（83.004 秒）通过；0063→0064 长链 `.runtime/ai-pg-587ea6d5945c/business-market-v2-synthetic-upgrade-evidence.json` 保持旧84表/历史文件/市场根，0044及0053仅精确版本化且OID/ACL/owner不变，前后备份独立恢复与空逆迁移重装通过。前三轮目标PG分别揭出直接SQL遗漏必填字段、JSON提取运算符优先级及0053第五工具原全拒，第四轮揭出延期孤儿守卫以NOLOGIN调用者权限读取报告表；修复仅补固定字段/括号、隔离synthetic窄例外和固定只读SECURITY DEFINER，不扩表权限。失败轮不计成功证据。
