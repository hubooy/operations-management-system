# 推广计划与单元派生视图设计

状态：**纯算法与内部 owning 服务已形成候选，未注册新运行时能力**。第三十六批新增真实封存来源的内部页和精确行读取；公开工具、完整报告选择、规则及文件接线仍是下文设计。合成测试不读取原业务文件、生产数据或平台，不证明真实店铺的计划/单元字段完整；旧八维、旧工具 catalog 摘要和旧报告文件保持不变。

当前纯候选为 [promotion_views.py](../backend/business_analysis/promotion_views.py) 和 [test_promotion_views.py](../backend/business_analysis/test_promotion_views.py)。实际签名是 `table(source, pages, expected, *, view, baseline_source=None, baseline_pages=None, baseline_expected=None, limits=None)`；context 内 `header()`、`page(offset=0, limit=20)`、`scan()`。测试允许仅降低 `maxGroups/maxScratchBytes/maxPages/maxSourceRows/maxResponseBytes`；两侧合计最多 2000 页、200000 原始行，单页结构最多 128KiB，分组/临时盘/响应仍各自限额。64MiB 封存任务配额和真实授权由后续 owning 层负责，此纯对象不是持久证据或授权凭证。新测试执行真实网店 `_project` 代码构造行，元数据沿实际 `read_page` 形状，不初始化 Django 或访问数据库；不能用此代替真实 reader 集成验收。

## 1. 核源结论：先支持京东，天猫不能从已汇总事实还原计划

| 身份或指标 | 当前规范来源的实际行为 | 新视图可以承诺什么 |
| --- | --- | --- |
| `planId / planName` | [网店 analysis.DIMENSION_FIELDS](../backend/netshop/analysis.py) 只从原始 `计划ID/计划id`、`推广计划/计划名称` 投影；没有 ID 时为 null | 使用已投影的精确 ID，名称不作主键；不能从名称推 ID，也不承诺每条京东记录都有 ID |
| `unitId / unitName` | 只接受 `单元ID/单元id`、`推广单元/单元名称` | 单元必须连同 planId 分组，不假设跨计划单元 ID 全局唯一 |
| `matchType` | 只接受 `匹配类型/匹配方式`；不映射成统一枚举 | 不合并“精准”“精确”等不同原值；缺失不等于广泛匹配 |
| 三类商品 ID | `promotedSkuId/triggerSkuId/attributedSkuId` 分别来自智能投放推广 SKU、触发 SKU、跟单 SKU | 三种角色不同；第一批不做词货联合分析，更不能以一般 `skuId` 替代三者 |
| 京东推广 | `jd_promotion/ad`；导入逐原始行保留 raw，未调用天猫商品日预聚合 | 可以对**封存规范事实**按上述身份派生；仍需统计实际缺 ID 记录，不能只凭源码支持判定源业务完整 |
| 天猫推广 | `tmall_promotion/promotion_daily`；商品×日自然键，多计划原始行先合并 | 第一版计划/单元视图明确 `unsupported_source_grain`；保留原有商品、店铺视图 |

天猫的关键证据在 [prepareTmallPromotionRows](../lib/netshop/normalized-import.ts)：按日期+主体 ID 汇总；当一组有多行时将 `计划ID/计划名称/计划` 清空，只保留“计划列表/计划数量/计划明细行数”等溯源说明。其 [导入测试](../tests/tmall-netshop-import.test.ts)用 plan-a 花费 1000 分、plan-b 2000 分，验证合并为商品日花费 3000 分。即便某个商品日仅一行保留了计划 ID，同一个期间仍可能同时存在已合并行，不能将这些零散 ID 拼成完整计划报表。

天猫直连文件有 `计划名字/场景ID` 等表头，也不代表现有 AI 规范投影接受所有同义表头；尤其不能把“场景”解释为“单元”。未来若要天猫计划分析，需要 owning writer 单独保留计划/场景粒度、自然键、覆盖和控制合计的新事实合同，再引入新来源；不能改写现有商品日事实或按计划列表均摊金额。本设计两批不包括该新源。

`_scalar` 已将可接受的源标量转换为去首尾空白的文本、缺失为 null、超长拒绝；新版使用这些**封存后的**规范值，不再重新解析 raw，也不能恢复导入前丢失的前导零或精度。现有 [网店合成测试](../backend/netshop/tests/test_analysis.py)证实计划名和三类 SKU 投影及直接金额/null 行为，但不证明所有真实导出都填充 planId、unitId、matchType。

