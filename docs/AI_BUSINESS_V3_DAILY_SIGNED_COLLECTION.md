# v3 日来源内部签名单页采集候选

2026-09-24。`business_daily_collection_v3.advance_daily_source` 是显式内部函数，没有公共路由、定时领取、模型工具或报告入口。它只接受 v3 run/source ID、父期望版本、真实当前无范围限制管理员与内部请求 ID；不接受调用方页面或游标。每次从真实 v3 目录及不可变页重建来源状态，锁外通过现有中央只读工具签名取得一页，并以 `PageReconciler` 核对控制汇总、rowId递增、页SHA、同来源版本、精确筛选和分页链，之后在 AI mutation 内重新核账号与父/来源 CAS、共享额度，按 chunk→source checkpoint→parent 字节/版本同一事务落地。来源可 `finished=true`；父任务一直是 `collecting/manual`，无混合封存。

首页仅用 `get_business_source_page`。后续从真实最后一块与原检查点核对 sourceRef、完整 sourceRevision、签名游标和末行 ID，再固定调用该域已有的 netshop/sales/market continuation 工具。拥有方续读服务会先读原签名：未过期按原游标，只有真实签名过期才按受限条件临时重签；AI 不根据泛化409或调用方口述自行重试。返回页必须仍属于原来源/版本，抢先更新和权限变化都拒绝追加。来源变化时保留原检查点，不把新数据接在旧链后。

由于当前 `inspect` 每次完整重放旧块，本片对单个日来源设64页上限，且在重放前以只用于拒绝的廉价计数预检。超限保持任务未完成，不裁剪后冒充完整。当前 `persistentEvidenceVerified=false` 仍反映整个混合 v3 任务未封存及业务来源质量未实测；即使日、财务来源分别完成，也不得发布五 Agent/HTML/XLSX，必须在未来独立完整核验与父封存门禁后启用。财报与日来源不是同刻数据库快照。

隔离 PostgreSQL 测试 `ai_assistant.test_business_daily_signed_v3` 应覆盖两页续读、原游标传入所属域工具、过期路径的 owning 层独立测试、旧 revision拒绝、伪造页/错账号/错来源、签名工具审计失败、父CAS抢先及64页廉价门禁。模拟工具回执只能证明 AI append 的决策；真正跨进程签名取数、过期超过一小时、真实业务规模与费用仍需另行验收。正式服务、业务表和工作流未变。
