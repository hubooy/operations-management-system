# 确定性全量诊断筛查与覆盖证明设计

状态：**整体为候选设计；第 31 批已实现未公开的纯筛查第一片，尚未接入报告运行时**。2026-09-17 基于当前经营分析、集成报告与导出代码审查；本子任务未访问正式数据、运行 PostgreSQL 或调用模型。本文不改变旧报告协议、工具目录或发布权限。

## 1. 结论与现有能力边界

下一批宜先增加独立的确定性筛查器：消费明确计划内的完整、已核验分析表，计算固定规则的精确命中总数，再保留有界、稳定排序的候选。先提供内部服务与测试，随后单独接入新的报告执行协议。不能仅把分析工具的第一页改为金额排序，就宣称完成全量诊断。

当前存在真实的后页漏诊风险，但不是后端只采集了第一页事实：

| 代码位置 | 已保证的内容 | 没有保证的内容 |
| --- | --- | --- |
| `business_analysis/results.py` 的 `build_table/stream_table` | 消费来源完整分页，经 PageReconciler 与聚合守恒核验后输出；流式表还要求完整消费才能完成行数核验 | 模型阅读了所有输出行，或每行经过异常规则筛查 |
| `business_analysis/partitioned.py` 的 `page/scan` | 按 `entity COLLATE BINARY` 确定排序 | 第一页是最严重问题；其排序不是退款、费用或利润排序 |
| `business_analysis/mapped_results.py` | 完整关联和分组，核验金额守恒；SKU/SPU 页保留关联状态与比较资格 | 模型已阅读所有 SKU/SPU；第一页覆盖后页异常 |
| `business_integrated_receipts.py` | 各 job 独立读取完整来源目录；必读预算有完整分页证明；分析回执绑定真实结果 | `mapped.complete` 当前仅为 `mapped_count > 0`，没有要求所有关联结果行或固定筛查结果均被阅读 |
| `business_diagnosis.py` | 核验模型主动给出的引用，最多 12 条 findings、合计 32 条 references；原生和关联引用均可重算 | 枚举未引用行；`factsVerified: true` 不是“所有经营异常均已发现”，也不是因果关系已经证明 |
| `business_export.py` / `business_mapped_export.py` | 后续文件包含完整原生和关联明细及比较表 | 完整文件不能反向证明模型已发现其中的问题 |

可复现场景：41 个按身份排序的 SKU，第 41 行退款额最大且毛利为负。商品、独立复核、整合 Agent 各成功读取前 20 行，就可能满足当前映射阅读门禁。其已引用数字全部正确，报告仍可遗漏第 41 行。大费用变化也存在相同风险。该场景是代码路径推导，不是已观察到正式报告漏诊。

必须分别展示四层覆盖：**事实完整核验、固定规则全量筛查、候选阅读、模型诊断解释**。任一层完成不能替代下一层；固定规则全部执行也不能宣称覆盖所有可能的经营问题。

## 2. 指标和维度必须来自真实来源

