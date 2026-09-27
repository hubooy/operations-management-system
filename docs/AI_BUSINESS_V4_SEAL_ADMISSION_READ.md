# v4 封存准入只读候选（0037）

0037 **不创建封存表、不改变父任务状态**。它只提供一个固定 SQL 的受限读锁函数：在同一事务内先锁财务、后锁网店的全局修订行，核 finance.0003/0004 与 netshop.0003 已迁移、写源触发器启用、受保护 marker 无残留、写入角色不能改 marker 或调用其内部函数。普通 AI writer 只获此函数的 `EXECUTE`，不获得业务修订/marker 表的直接写权；AI reader 不获 `EXECUTE`。函数无店铺、表名或调用方 SQL 参数，角色/authority 不符时失败关闭。

内部 `business_v4_seal_admission.inspect` 在持有两域修订锁的**同一只读事务**中，要求当前无范围管理员、同一已完成 run 的固定京东推广窗口及唯一财报来源、当前父版本与目录摘要，并要求每个来源事实块、签名工具审计、验证尝试及分段均形成于全部写源门禁安装之后；较早的0036分段不得追认旧事实。它对每个来源从第 1 段顺序复核 0036 不可变 HMAC、连续的 16 页边界、前后有限状态、收据/完整请求参数证明链、真实 chunk/receipt 数与最后一段对完成检查点的双向一致性。缺段、伪 MAC、旧密钥、撤权、账号版本或目录 CAS 变化均拒绝，不接受调用方提供的事实或证明。

准入还会流式重读**当前 chunk/receipt/audit 的身份、摘要、时间、行数、字节数及收据链**，在每段末与受 HMAC 保护的进度核对；它不在锁住两域写源修订的同一事务里再次载入最多 2 GiB 的原始页 JSON。原字节/行值的逐页核验发生在0036建段时，0035的不可变触发器和最小权限保护此后的物理页。该前提不覆盖受信数据库所有者临时改写函数/触发器又恢复的行为；0037当前目录检查无法追溯这种维护级动作。正式封存仍应由独立 seal 进程按最终威胁模型重核全页或使用不可回退的安装 epoch。本候选不把 DB owner 作为对抗角色。

结果是有界 `sealAdmissionCandidate`，逐来源列固定 `sourceRef/revision`、来源版本、页/行/字节、收据链、推广缺日与财报自然月缺月/精确 scope。活源全局修订若已**单调前进**，来源标为 `historical_revision`，保留采集时版本，不伪称仍最新；若回退或同版本内容变化则拒绝。各来源不是同一时刻快照，财报金额不能摊到日/SKU，也不能与 ERP、B 端或广告归因额直接相加。

返回始终 `sealed=false`、`sourceAuthorityVerified=false`、`upstreamSignatureVerified=false`、`reportGenerationSupported=false`、`agentDispatchSupported=false`。段 HMAC 是本系统受保护运行边界内的见证，不是京东或财务上游独立数字签名。0037 不注册公共路由、Agent、renderer、模型或文件；真正封存还需独立 seal_writer 权限/迁移及再次 CAS/人审门禁。普通 AI writer 不能把这个候选直接用于修改父任务 `sealed`。
