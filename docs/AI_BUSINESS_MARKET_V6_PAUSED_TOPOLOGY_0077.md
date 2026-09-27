# 0077 市场 v6 同报告五 Agent 持久暂停拓扑（隔离候选）

0077 精确依赖 0076。新专用角色初始 `NOLOGIN`、无密码和成员，迁移不启用模型、费用或外部调用。隔离候选的创建函数在 AI revision 行锁内，核管理员版本、当前 0063 计划、0065 零预留费用候选及封存来源，按固定身份一次插入新报告、暂停工作流、六节点和五个不同的暂停 Agent。运行时 `model_id=''`、`allowed_tools_json='[]'`，provider/tool/read 结果表为零；旧 0065 标识只存为 `sourceUnverifiedCostCandidateId`，不作为可花费账本。

专用受保护表记录创建根和一次显式取消；普通 AI reader/writer 不得直接读取或写入。附加触发器禁止这些工作流和 Agent 被旧控制路径恢复或重绑，并拒绝 provider/tool/read 回执、Agent checkpoint/event、工作流 event、报告 delivery 和文件 run。创建按同一 AI revision 锁重算 owner 活动 flow≤4、job≤8 与全局 flow≤24、job≤64。取消须同身份/版本、五任务六节点原状且上述效果行全无；受保护取消回执与状态更新同事务提交，释放容量。结果未知时只能用确定性身份查询 OUTCOME，`absent_observed` 也不授权自动重试。

新 SQL 仅由测试环境的真实非超级专用登录连接调用；Python 客户端还要求隔离数据库、端口、环境和显式开关。角色仍没有绑定某一管理员/报告的正式工作许可，即使隔离LOGIN能用当前 owner 和报告字段完成受控测试，也不能作为生产授权。正式 PrepareApp、每日备份与恢复仍在迁移/受保护表出现时提前拒绝。没有正式模型选型、可核费率、人工每报告人民币上限、付费原子预留、真实 Agent 读取、引用或报告发布权限；`business_market_v2_paid_gate.before_reservation` 继续拒绝该 profile。

当前取消仅由共享专用 LOGIN、报告/owner/版本及当前管理员行核定，没有独立单报告工作许可；`OUTCOME=cancelled` 只证明存在某次受保护取消，不把查询方的取消请求摘要与历史取消行重新绑定。正式开关前须另版实现当前管理员授权、精确取消请求结果协议及恢复边界；本候选的重复取消仍返回 unknown 且不自动重试。

效果触发器覆盖的是当前列明、直接以 job/flow/report 为外键的表；未来若新增任何这类直接子表，必须同步扩展 0077 的副作用登记、禁止触发器、CANCEL/OUTCOME 零效果复核及隔离负例。当前实现不声称对未来未知表自动生效。

2026-09-26 唯一隔离 PostgreSQL 55828 的自身角色套件 **4/4 通过**（309.946 秒）：真实非超级登录原子创建同报告五暂停 Job/六节点、零派发/模型；合法完整字段的旧 writer 副作用行被精确新触发器拒绝；取消释放 owner 配额、同请求重复不重发；双并发仅一个创建、另一个在 revision 锁后精确超配额拒；第五 Job 冲突使先前四 Job 与报告整事务回滚；目录同型 CHECK 漂移拒。`tests.log` SHA-256 `110bb4636b957b10dbca3b20f4c1282ffaf9678f0cd82cd15942230ccb8e730e`，源 pg_ctl 停机、55828 无监听。升级仍须冻结旧 0060/0062/0064/0074/0076 OID/ACL/源码并分别恢复迁移前后备份；当前不宣称正式业务可用。

2026-09-26 唯一隔离 55830 focused 0076→0077 **exit0**：旧 28 函数在源库的 OID/正文/ACL/owner、旧 12 张保护表 owner/有效 ACL 未变；迁移前后两份 custom dump 分别恢复并复验，最终 14 张保护表、7 个新函数；空逆迁移保留无登录/无密码/无成员的全局角色并重装通过；正式每日备份在写目标归档前拒。测试库旧 `ai_business_file_chunks`/`ai_business_volume_chunks` 均为 **0 行**，所以“旧文件行保持”不证明非空历史成品字节恢复。正式恢复门禁当前由 `django-postgres-maintenance.ps1` 迁移收据与备份 helper 的受保护表 TOC 静态合同覆盖，本 focused 脚本没有执行正式恢复拒绝负例，不能声称该负例实测通过。

55830 run-root `D:\.codex\worktrees\ai-market-human-cap\运营管理系统\.runtime\ai-pg-5e783c76e04d`；evidence SHA-256 `4aed985899b899c47cc99209cfaa2287285bbeaeb0a1cd88195e58b7c6cd9387`，before/after dump SHA-256 `48fab749726c85fa667184319c29d4a34b58cd2fcc4f826c50cf41ec51abb8e7` / `50c5f51d6304f8529770acd277fc37b075d8dd7bc13a1558191aea10c4efa965`。源 `pg_ctl` 为 no server running、55830 无监听；无生产写入、模型调用、发布或下载。
