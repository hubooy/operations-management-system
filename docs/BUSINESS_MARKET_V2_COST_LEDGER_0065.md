# 市场五 Agent 模型费用需求账（0065 候选）

`AiModels` 只有 tokens、超时、轮次等运行上限，没有权威价格或汇率。`market_model_cost_envelope` 的纯整数计算也明确不给费率、人工批准或资金预留授权。0065 因此新增 SQL-owned **待核费用需求账**，而非可消费的预算余额：受保护的 NOLOGIN `teruisi_ai_market_cost_attestor` 仅能用窄函数把当前 0063 五 Agent 计划、模型版本、CNY 费率声明、五角色每轮 token 上限、管理员费用上限**声明**原子固定一行；数据库重算每轮向上取整的最坏金额。`required_cents` 记录待预留需求，`reserved_cents` 永远是 0，状态只能是 `pending_rate_and_approval_verification`，所有 provider 调用许可都为 false。

输入/输出纳元费率必须为正且在限定范围内，CNY 有效期不超过 31 天、当前时间有效；USD 或其他币种、缺失/过期价格、错模型/版本、未知额外字段、收费工具、任一角色缺失或超上限、总额超声明上限都失败。费率来源 SHA 和批准 SHA 只作为**待核身份声明**；它们不是供应商价格、汇率或人工批准的证明。即使 SQL 接受候选，`tariffAuthorityVerified=false`、`humanApprovalAuthorityVerified=false`、`extraChargeCategoryCoverageVerified=false`、`fundsReserved=false`、`providerCallsAllowed=false`。无实际扣款、预留、释放或 provider 请求。

reader 只能取同账号/当前版本的窄回执，reader/writer/attestor 对账表均无直接 SELECT/DML；普通 writer 无写入函数权限。0060–64 旧根、合成链和 v1 目录保持不变。未来真实调用前需独立采用有来源的模型价目、汇率与所有计费类别，核管理员精确费用批准，在真实资金账本同事务原子预留并逐次发放/核销许可；未知结果不重试，不能用本候选行冒充已经预留。

迁移名固定 `0065_business_market_v2_model_cost_reservation`，0066 后继依赖此名。纯测试 `ai_assistant.test_business_market_v2_cost_candidate` 与 `business_analysis.test_market_model_cost_envelope` 已覆盖整数上限和关闭边界；隔离真实角色 PG 目标 `ai_assistant.test_business_market_v2_cost_admission` 由主任务串行执行。未获取真实价目或批准，也未生产部署。

0064→0065 的隔离升级入口为 `python tools/ai-postgres-rehearsal.py --business-market-v2-cost-upgrade --upgrade-only --port <隔离端口>`。脚本要求 0064 的独立升级/备份恢复回执，冻结旧 84 张 AI 表、市场根与回执、renderer 1–7 文件字节及旧 AI 函数 OID/正文/ACL；仅接受成本表及两个索引、四函数、两个触发器和 NOLOGIN 窄权限，然后核验升级前后独立备份恢复与空逆迁移重做。该入口目前只完成静态和纯测试，真实 PostgreSQL 演练仍待主任务串行执行；不得据此声称升级验收通过。

整合分支已复核纯五项及隔离真实角色 PostgreSQL 两项 `.runtime/ai-pg-20df4bcf7ab3/tests.log`（57.149 秒）通过；0064→0065 长链 `.runtime/ai-pg-21f2c2323009/business-market-v2-cost-upgrade-evidence.json` 保持旧 84 表/行、renderer1–7字节、全部旧函数OID/正文/ACL与市场根，前后备份独立恢复、空逆迁移重装通过。首次演练在空回退时遇测试 SQL `LIKE` 中未参数化的 `%`，只修演练脚本并完整重跑；失败轮不计成功。数据库已停止并归档隔离数据，无生产写入、真实价格或费用预留。
