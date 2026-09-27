# v4 京东网店推广单页采集（内部候选）

`business_v4_netshop_promotion.advance` 只接受已建立的 v4 `netshop / promotion / 京东` 精确来源，其窗口可以是同一固定 run 中明确请求的 `current`、`previous` 或 `yearAgo`。纯三窗口选择只校验计划内同店、原始日期、窗口与唯一来源，不能代替拥有方权威。首次页走现有签名 `get_business_source_page`；后续只从该窗口已存检查点和**最后一个**不可变事实块推导原签名 cursor、完整来源修订及最后行 ID，调用现有 `get_business_netshop_continuation_page`。拥有方在原游标真正过期时会验签并在修订不变的前提下安全续读；不同窗口的 sourceRef、页链、审计与检查点彼此独立。AI 不解码、不伪造游标，也不把旧修订或其他店铺混入 run。调用方不能提供页面 JSON。

工具页在锁外通过完整过滤器／比较期间、平台店铺、来源类型、sourceRef/revision、行序、页 SHA 与来源控制汇总核验，并回查本次请求窗口内唯一成功工具审计及**执行时完整规范参数 SHA**。中央审计只存 SHA，不落原始签名 cursor；其他工具 surface 仍按既有脱敏摘要格式。锁内复验当前无范围管理员及父任务/来源版本和检查点，按“chunk→audit receipt→来源 checkpoint→父计数”一次事务提交。每步只读固定计划、当前来源状态和最后一块，不重放此前所有页；1,000 页合成检查点测试断言只发起一次按 `(run,source,sequence)` 的末块查询。单来源 16,384 页/2 GiB、整 run 65,536 页/8 GiB 的硬限制在应用与现有 0035 物理门禁同时执行，超限原检查点保持不变。

本片没有公开接口、调度器、其他店铺/平台采集、封存、Agent、模型或报告/文件调用。成功的一页只证明该次拥有方签名工具、类型/行链与物理 CAS；即使来源 `finished=true`，也只是该分页控制汇总到终页的**物理状态**，并非整个 v4 目录的来源权威或业务覆盖结论。完整目录尚未做独立全量重放和 attestation，返回值始终 `sourceAuthorityVerified=false`、`persistentEvidenceVerified=false`。下一阶段必须验证长时间/跨进程恢复、财报与其他日域的独立采集以及完整来源扫描，再讨论封存和正式交付。
