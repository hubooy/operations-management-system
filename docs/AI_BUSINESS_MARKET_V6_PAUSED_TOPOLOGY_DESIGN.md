# 市场 v6 同报告五角色暂停拓扑：未安装设计

状态：**纯合同与拥有方创建前读取候选**。当前不占迁移号、不创建数据库行、不注册执行入口或调用模型。`0076_business_promotion_budget_v11_ticket_bound_signer` 已由并行任务预留；等该迁移文件及权限冻结后，v6 暂停拓扑才考虑用后继 `0077` 实现，并从精确的 0076 文件名建立依赖。

## 本轮已实现的纯边界

`business_market_v6_paused_topology_contract` 从既有 v6 拟建计划与 0065 零预留账构造稳定的 report/workflow ID、五个不同角色 job ID、六节点和创建意图。请求 ID、当前管理员版本与来源报告决定报告/流程 ID；每个 Agent role 的 job ID 仍遵循既有 v6 `market-v6-job` 派生规则。纯输出的 `candidateOnly=true`、`reportPersisted=false`、`jobsPersisted=false`、`quotaObservationOnly=true` 不能被当作 SQL 回执。

0065 行仅保存为 `sourceUnverifiedCostCandidateId/Digest`，并明确 `sourceCostCandidateSpendable=false`。它已有模型 ID/版本和待核价目，不能成为以后用户选择模型的可花费账本；以后必须另建以新 v6 report/所选模型/权威价目/人工上限绑定的版本化费用权威。当前拓扑 model ID 为空、model version 0、approvedCnyCapCents 为 null，provider/tool 计数为 0；费率权威、人审上限权威、持久预留、providerCallsAllowed、工具派发、Agent 已读、数值引用、人审和发布全部 false。0074 的人审上限即便存在，也不自动移作新 v6 模型的批准。

只读 owning `prepare` 仅在隔离 test、显式 `AI_MARKET_V6_PAUSED_TOPOLOGY_PREPARE_ENABLED=True` 且既有 `AI_MARKET_V2_READ_PLAN_V6_ENABLED=True` 时重新读 0063 计划、0065 候选和硬关闭的 paid gate，查当前无范围管理员版本、拟建 ID 未占用和观察配额，最后再次读取以拒绝普通竞态。它**不持有创建锁**。工作流活动上限为每账号 4/全局 24；五个暂停 job 会计入调度器活动上限每账号 8/全局 64，故创建前须观察现有数量加 1/5 后仍不越界。观察值不是预留；SQL 须在同一 AI 全局修订锁事务内重新计数。

## 后继 SQL 协议（待 0076 稳定后编码）

新增独立版本的 `CREATE_PAUSED`、`CANCEL_UNSTARTED`、`OUTCOME`。现有 0060、0062、0064、0074 函数正文、OID 和 ACL 不改。新 SQL 不能接收调用者的 `providerCallsAllowed=true`、模型凭据或价目作任何正向权限。签名 Django AI writer 中的管理员请求先进入既有 `policy.mutation`/AiWriteReceipt 事务；SQL 函数自身也锁 `ai_data_revisions(domain='ai-assistant')` 并复验活动 authority epoch/cutover、管理员、来源执行报告/流程、0061 context、0063 plan、0065 行的完整摘要及恒零预留。0065 候选费率即使过期也不赋权或充当新模型价目。所有 ID 和意图摘要由 SQL 独立计算，不信任 Python DTO 或请求布尔值。

在该锁内重核活动 flow/job 配额、报告/流程/五 job/六 node ID 尚空，固定顺序一次插入新暂停 workflow、report、五 `status=paused, phase=paused, model_id='', model_version=0` 的 job 和六 pending node。父流程 `retryable=0`、零租约/轮次/工具次数，不可由通用 resume 变 queued。用新增窄触发器及延期完整性检查固定同报告/同流程/五角色/六节点；创建末尾逐表核 provider dispatch/result、tool dispatch/result 和 0062 read receipt 都为零。任何第五个 job 冲突或末尾栅栏失败，整笔回滚，包括通用写回执。不得复用 0064 合成 provider 行或把拥有方观察当 Agent 已读。通用 `business_market_v2_paid_gate.before_reservation` 保持原硬拒；新数据库触发器另阻断此 profile 的直接 provider/tool/result DML，避免仅依赖 Python gate。

同一报告长期暂停会占 1 个 workflow 和 5 个 job 名额，因此同一后继版本必须提供 `CANCEL_UNSTARTED`：只允许当前有效的原 owner 管理员，在全部五 job 与节点仍处于初始暂停/待执行、且任何 provider/tool/result/read 记录均不存在时，固定顺序持锁把 flow、jobs、nodes 原子置 `cancelled` 并追加审计，保留原报告快照和来源。重复取消只回精确已取消事实；任何派发、账号/报告身份冲突或部分状态一律拒绝。被撤权账号无法自取消时须走后续受保护运维闭合，不得删除证据或改全局配额。

网络回复丢失后调用方只能用原请求 ID 查询只读 `OUTCOME`，区分 `committed_paused`、`cancelled`、`absent_observed`、`conflict` 与无法判定的 `unknown`；不自动重放 CREATE/CANCEL。`OUTCOME` 每次核当前账号版本与报告/流程/五 job/六 node 及全部零派发字段，不因一张父行存在就返回成功。所有返回继续 `modelId=''`、`durablePaidReservation=false`、`providerCallsAllowed=false`、`agentReadPersisted=false`。

## 后继隔离 PostgreSQL 验收

真实受限 writer 正例须证明五 job/六 node 共享一个新报告/流程、固定角色顺序和 deterministic ID、零模型/零 provider/tool/result/read 行；`provider.turn` 与远程工具均不调用。负例覆盖跨账号/店铺、源根/管理员版本变更、费用候选变更、重复请求与异请求冲突、第五个 job 后置失败全回滚、两连接并发容量、直接 DML/手工 resume、取消释放活动配额、取消与创建竞态、任意派发后取消拒绝、丢回复只读 OUTCOME。新版本升级还须冻结旧 0060/0062/0064/0074 函数 OID/正文/ACL/owner、已存在 renderer 字节、13 个受保护角色/表权限；前后独立恢复、空逆迁移及正式备份/PrepareApp 提前拒绝。未拿到真实模型/费率/账号/人审上限前，任何正例仍不得产生 provider dispatch、Agent 本人已读、数值引用或可发布文件。
