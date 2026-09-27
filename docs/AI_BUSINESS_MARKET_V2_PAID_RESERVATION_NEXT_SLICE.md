# 市场 v2 五 Agent 逐轮费用预留：下一切片合同

本文件描述**未启用**的下一切片。现有 0065 行仅是报价需求：`reserved_cents=0` 为数据库 CHECK，`providerCallsAllowed=false`。通用 Agent 的 `business_market_v2_paid_gate.before_reservation` 继续拒绝全部市场任务，即使独立运行开关被打开。本切片不调用模型、不生成付费许可。

0069 候选在隔离分支实现了 SQL 拥有的合成演练账。只有数据库名 `teruisi_ai_rehearsal` 或 `test_teruisi_ai_rehearsal`、loopback 端口 55440–55999，以及三个独立 NOLOGIN 角色可调用采纳、预留、派发开始函数。迁移后表为空；生产库即使安装该迁移也无法由这些函数插入正向预留。`providerCallsAllowed` 在纯合同和三个 SQL 回执中始终为 false。真实费率来源、人审批准及收费类目仍须在后续受保护采纳流程中独立核验，现有合成摘要不等于权威。

## 已有能力与新增纯合同

- `market_model_cost_envelope.reserve` 已将五角色、每轮最大输入/输出 token 和 CNY 单价按整数向上取整，算出五角色最坏成本；它不证明费率、汇率或人工批准。
- 0065 已将这个需求与当前模型配置和执行计划绑定，持久记录但强制预留为零。不能修改 0065 行以补齐授权，也不能将一个 SHA 摘要视作批准。
- 新增 `business_market_v2_round_reservation_contract` 从完整 0065 候选重新计费，输出稳定的 `(plan, ledger, role, round)` 槽位 ID 与精确请求绑定的意图摘要。相同槽位换请求会产生不同意图；报价本身仍为 0 元预留和 `providerCallsAllowed=false`。
- 纯状态合同仅允许 `quoted_unreserved → reserved_awaiting_dispatch → dispatch_outcome_unknown → result_observed_unverified`。后续 `result_verified_closed` 只能由未来受保护的数据库验真路径写入，纯状态函数无法生成；同一角色须此前一轮已验证关闭才能进入下一轮。`dispatch_outcome_unknown` 无自动重试边；即使看到模型结果，纯投影仍保留整轮上界，不凭未经认证的 usage 释放费用。纯投影只供设计/测试，不能用调用者传入的条目决定准入。

## 0069 演练合同与后续正式能力边界

0069 新增 `ai_business_market_v2_paid_authorities`、`ai_business_market_v2_round_reservations`、`ai_business_market_v2_round_events`。首表保存完整费率原币、CNY 汇率整数分子/分母、收费类别、来源摘要及人工上限/人审摘要的合成合同；SQL 复算 CNY 单价，并绑定 0065 候选、当前模型和管理员。第二表以 `(plan_id, role, round_number)` 唯一，`SELECT authority FOR UPDATE` 序列化同报告预留，精确重放不重复扣额、改请求或改意图拒绝；五角色最多各自第一轮，因为本切片没有认证结果关闭能力。第三表只能追加一次 `dispatch_started`；事务提交后即把网络结果视为未知，重复派发拒绝。未验证服务商计费前不释放金额。

隔离 PG 目标测试为 `ai_assistant.test_business_market_v2_paid_round_role`，其专用会话 helper 只接受 0069 三个新角色，不扩大旧 reader/writer helper。目录检查同时锁定触发器事件、主/唯一键、外键目标与删除规则、CHECK 定义和持有金额索引；测试用可回滚的 DDL 漂移作负例。完整 0068→0069 前后备份独立恢复/空逆迁移脚本为 `tools/business-market-v2-paid-round-upgrade-rehearsal.py`，由 `tools/ai-postgres-rehearsal.py --business-market-v2-paid-round-upgrade --upgrade-only` 调用。交主任务串行运行，不能把脚本存在或纯测试通过当成真实数据库验收。

后续正式能力仍需：

1. **费率权威记录**：独立、仅受保护采纳入口可写的不可变行，绑定 `provider_id/model_id/model_version`、具体来源标识和采集时间、原币种、输入/输出及所有收费类别、CNY 换算依据/生效区间、完整报价摘要。未知类别或收费工具存在时不采纳。来源摘要及调用者 JSON 不是权威本身。与当前 `ai_models` 配置版本、状态和能力核对，过期即拒绝。严禁存放密钥/令牌。
2. **人工上限记录**：仅受保护的人审路径采纳明确的报告 ID、计划 ID、模型、费率版本、CNY 分上限及批准人/时间/版本。不能从 0065 的 `approvedCapClaimCents` 和 `approvalDigest` 推断批准。账号或报告版本变化、撤销、预算期过期都拒绝新预留。
3. **正式预留和派发原子化**：0069 独立演练表不改变 0065 恒零行，也不连接通用 Agent 的付费调用入口。未来须在同一受保护事务中核已采纳的权威费率/批准、当前根/账号/模型、逐轮上界与剩余额度，并将预留和真实 provider dispatch 意图绑定后提交。相同请求只回原回执，不能重新网络发送；不同请求或跨报告争槽失败。严禁单纯进程内锁或先查询后插入。
4. **派发边界**：付费 HTTP 请求只能在上述事务提交、并把 `dispatch_started` 持久化以后进行。网络超时、进程崩溃或不确定是否发送时，阶段维持 `dispatch_outcome_unknown`，不得自动重发同轮或改换请求重用该槽；需人工核服务商结算再决定补救。只有受保护且经过验证的服务商结果和结算证据能关闭该轮，释放差额须独立追加事件，不覆盖历史。
5. **角色与 ACL**：费率采纳、人审批准、预留/派发、只读回执分离成专用 NOLOGIN 角色；无成员、无继承、无表直接写权，函数仅授精确角色。`SECURITY DEFINER` 函数固定 `search_path=pg_catalog,public`，核 `session_user`、owner、角色属性/成员、计划和当前用户。行更新/删除/TRUNCATE 由数据库触发器拒绝，事件只追加。健康门禁核函数正文/owner/ACL、表/列 ACL、约束、索引、触发器绑定和角色漂移；备份恢复、空逆迁移和旧表/旧函数冻结需隔离 PostgreSQL 演练。
6. **调用前复核**：未来正向 paid gate 必须在每次 provider 派发事务中读取 SQL 拥有方回执，不能使用本模块的报价或调用者布尔值。工具调用若另外收费，必须先扩充费率类别和相同原子预留流程。单报告全部收费总额与人工上限以数据库整数分为准。

最小真实角色测试矩阵：两 Agent 并发争相同/不同槽、相同意图幂等重放、改请求冲突、累计超限、过期/撤销费率和批准、模型版本变更、跨店跨账号、直接 INSERT/UPDATE/DELETE/TRUNCATE、角色扩大、事务回滚、派发未知后重试、已观察但未认证 usage 不释放。测试只能用隔离数据库及合成费率；真实价格、用户上限和服务商调用需另行确认。正式服务迁移、付费调用和生产密钥均不属于本切片。
