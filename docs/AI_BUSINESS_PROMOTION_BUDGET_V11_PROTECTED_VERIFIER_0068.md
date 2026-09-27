# renderer 11 受保护验证器回执：0068 候选

0067 的 HTML/XLSX 验收摘要是无密钥声明，不能授权 `ready`。本候选增加独立 HMAC 验证层，**仍不发布、下载、运行模型、调用 Office 或连接生产**。0066 的 v11 `ready` 双重硬拒保持原样。

## 信任边界

- `business_promotion_budget_v11_preflight.prepare` 必须由隔离的受保护验证器进程亲自执行：重建当前批准内容和预算、逐块验 SHA、解压 HTML gzip/NDJSON、核行摘要、重建并比较 HTML/XLSX、逐个检查 ZIP OPC/XML/公式，最后复验来源根。`sign_after_preflight` 没有任意正文或摘要参数；它再次确认 0067 追加式行与新鲜 preflight 完全一致，才对版本化正文做 HMAC。
- 正文固定签发用途 `stage-to-ready-once:v1`，绑定 key ID、run/attempt/version、report、owner、binding、0067 ID/正文 SHA，以及完整 0067 证明。证明中包含批准内容与人审、预算、compact/full manifest、每卷文件 SHA、HTML 行和 XLSX OPC/公式过程摘要。原始密钥不在源码、迁移、日志、普通 Django settings 或 API 参数中。此分支没有加载或发放凭据的服务入口。
- 数据库 0068 新建**空**密钥表，由独立 `NOLOGIN NOINHERIT` key-owner 持有；普通迁移角色只可调用私有布尔 MAC 验证函数，不能 `SELECT` 表或获得签名函数。新的 `NOLOGIN NOINHERIT` publisher 只获外层只读验证函数执行权，没有密钥表/私有函数权限。attestor、writer、reader 也没有这些权限。外层函数对照当前暂存任务、0067 原文、报告/流程/管理员根并验 MAC；无活动密钥、MAC 错误、撤销密钥或根改变均拒绝。HMAC-SHA256 仅使用 PostgreSQL 内建 `sha256(bytea)`，不依赖可能缺失的 `pgcrypto`。
- 上线前必须解决受限会话身份：0067 的 `session_user=attestor` 与 0068 的 `session_user=publisher` 均指向 `NOLOGIN` 且无成员角色。当前隔离 PG 测试借超级用户会话执行 `SET SESSION AUTHORIZATION` 来模拟精确身份；这**不证明**受保护进程能用非超级用户凭据建立生产会话。不得把超级用户数据库凭据交给运行时，也不得为使测试通过而直接放开这两个角色的 LOGIN 或成员权限。应先设计独立受限登录身份及相应的运行时门禁，再用真实非超级用户连接做角色/权限验收。
- 当前 Python 签发入口经默认 Django ORM 读取 0067 证明行，而 0067 已撤销普通 reader/writer/attestor 对该表的 SELECT；隔离测试的超级用户 ORM 读取同样不能证明受限进程可签发。正式接线前应提供精确 `runId/attempt` 的受保护只读访问或等价窄权限连接，仍不得把全表读权授给 Web 角色，并以非超级用户完整验收。
- 密钥必须由独立受控运维流程在受保护进程及 key-owner 表两端原子配对启用，至少 32 字节；同一时刻至多一个 active key。轮换先在受控事务撤销旧 key，再引入新 key；旧回执随即失效。迁移本身不安装任何真实或合成密钥。签发或验证结果未知时不能把“调用已发出”当成功；未来发布函数必须以原请求做一次性 CAS 与只读 OUTCOME，不能盲目重发。
- 目录复核固定密钥表拥有者、列及约束、活动 key 唯一部分索引、不可变/禁止截断触发器的事件位与 OID、key guard 函数正文及受限角色 ACL。隔离 PG 负例以事务内禁用 guard、删除唯一索引或约束检验漂移均拒绝并回滚。目录复核只是发布前置条件，不能替代发布事务内的锁和数据复验。

## 尚未获发布资格

0068 只证明受保护验证器签发过**这份**当前 0067 正文，不使 0067 本身的无密钥声明变可信，也不开放 `ready`。真正的独立验证器进程、受限数据库身份、凭据隔离、发布 CAS/OUTCOME、完整来源根与文件字节同事务锁定、下载窄栅栏、真实 57.5 万行容量及原生 Office 重算尚未完成。外层 SQL 的当前根复核是候选验证，不代替将来的发布事务门禁。任何拥有 PostgreSQL 超级用户或 key-owner 机密的人处于此信任边界之外。

## 验证

纯测试验证 HMAC 用途与正文修改不可复用、无密钥声明无法签发；静态测试断言 0068 空密钥、私有函数、角色与无发布路径。隔离 PostgreSQL 目标 `ai_assistant.test_business_promotion_budget_v11_verifier_receipt_role.BudgetV11ProtectedReceiptRoleTests.test_0068_synthetic_key_only_verifies_fresh_protected_receipt` 使用事务内合成密钥，不提交密钥，不触碰生产；它须验证普通角色无法读密钥或调用私有函数、无密钥拒绝、正确密钥接受、文件/过程摘要篡改拒绝、撤销旧密钥拒绝、任务仍 paused。

显式升级入口 `python tools/ai-postgres-rehearsal.py --business-promotion-budget-v11-verifier-upgrade --upgrade-only --port <隔离端口>` 先串行复现 0065→0066 与 0066→0067 各自独立备份恢复，再只在其精确 0067 种子上演练 0067→0068。最后一段冻结旧 86 张 AI 表、renderer 1–11 当时存在的文件字节、旧函数 OID/正文/ACL/owner、表列权限与原有角色成员，检查只新增一个**非业务目录**的私有密钥表及索引/触发器、三个函数和两个 NOLOGIN 无成员角色；校验空密钥、前后独立备份恢复、空密钥逆迁移重装、v11 ready 双拒绝。密钥表故意不进入普通 AI ORM/业务数据备份清单；全库备份仍会包含它，未来实钥备份必须另行落实密钥保护。主任务统一串行运行此 PG 演练；在实际通过前不能声称数据库验收完成。
