# v11 受限签发读取桥与后继 0076 协议（隔离候选）

本候选不增加迁移号、不修改 0067/0068/0070/0073 SQL，也不开放 renderer 11 的 `ready` 或下载。`backend/business_analysis/promotion_budget_limited_signer_v11.py` 的正式入口默认抛 `SignerBlocked`；测试显式启用后，仍只接受精确隔离库、真实非超级 `sign_login` 会话与独立 `ai_reader` 会话。密钥提供者默认 `NotConfigured`，测试仅注入随机合成 32 字节密钥。

`sign_once` 先核固定 key 配置及独立角色，然后只调用一次 0070 `ai_budget_v11_read_proof_ticket_v2(ticketId,runId,attempt,attestationSha256)`。它严格检查返回的 claim、报告、owner、版本、绑定、0067 证明原文与 SHA。`BoundedRunReader` 在另一非超级只读会话中，前后重读同一个 run/attempt/version/report/owner/binding；中间调用已有 `business_promotion_budget_v11_preflight.prepare`，该过程实际重建完整 HTML/gzip/NDJSON、XLSX OPC/公式与来源根，且有 600 秒上限。任何领取响应丢失、读取或文件漂移都返回 `retryAllowed=false` 的未知状态，不盲目再次领取。返回的测试回执固定 `ticketBoundInMac=false`、`releaseAllowed=false`、`readyAuthorized=false`。

当前 PG 夹具仅在**同一个隔离测试进程**临时切换 Django 连接到真实非超级 `teruisi_ai_reader`，并将 Django 进程角色设为 `ai_reader`/只读；签发连接是另一真实非超级 `sign_login` 连接。它可验证权限和全字节路径，但不能声称已实现生产独立 reader 服务进程或凭据隔离；候选结果固定 `readerProcessIsolated=false`。正式设计须另起独立受保护 reader 进程并在其启动/连接回读上述身份和列级投影。

当前 SQL 有两个不能靠 Python 包装弥补的阻断。0070 的一次性票据只读取 **0067 旧证明表**；0073 的新 LOGIN 侧车写入另一张证明表，不能把两者冒充同一个证明。0068 v1 受保护回执正文没有 `ticketId`/`claimId`，0070 v2 验签只改了调用角色，无法在数据库中证明 MAC 与精确一次性 claim 同源。0073 本身对任意 run 的 `ATTEST/OUTCOME` 可执行权也缺独立单报告许可，所以其角色在正式目录仍必须 NOLOGIN。上述任一缺口未封闭前，不能把测试 MAC 当成发布权威。

0070 票据在领取时检查有效期，但没有供 signer 在最长 600 秒全字节复核完成后再次核领取/过期/撤销的窄结果函数。测试 MAC 可能在领取后许可到期时才产生；候选仍拒发布。后继协议必须在签发完成和发布事务内分别核同一个不可变 claim 与许可状态，不能仅依赖领取时检查。

后继版本化 SQL **预留 0076，待 0075 落地后重新审查编号、前驱和目录**，至少应具备：

1. 独立管理员签发的短期单报告工作许可，精确绑定 `ownerEmail/shopId/reportId/runId/attempt/runVersion/actorVersion/bindingDigest/attestationSha256`、许可目的、过期时间及批准来源摘要；一份许可只能被同角色领取一次。令新 attestor 只对获许可 run 执行，不激活 0073 的全局 run 凭据。若不允许替换 0073 旧函数/guard 的正文与目录，应使用新版本证明表、角色、函数，不在旧 0073 目录上偷换定义。
2. 新签发领取函数只返回该许可的完整 0073 后继证明及精确只读来源材料，领取行记录不可变 `ticketId/claimId/run/attempt/version/owner/报告根/证明SHA/claimedAt`；失去响应时按同 ticket/claim 查询明确结果，`absent_observed` 也不自动重试。
3. `protected-verifier-receipt-v2` 的规范正文和 MAC 目的域必须包含 `ticketId`、`claimId`、领取行规范摘要、证明 ID/SHA、当前完整 HTML/XLSX 每卷字节 SHA、manifest、批准/人审/预算及报告根、选用 keyId、验证器版本和一次性发布意图。签发器亲自两遍复核这些字节，不接受调用方给定的过程摘要。新验签函数锁定当前 run、领取行与活动 key，并比较上述字段；拒绝 v1 正文、跨报告重放、过期/撤销、错 owner/版本与改变中的字节。
4. 原子发布另用后继版本函数：在同一事务内锁当前 report/run/证明/领取/活动 key，核未发布状态与精确请求摘要，CAS `staged_unpublished→ready` 并写不可变发布回执；`OUTCOME` 只按原请求摘要查 committed/conflict/absent，未知不盲重发。普通 writer、旧 0067/0073 attestor 和 Web 身份仍不能直改 ready。最后另建版本化窄下载栅栏。