| 来源/维度 | 可用的筛查输入 | 必须保留的限制 |
| --- | --- | --- |
| 本期/基期推广；店铺、品类、SPU、SKU、关键词、搜索词 | 实际存在的 `spendCents/reportedGmvCents/impressions/clicks/reportedOrderLines/cartQuantity`；有完整分子分母时可展示 CTR、CPC、ROAS | JD 与天猫归因含义不同；归因窗口未另行核验时仍未知。ROAS 是报表归因成交/推广费，不是 ERP 利润率。维度字段缺失不能因支持该 view 就声明有该维度 |
| 原生商品销售；店铺、品类、SPU、SKU | 实际存在的 `paymentCents/paymentQuantity/productDayVisitors/pageViews/reportedOrders` | 商品×日访客累加不是店铺去重 UV；跨商品订单行不可宣称去重订单数。原始 SKU/SPU 可用性依平台和 dataset 而异 |
| B 端 | 真实 `jd_b2b` 来源中具备的上述商品指标和维度 | 商用设备或商品名称不等于 B 端数据；不能用普通店铺销售冒充 B 端销售。无 B 端来源就记为无来源 |
| ERP 原生店铺汇总、显式关联 SKU/SPU | `netSalesCents/positiveSalesCents/refundCents/costCents/grossProfitCents/reportedGrossProfitCents/feeCents` 及各数量指标 | `grossProfitCents = netSalesCents - costCents`，未扣分摊费用；源报毛利独立保留。费用不是推广费，不可拼接为广告 ROI。日期是来源业务口径，不能改称下单日或支付日 |
| ERP 关联 SKU/SPU | 固定 `mappingPairs`、算法与主数据后的上述指标 | 只用精确匹配；歧义/未匹配保留，不扇出金额。当前主数据不能证明历史 SKU 归属；跨期必须同 master、平台、店铺、渠道和原始日期范围 |
| 市场、品类/品牌/商品样本 | TOP 样本 GMV/销量上下界与其有效区间比较 | 不是全市场总额、市场份额或精确成交。样本更替影响必须披露；价格/排名原始字段不能直接当作现有聚合结果的可加指标 |

每个来源分别筛查，不跨来源去重猜测或累加“总损失”。店铺、品类、SPU、SKU 是同一金额的不同投影，其候选金额和命中数也不能相加当全店独立问题总额。缺失维度可产生“维度缺失”覆盖记录，不能把未知商品组冒充一个已识别 SKU。

## 3. 首批固定规则

规则使用整数分/整数计数比较，不以浮点显示比率决定是否命中。不自动设置经营预算、增长目标、退款阈值或主观“严重”分数。以下命中表示值得查看的数值现象，不等于归因结论或处置指令。

| 固定规则 ID | 精确条件 | 候选排序量与解释 |
| --- | --- | --- |
| `promotion_spend_up_gmv_down` | 可比基期存在；本期 spend > 基期 spend，且本期 reportedGmv < 基期 reportedGmv | 按增加推广费降序；显示两项原值与差额，说明报表归因窗口限制，不能断言推广造成成交下降 |
| `promotion_spend_without_reported_gmv` | 本期 spend > 0 且 reportedGmv = 0，二者均完整有效 | 按推广费降序；“有花费、报表归因成交为零”，不是证明没有真实销售或未来转化 |
| `erp_refund_present` | refund > 0 | 按退款额降序；只是退款核查队列，不能把任何退款认定为异常。退款额/本期正销售若展示，必须注明非同批订单退款率 |
| `erp_refund_up_sales_not_up` | 可比基期存在；refund 增加且 positiveSales 不增加 | 按增加退款额降序；不推断质量、售后或推广原因 |
| `erp_gross_profit_negative` | grossProfit < 0 | 按负毛利绝对值降序；明确为净销售减来源成本，不是最终净利润 |
| `erp_fee_up_net_sales_down` | 可比基期存在；fee 增加且 netSales 减少 | 按增加费用降序；说明来源费用口径，不替换为推广费 |
| `product_traffic_up_payment_down` | 可比基期存在；productDayVisitors 增加且 payment 减少 | 按减少支付金额降序；访客为商品×日累计，只描述量额分化，不推出转化因果 |

单期规则要求所用指标非空、`missingRows == 0`。比较规则另要求两侧真实行存在、所用指标两侧完整、日期覆盖合格且来源口径兼容。不得把缺行填零；基期为零或负数时不能计算增长率，即使整数差额仍可展示。关联行非 matched、缺 SKU/SPU 或比较资格不满足时，比较规则标记不适用；其真实单期退款等可保留在明确的“未归属/歧义核查”队列，不包装成已匹配商品风险。

首批支持上述原生推广/商品/B 端规则，以及 ERP 原生店铺与显式关联 SKU/SPU 规则。仅实际有字段的来源执行。**市场区间与日内趋势先列入请求覆盖但标记未实现**，不能静默删除：