## 2. 最小第一版合同

建议独立新模块 `backend/business_analysis/promotion_views.py`，新 schema `business-promotion-result-table-v1`、算法版本 `promotion-plan-unit-v1`。不往 [results.VIEWS](../backend/business_analysis/results.py)追加键，不改变旧 `business-result-table-v1` 输出。视图名由代码固定，禁止模型传任意维度数组或 SQL：

| view | 完整身份键（平台+店铺始终包含） | 意义 |
| --- | --- | --- |
| `plan` | platform, shopName, planId | 某精确计划下已观察推广事实合计 |
| `unit` | platform, shopName, planId, unitId | 某计划中的精确单元合计 |
| `unit_match` | platform, shopName, planId, unitId, matchType | 某单元的原始匹配方式分组；是第三个独立视图，不与单元合计再次加总 |

每次只接受一个固定来源的完整页流及可选同源基期，不接受跨平台混合流。源描述显式固定 `{key,domain,query,sourceRef,evidenceDigest}`，仅 `domain=netshop, platform=京东, dataset=promotion, source=jd_promotion, sourceDataset=ad` 可执行；相同 sourceRef 不能替代对 domain/query/header 的检查。支持单期 current/previous/yearAgo；比较仅 current 对 previous/yearAgo，原查询平台、店铺、数据集和用户原始起止日完全相同，仅 window 不同。

建议纯 API：`table(source, pages, expected, *, view, baseline_source=None, baseline_pages=None, baseline_expected=None)` 为 context manager，产出只读 `header()`、`page(offset,limit=20)`、`scan()`；只有输入两侧全部页核对结束才能读取派生表。首次完整消费每一侧一次，分页/scan 均读当前临时分区，退出后失效。`source/expected` 在首个 next/callback 前冻结；基期参数必须成套出现，不能猜源。纯模块不认证、不打开公共 API、不接受模型提供的自报 proof。

新 header 至少包括 schema/algorithm/view、固定 `groupingKeys`、本期与基期源绑定、queryDigest、sourceMetadata/baselineMetadata、sourceWindow/comparisonWindow、各侧规范期间、dateCoverageComparable、身份覆盖计数、total、tableBindingDigest、limitations。`tableBindingDigest` 绑定算法、view、两侧固定描述及可信核对记录；不称为额外独立源证据。行含 `id/rowIndex/entity/currentRowCount/baselineRowCount/metrics/baselineMetrics/ratios/comparisons/identityQualified/missingIdentityFields`；rowId 绑定 tableBindingDigest+规范 entity，不能跨视图或跨报告复用引用。

## 3. 空身份、名称及角色不能制造确定性

- 非空 ID 必须为规范文本；不把 `001` 和 `1` 转成同一数字，不做别名/大小写/Unicode 近似匹配。平台+店铺+计划共同限定单元，不将两店同计划 ID 或两计划同单元 ID 混合。
- null 缺身份与字面字符串“未知”“null”严格区别。缺 planId 的金额进入**未归属核查桶**；缺 unitId 仍保留已有 planId，不并入任何真实单元；缺 matchType 保留已有计划+单元，不能当作某一已知匹配方式。缺失模式及所有已知键共同分组。
- 核查桶可以合计未归属金额以守恒，但它不是一个真实计划/单元：`identityQualified=false`，不输出该桶的跨期增长率或可执行投放对象。两期各自缺失即使落同一个桶也不能推定是同一业务实体；两侧观察值仍保留，比较为空。全源分配到已识别+核查桶的行数和指标应守恒。
- 名称不参与身份和跨期匹配。同 ID 更名不拆分，同名不同 ID 不合并。最小首批不在聚合行任取第一个计划/单元名称作为权威名称；只显示 ID，并引导在原始规范明细查名称。后续要显示名称变体，应另设有界完整变体证明，超限明示，不能静默挑选“最新名字”。
- 京东计划视图暂不使用任何 SKU 角色。后续词货必须为“关键词×推广 SKU”“关键词×触发 SKU”“关键词×归因 SKU”分别定义视图；每个视图保留自己的源合计，但三份视图不是三份独立费用。不能把三个字段依次 coalesce 成同一个商品身份。

## 4. 指标与比较复用方式

