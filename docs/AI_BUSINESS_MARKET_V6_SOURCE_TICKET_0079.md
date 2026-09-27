# 市场 v6 同报告来源页票据（0079 候选）

0079 从已经持久化的 0077 报告、暂停 workflow 和 `market_b2b` job 出发，追溯 0063 执行计划、0053 准入报告、0045 不可变市场材料及原始封存证据。独立受保护表最多为每份报告记录 64 张 `rank_entry_exit` 页票据；每张绑定管理员及版本、报告/任务、原始证据根、市场 manifest/table 根、当前/基线观察日和最多 38,000 字节的材料页号。材料分片按字节有界，行数不固定；重读须核完整 NDJSON 页链 SHA、行数、字节数及所选页摘要。再次读取使用精确 `OUTCOME`；未知提交结果不自动重发 `ISSUE`。

**这是市场 TOP 样本指针，不是完整业务数据源。** 本店身份、本店销售、B 端销售和同比期在票据中固定为 `unknown_not_supplied`，不能填零或跨另一份 integrated v2 报告拼接权威。未来若要连接同店三期来源，须先有独立受保护的同报告/同店/同修订链接，并逐页证明来源根、日期、计量口径、行集合和缺口；原始市场样本金额不能加到本店或 B 端销售。

普通 `ai_reader`/`ai_writer` 没有 0079 表或函数权限。专用角色初始 `NOLOGIN`、无成员，SQL 入口仅限隔离测试库及回环端口。共享测试角色只核数据库中的管理员当前版本，**不等于独立的单报告人工许可**；若未来正式接线，必须另有受认证请求票据绑定当前操作者与精确报告。0077 的 `sourceUnverifiedCostCandidateId` 仍不是可支出账本。没有正式 API、模型选择、费率、人民币上限、费用预留、provider/tool dispatch、Agent 读取回执或数值引用授权。SQL 票据只有根和页范围，不复制页内容；Python owning reader 可双遍重放实际页，但在尚未打通受保护 OUTCOME 到 reader 的可信交接前，单独传入 Python 的 JSON 票据仍为候选，其 `protectedTicketProvenanceVerified=false`。

当前验证仅有纯合同 4 项通过、Python 编译和 `git diff --check`。55831 首轮隔离尝试在迁移目录 ACL 校验阶段因新测试代码游标读取顺序错误而失败，已修复；**角色 PG 尚未成功**。55832 第二轮在 runner 等待期间被异步消息中断，隔离 PostgreSQL 已停，但没有 `tests.log` 或 `failure.log` 终态，不能算通过或业务失败。该旧 runroot 的临时凭据文件未读取；精确清理命令被自动策略拒绝后保持原样，不另试绕过。后续须在新的受控隔离 runroot 完成非超级角色正反、目录漂移、并发/配额、跨报告、0078→0079 升级及双独立恢复。正式 Prepare、备份和恢复源代码已加入精确 0079 早拒，不能把隔离候选当作生产可用，更不能开启付费或发布。

2026-09-27 更新：上段“角色 PG 尚未成功”是前两次失败时点的记录，已由整合分支**全新**隔离库 55843 的精确角色用例 **1/1 通过**替代。它同装 finance.0006 与 0076–0079，完成已持久 0077 报告与 `market_b2b` 材料页的真实角色绑定和双遍 owning replay；测试库销毁，`pg_ctl no server running`、端口无监听，新 runroot 未留密码文件。E 日志 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-business-0079-source-ticket-role-20260927\tests.log` SHA-256 `a5616575a7f93a21d5fe9a35499b1f5efc7472a89b995c080fb920804afe0638`。旧 55832 无终态与清理受阻仍单独保留，不因新库通过而抹除；0078→0079 独立升级/恢复、非空票据逆迁移和容量仍待验。

新增的 0078→0079 focused 脚本将检查旧 0077/0078 函数 OID/源码/ACL/owner、旧受保护表、旧文件字节摘要、前后两份独立恢复、空逆迁移保留关闭的全局角色并重装，以及正式备份/恢复/Prepare/Deploy 早拒；**脚本尚未运行，不能把这些条件记为通过**。`0079` 单独迁移的正式早拒负例已加入 PowerShell 测试但尚待执行。非空票据逆迁移拒绝目前仅由迁移源码中的存在性守卫约束，缺真实 PG 负例，单列待验。市场样本之外的来源/跨报告、实际费率模型、付费调用及发布仍默认关闭。

2026-09-27 后续更新：上段 focused 待验状态已由整合分支 55849 隔离演练替代。0078→0079 旧 65 函数源库 OID/正文/ACL/owner、15 张旧受保护表 owner/ACL、旧文件块字节摘要均保持，迁移前后两个 custom dump 各自独立恢复；空逆迁移保留关闭的 NOLOGIN 全局角色并可重装，正式 backup/restore/Prepare/Deploy 早拒。旧受保护行和旧文件块均是 **0 行**，不能据此宣称非空真实成品恢复。E evidence `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-business-0079-focused-upgrade-20260927\business-market-v6-source-ticket-upgrade-evidence.json` SHA-256 `cdbe87e97d35d9198828fc3398bb0b450af64a00997d8f3468dfdeac2edb585c`；源 `pg_ctl no server running`、55849 无监听且无密码残留。PowerShell 正式迁移门禁测试通过，528 模块静态边界无违规。非空票据逆迁移的实际 PG 负例、真实全量容量及跨域权威仍待验。

2026-09-27 再验：上述“非空票据逆迁移待验”已由全新 55852 隔离库精确角色用例 **1/1** 补齐；持久票据行存在时 `0079.uninstall` 拒绝并保留该行，同次复验市场材料页双遍重放。E 日志 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-business-0079-nonempty-reverse-20260927\tests.log` SHA-256 `025d399c8587248b4eb3ba9a2bd89556771383aa354f2a997f100e83310c264b`；测试库销毁、源停机、55852 无监听且无密码残留。真实约 575,095 行容量、单报告独立许可与跨域同根来源继续未验。
