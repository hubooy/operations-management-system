# v4 最终封印调用层候选

`business_analysis.v4_final_commit_step` 是默认关闭的纯编排层。调用者必须注入已经完成 sealer 身份认证与当期 authority fencing 的 `autocommit` 连接、已领取票据的 nonce/claim 与发行时 `requestDigest`、规范父封印正文及派生父封印密钥。模块不创建连接、不取主密钥、不签发票据、不切换角色、不启动模型，也不读写生产。只有 `enabled=True` 才执行。

调用层先用 0042 `ai_v4_sealer_ticket_context` 核对当前 claim、父任务的 collecting/manual 前置状态、父版本、计划与目录摘要、keyId 和无既有封印；用派生密钥验证正文真实 HMAC，并用 `commit-seal-v1` 纯合同重建与发行时一致的请求摘要。提交前再次读取 0042 上下文，随后**仅调用一次** 0052 `ai_v4_sealer_commit_with_consumption`。0052 在数据库中独立复核真实票据摘要、来源根、连续候选回执、来源修订，并原子写入封印与消费。

调用成功返回后，调用层再用 0043 `ai_v4_sealer_consumption_result` 做精确读取并比对 run、attempt、版本、正文摘要和消费时间。写调用发生驱动异常时返回 `unknown_commit_result`，绝不盲目重试；读取异常返回 `unknown_readback_result`。调用方只能显式使用独立的 `recover_claimed_seal` 查询 0043 的精确消费结果。上述返回均为消费账本状态，`authorityVerified=false`，不能直接触发报告交付；正式授权仍须既有 `verify_seal` 全链验证。

0042 上下文不暴露票据 `requestDigest` 或独立来源根。前者必须由可信票据发行流程原样交给调用者，0052 会对真实票据再核；后者由 0052 数据库函数独立复核，调用层不从本地候选推断。纯 fake-driver 13 项测试及隔离 PostgreSQL 两项实际角色模拟 `.runtime/ai-pg-e2bcd735e87a/tests.log` 通过：真实派生父 HMAC 下单次消费、独立 `verify_seal`、原票据结果恢复，以及错误请求摘要在提交前拒绝。角色仍 `NOLOGIN`，没有真实 sealer 登录/受保护凭据、生产迁移、付费模型或原生文件验收。