基础指标仍为源中的 `spendCents/impressions/clicks/reportedOrderLines/reportedGmvCents/cartQuantity`；京东可出现 `directGmvCents/indirectGmvCents/newCustomerGmvCents`。只消费 [analysis.py](../backend/netshop/analysis.py)已投影并被 PageReconciler 核对的键及原值，不自行补造“直接订单数”“间接订单数”。缺指标为 null，不用 `reportedGmv-directGmv` 补 indirect，不假设直接/间接/新客金额互斥可加总；归因金额不是 ERP 净销售或利润。

沿 [group_result](../backend/business_analysis/aggregation.py)保留每指标 `value/presentRows/missingRows`；局部已知合计可以展示，但必须带缺失数。全部缺失值保持 null；来源零行时返回零个结果实体，不凭空生成零金额计划。CTR、CPC、ROAS、订单行转化率先合计分子分母再算；任何必要指标有缺失，相关比率和比较不可用。

复用 [PartitionedGroups](../backend/business_analysis/partitioned.py)的固定维度配置、临时 SQLite、逐页消费、分组校验与双侧并集：现有 DIMENSIONS 已包含 planId/unitId/matchType，允许多字段分组，无需扩旧维度集合。不要以 monkeypatch 修改 VIEWS，也不要复制旧 `_assemble` 后偷偷改旧输出。新模块负责独立 header/row 包装，内部直接配置上述明确键，复用稳定的 `group_result/compare/ratio/PageReconciler`。必须分别核对各指标总分、缺失数、两侧行数及源摘要；所有检查成功后才产出结果。

沿用原 [comparison_periods/compare](../backend/business_analysis/contracts.py)：缺日、缺侧、缺指标、缺完整身份均不可比较，不能补零；基期零/负数保留差额与明确状态、增长率为空；CTR/订单行转化率按百分点，ROAS不是百分比。同比闰日可能两侧天数不同，只比较原区间总额并明确天数，不暗作日均或等天数归一化。日期齐全不代表归因窗口成熟，现有来源 `attributionWindow` 默认未知，不以可计算比率证明增量效果。

容量仍采用现有源 100 行页、每表并集最多 250000 组、临时分区 256MiB、严格整数范围；比较两侧并集超过上限整拒。页对象固定 20 行以内、UTF-8 最多 38000 字节，可返回完整行前缀并给真实 nextOffset，单行超限整拒；完整 scan 不按页重扫来源。组数、临时盘、行宽和输出量分别有界，不以提高旧单表/文件限制解决超限。

## 5. 两批交付边界

### 第一批：纯算法，不接现有 profile

建议只新增 `promotion_views.py/test_promotion_views.py`，必要时另加临时合成文件演练。用真实规范页结构和 PageReconciler 生成 expected，不能捏造先前映射测试那类不存在的 metadata 字段。测试/验收至少包括：

1. 两店同 P1、P1/P2 内同 U1、相同名称不同 ID、`001/1`、中文/超 BMP ID、缺 plan/unit/matchType；已识别与核查桶守恒，空源无虚构对象。
2. 合成 A 店 P1/U1 精确匹配花费 100、P1/U1 广泛匹配 200、P1/U2 花费 300、P2/U1 花费 400：plan 合计 600/400，unit 为 300/300/400，unit_match 为 100/200/300/400；末两条的 matchType 缺失应在各自单元核查桶，而非落进一个全店“未知”实体。每个视图总花费均 1000，不能相加成 3000。
3. 全字段/局部 null/全 null、direct 有值而 indirect 缺失、源金额与计数守恒、分母零、比率重算；天猫（即使单行带 planId）、非推广源明确不支持。
4. 本期/环比/同比，异店/异源/异 query 拒绝，缺日/缺侧、零负基期、闰日、计划更名、单元跨计划迁移不当成同实体增长。
5. 页尾多/少行、摘要/顺序篡改、源发生异常、未完全耗尽或 context 退出异常不得提供完成结果；遍历次数断言与输入别名修改隔离。
6. 25 万组/双侧并集边界、磁盘故障、UTF-8 超限、页偏移严格整数/摘要/nextOffset、来源和分页累计一致。允许注入更小测试容量，实际限制不放宽。

纯测试通过只证明算法合同；没有账号、DB、真实来源或 Agent 完整诊断通过含义。

### 第二批：owning 接入，先内部闭环再显式开放

