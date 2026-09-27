# 市场 v2 同任务已读回执门禁（0062 候选）

现有通用 `ai_agent_jobs`、provider dispatch/result、tool dispatch/result 可表示一次真实模型返回并触发工具读取；词货 v1 已有 calling 派发、拥有方工具和完成态回执复核。市场 v2 的 0053/0060 守卫禁止这些子记录，0061 仅证明 context。0062 因此只增加 SQL-owned、默认空的 `ai_business_market_v2_read_receipts` 和独立 NOLOGIN `teruisi_ai_market_read_attestor` 窄函数，不启动模型或创建任务。

窄函数仅在实际同一 0060 报告/流程/节点/job、成功的 provider dispatch/result 与 tool dispatch/result 已持久存在，provider 回执确实包含同 ID/名称/参数工具调用、五工具目录摘要和角色一致，且 0061 context proof 每次重核通过时，才允许记录一条不可变回执。reader 只能取账号/版本绑定的窄回执；reader、writer、attestor 均无证明表 SELECT/DML。当前 0060 paused、无节点/job 的硬守卫使它**不可能出现正例**；测试只验证缺少实际派发、越权和虚构 job 均失败。任何回执 DTO 自报 `persistedRead=true` 都不能绕过 SQL。

数值引用继续关闭。纯合同可将市场 TOP 样本的指定行/指标/字段与同 job、角色、报告、表绑定比较并重算候选单元格，但返回 `numericCitationAllowed=false`，直到后续独立单元格证明持久化。市场样本不能转成本店、ERP 或 B 端销售额，也不能把价格带汇总和成员相加。

真正激活需要独立版本：固定模型策略和新执行 profile/graph，保留旧 0044/45/53/60/61 根不变，版本化放宽对应 DB 子表守卫，接入现有 calling→拥有方工具→完成态回执链，并让 0061 context 核验在新 profile 下保持有效；真实模型调用和数值引用另行验收。0062 自身没有付费调用、生产路由或文件发布。测试目标为纯 `ai_assistant.test_business_market_v2_read_receipt_contract` 和隔离 PG `ai_assistant.test_business_market_v2_read_receipt_candidate`；均不产生市场 Agent 任务。升级/备份/空逆迁移演练入口为 `tools/ai-postgres-rehearsal.py --business-market-v2-read-receipt-upgrade --upgrade-only`，它以已验 0061 种子冻结旧 82 表、renderer1–7、旧函数和市场根/权限，验证新 83 表、NOLOGIN 与窄函数/触发器、前后独立恢复及重装；真实读取正例仍被 0060 门禁禁止。
