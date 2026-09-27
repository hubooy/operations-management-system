# v4 财报段重放进度候选回执（0048）

0048 仅替换 0047 的 `ai_v4_sealer_record_replay_progress` 函数体，使原 append-only 回执同时接受 `business-v4-sealer-finance-segment-candidate-v2`。原推广 candidate v2 的字段、标志和段进度校验保留；票据、claim、来源根、0036 段 proof 链、前候选摘要链、同段幂等及表权限均复用 0047。`teruisi_ai_seal_writer` 仍为 `NOLOGIN`，直接 seal 仍关闭。

财务分支要求来源 `domain=finance`、`temporal_role=monthly_context`，规范 query 原文与来源 query 摘要相符；候选来源根、来源版本、来源引用、修订和 keyId 必须与库中票据、来源及段一致。候选仅含固定 21 个字段，`candidateOnly=true`、`authorityVerified=false`、`financeReplayed=true`、`upstreamSignatureVerified=false`、`sealCommitted=false`。六字段有限进度的页数、行数、原文字节、块摘要、收据链和完整 `financeState` 必须逐字段等于 0036 段进度；`financeState` 的 query 摘要、来源引用/修订、计数、`persistentEvidenceVerified=false` 及末段 `finished` 也单独复核。NULL 缺值使用 `IS DISTINCT FROM` 拒绝。

本回执仍只证明数据库候选与既有进度账本一致。它不独立验证原始财报页、工具审计、0036 HMAC、上游签名，也不授予父报告封存、Agent 或文件权威。调用方仍须用受保护 claim 逐页重放并核验段 MAC。0048 逆迁移前若存在任意 finance candidate 行则拒绝；空 finance 行时恢复 0047 的原函数体。

目标隔离 PostgreSQL 3 项通过 `.runtime/ai-pg-61ff5ac84d1a/tests.log`：用项目真实财报导入及 finance owner 一页夹具核财务写入、同票据幂等、推广 v2 兼容、状态/NULL/标志篡改、未封存、非空财务逆迁移拒绝及目录门禁对旧函数体漂移的拒绝。0047→0048 独立升级、旧79表与renderer1—7字节、旧函数OID/ACL、双备份恢复及空回退再升级通过 `.runtime/ai-pg-3fb70eedf058/business-v4-finance-replay-progress-upgrade-evidence.json`。目录门禁按0047/0048迁移回执分别固定对应函数体，不放宽角色、ACL 或旧版本检查。17 页跨票据真实时间路径仍待专项演练；此候选未生产采用。
