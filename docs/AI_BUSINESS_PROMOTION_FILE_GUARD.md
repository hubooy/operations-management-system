# 词货 renderer 7 的文件存储门禁候选

2026-09-24，隔离整合分支的 `ai_assistant.0027_business_promotion_file_guard` 仅增加文件任务和多卷分片的数据库存储边界。它尚未注册 renderer 7 的 Python 文件生成器、完整 manifest 格式、下载入口或模型调用；renderer 7 的 `ready` 状态由父任务与完整性触发器双重拒绝。

迁移从已应用的 `0025_business_file_opc.NEW_SQL` 五个函数精确派生。旧单文件分片函数及多卷 compact manifest 函数保持原字节；其余三个函数只扩新版本的父 run、同 attempt 多卷分片与总体容量核验。`ai_business_file_bound` 扩至 7，但新版本父 run 必须在初始 queued 状态绑定 `0026` 已保护的词货报告、相同 owner/scope、固定两视图 selector 与 SHA 格式 binding。历史 1–6 的行、manifest、分片、尝试次数和下载格式不变；无新表、列或角色权限。任意 renderer 7 行，即使已取消，都会阻止逆迁移。

允许 renderer 7 的 queued、building、paused、cancelled 及不超过原限额的分片，是为后续完整构建器保留可恢复存储路径。这里的 staged 分片不是报告或文件交付：`business-file-delivery-v2` 对 renderer 7 的完整清单、来源证明、词货两视图费用守恒及人审绑定尚未冻结，所以本迁移明确拒绝 ready；不能通过重用 renderer 6 的清单发布。

隔离 PostgreSQL 专用测试 `ai_assistant.test_business_promotion_file_guard_migration.Renderer7GuardTests` 3 项通过，日志 `.runtime/ai-pg-efb084c43045/tests.log`；覆盖正确父报告分片写入、错父/错 owner/错误摘要、旧单文件分片或错误 attempt 拒绝、ready 双门禁、旧 1–6 初始任务以及空逆迁移恢复原函数/有新行逆迁移拒绝。独立 `0026→0027` 备份恢复演练使用 `tools/ai-postgres-rehearsal.py --business-promotion-file-guard-upgrade --upgrade-only`，证据位于 `.runtime/ai-pg-debffe9408d2/business-promotion-file-guard-upgrade.json`。65 张旧 AI 表、renderer 1–6 完整文件字节、五个 guard 函数与角色权限升级前后相符；新 7 分片可暂存、ready 被拒绝，存在新 7 行时逆迁移被拒绝。0026 与 0027 两次独立备份均恢复成功，恢复后的 owning 来源页及新报告一致。全程是隔离合成数据，无模型与生产写入。

后续只有在 renderer 7 的完整 manifest 与对应 HTML/XLSX 两表格式确定、旧 1–6 字节兼容和恢复/下载/权限测试通过后，才能用后续版本化迁移打开 ready。届时必须复验新 profile 五角色证据读取、人工复核和词货两表的全量材料摘要，不得把本次存储门禁当作正式文件能力。
