# 词货 renderer 7 内部发布候选

2026-09-24，隔离整合分支的 `ai_assistant.0029_business_promotion_file_ready` 与内部 `business_promotion_volume_stage.publish` 只用于经实际五 Agent、人审和文件完整性复验的 renderer 7 持久任务。公共创建与下载尚未接入，未在生产数据库迁移或部署。

0029 从已经验证的 0027 文件函数精确派生，旧 renderer 1—6 的 chunk、manifest 与 ready 路径继续保持原语义。新 7 父任务必须由同一已暂存 attempt 从 `paused/renderer_unpublished/staged_unpublished` 进入 ready，保持原 compact 清单、分片字节数与 attempt；数据库再次核对 compact 父版本为 7、分片顺序/数量/容量、精确词货报告、已完成的六节点工作流输出、五个独立完成的 Agent 输出以及人审批准事件。任何 renderer 7 行存在时逆迁移拒绝。

内部 `publish` 在事务外从每个持久分片重算 SHA，并对完整 JSON 清单、词货两视图材料、当前已批准五 Agent 内容和人工复核重新核验；前后及短事务内使用同一个有界状态栅栏，包含实际 Provider 与工具派发/结果账本的数据库内 SHA。短事务按预期版本 CAS，随后由 0029 数据库函数放行 ready。文件读取接口仍拒绝 renderer 7，因而内部 ready 不等于对外交付。PostgreSQL 的 compact 门禁沿既有多卷信任边界检查结构、分片链和容量；完整文件 SHA、来源数值及内容语义由持有写权的 owning `publish` 实际验证，数据库函数本身不声称独立完成这些扫描。

隔离 PostgreSQL 中，合法已暂存任务通过内部 `publish` 进入 ready，日志 `.runtime/ai-pg-221fe90a8d24/tests.log`；Provider/工具账本在复验与事务间变化的竞态负例保持 paused，日志 `.runtime/ai-pg-f654e4418d77/tests.log`。持久分片不可改写、compact 清单篡改及当前账号撤权均拒绝内部发布，日志 `.runtime/ai-pg-e7388be0bb10/tests.log`；未批准或未完成的工作流由新数据库门禁拒绝。各定向测试拆开执行，未为运行时间扩大现有门槛。

独立 `0028→0029` 升级与备份恢复演练已通过，证据 `.runtime/ai-pg-d8ac0e02f918/business-promotion-file-ready-upgrade.json`：旧 65 张 AI 表、renderer 1—6 的文件字节和读写权限不变；升级前后备份分别恢复到独立数据库。未经完整人审的旧 7 暂存行不能经直接 SQL 进入 ready，存在 7 行时逆迁移拒绝。该演练使用合成数据，合法发布由上述独立 PostgreSQL 测试验证；无付费模型或生产写入。
