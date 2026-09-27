# renderer 10 证明与发布两步编排候选

`business_promotion_budget_v10_publish_step.attest_and_publish` 默认关闭，只使用外部注入的已认证 `autocommit` attestor 连接。它不创建连接、不读取凭据、不切换角色、不接公开路由或自动计划。调用时重新执行当前拥有方 `business_promotion_budget_v10_preflight.prepare` 的全量暂存/批准内容复核；若上游已提供预检正文，必须与此次新结果逐字段相同。任一本地 DTO 均不自行授权发布，0057/0058 数据库函数会再复核当前报告、预算、文件和角色。

编排用规范预检正文调用 **一次** 0057 `ai_budget_v10_attest_staged`，要求返回与 run/attempt 绑定的精确 attestation ID；然后用版本化 `promotion_budget_v10_publish_request.request_digest` 构造原请求摘要，调用 **一次** 0058 `ai_budget_v10_publish`。已知发布成功后，独立读取 **一次** 0058 `ai_budget_v10_publish_outcome`，逐项比对 run、attempt、expectedVersion+1、requestDigest、attestationId 与完整清单文件 SHA。

0057 写入后驱动异常或返回异常值时，只返回 `unknown_attestation_result`。当前 attestor 没有 0057 的窄结果读取函数，不查询侧表，也不猜测是否提交，更不重发 0057。0058 写入后驱动异常时返回 `unknown_publish_result`，其中保留原始请求摘要和精确绑定；只有调用者显式使用独立 `recover_publish_outcome` 才会读取 0058 OUTCOME，且 `not_committed` 也**不会**自动重试发布。OUTCOME 读取异常或不匹配保留未知状态。

此编排返回的 `authorityVerified=false`、`readyAuthorized=false` 是对应用层权限的保守声明，即使 0058 数据库发布回执为 committed 也不能省略当前账号、下载门禁、完整文件摘要及业务来源的独立复核。它未实现真实受保护凭据、角色启用、原生 Excel 验收或生产采用；纯假驱动测试只证明单次调用和未知结果的控制流程。
