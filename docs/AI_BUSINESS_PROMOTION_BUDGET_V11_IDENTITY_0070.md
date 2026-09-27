# 0070：预算 v11 受限身份与一次性证明票据候选

`0070_business_promotion_budget_v11_limited_identity` 依赖市场 0069，仅新增预算 v11 的三职责**初始 NOLOGIN** 角色、两张 SQL-owned 只追加票据表，以及三个版本化 v2 窄函数。迁移不写密码、不启用 LOGIN、不安装 HMAC key、不接公开路由或服务进程。renderer 11 仍只能 `staged_unpublished`；`ready` 双守卫、分块下载拒绝和 renderer 1–10 路径不变。

- `attest_login` 只可在已存在 0067 同 run/attempt 证明且当前文件仍已暂存时，签发一个至多十分钟、绑定父版本/报告/owner 经证明行间接核对/文件 binding/证明 SHA 的随机 UUID 票据。`UNIQUE(run_id,attempt)` 防止另一张票据绕过；响应未知时没有自动重发或 outcome，保持阻断。
- `sign_login` 只能以票据 ID + 精确 run/attempt/SHA 一次性领取。数据库在父任务、票据和 0067 行上锁并重核状态、版本、报告/owner/binding 与原文 SHA；同一事务向单独的只追加 claim 表写唯一 `ticket_id`，才返回 0067 原文及必要标量。两个并发领取至多一笔成功。业务角色没有两表、0067 表或私钥表的直接读写权限。
- `publish_login` 仅可调用从 0068 正文显式版本化的 `verify_protected_receipt_v2`，它是**只读验签**，没有发布、签名 oracle 或活动 key 行锁。原 0067/0068 函数的 OID、正文、ACL、所有者不变；迁移安装时逐项对照，旧角色仍 NOLOGIN。

三角色均 `NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`，无成员关系，且不继承 Web reader/writer。只有独立受控运维流程将来可给三者配置各自凭据并启用；本次**没有此授权或实现**。测试可在隔离库用随机合成密码临时启用，再撤销 LOGIN 和清除密码；真实角色目标验证三个独立非超级用户连接的 `session_user=current_user`、精确票据、一次性 claim、只读验签及跨角色/表权限负例。测试中的 0067 写入、full-byte preflight、HMAC 签发与 key 配对仍由隔离管理连接准备，不代表生产端到端身份接线。函数 `SECURITY DEFINER` 在隔离环境仍由安装管理员拥有，正式部署前需给它们独立最小权限 owner，且不能改变 0068 私有 MAC ACL。

**剩余硬阻断**：`preflight.prepare` 仍经默认 Django ORM 读取管理员、报告、五 Agent、人审、预算、卷块和来源；`sign_after_preflight` 仍 ORM 直读 0067 表。单张证明票据不能提供同票据的多表/逐块有界只读流，旧 0067 ATTEST 也仍要求原 NOLOGIN 身份。无法从本切片推断独立 signer 或 attestor 可完成全链。票据签发/领取丢回复无只读 OUTCOME，不能重放；0068 验签未锁 key，不能用于 0069 原子发布。v11 `ready`、下载、模型与 Office 均保持关闭。

显式隔离演练入口 `python tools/ai-postgres-rehearsal.py --business-promotion-budget-v11-identity-upgrade --upgrade-only --port <隔离端口>` 必须先重放并独立恢复 0065→0069 的每段前驱，再从精确 0069 种子冻结旧 89 张 AI 表、旧 renderer 1–11 已存在的文件行/分块字节、全部旧 AI 函数 OID/正文/ACL/owner、角色成员和表列权限；验证仅新增两张非业务票据表/索引/触发器、四个函数、三个 NOLOGIN 角色，且票据为空。前后全库备份分别恢复到新库、空票据逆迁移与重新安装后再比较。隔离真实角色目标：`ai_assistant.test_business_promotion_budget_v11_identity_candidate.BudgetV11IdentityCandidateRoleTests.test_non_superuser_exact_ticket_read_and_receipt_verify_only`，要求预置 AI runtime reader/writer 角色。主整合任务串行运行 PostgreSQL，未通过前不能声明验收。
空票据逆迁移撤销三个角色在当前库的 USAGE 与函数权限，保留不可登录、无密码、无成员的角色名作为集群级审计痕迹；独立恢复库可能仍引用这些全局角色，不能为清理而级联删除。重新安装须复核角色属性后复用。
