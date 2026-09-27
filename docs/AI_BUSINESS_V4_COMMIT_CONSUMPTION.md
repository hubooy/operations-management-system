# v4 父封印与票据消费候选（0052）

0052 只新增 `ai_v4_sealer_commit_with_consumption`。入口从真实 `SESSION AUTHORIZATION teruisi_ai_seal_writer` 的已领取票据开始，保持角色 `NOLOGIN/NOINHERIT`；不授予旧 `ai_v4_commit_seal` 直接执行权，也不允许 sealer 直写消费表。安装前核对旧封印、claim、根目录、回放写入、消费触发器的冻结函数、所有者及关闭的直接权限。消费账本非空时拒绝逆迁移。

函数按 `commit-seal-v1` 对精确规范正文原始 UTF-8 字节求 SHA-256，并用身份、父版本、计划与目录摘要、正文摘要、正文 MAC 和 keyId 重建请求摘要，要求它等于最终票据的 `request_digest`。它重新核对活动 claim、当前来源根、每个来源的全部连续分段、段证明摘要、候选摘要链、历史票据有效领取记录及相同根、身份、密钥版本。缺少任何一个分段候选时拒绝提交。通过后调用 0038 原封印函数，并在同一个 PostgreSQL 函数调用中插入 0043 消费回执；中途异常会回滚整个语句，0043 的延迟触发器继续拒绝单独封印。若结果不确定，调用者必须通过已有 `ai_v4_sealer_consumption_result` 精确查询，不盲目重试封印。

**验签边界：** PostgreSQL 只能验证 `bodyMac` 的格式以及它在请求摘要中的绑定，不能独立验证真实 HMAC。候选回执的 `authorityVerified=false` 也不应被解释为已获权威授权。正式执行器必须在受保护环境中取得独立派生的父封印密钥，先验证正文真实 HMAC、来源权威、当期 `authority_epoch/cutover_id`，再通过受控身份调用；不得把主密钥放入命令行、环境变量、日志或测试夹具。0052 本身不激活登录、不连接生产、不派发 Agent/付费模型，也不交付报告。

隔离 PostgreSQL `BusinessV4CommitConsumptionTests` 五项通过最终 `.runtime/ai-pg-929da4f9223d/tests.log`：完整推广和财务候选的单语句封印消费、按原票据精确读取消费终态与内部 `verify_seal`、缺回执/错误 claim/错误请求/非规范正文阻断、旧直达封印拒绝及非空回执逆迁移拒绝。目录门禁撤权负例另有一项通过 `.runtime/ai-pg-d5e9d4b49403/tests.log`。首次安装暴露 PL/pgSQL `CASE` 括号语法，第二次运行暴露局部 `ticket_id` 与表列歧义；均已修复，不能把失败轮次算作通过。0051→0052 独立升级/恢复/空回退再升级通过 `.runtime/ai-pg-de8865ae6bd3/business-v4-commit-consumption-upgrade-evidence.json`：旧79张 AI 表、renderer1—7文件字节、所有旧 AI 函数OID/正文/ACL保持；新函数签名、所有者、SECDEF 与权限固定，前后备份独立恢复。以上仅是隔离合成角色验收，不代表受保护凭据、真实来源规模或正式业务采用。
