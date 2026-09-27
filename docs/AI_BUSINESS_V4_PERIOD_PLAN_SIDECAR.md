# v4 三期日期候选持久侧表（0055）

`ai_assistant.0055_business_v4_period_plan_candidate` 只新增 `ai_business_v4_period_plan_candidates` 和一个窄的 `ai_v4_record_period_plan_candidate` 写入函数。既有 0035–0054 的函数、表、角色权限和报告文件版本均不替换。侧表每个 v4 run 最多一行；相同 run、最新 attempt 与完全相同规范正文再次调用返回原回执，任何摘要或来源根漂移拒绝，已有记录时拒绝逆迁移。AI reader、seal writer 和普通表访问均没有侧表 SELECT/DML；即使误授 INSERT，触发器也拒绝直接写入及修改/删除/截断。

纯 `period_bound_plan_v1` 可在**采集前**生成非授权候选；0055 的数据库落库则只允许四个来源都标记完成、当前管理员仍有效、父任务仍 collecting/manual、最新验证 attempt 与父版本/目录一致之后进行。库内重新检查原始 v4 计划、三个京东同店推广窗口、财报自然月查询、逐来源原始日期和来源身份，独立计算 current/previous/yearAgo 日期及每个 `expectedDayDigest`，再绑定数据库重新计算的整体来源根。拥有方 `business_v4_period_plan_candidate.create_candidate` 从当前持久计划重建纯包络并在当前 AI 写入事务内提交；它不接受调用方自报的包络。

这仍是**candidateOnly**：预期业务日不证明已采集，更不证明缺记录日期为零；后续须由拥有方提供逐日有记录/明确零日/缺日的可信证明。财报只保留自然月，不能分摊为 30 日日利润或 SKU 利润；`sourceAuthorityVerified`、`observedDailyCoverageVerified`、`zeroDayCertificationVerified`、Agent 引用和 renderer 均为 false。0055 不接 sealer 封印、模型、HTML/XLSX 或公开路由。目标隔离 PostgreSQL 测试含 30 天与闰日 Python/SQL 日期摘要一致、幂等、错日期/账号/最新尝试/来源根、误授权直接写入与非空逆迁移；测试使用高权限**合成完成计数**专测侧表绑定，不代表真实页/工具审计完整或业务验收。

隔离 PostgreSQL 六项目标 `.runtime/ai-pg-ca5ae4f621c3/tests.log` 通过（18.653 秒）。首轮新表的 TRUNCATE 触发器连真实表所有者执行的隔离测试 flush 都拒绝，导致后续夹具残留；现按既有封存表规则只允许真正所有者/数据库超级用户清理，运行时 reader/writer 仍被拒，重跑六项全部通过。升级/备份恢复与正式受限角色探针仍是单独门禁。

0054→0055 完整隔离升级 `.runtime/ai-pg-1bd88d32948d/business-v4-period-plan-upgrade-evidence.json` 通过：旧79表、renderer1—7字节、旧AI函数OID/正文/ACL与来源/封存/文件守卫保持，新侧表为第80表且三函数两触发器权限关闭，前后备份独立恢复、空逆迁移重做通过。首次恢复比较把 PostgreSQL 默认所有者 ACL 的不同表示误作权限漂移，测试脚本已归一 ACL 后重跑通过；这轮没有生产写入。

预先创建真实 reader/writer 的隔离目录正负探针 `.runtime/ai-pg-d4f88515ad1d/tests.log` 通过：当前函数/表/触发器目录完整，撤掉 writer 的窄写函数 EXECUTE 会被健康门禁拒绝，未开放任何侧表直读。
