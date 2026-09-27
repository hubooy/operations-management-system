# renderer 11 受限生产身份：发布前设计门槛

状态：**仅设计与阻断测试**。0067/0068 迁移源码、空密钥、v11 `ready` 双重硬拒及下载拒绝均不变；本文件没有创建角色、部署凭据、运行模型或启用发布。

## 为什么当前角色不能接到生产

0067 的 `ATTEST`、`REQUIREMENTS` 和表触发器要求 `session_user=teruisi_ai_budget_v11_attestor`，同时该角色必须 `NOLOGIN` 且没有成员。0068 的 `VERIFY` 对 `teruisi_ai_budget_v11_publisher` 同样要求精确 `session_user`，该角色也为 `NOLOGIN`。PostgreSQL 的 `NOLOGIN` 角色不能成为普通客户端连接的初始用户；`SET ROLE` 只改 `current_user`，不能把一个 Web 登录变为受信任的 `session_user`。目前隔离 PG 测试以超级用户连接后 `SET SESSION AUTHORIZATION` 模拟，证明的是 SQL 内部检查，不是非超级用户生产接线。把超级用户凭据交给受保护服务会消除这个边界。[PostgreSQL 17 CREATE ROLE](https://www.postgresql.org/docs/17/sql-createrole.html)、[SET ROLE](https://www.postgresql.org/docs/17/sql-set-role.html)、[系统用户标识](https://www.postgresql.org/docs/17/functions-info.html)。

另一个独立缺口是签发器 `sign_after_preflight` 以默认 Django ORM 直接读取 0067 证明表。0067 已撤销普通 reader、writer、attestor 的表和列 `SELECT`；测试连接有迁移超级用户权限，所以成功并不证明受限验证器可读。不能通过给 Web 角色整表读权来修复。

## 最小受限身份与进程

未来另起显式迁移，保留已执行的 0067/0068 迁移文件不变。新迁移创建三个**相互独立的专用 LOGIN** 身份，示意为 `teruisi_ai_budget_v11_attest_login`、`teruisi_ai_budget_v11_sign_login`、`teruisi_ai_budget_v11_publish_login`。三者均 `NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`、无任何角色成员、无 `CREATE`/表写权限；Web reader/writer 不得持有或继承它们。原 NOLOGIN attestor、publisher、key-owner 保留且无成员，不直接变为 LOGIN。三个登录身份由三个受保护服务进程分别持有，不复用 Django Web 连接池。连接只可到指定数据库与回环地址；具体 `pg_hba`、证书/SCRAM、连接数、服务账号和轮换方案应在受控部署清单中逐项核验，不能在源码、普通 settings、日志或命令行记录凭据。

| 身份 | 唯一业务权限 | 不可获得 |
| --- | --- | --- |
| attestor LOGIN | 调用改为核其精确 `session_user` 的追加式证明函数 | 0067 表直写、密钥、签发、发布 |
| signer LOGIN | 仅用同 `runId/attempt` 的一次性受保护任务票据读取 preflight 所需来源与精确 0067 证明 | 证明整表查询、任意正文签名 SQL、文件发布 |
| publisher LOGIN | 调用改为核其精确 `session_user` 的验签；以后才考虑独立发布/结果查询函数 | 私钥表、私有 MAC 函数、签发入口、业务表直写 |
| key-owner NOLOGIN | 继续持有私钥表与私有布尔 MAC 函数 | 客户端登录或角色成员 |

签发 HMAC 密钥只注入独立验证器进程内存，数据库只由受控密钥配置流程向 key-owner 表配对安装；publisher 与 Web 进程均不接收密钥。Windows 服务账号和凭据存储应与现有 Web/Worker 账号分开，具体 DPAPI/服务绑定由部署门禁验证，不能把密钥作为普通环境变量或 API 参数传入。凭据泄露或撤销时先停相关服务并令活动 key 失效，旧回执随 key 撤销失败关闭。

## 后续迁移的精确数据库改动

1. 后继迁移只重定义 v11 专属的 `attestation_guard`、`attestation_requirements`、`attest_staged` 与 `verify_protected_receipt` 的身份谓词和执行 ACL，使 `session_user` 分别绑定新的 attestor/publisher **LOGIN**。表拥有者、私有 MAC、key-owner 与 renderer 1–10 守卫保持原样。函数 OID、旧表、旧文件字节以及 0067/0068 既有证明必须前后冻结；正文变化是显式版本迁移，不能冒称 0067/0068 未变。新角色的 `rolcanlogin=true`、无成员、无危险属性和精确函数授权需要运行时及目录双重校验。既有 0067/0068 `verify_catalog` 是其当时版本的检查器；后继迁移须建立新版本检查器，不得偷偷修改旧迁移源码。
2. 新建独立 `SECURITY DEFINER` 只读证明函数，输入受保护任务票据 ID、精确 `runId/attempt` 与新鲜 preflight 的 `attestationSha256`。它在固定 `search_path=pg_catalog,public` 下核 signer **LOGIN** 的 `session_user`、票据一次性领取/短时有效、同一管理员/报告/绑定/版本及 `paused/staged_unpublished`、0067 行的 SHA 与当前根，只返回该行的 ID、原文 SHA、原文及必要的报告/owner/binding 标量。没有“按报告列举”或任意 `SELECT`；票据由 SQL 在已批准且已暂存的精确任务上创建并审计，Web 输入不能自行制造有效票据。若票据领取、回执或网络结果未知，调用方只读查原票据结果，不能重发授权。
3. 目前 `preflight.prepare` 还直接走 Django ORM 和来源读取。受保护 signer 接线前必须把它的**所有**来源读抽象到同票据绑定的只读数据通道：报告/工作流/人审/预算根、卷块流、来源修订、管理员状态均在每次读取时核同 `runId/attempt` 和当前权限，限制行数/字节/时长；不能只给 0067 证明做窄函数却让其他 ORM 读取使用超级用户。签发前后按原全字节语义复验，票据和根任何漂移都拒绝。没有完成此步，不能称独立验证器可运行。
4. signer 只能调用固定目的的 `sign_after_preflight`，不接受调用方自报 digest、任意正文或成功标志。签发前精确 0067 原文与新鲜 preflight 相等；签发后 publisher 在未来发布事务内锁活动 key 和当前根验 MAC。0068 当前无 key 行锁的只读验签不能直接当 0069 发布门禁，见发布设计文档。

## 非超级用户端到端验收

在隔离 PostgreSQL 的迁移安装阶段可使用管理身份；**关闭管理连接后**，业务正反测试必须用三个真实 `LOGIN` 连接分别执行，并证明 `session_user=current_user=对应登录身份`。测试不能调用 `SET SESSION AUTHORIZATION`，也不能把任一服务账号授予超级用户、CREATEROLE、key-owner、Web 或其他服务身份成员。独立检查 `pg_roles`、`pg_auth_members`、数据库/模式/表/列/函数 ACL 和 pg_hba 绑定；任一目录漂移失败关闭。

- 正例：独立 writer 合成已批准暂存任务；attestor LOGIN 追加唯一证明；signer LOGIN 以精确一次性票据完成所有来源和 HTML/XLSX 全字节 preflight、窄读同一 0067 原文并签回执；publisher LOGIN 仅验同一回执。无真实密钥的测试只在回滚事务内使用合成 key，且报告始终 `paused/renderer_unpublished`。
- 反例：Web reader/writer、attestor、publisher 均不能读私钥、读取 0067 整表或直接改 `ready`；signer 不能调用发布或私有 MAC，publisher 不能调用签发。三服务账号相互不能 `SET ROLE`；非超级用户 `SET SESSION AUTHORIZATION` 到别的身份失败。错误/过期/重放票据、不同 run/attempt/owner、审批撤销、来源/卷块/预算变更、key 撤销及响应丢失均拒绝或返回明确 unknown，不补发。
- 升级/备份门禁：旧 renderer 1–10 字节与函数、旧 0067/0068 证明与私钥表、角色成员/ACL 前后核对；保护真实密钥备份。完成身份与读通道验收之后，才着手 0069 同事务发布 CAS/OUTCOME 和 v11 下载栅栏；本设计本身不授予它们。
