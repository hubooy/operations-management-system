# ERP逐事实归属候选

`business_analysis.erp_fact_assignment.assign_facts` 是下一版跨来源 KPI 的**纯临时账本**。调用者必须使用同一个已封存来源目录中的 `mappingPlan`、准确 `pairKey`、ERP 销售页和本期主数据页，并传入各自完整的 Reader 核对结果。账本先逐页核验店铺、比较窗口、来源 revision、页链、业务日期覆盖与主数据快照，再在私有 SQLite 中按源行保留候选；只有全部源页、金额总额、退款符号和输出行容量通过后才开放 `scan()`。调用者离开 context 后不能继续读取。

每条 ERP 源行保存原 `sourceRowId`、源摘要、业务日、线上规格码、产品编码、ERP 类目，以及销售的全部十一项有符号指标。唯一匹配只按**非空线上规格码 `onlineSpecCode` 精确等于当前主数据 `merchantCode`**：同一编码的所有候选都具有非空 SKU、SPU 与平台类目且只形成一个完整身份时才 `matched`；重复主数据源行仍完整列出。跨 SKU（包括同 SPU 多 SKU）为 `ambiguous`，缺 SKU/SPU/类目为 `incomplete`，无候选为 `unmatched`。后三种状态均不分配 SKU/SPU/平台类目，原金额进入 `unassignedTotals`，不会复制到每个候选。`productCode` 仅保留供排查，绝不隐式回退匹配。

ERP `netSalesCents` 保留退款负额，`refundCents` 为该负额的绝对值；`costCents` 可为零或负，`grossProfitCents=netSalesCents-costCents`。这些是源销售毛利而非 SKU 净利润或成本质量认证。账本不读取或加总网店支付、推广归因、B 端和月财报。当前主数据不能证明历史所有权，返回 `authorityVerified=false`、`historicalOwnershipVerified=false`、`netshopAdFinanceCombined=false`。

临时磁盘最多 256 MiB，输入合计最多 64 MiB、2,000 页/每页最多 100 行，ERP 事实最多 200,000 行，单条含**全部**主数据候选的输出最多 38,000 UTF-8 字节。读取同一编码候选前先在临时库计数和计算规范 JSON 的 UTF-8 字节数：最多 256 条、30,000 字节；超过任一上限则在装载候选前整次失败，不截断或将大集合载入内存。最终完整 ERP 行仍受 38,000 字节门禁。现阶段没有 owning DB 服务、Agent 读取回执、跨来源 KPI 数值或正式 HTML/XLSX；下一步须由当前封存 Reader 提供真实页，并把本账本与各来源控制总额及待分配池逐项对账。
