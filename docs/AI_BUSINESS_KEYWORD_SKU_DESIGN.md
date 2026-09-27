# 关键词 × 明确推广 SKU：第 55 批候选纯合同

2026-09-18。只实现可验证的京东推广联合聚合，不修改旧八维分析、计划/单元视图、来源 JSON、工具目录或既有报告口径。尚未接入封存业务服务、Agent、工作台、筛查规则、引用解析与文件交付，也未做本批 PostgreSQL 验收。纯结果始终 `authorityVerified=false`。

## 真实字段与已知限制

`lib/netshop/normalized-import.ts:prepareNormalizedNetshopImport` 保留原始行 `raw`；京东 `metricsFromRow` 保存源指标名，金额单位转换由后端 `netshop.import_service._row_projection` 完成。`netshop.analysis._project` 的明确角色来自原始行精确表头，而非顶层 `skuId`：

| 规范字段 | 原表头，按既有优先顺序 | 本批用途 |
|---|---|---|
| keyword | 关键词 | 联合身份 |
| promotedSkuId | 智能投放推广SKU ID、智能投放推广SKU、推广SKU | 联合身份 |
| planId | 计划ID、计划id | 上下文身份 |
| unitId | 单元ID、单元id | 上下文身份 |
| matchType | 匹配类型、匹配方式 | 上下文身份 |
| searchTerm | 搜索词、用户搜索词 | 不替代关键词 |
| triggerSkuId | 触发SKU ID、触发SKU | 不替代推广 SKU |
| attributedSkuId | 跟单SKU ID、跟单SKU | 不替代推广 SKU |

既有导入器的通用 `skuId` 使用表头正则首匹配；当触发或跟单 SKU 列排在前面时，会取到该角色。新模块完全不依赖这个值，亦不修旧口径。实际 TS 导入器的合成 CSV 已复现该限制，并接实际后端金额投影与实际读取投影函数，证明明确 `promotedSkuId` 仍保持正确角色。该检查不是数据库落库/封存验收。

既有投影选择第一个非空的同角色别名；若同一行同时提供多个互相冲突的推广 SKU 别名，本层已经看不到全部原表头，因此无法认证别名一致性。也不证明 SKU 主数据归属或平台身份有效。纯结果保留该限制，不根据商品名、搜索词、商家编码、顶层 SKU 或 ERP 映射猜测推广对象。天猫当前规范源没有同等明确的推广 SKU 角色，本批不支持。

## 新纯 API

`business_analysis.promotion_keyword_sku.table(source, pages, expected, *, view='keyword_sku', baseline_source=None, baseline_pages=None, baseline_expected=None, limits=None, checkpoint=None)` 是临时 context manager：

- `keyword_sku`：精确平台、店铺、关键词、明确推广 SKU 聚合；同一个词商品的计划/单元/匹配方式合并。
- `keyword_sku_context`：在上述身份之外保留计划 ID、单元 ID、匹配方式；用于区分不同投放上下文。
- 输入保持既有完整来源结构与 `PageReconciler` 封存期望结构；两侧迭代器都必须读到 EOF，页摘要、源摘要、金额/缺失数量、源级日期覆盖及分区结果核对完成后才 `yield`。
- `header()`、`page(offset=0, limit=20)`、`scan()`、`read_row(row_index, row_id)` 只在 context 内有效。页包含完整元信息、实际完整行前缀、`nextOffset` 与 `pageDigest`；全表迭代采用单次有序 SQLite 扫描，不逐页重建来源。
- 新 schema 为 `business-promotion-keyword-sku-table-v1`，算法为 `promotion-keyword-promoted-sku-v1`。表摘要绑定算法/视图/双方精确来源与查询/源核对证明/元信息；行 ID 绑定表摘要与联合实体，不能跨视图、来源或比较窗口复用。

## 业务口径

