# v4 推广段重放进度候选回执（0047）

0047 为独立 `teruisi_ai_seal_writer` 增加一张 append-only 回执表。它仍是 `NOLOGIN`，没有独立凭据或运行器。此迁移没有打开旧 `ai_v4_commit_seal`，没有提交 seal、修改父任务、接入 Agent 或授予报告权威。新回执只记录一个已由 0042 claim 读取并由调用方重放的推广段候选；`candidateOnly=true`、`authorityVerified=false`、`sealCommitted=false` 是必需值。

受保护写入函数 `ai_v4_sealer_record_replay_progress(run, attempt, source, segment_index, actor, actor_version, nonce, claim, candidate_json)` 只授予专用 sealer 执行。它在写入前后核验 0042 当前 claim；从数据库重新读取 0041 ticket、最新 0036 attempt、来源根、京东推广来源和精确 0036 segment。候选必须是规范 JSON、`business-v4-sealer-promotion-segment-candidate-v2`，且摘要与原段进度逐字段相符。首段 `previousCandidateDigest` 必须是 64 个零；后续段必须存在同一 run、最新 attempt、source、sourceRoot、actor 和 keyId 的前段独立回执，前段 ticket/claim 在其记录时有效，且同时连续匹配 0036 proof 摘要链与候选摘要链。跨段可换新 ticket。重复记录在新 ticket 的当前 claim 仍有效、规范 JSON 完全相同时返回原回执，冲突拒绝。

只读函数 `ai_v4_sealer_replay_progress` 使用同一 claim 返回精确段的已存候选和摘要，供下一段的受保护前驱核验。普通 AI reader/writer、PUBLIC 及 sealer 对物理回执表均无 DML 或直接 SELECT；运行角色的 UPDATE、DELETE、TRUNCATE 被拒绝。非空表阻止 0047 逆迁移。数据库只证明回执与既有账本一致；它不能独立证明页面原文重放、0036 HMAC 真伪、上游来源签名或财务段，调用方仍须在受保护边界完成这些检查。

目标隔离 PG 测试 3 项通过，见 `.runtime/ai-pg-363a8b109724/tests.log`；旧票据误授 TRUNCATE 防删除目标回归另通过 `.runtime/ai-pg-1db4fbb151b6/tests.log`。测试覆盖有效单段、完全相同幂等、进度篡改、首段候选链篡改、关闭的普通角色权限、NOLOGIN 与旧 commit 关闭、非空逆迁移阻断，以及目录门禁对函数 ACL 漂移的拒绝。0046→0047 独立升级、旧 78 表/renderer1—7 字节与旧函数权限保持、前后备份恢复和空回退再升级通过 `.runtime/ai-pg-40d04edc995a/business-v4-replay-progress-upgrade-evidence.json`。现有业务夹具仅有一页，不能在不改变 0041 的 180 秒活跃 claim 规则下快速产生第二张合法票据和第 17 页；跨票据两段的真实时间路径仍待专门 PG 演练。0047 仅为候选源码；未在正式数据库安装。