验收先用纯合同与真实非超级登录隔离 PG：窄领取、拥有方两遍全字节校验、错 ticket/run/shop/owner/attempt/version、文件或来源根在校验中变化、过期/重放/丢响应、key 撤销、跨报告 MAC 和权限漂移；升级前后冻结旧函数 OID/正文/ACL/owner、renderer 1–10 文件与 v11 旧证明，前后独立备份恢复及空逆迁移重装。当前文件只验证读取桥，不是 0076 的实现或上线许可。正式接线还需要独立服务身份/凭据、生产密钥托管与轮换、可跨机恢复的加密备份、管理员授权及真实业务验收。

2026-09-26 隔离 PG 定向尝试五轮**未通过**：首轮真实 signer 地址文本为 PostgreSQL 的 `127.0.0.1/32`，固定精确身份判定已修；后续真实非超级 signer 均只领取一次 0070 票据，之后返回 `unknown_after_ticket_claim`，不生成回执、不重领。第四轮固定码为 `owning_reader_acl_denied`，隔离 PostgreSQL 日志定位到 `ai_models`：测试夹具虽切换数据库 LOGIN 到 `ai_reader`，Django 进程角色仍是 `development`，`configuration.resolve_model` 因而走整行 ORM SELECT，包含密钥列并正确被列级 ACL 拒绝。第五轮夹具已设 `ai_reader`/只读并核 `MODEL_RUNTIME_READER_COLUMNS` 仅非密投影，模型读取通过；完整字节路径下一步在 `business_promotion_read_receipts._providers` 对 `ai_agent_provider_dispatches` 按 job 做直接 ORM SELECT，再被既有 ACL 正确拒绝（SQLSTATE 42501）。`database_contract.READ_TABLES` 刻意不含该派发表，其关联 provider/tool 结果也可能含模型/工具原文，不能通过授整表 SELECT 修补。第五轮 `.runtime/ai-pg-9af016e6fe83` 与前四轮源均由 runner 停机；五轮不构成完整 HTML/XLSX 签发通过。

要继续全字节签发，后继 0076 应提供**同工作许可/同报告/同 job 的版本化窄读取通道**：由受保护 SQL 内部核票据与当前 run/attempt/owner/报告根，再有界返回 `business_promotion_read_receipts._providers/_ledger_fence` 所需 provider dispatch/result 与 tool dispatch/result 字段及原文摘要；每页固定序号/长度和请求摘要，读取前后复核不可变 job 与来源状态，禁止跨 job 枚举和原表/列 SELECT。签发器必须用该通道重放五 Agent 已读、工具结果和批准内容；不能只给 `ai_agent_provider_dispatches` 加列级授权，因为该重放还涉及同链其他封闭表和潜在响应正文。0076 前该 Python 桥保持默认关闭、真实 role 测试只期望精确安全拒绝、单次 claim、密钥表空且无 MAC/发布。

随后唯一隔离 PostgreSQL 55787 的**预期安全拒绝**目标 1/1 通过：真实非超级 `sign_login` 与 `ai_reader` 两连接，`ai_models` 非密列投影通过，四张派发/结果账本对 reader 整表 SELECT 均为 false；0070 只产生一张 ticket 和一张 claim，受限重建在 `ai_agent_provider_dispatches` ACL 前停止，固定码 `owning_reader_acl_denied`、无 MAC、私钥表 0 行、run 仍 paused、v11 HTML 下载拒绝。此通过仅证明失败关闭，不证明全字节签发。runner 退出后 `pg_ctl status` 为 no server running、55787 无监听、测试库已销毁。无凭据日志与状态摘要归档于 `E:\codex-artifacts\ai-business-trial-acceptance-20260925\archived-pg\ai-pg-5a3037141150-v11-limited-signer-denial`；`tests.log` SHA256 `E39DD15E96734FD6350DFECD1BFB0E4523480289C77527864F512DF74D606688`，`postgres.log` SHA256 `A3C35444C63C870F64060084E00EE8D213A297B104E8F477BB5494079A97C65E`，`evidence.json` SHA256 `874DEA73DA038E2DC525316A88CAE959DB1C9411277954B76DB85A39D3455510`。