第三十六批实际接口：`business_promotion_views.page(report_id, params, principal)`，params 固定 sourceKey/view、可选 baselineKey、整数 offset 与固定 limit=20；`read_row(report_id, source_key, view, row_index, row_id, principal, baseline_key=...)` 精确重算行引用。来源来自当前授权报告完整目录，双方事实全部读取并退出临时表后再次授权，整响应包含绑定仍不超过38000 UTF-8字节。该内部接口没有注册路由、工具、新 profile、规则或文件；不能据此认为下表已全部接通。测试证据见 [第三十六批候选记录](evidence/ai-business-promotion-owning-candidate.json)。

新增 `backend/ai_assistant/business_promotion_views.py`，从实时 owner-authorized、已封存 v2 的 `Reader` 取得固定 sources/info/pages；按 sourceKey 精确选京东推广，基期须由同报告完整目录显式选择。前后重载 current principal、report/workflow 固定输入、封存摘要及完整来源，消费和 context 退出后复验；分页结果不是跨 owner 的凭证，不自动缓存持久化。

第二批开始前先固定以下接线合同，不能只加一个工具参数便声称报告支持：

| 接入面 | 必需变更与门禁 |
| --- | --- |
| 请求/固定计划 | 独立新选择字段/版本（建议 `promotionViews` 固定枚举集合）纳入报告快照与工作流轻引用；旧 `analysisRequest.requestedDimensions` 只接受五维，不向旧字段塞新枚举。默认旧报告不增加视图，也不自动选择全量 |
| 服务/路由 | 新内部页入口在无公共注册下先实测；开放时 reader GET、实时无范围 admin、未知/重复参数拒绝、source+view+baseline+offset 有界，按实际表绑定复验 |
| 工具/Agent | 新版本工具和 surface/profile，保留全部旧定义与 catalog hash；先目录再新视图页，调用/输入/响应预检不扩限，独立 Agent 回执绑定 view、算法、源、query、rowId、分页链。新旧结果不可借用读取证明 |
| 结构化引用 | 独立版本的 promotion 引用解析器，固定 view 替代任意 dimension，服务端重算行身份和数值；不能把新 rowId 喂进旧 business_diagnosis 的八维 handler |
| 规则筛查 | 新描述/选择协议明确请求的三种视图；复用规则数学而不扩旧31规则目录与旧筛查摘要。计划表数/分区完整预检，超过64分区整拒，不缩为前几个计划。只给身份齐全且日期合格的行提行动候选，核查桶只作缺口 |
| 文件 | 显式 opt-in 的新报告/渲染路径追加 TableSpool 表；新表用既有分/比例格式、缺失和完整行摘要。旧 renderer1/2/3及既有v4默认表清单字节均不变；若需要持久文件入口支持新报告，先选新renderer/profile绑定并补容量/恢复合同，不能把旧ready文件重新解释为新表 |
| DB/恢复 | 第一批不迁移。第二批若新profile或snapshot字段触碰0022 guard允许集，必须新迁移精确开放，不能改旧迁移或跳过guard；逆迁移有新报告则拒绝。不存在可信持久派生存储时按封存事实重算，不用跨请求内存对象假装恢复 |

建议第二批先交“真实合成 owning-reader→封存→内部纯视图→引用解析→临时双文件”闭环；公共创建/模型工具/持久文件一旦涉及新profile、guard及旧兼容，作为同批明确的后续受控步骤或再拆下一批，未接部分逐项标未开放。不可用临时文件通过替代正式下载/备份恢复通过。

组合验收必须补实际 reader 角色、owner/账号切换、撤权/版本变化、真实header结构、最终源页失败、跨view引用拒绝、旧catalog/旧八维/旧文件摘要回归；DB及全量构建由主线程串行运行。真实模型需在参考问题中区分“计划整体差”与“计划内某单元/匹配方式差”，明确缺身份/样本门槛及归因窗口，不因新增视图就自动生成投放指令。

## 6. 此切片不会完成的能力

天猫计划粒度新源、关键词/搜索词×三类 SKU 联合表、跨源利润关联、月度财报、市场进出榜及行业份额都不由计划/单元视图补齐。可先用现有京东字段修补最小派生缺口，但真实京东字段覆盖仍须独立来源验收；不能为了两平台界面一致而隐藏天猫不可还原的事实粒度。
