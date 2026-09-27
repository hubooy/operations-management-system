# 市场五 Agent 付费模型费用上界候选

现有 `AiModels` 可限制 token、轮次和工具次数，但没有可核的价格与汇率版本，不能仅凭这些字段宣称“付费预算已受控”。`backend/business_analysis/market_model_cost_envelope.py` 增加**未注册纯合同**：必须给出管理员另行核实的 CNY 输入/输出纳元费率快照、有效期、模型版本、价格来源摘要和人工批准上限；按五个固定角色的每轮输入/输出 token 最大值逐次向上取整，再求整任务最坏费用。未知价、过期、错模型、收费工具未计价或最坏费用超批准上限一律拒绝。观察实际 token 时也只按原预留边界复算，不释放或扣减资金。

这个合同**不含任何真实价格**，费率/汇率摘要不是权威签名。输出固定 `tariffAuthorityVerified=false`、`humanApprovalAuthorityVerified=false`、`fundsReservedInDurableLedger=false`、`providerCallsAllowed=false`；实际提供方还可能有缓存、推理、图片或工具等其他计费类别，未覆盖时不得激活。真实调用前须有受保护费率采用、CNY 汇率和计费类别核验、原子预留账、逐轮许可、成功使用量对账及未知结果不重试。此模块没有模型请求、数据库迁移、外发或生产开关。

三项纯测试覆盖五角色向上取整、低上限/未知价/过期/收费工具拒绝、超额与重复使用量、即使重算摘要也不能伪造调用授权。该候选为后续版本化市场 Agent 激活提供算术边界，不等于模型价格已核或预算已批准。