0076 细化候选（函数名是拟定协议，不是已安装 SQL）：

| 新窄函数 | 输入及界限 | 返回及复核 |
| --- | --- | --- |
| `ai_budget_v11_claim_sign_work_v3(ticket uuid,run text,attempt int,attestation_sha text)` | 仅签发角色精确 `session_user`；票据来自独立批准者，锁同 run/attempt/version/report/owner/binding/证明、未过期且未领；唯一 `ticket_id` 插入 claim | `claimId`、固定工作许可摘要、证明 SHA、当前报告/人审/预算根；无任意 run 搜索 |
| `ai_budget_v11_read_agent_ledger_v3(claim uuid,run text,job text,kind text,ordinal int,expected_root text)` | 每次验证 claim、当前身份/批准/报告根；job 必属同报告 workflow 的五固定 Agent；`kind` 仅 provider/tool，provider 最多20、tool最多40，精确 ordinal；各 JSON 原文分别 ≤256 KiB、参数 ≤8 KiB | 仅返回该 job 的必要投影及成功结果原文、独立存储摘要；SQL 内核原始SHA/序号/所属 provider，返回同一次账本根和请求摘要，不带 provider credential 或其他 job 行 |
| `ai_budget_v11_sign_work_outcome_v3(ticket uuid,claim uuid,request_sha text)` | 只查原票据/原领取/原请求，无另一 run 的存在性查询 | `committed/conflict/absent_observed`，全部 `retryAllowed=false`；响应丢失不重新领取 |
| `ai_budget_v11_verify_protected_receipt_v3(run text,attempt int,receipt_text text,mac text)` | 独立 publisher 身份，锁 claim/票据、当前 run/报告/证明与活动 key；拒 v1 正文及已过期/撤销/错目的；key 行须在发布事务内保持锁 | 只返回布尔/固定证明 ID，不返回 key、任意正文签名能力或受保护表行 |

四账本投影须覆盖当前 `business_promotion_read_receipts.py` 对 `ai_agent_provider_dispatches`、`ai_agent_provider_results`、`ai_agent_tool_dispatches`、`ai_agent_tool_results` 的实际字段：provider 的 `dispatch_ordinal/job_id/owner_email/actor_role/model_id/model_version/tool_policy_digest/request_digest/lease_epoch/state` 与 `response_json/response_digest`；tool 的 `tool_call_ordinal/provider_dispatch_id/provider_call_id/tool_name/arguments_json/arguments_digest/invocation_id/lease_epoch/state` 与 `result_json/result_digest`。还须返回有限的 `id` 和版本/时间以维持归属；不得暴露表级 `SELECT` 或跨 job 列举。签发前、逐页及签发后重算同一 job/报告根；任何中途变化使本次 claim 结果 `unknown/conflict`，不能换票据自动再签。

回执 v2 的规范正文必须把 `ticketId`、`claimId`、工作许可摘要、四账本页链根、0073 后继证明 ID/SHA、报告/owner/shop/run/attempt/version/binding、完整 HTML/XLSX 每卷字节摘要、批准内容/人审/预算根、`keyId` 和验证器版本纳入 **新目的域** MAC。数据库验签必须用同一 claim 行独立重算字段，避免 Python 返回中附带 ticketId 但 MAC 不含它的当前断链。0068/0070/0073 已安装函数的源码、OID、ACL 保留，0076 新建函数/表/角色及独立目录检查；旧角色保持 NOLOGIN，除精确新函数 EXECUTE 外不扩权。与 0075 的真实前驱和受保护清单在落迁移前重新冻结。
