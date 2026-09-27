# v4 sealer 来源身份窄读边界

`0049_business_v4_sealer_source_bridge` 为独立封存器补齐一条来源级读取：`ai_v4_sealer_ticket_source(run, attempt, source, actor, actorVersion, nonce, claim)`。0042 原有 `ticket_context` 给出计划与目录身份，`ticket_segment` 给出单段证明，`ticket_page` 给出原始单页；它们均未在一行中给出固定来源查询及完成计数，无法安全初始化推广或财务纯段重放。

新函数先后调用 0042 的 `assert_claim`，在两次校验之间按精确 `run_id` 和 `source_id` 读取一条已完成来源。0042 的检查继续覆盖真实 `session_user`、有效 claim、最新 attempt、管理员账号版本、来源根、写侧修订栅栏与父记录状态。来源必须与 run 外键一致，完成检查点中的 `pageCount`、`rowCount`、`storedBytes`、`sourceRef`、`sourceRevision` 与来源记录一致。函数只允许财务月度与网店日度来源；其它 domain 默认拒绝。

返回值包含来源 ID、key、序号、domain、temporal role、规范 query 与摘要、来源身份摘要和修订提示、固定版本/ref/revision、完成页数/行数/字节数、票据 `sourceRoot`、attempt `keyId`、完整 checkpoint 摘要与最后页摘要。推广来源另返回规范的首末页身份 metadata；财务来源另返回完成 `financeState` 的摘要，供纯重放与最终对账比较。不会返回其它来源、来源表全行、工具审计参数或原始事实页。`run_bound_capability_verified=true` 只表示本次读通过 claim 门禁，不表示上游业务签名、来源权威、seal 或报告已获准。

该函数是 `SECURITY DEFINER`，仅 `teruisi_ai_seal_writer` 可执行。角色继续为 `NOLOGIN`，没有新增表 ACL、角色成员或直接 seal commit 权限；reader、writer 和 PUBLIC 不能执行。逆迁移只删除这个函数；0049 不写新表或回执。隔离 PostgreSQL 4 项 `.runtime/ai-pg-9f81d7408498/tests.log` 覆盖精确单来源、跨 run/source/claim/账号/撤权拒绝及目录门禁 ACL 漂移；0048→0049 旧79表/renderer1—7字节与旧函数OID/权限、双备份恢复及空回退再升级通过 `.runtime/ai-pg-2954dd9f2b31/business-v4-sealer-source-bridge-upgrade-evidence.json`。这是隔离候选，不构成正式角色激活或封存授权。