保留消耗、展示、点击、平台订单行、平台归因金额、加购数，以及存在时的直接/间接/新客归因金额；从聚合分子分母重算 CTR、CPC、ROAS 等，不能平均行比率。缺金额保留 `null` 和 `presentRows/missingRows`；缺侧保留 `metrics=null`，不伪造零成交。

缺关键词、明确推广 SKU，或上下文视图必需的计划/单元/匹配方式时，金额仍完整进入缺身份桶，`identityQualified=false` 并列出缺失字段。该桶不是一个真实商品，不允许据此形成具体商品调整动作，也不计算其跨期变化。两种视图来自同一批事实，不得再次相加。

本期比较只允许同店、同数据集、同原始起止日期的 `current → previous/yearAgo`，不得跨店或自动换基期。双方源日期覆盖齐全、身份齐全、该指标两侧均存在且无缺失时才比较。零/负基期保留准确差额，不产出无定义增长率。源级每天有记录不表示每一个词商品每天都有记录；同比闰日区间可能不同天数，按原区间总量比较，不按日归一。归因金额不能解释成 ERP 净销售、利润、增量收益或因果效果。

## 容量与取消

两侧合计最多 2,000 页、200,000 源行；并集最多 250,000 组；SQLite 主分区限 256 MiB，缓存 2 MiB，临时排序使用文件。该限制继承现有分区实现，不宣称 SQLite 辅助排序文件或进程总 RSS 都被 256 MiB 严格覆盖。调用者仅可降低已知额度，布尔、浮点、未知键或超原上限均拒绝。

每页最多 20 个完整结果行且 UTF-8 不超过 38,000 字节；超大单行/元信息失败，不拆字段、不丢行。全量扫描不限于第一页/Top N。协作式 `Checkpoint` 贯穿来源页边界、分区聚合/SQL 进度、结果读取和正常退出；SQLite 中断恢复原异常，即使调用者吞掉读取取消，最终退出仍失败。关闭悬挂 scan 游标后才移除临时目录。

## 下一片：真实封存 owning 适配

新增独立 `ai_assistant/business_promotion_keyword_sku.py` 与真实 PG 测试，保留旧 `business_promotion_views.py` 不变。最小接口可沿用：

1. `table(report_id, source_key, view, principal, *, baseline_key=None, checkpoint=None)`：复用 `business_diagnostic_screening._load/_revalidate` 的真实报告/工作流/owner/scope/封存绑定；从完整可信 Reader 目录取 source 与 `info.expected`，不接收客户端来源证明。调用本纯 `table`，前后均复验实际身份及快照。
2. `page(...)` 和 `read_row(..., row_index, row_id)`：先完成上述 context，再返回；额外 owning 封套也要计入 38,000 UTF-8 字节、按完整前缀分页，不修改纯 schema。只表示该来源遍历已核验，不提升为整份报告/全维覆盖。
3. 真实 JD 规范导入合成夹具至少同时含关键词、明确推广 SKU、不同触发/跟单 SKU、计划/单元/匹配方式，经实际导入→拥有者读取→采集→封存后计算；覆盖缺身份、实际分单位、两期、多页尾部错误、跨 owner/撤权/context 退出复权及取消。旧 PG 计划/单元夹具未包含关键词/明确推广 SKU，不能直接据其成功声称联合身份已验证。
4. 后续才设计固定报告 profile/引用选择器、筛查与全表导出；需明确枚举全部请求来源/视图/窗口，重新容量准入和覆盖证明。不得仅加一个工具就称原五 Agent 已阅读或导出这些新维度。

## 验证范围

本批新 Python 15 项纯测试与旧推广 20 项共同回归；另 3 项 Node 测试实际执行 TS 规范化导入器，再执行从真实后端源码提取的纯金额投影/读取投影函数，覆盖 6 个合成 CSV 场景。仅投影函数通过 AST 隔离 Django 导入，TS 导入器本身没有 mock；未模拟数据库成功回执。候选证据见 `docs/evidence/ai-business-keyword-sku-candidate.json`，不代表生产已采用。