- 后续市场规则可在完整上下界下用 `currentUpper < baselineLower` 识别确定下降的样本区间；需保持原 TOP 样本语义，不推广到全市场。
- 日趋势需另行固定日历、缺日和比较窗口规则；不能直接把本期/去年同期不同日期字符串 join 后当同比，也不能自动选择最有利的切点。
- 不实现推广—ERP 的因果归因、净利润、库存/履约根因、市场份额；没有可靠数据与口径就输出明确缺口。

## 4. 最小纯协议与服务边界

建议新增 `backend/business_analysis/diagnostic_screening.py` 与对应纯测试；再新增 `backend/ai_assistant/business_diagnostic_screening.py` 作为只读 owning adapter。初批不改数据库、不开放路由、不改旧 diagnosis 或工具目录。

固定计划 `business-diagnostic-screen-plan-v1` 至少包括：

- `algorithmVersion/ruleSetVersion`、规则集合与明确的筛查容量策略；计划摘要由 canonical JSON 生成。
- `reportId`、可信 evidence 五字段、owner/scope 绑定及 native/mapped 算法版本；有映射时固定 `mappingPlanDigest`，源引用来自可信 Reader，不接受模型提交的 source metadata。
- 完整 `tables` 选择列表：每项 mode、sourceKey 或 pairKey、dimension、window、可选精确 baseline 及 ruleIds；包括请求但不适用的项和原因。原生和关联同一维度不能互相顶替。
- 候选分区与排名定义。按规则、来源语义、mode、dimension、window 分区；不把金额、比例、不同口径放进一个不可解释的总排名。

所有表选择由服务根据完整已验证目录、固定映射计划和请求维度构造，不能由模型提供第一页 rows/header 来充当全量输入。跨期匹配必须显式唯一且同口径；多候选不得猜选。

建议内部接口：`with screen(plan, open_verified_table, revalidate) as result:`。adapter 使用原生 `stream_table` 或关联 `mapped_table(...).scan()`；每次只打开一个表，完整消费后关闭再进入下一表。纯核心只负责规则/计数/有界候选；来源真实性、权限与封存由 adapter 负责。

最小第一片可以只写纯核心，不必先实现服务或持久 screening plan：

```python
scan = DiagnosticScan(descriptors, ruleset="diagnostic-signs-v1", limits=limits)
for descriptor in descriptors:
    with open_verified_table(descriptor) as table:
        scan.consume(descriptor, table.header(), table.scan())
    # consume 完成仍是内部暂存；owning context 退出可继续使整个任务失败。
revalidate()
result = scan.finish()  # 检查每个 descriptor 恰好完成一次后，输出 passive JSON。
```

`consume` 必须完整遍历 iterable，不接受“已读完”布尔参数，不暴露可发布的中间结果；重复/遗漏 descriptor、行计数不符、错误 rowIndex 或重复 rowId 均拒绝。纯核心的成功只证明传入的固定表流满足算法合同；只有随后服务层的 `revalidate` 和绑定封套才能证明其是当前授权证据。首批纯 API 不取 principal、不读数据库、不接受模型自由表达式，也不自动创建报告。

发布结果的必要条件：

1. 每表行序连续，原始 rowId 和 rowIndex 保留；实际消费数等于可信 header 总数，滚动 canonical 行摘要完成。
2. 原有 PageReconciler、金额守恒及 context 退出后的复验全部成功。不能提前取满候选就停止生成器，也不能在 context 退出前返回“成功”结果。
3. 所有计划项都有终态：已筛查或明确不适用；空表有可见的零行证明。损坏、超限、权限失效或中途失败时，整次筛查不发布可供报告使用的成功对象。
4. 最后重新核验报告固定绑定、owner/scope、封存版本和来源目录。绑定变化必须重新执行，不能迁移旧候选到新计划。

## 5. 覆盖证明、候选页和容量

结果 `business-diagnostic-screen-result-v1` 保存固定计划摘要、绑定摘要、每表总数/消费数/行摘要、每规则统计及候选摘要。每个表×规则都满足：

`scannedRows = eligibleRows + ineligibleRows`，`matchedRows <= eligibleRows`。

