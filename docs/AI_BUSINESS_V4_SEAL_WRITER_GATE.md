# v4 独立封存数据库门禁（0038，默认关闭）

0038 在已通过的0037只读准入之后，只增加一个 run 唯一、不可更新删除的 `ai_business_v4_seals` 表和受限数据库状态转移。正式部署必须先由受保护的角色 Provision 建立精确 `teruisi_ai_seal_writer` **NOLOGIN、NOINHERIT、无超管/建库/建角色/绕RLS及无角色成员关系**；迁移只验证这个身份并授予唯一原子函数的执行权，不生成密码、不开登录。角色缺失或属性漂移时迁移失败。未来若获受控批准，Provision 才能为**同一角色**配置独立 DPAPI 凭据并激活 LOGIN；本片没有此步骤。

普通 `teruisi_ai_writer` 对 seal 表仅可 SELECT，不能 INSERT/UPDATE/DELETE/TRUNCATE，也不能执行 `ai_v4_commit_seal`。`ai_reader` 不可执行。原子函数只接受固定 run、attempt、预期版本和封存正文原文/SHA/MAC/key-id，不接受调用方页面或布尔授权替代数据库事实。它用0037函数在同一事务固定顺序锁财务→网店修订，核当前无范围管理员、最新验证尝试、固定京东推广窗口与唯一财务自然月来源、所有来源完成、段数/末段/真实块与收据数、目录及父版本，再插唯一 seal 并仅允许 `collecting/manual → sealed/manual`、父版本+1及 AI 全局修订推进。封存后原0035来源/块/收据门禁继续拒绝追加。活源修订可已单调前进，但正文必须如实标明 `historical_revision`，不得伪称当前数据。

数据库先限 UTF-8 正文字节再解析，并对顶层/每来源精确字段集合、重复键、原文 SHA、版本、来源身份/计数、锁定时的活源修订、自然月缺月、推广覆盖、禁止跨域原子假设及禁用报告/Agent/财务日摊/SKU利润等字段做硬校验；额外 `sourceAuthorityVerified:true` 等字段拒绝。MAC/key-id 目前只验形状：**PostgreSQL 没有应用 HMAC 密钥，不能验证 MAC 真伪**。隔离测试刻意用随机 64 位十六进制 MAC，说明本片只证明独立数据库身份与原子状态边界，**尚不可交付封存**。普通 AI writer 的直接 SQL 伪封存会被独立 DB 身份和权限挡住，但获专用 seal_writer 凭据者仍属受信封存身份。下一阶段的独立 sealer CLI 必须在同一连接/事务自行重验所有0036段 HMAC/来源状态、生成并签正文，再调用窄函数；任何封存读取必须经 `verify_seal` 重验正文 MAC，不能只相信裸 `status='sealed'`。本片尚无 sealer 凭据、CLI、公开路由、Agent、人审发布或文件输出。

0038 的前置仍是 finance.0003/0004、netshop.0003 写入修订门禁和0037只读准入。隔离验收须证明真实 `session_user` 的专用角色可完成精确状态转移，普通 writer/reader 不能执行，错误来源/正文/MAC形状和封存后追加均拒；迁移前后旧AI 73表、renderer1–7字节及ACL保留，新74表及函数最小权限经独立备份恢复，空回退可行、有seal逆迁移拒绝。生产角色/服务和真实数据容量仍需另行受控验收。
