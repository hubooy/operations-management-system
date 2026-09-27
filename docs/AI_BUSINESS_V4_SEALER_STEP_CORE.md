# v4 单段 sealer 候选编排（默认关闭）

`backend/business_analysis/v4_sealer_step_core.py` 只处理一个已选来源的一个连续段，最多读取 16 页。调用方提供已认证、`autocommit=True` 的受限 sealer 连接、已派生的 32 字节 0036 MAC 验证密钥，以及已经领用的 ticket 参数。模块不建立连接、不读取凭据、不发行票据、不激活 NOLOGIN 角色，也不调用 seal。`enabled` 默认为 `False`。

顺序固定为：0042 claim context → 0049 同 claim 的 source → 0047/48 当前段精确回执 →（后续段）前段精确回执 → 0042 segment → 最多 16 个 0042 page → 再次 0042 context → 0036 HMAC 与推广/财务纯重放 → 0047/48 写回 → 同 claim 精确回读。0049 的查询原文/摘要、来源序号/数量、身份和计划中相同 ordinal 的 sourcePlans 项互核。0049 的 sourceRoot 由 0042/0049 共同的 claim 绑定；0042 context 没有单独的 sourceRoot 列，根的实际数据库复算发生在 `ai_v4_sealer_assert_claim` 中，因此不能宣称这里有第二份独立来源根。未封存 context 的 seal body 为空，不拿它证明封存。

前段候选必须由当前 claim 的受保护回执函数读回，再验证摘要、同 run/attempt/source/root/key 和连续段身份，供纯重放回调使用。若当前段已有回执，返回 `existing_candidate`，不声称本次重新完成页重放或 MAC 验证。写入异常返回 `unknown_write_result`，不自动重试；写入成功则要求精确原文、摘要和时间戳回读一致。所有状态固定 `candidateOnly=true`、`authorityVerified=false`。这仍不是正式 seal、上游签名验证或生产准入。

纯 fake-driver 测试覆盖推广、财务、调用顺序、已存候选、错身份/计划/来源摘要、缺页、错误 HMAC、缺前段回执、写入未知和写后不一致。另在隔离 PostgreSQL 中以已领取 claim 和受限角色完成了推广、财务各一段候选的写入/回读、重复请求幂等、错误派生密钥拒绝；0047 读取回执曾因 `varchar(64)` 与声明的 `text` 不一致报错，现由 0050 显式转换修复。四项针对性测试通过，见 `.runtime/ai-pg-a1a7fdb52846/tests.log`。测试使用合成来源和隔离角色配置，不等于受保护登录、跨票据长任务或生产授权验收。