不适用原因采用固定优先顺序且互斥计数，例如来源不支持→维度缺失→关联歧义→基期缺行→日期不可比→所需指标缺失。另行列出请求层面无来源/功能未实现的缺口，不伪造其 scannedRows。跨规则命中可重叠，matchedRows 不能加总为独立实体数。

`scanComplete` 只代表计划内可执行表的完整扫描；`requestedCoverageComplete` 另表示请求范围是否仍存在不支持项。二者都不是“发现全部业务问题”。模型是否读取候选使用第三份 job 级独立证明，不能写回这两个字段冒充完成。

首批可采用下列**待实现、需实测的新增限制**，与已有事实 64 MiB/2000 页、单表 25 万组等额度分别计量：最多 256 个计划表、累计 200 万次结果行访问、最多 64 个候选分区、每分区保留前 16 条。固定保留上限 1024 条，候选 canonical 总字节最多 8 MiB，覆盖元数据最多 1 MiB。超限失败并返回具体容量原因；不截断表计划、少扫后页或自行删维度。该策略不承诺所有 48 来源×所有维度×全部比较组合一次均可准入。

扫描用每分区有界 heap；仍对**每一行**计数与执行规则。按规则定义的整数排序量降序，再按稳定 tableKey/rowId 排序；不得依赖输入偶然顺序或随机 tie-break。无须新增临时 SQLite；原始表的临时资源继续由 owning context 关闭，异常路径也必须释放。计数及累计金额超 JS 安全整数时拒绝输出数字，不能舍入后继续。

候选页 `business-diagnostic-candidates-v1`：固定结果绑定、partitionKey、offset、最多 20 条、实际 UTF-8 ≤38,000 字节，完整行前缀和真实 nextOffset；单行超限拒绝。明确返回 `matchedRows/retainedRows/omittedRows` 以及覆盖证明摘要。**读完所有 retainedRows 不是读完全部 matchedRows，更不是模型读完所有明细。** 首批文件若只附候选，表名和清单须写“排名候选”；完整分析明细仍由既有导出提供。不能把候选 Top N 的金额当全部异常金额。

每个候选至少包含 candidateId、ruleId、table selector、原 rowId/rowIndex、排序量、条件所需精确值、原生/映射引用与限制说明；candidateId 绑定筛查算法、计划/证据摘要和行身份。重算一致才能引用。引用仍指向原 owning table；不允许模型自造候选或修改其数值/排名。

## 6. 模型接入单独成批，冻结旧工具目录

第一批仅内部筛查与测试，不改变现有 integrated proof 的含义，不把筛查成功强塞进旧 `mapped.complete`，也不声称模型已用到该能力。

后续使用**新 profile + 新 surface + 全新工具名**接入。旧所有工具的 schema、description、allowedSurfaces、canonical catalog SHA 保持不变。新 surface 可用独立新条目复用目录/分析/预算 handler，再增加筛查候选工具；不能直接往旧工具的 allowedSurfaces 追加一个值。

创建时固定筛查计划与策略版本；模型执行前完成筛查并校验容量。每个专业 Agent、独立复核、整合分别持久记录自己读取的覆盖摘要和必读候选页；数学结果可在有界、同绑定阶段复用，读取证明不能跨 job 共享。新 profile 的准入预先核算实际工具调用/输入预算，超过限制直接拒绝，不靠少读页满足原调用上限。

新诊断协议可以接受 candidateId，并由服务查固定候选、重验规则，再展开成现有精确原生/映射 references。旧诊断请求与校验器保持兼容原样。未筛查出的观察仍可使用真实表引用，但必须标成额外人工/模型观察，不能扩张覆盖证明。

最终报告结构化展示：请求覆盖缺口、规则全量计数、候选保留/遗漏数量、各 Agent 候选阅读完成度、实际解释了哪些候选。模型 findings/引用上限仍可能使解释少于候选；不能用一段“全部已分析”抹掉差额。调整方向应带条件、观察指标和回滚标准；筛查结果不直接执行预算或业务写入。

