# 市场 v2 正式费率与单报告上限采纳边界

截至 0069，系统只有 `ai_business_market_v2_cost_ledger_candidates` 的费用需求，以及仅限隔离库的合成预留账。两者不能证明服务商费率、CNY 换算或本人批准；通用 Agent 的市场付费门禁继续关闭。本设计不填写任何真实价格、汇率或批准金额，不请求密钥，也不调用模型。

`business_market_v2_authority_adoption_contract.build` 复用现有 0065 算术与 0069 合成合同的固定字段检查，产出两份规范提案：费率来源提案绑定计划、报告、账号版本、当前模型版本、原币价格、CNY 汇率、全部收费类别和材料摘要；人工上限提案再绑定精确费率提案摘要、批准人、费用分上限及有效期。两份提案的独立核验位和 `providerCallsAllowed` 始终为 false。它们仅是待采纳数据，散列值不证明材料真实性，合成测试结果也不能作为用户批准。

0072 候选新增默认空的 `protected_business_market_v2_rate_proposals`、`protected_business_market_v2_cap_proposals` 与 `protected_business_market_v2_authority_revocations`。提交函数分别要求 `teruisi_ai_market_rate_proposer`、`teruisi_ai_market_cap_proposer`、`teruisi_ai_market_proposal_revoker` 三个无成员的 NOLOGIN 角色；reader 只可通过窄回执查看本人当前账号版本的提案摘要、待核状态和撤销位，不可直接读表。普通 writer 不可写入。函数复核当前模型/0065/报告/账号、CNY 汇率整数复算、严格输入与输出 token 两类计费、有效期、精确重放和追加撤销；目录门禁固定列/约束/FK/触发器事件/函数正文与 ACL。所有提案仅为 `pending_*`，没有 verified 状态或正式执行身份；真实价格、人审金额均未填入。0072 不改 0069 合成账或通用 Agent 的拒绝门禁。

独立角色目标为 `ai_assistant.test_business_market_v2_authority_proposal_role`，0071→0072 双备份独立恢复和空逆迁移为 `tools/business-market-v2-authority-upgrade-rehearsal.py`，由 `tools/ai-postgres-rehearsal.py --business-market-v2-authority-upgrade --upgrade-only` 调用。脚本与目标交主任务串行执行，纯测试或静态编译不等于数据库验收。

正式采纳仍应在后续切片新增经独立核验的受保护记录：

1. **费率采纳表**：唯一 `(provider_id, model_id, model_version, rate_source_version)`；原币输入/输出 nano 单价、汇率有理数和来源、生效/失效时间、严格收费类别列表、完整材料摘要、当前 `ai_models` 版本与 0065 tariff 摘要同时绑定。未知工具费、缓存 token 费、额外服务费或非 CNY 原币缺汇率材料一律拒绝。任何更价新建版本，不覆盖旧行。来源和汇率证据由独立受保护核验者确认，不能只信调用方提交的 `sourceDigest`。
2. **单报告人工批准表**：唯一 `(execution_report_id, plan_id, cost_ledger_id, rate_adoption_id)` 与独立批准版本；锁定当前管理员邮箱/版本、完整报告根、模型版本、费率版本、CNY 分上限及批准时刻/失效时间。批准须来自本人明确操作或已批准的等价受保护流程；0065 的 `approvedCapClaimCents`、`approvalDigest` 只是申报。批准金额至少覆盖五 Agent 最坏需求，且不超过明确批准值。费率、模型、报告根、账号版本变更后旧批准不可用于新预留。
3. **撤销事件表**：费率或批准以追加事件撤销，既有记录保留；锁定根行后检查撤销事件再进行每轮预留。撤销与并发预留由同一数据库行锁序列化，不能靠进程缓存判断。

采纳与读回执函数分别授予费率核验、人审批准、预留读取三个独立 NOLOGIN 角色；角色无成员、无继承、无表直接 DML，`SECURITY DEFINER` 固定搜索路径并核 `session_user`/owner/角色属性。迁移不创建任何费率、汇率、批准或授权种子，也不提供可由普通 AI reader/writer 调用的签名/自批准函数。受保护调用者和真实来源仍未配置时，空表和受限路径保持付费门禁关闭。未来即使采纳记录存在，也必须先接每次 provider 派发事务里的原子预留、未知结果不重试与真实收费核验，才能考虑将门禁改成正向许可。

隔离 PostgreSQL 目标须覆盖：空表/角色 ACL、当前 `ai_models` 与 0065 版本双绑定、汇率复算、未知收费类别、过期材料、人审账号变更、费率/上限撤销与预留并发、跨报告混用、直接表写入、备份恢复和旧 0065/文件字节/旧函数权限冻结。真实价格和用户批准缺失时只用合成材料；合成测试不能宣称正式权威已采纳。
