# v4 跨票据前段 claim 查询修复（0051，隔离候选）

0048 写入函数在第二段及以后读取前段 claim 时使用 `WHERE ticket_id=prior.ticket_id`。`ticket_id` 同时是函数的 PL/pgSQL 局部变量和 claims 表列。真实隔离 PostgreSQL 跨票据演练在这条语句报 `column reference "ticket_id" is ambiguous`，因此先前的单段测试不能证明跨票据续读可行。

0051 只将该查询改为带表别名的列引用 `prior_claim_row.ticket_id=prior.ticket_id`。它不改变候选结构、来源/段/claim 栅栏、时间限制、角色或授权，也不启用直接封存。迁移安装前核对 0048 writer 完整函数体、返回签名、`SECURITY DEFINER`、固定 `search_path`、独立 `NOLOGIN` 角色、函数及表权限、PUBLIC 撤权和直接 seal 禁令。`CREATE OR REPLACE FUNCTION` 保留原函数 OID 和 ACL；迁移后仍应由隔离 PostgreSQL 实测。

为避免恢复有歧义的旧写入函数后继续处理已经存在的跨段进度，0051 仅允许在没有任何 replay 回执时逆迁移。目标测试覆盖空回执回退再升级时 OID/ACL/函数体不变、函数体漂移拒绝、已有首段回执时逆迁移拒绝；三项隔离 PG 测试通过 `.runtime/ai-pg-5743154be602/tests.log`。另以真实 owning 页、0036 两段持久证明和自然到期的两张 0041 票据完成第 17 页重放、前段回执接续与重复请求验证；隔离 PG 一项通过 `.runtime/ai-pg-6e739a683421/tests.log`，父任务仍 `collecting` 且无 seal。0050→0051 独立演练通过 `.runtime/ai-pg-7033b21c9550/business-v4-prior-claim-qualification-upgrade-evidence.json`：旧79表、renderer1—7字节及其他 AI 函数不变；仅 RECORD 函数体变化，OID/ACL/签名保持；前后备份恢复及空回退再升级通过。本文件和迁移均不代表已生产采用或已经获得正式封存授权。
