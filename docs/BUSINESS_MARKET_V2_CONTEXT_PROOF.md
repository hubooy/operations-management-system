# 市场 v2 context 独立证明（0061 候选）

0060 的暂停执行档案保存 `marketContextDigest`，但当时数据库只验证其 SHA 形状。0061 新增 SQL-owned 不可变 `ai_business_market_v2_context_proofs`，由独立 `teruisi_ai_market_context_attestor`（NOLOGIN、无成员关系、无表权限）经 `ai_market_v2_attest_context(reportId)` 创建。该函数不接收客户端 context 或摘要：数据库从 0045 材料固定的原词货 v1 报告，取 `reportId`、封存证据 `runId`、`screeningIntent.id`、`sealedDigest`，按固定键序和无空格 JSON 字节重算 SHA-256。只有计算值与新 0060 档案的 `marketContextDigest` 一致，且 0053 准入、0044 停放、0045 材料、账号、selector、manifest、原报告和 0060 paused 流程均仍精确匹配，才写入证明。重放只接受同一 SQL 计算结果。

reader 只能通过 `ai_market_v2_context_receipt(reportId,ownerEmail,actorVersion)` 获得窄回执；函数重新计算并比对已保存证明、当前无范围管理员和账号版本。reader、writer、attestor 对证明表都没有直接 SELECT 或 DML；writer 无回执函数权限。回执表示 context 根已被独立证明，`agentReadPersisted=false`、`executionReady=false`；它不是同 job/provider 的市场页读取记录，不开放 Agent 节点、模型、工具结果、数值引用或文件发布。

迁移依赖 `ai_assistant.0060`；先单独完成 0059→0060 全链演练及目标 PG，再验证 0061。新增表使 AI 清单从 81 变 82；旧 0057、0058、0059、0060 升级脚本使用各自冻结的 81 表清单。纯合同测试目标 `ai_assistant.test_business_market_v2_context_contract`；隔离 PostgreSQL 目标 `ai_assistant.test_business_market_v2_context_proof`（预置真实 reader/writer 角色），覆盖 SQL 推导、独立 attestor、窄读、跨账号、伪造 SHA、表和函数 ACL。升级演练入口 `tools/ai-postgres-rehearsal.py --business-market-v2-context-proof-upgrade --upgrade-only`，冻结旧 81 表/renderer1–7/全部旧函数与 0060 守卫，检查证明角色和目录权限、前后独立备份恢复、空逆迁移再装。已验证真实 context 行的正反例在独立目标 PG 测试中，不使用旧升级种子重放较低 netshop 修订。未部署生产。

整合隔离验收：纯合同五项通过，真实 reader/attestor PostgreSQL 两项 `.runtime/ai-pg-a3f3e4702eda/tests.log`（57.792 秒）通过；0060→0061 升级 `.runtime/ai-pg-b2d94e5027cb/business-market-v2-context-proof-upgrade-evidence.json` 保持旧 81 表/行、renderer1–7字节、0060/0044函数身份权限及市场根，新增第82表与4函数/2触发器、NOLOGIN无成员角色权限、前后备份独立恢复及空逆迁移重装通过。以上全部未在生产迁移或启用模型。
