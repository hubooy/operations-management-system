# v4 京东推广完整来源只读重放候选

`ai_assistant.business_v4_promotion_replay.inspect` 只接受当前账号拥有、已完成分页但父任务仍为 `collecting/manual` 的精确京东 `netshop / promotion` 来源。它按固定 run 计划重建 `current`、`previous` 或 `yearAgo` 的唯一同店来源，按各窗口真实比较期间核验行日期与缺日覆盖；窗口之间不能互换 sourceRef、页链或请求审计。它是进程内部只读检查，不封存父任务，不注册路由、Agent、模型或文件版本，也不因三个窗口各自完成就推断整个目录已完成。

重放先核对固定容量计划、父目录、来源身份和真实账号，并在解析检查点 JSON **之前**限制其 UTF-8 字节不超过 32,768。随后分别顺序迭代事实块和成功工具收据；每块须与同序收据及审计严格一对一。中央 `business_collection` 审计仅保存执行时完整规范参数的 SHA，不保存原始 cursor；重放从固定查询和上一真实页的签名 `nextCursor`、sourceRef/revision 与末行 ID 重建每次首取/续读参数，逐页对比审计摘要。推广行、身份维度与指标须严格属于已有 `promotion_views` 固定字段，空来源仅接受原生六项控制指标，额外指标不得进入 `PageReconciler` 的累积状态。规范 JSON、原字节摘要、实际来源修订、源页过滤器和日期、请求 cursor 链、控制总额、源行数与覆盖日期都会从第 1 页重新计算，末页再与已持久化的完成检查点和父子计数比对。任何缺页、额外页、缺收据、错误工具名、跨源审计、缺日伪覆盖或末尾账号撤权均拒绝整份证明。采用有界迭代、十分钟重放期限、38,000 字节证明上限，以及当前 v4 单源 16,384 页/2 GiB、整任务 65,536 页/8 GiB 上限；不会把旧 v3 读取桥的 64 页/8 MiB HMAC 句柄套用于 v4。

结果只给指定窗口的完整来源证明及本次内部工具审计链摘要，明确 `fullSourceReplayVerified=true`、`internalToolAuditBound=true`、`payloadCursorChainVerified=true`、`requestCursorAuditVerified=true`，同时保持 `upstreamSignatureVerified=false`、`sealed=false`、`persistentEvidenceVerified=false`、`reportGenerationSupported=false`。旧 v4 物理行若仅有截断/空参数审计，不会被追认为已证明请求 cursor。内部 HMAC 工具调用和审计只能证明系统边界内的那次响应与不可变块绑定，**不是京东上游独立数字签名**。后续仍需整目录封存、跨域快照口径和正式报告门禁，不能由三个独立推广来源的结果推断完成。