## 7. 首批验收与后续服务测试

纯测试必须覆盖：

1. 41 行且重大退款/负毛利仅在最后一行；全量计数正确，风险候选可进入排名，消费尾页的证明真实存在。
2. 命中多于 16 条、相同排序量、行顺序扰动；保留集和摘要确定，matched/retained/omitted 精确，页边界不跳行、不重行。
3. 空表、空来源、缺维度、0/负基期、缺失指标、缺日期、缺一侧行；不填零、不产生虚假增长率，每项不适用原因守恒。
4. ERP 负退款符号归一、正销售与净销售区分、来源成本毛利与报送毛利区分；费用不被当成推广费，关联歧义不扇出，SKU/SPU 投影不累加损失。
5. 最后一页损坏、迭代器末尾异常、header 总数错误、提前关闭 context、退出复验失败；不得获得可发布成功结果，所有资源关闭。
6. 来源/店铺/渠道/日期/master 不同的比较拒绝；相同 sourceKey 但来源查询或证据摘要变化使旧结果和 candidateId 失效。
7. 中文宽行 38,000 字节边界、单行超限、候选/覆盖内存额度、计划表/行访问上限、整数溢出；均明确失败而非截断。
8. 未支持市场规则和趋势明确进入覆盖缺口；不把样本区间转点值，不把商品×日访客称作去重 UV；规则不读预算默认值。

服务集成阶段由主线程另跑隔离 PostgreSQL：真实 sealed 原生+映射+基期，失权与 context 退出复验，固定报告绑定篡改，五 job 独立回执，失败不重放模型，以及旧 profile/catalog golden 完全不变。纯测试不能代替这部分。

首批交付标准是“固定范围已完整、可复算地筛查，缺口和候选截取诚实可见”；后续再交付新 profile 的模型阅读、诊断引用与文件覆盖证明，不合并宣称一次完成。

## 8. 第 31 批实际第一片（内部、未发布）

已新增 `business_analysis/diagnostic_screening.py` 与 `test_diagnostic_screening.py`，实际入口是：

```python
prepared = prepare(binding, descriptors, open_table, limits=None)
# open_table(descriptor) 是内部 context manager，yield (完整 header, 完整 rows iterable)。
```

由 `prepare` 自身按固定 descriptor 摘要排序逐个进入、完整耗尽并退出 context，全部正常后才返回。iterator 尾部异常、context 退出失败、context 吞掉未耗尽错误、任一表缺行或过量都不能取得成功返回。结果固定 `schemaVersion=business-diagnostic-screen-prepared-v1`、`status=prepared_unpublished`、`authorityVerified=false`；**没有 publish API**。调用者伪造 expected total 或全套描述最多只能得到未认证的 prepared 结果，不能据此生成来源授权证明。

七条整数规则、精确 eligible/matched/ineligible 计数、稳定 Top K、全部行的有长度边界 SHA256、header/plan/result 摘要已实现。来源 query/sourceRef/evidenceDigest、映射 plan/pair/master 与 evidence 绑定完整固定；表顺序不影响重跑结果。实际分区为表×规则，最多 64 个，限制比未来跨表语义分区保守。覆盖显式保留真实 source/baseline 日期状态、窗口和天数：来源范围有记录不代表每个实体逐日有行；闰日同比天数可能不同，不擅自按天归一。

新增限制采用第 5 节上限，另有单 row/header 128 KiB、每表 rowId UTF-8 合计 16 MiB，用于检查重复行身份。容量按逻辑数量与序列化字节计量，不宣称限制整个 Python RSS。所有容量参数只允许调低，bool/float 冒充整数拒绝。

当前未实现候选 HTTP 分页、新 profile、来源权限认证、持久 screening plan、模型必读回执或筛查结果文件。18 项新纯测试通过，包含真实原生 `build_table` 与真实 `mapped_table` 结果流；加旧结果表和 mapping plan 回归共 36 项通过。该证据不代替后续 owning service 的隔离数据库与权限测试。
