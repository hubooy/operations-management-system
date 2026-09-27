# 市场五 Agent 单报告人民币上限：0074 隔离候选

`0074_business_market_v2_human_cap_approval` 在独立开发树中新增两张受保护、只追加的批准/撤销表，以及预览、批准、撤销和只读状态函数。它直接绑定当前 0065 零预留费用需求账、执行计划、报告快照、账号版本和模型**非密钥**配置摘要；不依赖 0072 的 NOLOGIN 提案角色来冒充人工确认。金额仅允许在既有候选 `required_cents` 与 `cap_claim_cents` 之间收紧，单位为人民币分，有效期最多 31 天。管理员必须经精确同源 POST 显式提交；同一请求可幂等回放，换金额、换报告、账号版本变化或撤销后的重放均拒绝。撤销只追加，后续查询能区分已批准、撤销、过期、来源变化和缺行。

这只证明**当前管理员明确设置过上限**，不证明候选价目、汇率、计费账号或其他收费类别。0065 行仍强制 `reserved_cents=0`，0072 待核提案不改，0069 仅隔离演练；所有 0074 回执固定 `tariffAuthorityVerified=false`、`credentialAccountVerified=false`、`fundsReserved=false`、`providerCallsAllowed=false`。通用市场 Agent 的付费门禁和模型派发未改变。尚无工作台审批按钮或真实模型选择，不能将精确 API 入口说成完整面向用户的审批流程。

正式 `PrepareApp`/`DeployApp`、普通迁移及备份/恢复均在 0074 迁移或受保护表出现时提前拒绝；此候选没有生产配置、付费请求或外部发送。最终修订后的隔离 PostgreSQL 真实 AI writer 五项通过，覆盖显式审批/幂等/撤销/跨账号、管理员版本前后回放拒、同管理员双报告交叉拒与第二报告自身正例、直接 DML/ACL、关键约束和触发器 `WHEN false` 漂移。最终证据 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-3604d840116a-human-cap-role-final\tests.log`，SHA-256 `211B39A9147B160D490EEFE3BD83298D665D45383219DD5C3727F12D9A67BD4B`。本段仅属候选角色验收，不是正式采用。

`0073→0074` 隔离升级与第二全新 PostgreSQL 集群恢复最终通过：旧函数 OID/正文/ACL/owner、旧 7 个文件块字节及 9 张 protected 表 owner/relkind/规范化表列 ACL 和全部 `teruisi_*` 角色有效表列权限保持；新批准/撤销表为空、无新增全局角色。前后独立备份恢复、空逆迁移重装、正式备份在写档前拒绝通过。第二集群 11 张受保护表、13 个角色的 owner/ACL 保留，合成私钥仅在内存封装 AES-256-GCM v2 分块归档；错密钥、截断、改块、换序、复制块、错误上下文六类损坏均拒绝，明文 dump 文件为零。两端 `pg_ctl no server running` 且无 listener，整段归档在 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-3e8d545dc06c-human-cap-0074-passed`，215,205 文件、3,990,173,768 字节前后核一致；升级证据 SHA-256 `83D2C6A8BCD625DADB73DDC618CFA1C759BD8BD3F25DE468CE5144F8216F7DA0`，第二集群证据 SHA-256 `E32831BB873617AD59D2F6F9BEED3AA8ACC6FF156A37D7DDEF19DFCD3F78AFE0`。

首轮演练曾因测试脚本的参数化 LIKE 写法失败，后一次误将源表显式 owner ACL 与恢复库默认 `NULL` owner ACL 的目录表示认作权限变化；失败运行和日志保留在 E。只读逐表复核证明 9/9 表只有这项原始表示差异，owner、relkind、规范表列 ACL、受限角色有效表列权限全等；最终跨库比较仅规范这一表示，同库迁移前后仍逐原始目录和 OID 冻结。这些是测试脚本修复记录，不表示生产恢复获准。

本独立树从整合基线 `1bd52362` 创建，不含并行开发的 `finance.0005` 与 stream-v1。未来整合后还需迁移共存、权限、备份和最终组合回归；真实费率/汇率/计费账号、人审金额输入、逐轮原子预留、未知 provider 结果处理及付费模型效果均是后续门槛。

整合后的额外验收：含 finance.0005 的同一隔离库执行 0074 原始真实角色 5 项、finance 原始列侧车 7 项和双目录共存 1 项，共 13/13 通过；隔离测试 runner 仅在清理事务中恢复 finance 的 TRUNCATE 触发器状态，不改变正式只追加守卫。最终日志与 SHA 见 [财务侧车说明](AI_BUSINESS_FINANCE_RAW_COLUMN_V2_SIDECAR_0005.md)。这仍不授予正式审批、模型付费或下载资格。
