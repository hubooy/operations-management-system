# ERP 逐事实回卷的封存报告入口

`ai_assistant.business_erp_rollup_materials.prepare` 是内部、未注册的候选入口。它只接受 `business-agent-integrated-reference-v1` 固定报告及其已封存 v2 证据，调用方必须同时传入报告 ID、固定 `mappingPlan` 中的 `pairKey`、ERP `salesKey` 与本期商品主数据 `masterKey`。服务重新从数据库验证当前账号、报告/工作流固定引用、证据版本、来源目录和 mapping pair，不按相似店名或商品编码推断其他来源。

入口使用同一封存 Reader 将两类所选来源页完整重放，核对页链、来源身份、查询与比较窗口、ERP 控制总额和当前主数据快照。逐事实账本先保留全部候选与未分配池，再一次扫描生成店铺/日、平台品类/日、SPU/日、SKU/日及未分配原因/日五张类型表。材料清单绑定报告、账号、证据与关联计划摘要，附两类来源校验、账本和五表摘要；每张表的完整 NDJSON 有明确行数、页数、字节数及 SHA-256。返回前及临时材料读取结束时重新验证当前账号、报告和封存版本。

材料仅在 `prepare` 生命周期内可读取，容量超限、来源变更、账号撤权或逐层金额不守恒时失败，不发布部分材料。`authorityVerified=false`、`registeredRenderer=false`；本入口没有新增 Agent 工具、公开路由或正式 HTML/XLSX 文件任务。数值仍为 ERP 原始业务日销售与源成本，不合并网店访客/支付、推广、市场样本、B 端或月财报，也不证明当前主数据在历史销售日的归属。
