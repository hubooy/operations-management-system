# 正式报告市场视图选择候选合同

`backend/business_analysis/market_selector.py` 是纯函数、版本 `business-market-selector-candidate-v1`，用于未来新版正式报告固定 `marketSelector`。调用方须提供来自持久报告/封存证据的独立上下文、完整规范来源目录和人工明确选择，不能把模型声称的摘要或选择本身当作可信根。

选择必须指出一个目录中的京东市场本期来源，以及 1—20 个明确、有限、按价格下界排列且不重叠的分段，金额单位为分。可选的基期必须是相同平台、类目、POP/自营范围、SKU/SPU 榜单粒度、价格筛选和原始日期范围的 `previous` 或 `yearAgo` 来源。选择基期后仅允许明确单日对单日进出榜；多日期窗口仍可做本期价格带，但不生成进出榜视图。合同自动固定 `views`、比较日历、完整目录摘要、算法版本和 `selectorDigest`，总结构最多 16,000 UTF-8 字节，价格段最多 8,192 字节。超限拒绝，不截断或猜测基期。

`validate(sources, context, choice, claimed)` 必须使用独立可信的 `choice` 重建并与声称的整个结构比较。仅重新计算声称对象自己的哈希没有意义，尤其不能让修改后的价格边界变成新事实。候选始终保留 `authorityVerified=false`、`registered=false`、`ownProductIdentityVerified=false` 和 `wholeMarketCoverageVerified=false`；其摘要不证明报表行数、权限、来源覆盖或本店商品身份。

下游 owning 适配器仍须按固定选择读取当前封存报告来源，完整核对市场事实，最终复验账号/报告绑定，然后才能生成价格带或进出榜材料。价格区间跨段、缺值及区间外样本属于未分配，不取中点。TOP 样本缺席不等于零销量、退市或本店利润；市场榜单本身不含店铺归属。旧报告快照及其版本语义保持不变，本候选没有注册 Agent、公共路由、数据库表或文件 renderer。
