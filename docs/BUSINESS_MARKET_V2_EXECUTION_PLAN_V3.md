# 市场 v2 五 Agent 执行计划 v3（0063 候选）

旧 0060 报告与流程永久 paused，0053/0060 禁止 Agent job、provider/tool 派发和结果；0061 context 证明也要求没有这些子记录。0062 已读回执因此仍为空。直接把旧流程改成 running 会破坏冻结根及权限。0063 使用**独立 SQL-owned 执行计划表**记录下一个版本的五 Agent 图与策略，原 0044/45/53/60/61/62 数据、旧词货 v1 目录及所有派发门禁不改。

内部准备入口只有在显式 `AI_MARKET_V2_EXECUTION_PLAN_ENABLED=true` 时可用；它重新核当前无范围管理员、0060 原报告/停放/准入/封存来源，并两次取得中央新 surface 的同一五工具目录。计划锁定 0060 快照摘要、0061 context 证明摘要、selector/manifest、五角色图和预算有无。SQL 独立 `teruisi_ai_market_plan_attestor`（NOLOGIN、无成员关系）通过窄函数重新读取 0061 不可变证明和全部同账号市场根，核规范 JSON、图/目录与策略，然后写一行不可变计划。reader 可用账号版本绑定的窄回执读取；reader、writer、attestor 均无计划表 SELECT/DML。

模型选择保持 deferred、模型 ID/版本为空，当前 provider 轮次、工具调用及付费额度均为 **0**；未来上限分别固定 20/40，但不是运行许可。无预算时图不调用预算工具，有预算时预算仍只作人工复核输入，原生预算支出权限为 false。计划要求人审，`agentJobsAllowed=false`、`providerCallsAllowed=false`、`readReceiptAuthority=false`、`numericCitationAllowed=false`。它不是运行中的 workflow，也没有真实同 job/provider 已读或可发布报告。

下一版本需另建新的实际执行 profile/flow 与固定模型策略，逐次预留 provider 权限、复用既有词货 calling→拥有方工具→完成态回执链，并版本化 0044/0060/0061/0062 的精确守卫。隔离合成 provider/tool 回应只可在那时验证真实持久链归属，不能预先制造 0062 回执。0063 无公开启动路由、付费模型调用或生产采用。纯测试目标 `ai_assistant.test_business_market_v2_execution_plan_contract`；隔离 PG 目标 `ai_assistant.test_business_market_v2_execution_plan`。升级演练入口 `tools/ai-postgres-rehearsal.py --business-market-v2-execution-plan-upgrade --upgrade-only` 以已验证 0062 种子冻结旧 83 表、旧函数和 renderer1–7，并核第84表、NOLOGIN/ACL、前后独立恢复及空回退重装；主任务串行运行。
