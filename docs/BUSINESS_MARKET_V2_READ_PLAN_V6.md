# 市场 v2 新报告同任务已读：v6 创建前候选

`business-market-v2-same-report-read-plan-v6` 是**纯计划**，不是 0073 迁移或一份已创建报告。拥有方 `prepare` 默认关闭，仅隔离 Django `test` 环境能调用；它从当前无范围管理员的窄回执重新读取 0063 来源计划、0065 零预留费用账和通用付费门禁，两次核对身份及候选 ID 未占用。缺成本账、账号变化、旧身份复用或任一付费许可标志异常，整体失败，不写数据库。0069 没有可授予真实调用的 reader 证明；其现有行只是合成彩排。0072 rate/cap 仅为待独立核验和人工批准的提案。两者在 v6 均不被当成模型许可。

纯合同固定一个**全新** reportId、workflowId 与五个不同的拟建 jobId，来源只指向旧 0060 报告及其已核 0061/0063 根；每个角色槽位从一开始携带同一新报告/流程身份，并冻结后续 provider、tool、read receipt 必须逐级携带的报告/流程/job/派发键。provider dispatch、tool dispatch、read receipt 三类列表均为空，`jobPersisted=false`、报告/流程也未持久化。新 profile 为 `business-agent-screening-promotion-market-read-v6`，状态只能 `paused_pre_creation`，模型空、调用次数零、无外部调用，已读/数值引用/人工通过/发布全部 false。纯 SHA 和调用方给出的候选 ID 不等于 SQL 权威。

未来数据库正例必须在一笔受保护事务中重新计算 0060/0061/0063 来源、账号版本、0065/0069/0072 权威与费用，核候选 ID 空闲并创建**同一新报告**的流程、五个 job 和六节点，同时版本化窄守卫；不能修改或复用 0060/0062/0064 的行、函数 OID/ACL 和 paused 分支。只有独立授权且逐轮原子预留后，真实 provider 响应才能产生该 job 的 provider 派发；拥有方工具完成、同 job/provider 调用和结果一对一入账后，由独立 SQL attestor 生成新版本已读回执。旧 0062 函数不签新报告；0064 合成响应永远不能升级为本人已读。数值单元格仍需另行独立证明。

纯测试目标 `ai_assistant.test_business_market_v2_read_plan_v6_contract`；隔离 PostgreSQL 负向目标 `ai_assistant.test_business_market_v2_read_plan_v6_role` 验默认关闭和缺 0065 账时零新报告/零回执。此切片没有新表、角色、付费调用、真实模型结果或生产采用。
