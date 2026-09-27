# 经营分析权威范围选项：最小接口设计

2026-09-17。第三十批实现网店最小只读 GET，隔离 PostgreSQL 新增12项与既有9项共21项通过；第三十二批已完成本文 A/B/C 的实时账号适配、分页组件和工作台添加入口，52项Node与68项新旧Chrome通过。ERP、市场元数据目录及自然语言范围建议仍为后续设计。下文保留设计依据，当前实现与边界以 [第三十二批证据](evidence/ai-business-source-picker-candidate.json) 为准。不自动改别名、不读取客户或订单明细、不将选项存在称为事实完整。

**当前可用候选合同：** `/api/netshop/analysis-options`，每项 `identity={platform,shop,dataset}`，不是datasets数组；完整规范JSON最多38000 UTF-8字节，超限整页拒绝，不裁前缀。原响应没有principalKey，签名游标内部绑定账号/权限、筛选与revision。工作台已使用独立 `/api/ai/business-plan/netshop-options` 账号封套，保留原领域页摘要；跨域选项尚未全部完成。下文跨域统一形状均为建议，不覆盖此已实现协议。

## 结论

不能直接复用现有页面选项组成完整的经营分析选择器。可以复用三个 owning reader 的身份签名、角色校验、版本围栏、字段验证及已发布导入元数据。网店已经采用独立GET，ERP与市场可新增独立只读 `analysis_options` consumer，后续再由工作台适配。不注册模型工具，不改变历史工具定义、catalog 摘要或现有预览协议。

**真实店铺列表应定义为「本领域已发布来源中出现过的精确身份」，不是账号注册表、界面显示名或模型猜测。** 网店身份为 `(platform, shop)`；ERP 为 `(platform, shop, channel)`，三者必须同时满足现有分析读取器的原值及规范投影条件。两域同名不是跨域关系证明，只允许用户明确选取。没有导入记录的已配置店铺可另列“已配置但尚无来源”，不能混成可用数据身份；首版不必增加此列表。

## 现有能力与不能复用的部分

| 领域与代码 | 现有端点/数据 | 能证明什么 | 不能证明什么 / 新接口原因 |
| --- | --- | --- | --- |
| [网店路由](../backend/netshop/urls.py)、[视图](../backend/netshop/views.py)、[query.overview](../backend/netshop/query.py) | GET `overview`，按平台/可选店铺汇总 dataset/dateMin/dateMax/snapshotDate；产品/推广页面也带部分店铺选项 | 对该页面选择范围的已有数据概览 | overview 直接聚合 `NetshopRow`，不是分页店铺目录；按 dataset 键返回也不能作为跨平台完整来源清单。产品页面伴随商品/销售富化，不应用来只取身份 |
| [网店 consumer](../backend/netshop/consumers.py)、[批次模型](../backend/netshop/models.py) | POST `consumers/query` 的 `import_batch_search`，有 offset/limit（一般最多100）；批次有精确 platform/shop/source/dataset、状态及日期元数据 | 已导入批次的精确身份和导入日期提示；可复用已完成批次的标量列 | 返回的是批次而非去重身份，包含文件名等多余信息；默认也含非 completed。不得先拉完所有历史批次再在浏览器去重。范围 revision 表只覆盖部分商品日/推广，不能独立当全部 master/B2B 来源目录 |
| [网店规范源](../backend/netshop/analysis.py) | GET `analysis-records`；支持 promotion、京东 sku、京东/天猫 spu、京东 b2b、master | 精确数据集支持性、首页实际范围及控制汇总 | 会取事实；不是找店铺的接口。商品日访客不证明店铺去重 UV |
| [ERP 页面选项](../backend/sales/summary.py)、[consumer](../backend/sales/consumers.py) | summary 内 shops最多500/platforms最多200；`product_performance` 的 outletOptions最多500，先取最多2001身份候选并标 truncated | 对既有经营页面展示口径的可选项 | 有截限；summary 使用报告投影键，consumer `_canonical_outlet()` 会合并京东自营别名、补默认值、截文字，不能喂给精确分析查询；同时返回金额/产品，不是纯元数据接口 |
| [ERP 规范源](../backend/sales/analysis.py) | `consumers/query` 的 `analysis_records`；无记录时 knownChannels最多20并有截断标志 | 已指定精确平台/店铺后的有限历史渠道提示 | 必须先知道店铺；只在无结果首页返回，仍读取事实并计算金额。实际过滤同时要求 raw platform/shop/channel 与 platform_key/shop_key/channel_key 一致，不能拿展示别名替代 |
| [ERP 导入范围](../backend/sales/write_service.py)、[模型](../backend/sales/models.py) | 批次 scope 含日期及可选 channels；原销售行有精确身份投影 | 本次导入授权范围/日期及可能的渠道限制 | scope 没有完整 shop/platform/channel 组合，不能由 channels 推导店铺。`approved_sales_channels()` 是导入策略，不是全部实际店铺目录 |
| [市场选项](../backend/market/query.py)、[reader 路由](../backend/market/urls.py) | POST `queries` 的 `filter_options`：全局独立 categories/scopes/rankingDimensions 等 facets；`priceBands`来自发布的价格分类标签 | 各独立字段出现过；发布的价格分类规则标签 | facets未分页，直接对排行事实分组且结果全部返回；字段笛卡尔组合未必存在。**priceBands不是规范源的 `price_band_filter`**，不能将“未确认价格”或动态标签当导出时价格筛选身份 |
| [市场日覆盖](../backend/market/query.py)、[规范源](../backend/market/analysis.py) | `daily_coverage`要求 category/scope/rankingDimension/priceBandFilter，最多4000天；analysis_records只读单日榜，平台固定京东、维度SKU/SPU | 精确已知身份的有记录日期；规范源排除周/月重叠榜 | 不负责发现身份，不证明 TOP 样本完整、平台结算或全行业规模；不应为选择器先扫全部日事实 |
| [市场发布元数据](../backend/market/models.py)、[导入校验](../backend/market/import_service.py) | completed 批次 `scope_json.ranges` 来自导入行的精确范围去重，并校验 supplied scope 相等 | 新导入批次曾发布的真实范围、日期；可成为选项投影的来源 | 多范围 JSON 不能无界展开；旧迁移元数据可能不完整，不能把缺字段补成“全部”。历史出现过不代表当前事实仍存在 |

现有 reader 暴露范围比经营分析更宽：市场 queries 允许 viewer/analyst/operator/admin（另有 scope 门禁），网店部分读支持平台 scope，ERP 页面按 principal 过滤。**新选项遵循经营分析的无范围限制管理员门禁**，每个域自己复验 `role=admin && scope is None`，不能因为纯元数据而借用宽权限；签名及生产 reader/writer 路由隔离沿用原机制。

## 不从原始事实无限枚举的最小落地方案

1. **网店先独立完成。** 新 consumer 从 completed `NetshopImportBatch` 的现有标量身份列做数据库去重、确定性排序、keyset分页，筛掉不在 `analysis.SOURCES` 的 source/dataset 组合；每页最多21个候选，只返回20。成功批次的日期只标 `published_import_envelope`，不是当前事实覆盖。不得复用现有 overview、`_latest_batches()`的全量Python循环或先导出全部批次。检查元数据查询索引/执行计划；若历史元数据规模导致查询超时，再采用与下述相同的独立投影，不放宽超时。
2. **ERP 必须补精确身份元数据投影。** 现有批次无法给出店铺组合，应由 sales 域在成功发布事务中维护独立的 identity/date-envelope 投影；键使用当前 `analysis.read_page` 的精确三元组，只收 `is_business_row` 且 raw值与投影键相同、非空、长度/控制字符合法的记录。拒绝用 `_canonical_outlet`、report_shop_key 或渠道配置造身份。以本域 revision 绑定已完成投影；页面只查投影。首版投影可以只证明“曾在有效已发布数据中出现”，日期包络不能标为当前完整覆盖。
3. **市场复用发布范围，增加有界投影。** 在成功导入事务内把 `scope_json.ranges` 的 `(京东,category,scope,rankingDimension,priceBandFilter)` 和单日期范围写为独立选项索引，只收单日榜。不要在每次请求遍历全部批次 JSON，更不能连接独立 facets 拼出不存在的组合。价格项保留导出原始精确值。历史元数据不能证明的部分标为尚未建立，不用事实全表搜索临时兜底。
4. **历史初始化是受控作业，不能藏在 GET。** ERP 可用固定 revision、按主键分块的事实扫描建立候选投影，市场优先按批次元数据分块；只处理身份/日期，不输出金额或客户。每块有资源上限，任务完整结束并核验 revision 后原子发布。扫描中源变化则重建或明确失效，不发布部分目录。新的表/写入与历史初始化须另行实施迁移、最小 grants、健康检查和备份恢复；本文不把它们算作现有能力。
5. **没有完整目录就明确停用该域选项。** 返回 `options_not_ready`，不返回空列表冒充无店铺；其余域仍能独立选择。保留原精确手填入口及支持性预览，但手填值不标“已匹配权威身份”。不因索引缺失自动取事实或自动猜测。

投影应定义独立身份数、元数据字节和构建临时空间上限，具体数值先经合成边界/现有导入上限验证，不承诺无限目录。写入失败必须与本次源发布原子回滚，或采用显式“该 revision 的目录未就绪”协议；不得源已更新而目录仍冒充当前版本。原数据导入和权限不因选项读而拓宽。

## 跨域统一协议建议（尚未实施，不覆盖网店现有 GET）

后续跨域工作台可以采用 GET `/api/ai/business-plan/options`；网店复用现有GET，ERP/市场由AI reader调用指定领域的签名 POST `consumers/query {operation:"analysis_options", ...}`。POST是内部只读consumer传输，不走writer，不触发模型或新业务记录。仅注册新的精确路径与operation，不放宽通配 allowlist。下方先保留统一形状设想，网店先接页面的更小方案见文末三个小批次。

请求固定以下形状，未知/重复字段拒绝：

| 参数 | 规则 |
| --- | --- |
| `domain` | `netshop` / `sales` / `market`；一次只读一域 |
| `platform`、`shop` | 前者精确过滤，后者仅店铺域；ERP渠道只能在精确平台/店铺选定后选，不默认互相映射 |
| `category`、`scope`、`rankingDimension`、`priceBandFilter` | 仅market精确过滤；只筛已存在完整组合，不组装笛卡尔积 |
| `q` | 可选、最多100字符，仅搜索身份文字以减少候选；搜索命中不自动选定、改写身份或登记别名 |
| `cursor` | 可选签名keyset游标，绑定domain、完整过滤、principal权限摘要、目录revision、页长、上一身份键和到期时间 |
| `limit` | 固定20；浏览器无需传。内部未知值/超值拒绝；网店已实现整页UTF-8超限拒绝，其他域也优先沿用，不静默截断 |

根响应精确字段建议为：

```text
schemaVersion: "business-analysis-options-v1"
principalKey, domain, revision, query, queryDigest
items: [{ optionKey, identity, datasets, dateMetadata, provenance }]
pagination: { returned, limit:20, nextCursor, hasMore }
pageDigest
limitations
```

所有对象固定字段、禁额外键；响应完整JSON最多64 KiB，内部consumer data最多38,000 UTF-8字节；单行超限拒绝，不截断名称/渠道。`optionKey=SHA256(canonical({domain,identity}))`只是定位值，可信性来自实时授权及已发布目录，不能接受客户端自报SHA作证明。`pageDigest`覆盖除其本身外的规范完整页；不传客户、订单、原始行、金额、文件名、actor邮箱。

- netshop `identity={platform,shop}`，`datasets`列出当前支持且有成功导入身份记录的 promotion/sku/spu/b2b/master；每dataset自己的来源和日期元数据分别保留，不能用一个数据集的日期冒充全店。
- sales `identity={platform,shop,channel}`，`datasets=["erp_sales"]`；零/空渠道不能伪装为“未分类”后成为可分析身份，保持原精确读取器约束。
- market `identity={platform:"京东",category,scope,rankingDimension,priceBandFilter}`，`datasets=["market_daily_top"]`；不含shop。
- `dateMetadata`按dataset返回 `{kind:"published_import_envelope"|"observed_source_envelope",firstDate,lastDate,snapshotDate,coverageVerified:false}`，未知值明确null；master使用snapshotDate且不提供历史归属证明。初版不返回 `presentDates`/`complete=true`。导入包络、历史观察边界和完整所选区间覆盖是三个不同概念。
- `provenance`至少固定元数据来源类型、对应目录revision与“历史出现/当前索引”的明确含义；不通过单个最新批次ID宣称所有历史日期已完整覆盖。

不需要精确 `total` 就不要全表COUNT。空页仅在该revision目录已完整发布且过滤确实无命中时合法；403/503/超时、目录未就绪、游标过期/版本变化分别显示，不能统一变成“无数据”。过期/变更重新从第一页读取并清空未确认候选，保留问题文本和旧选择供明确重新核验。

## 与48来源规划及自然语言的关系

[planning_v2](../backend/business_analysis/planning_v2.py)仍负责按精确选项展开全部来源：数据集×窗口×店铺分别计数，master仅current；一店16来源加一市场三窗口是19来源。每页20选项和48来源上限无关系，不能只取前48身份作“全部店铺”。

首版选中返回的identity原值填入现有表单，保持完整人工确认与19/48可预览、49完整拒绝。选项revision在提交前复验可走新只读options校验切片；不往已有 `analysisRequest`/旧preview/冻结pending body随意加字段。若要持久固定“选项确认凭证”，另开版本而非改旧摘要。后台采集仍用真实源revision和完整封存核验，选项凭证不替代取数授权。

自然语言后续只从该有界目录中提出候选及理由；同名多店、无渠道、日期不明和市场组合不存在时交由用户明确选择。不能从问题里的店铺名称反推ERP渠道、用另一个平台同名店替换，或把当前master自动扩成历史master。无论匹配多确定，都不自动调用付费模型进行正式报告分析。

## 最小测试与实施顺序

先冻结协议与网店元数据consumer，然后ERP投影与market精确组合投影可独立开发，最后接AI薄路由和工作台下拉；不先写一个跨域事实DISTINCT兜底接口。

1. **纯合同：** 重复/未知字段、空格/空值、长中文与转义UTF-8、同名跨平台、同店多渠道、错误dataset/market组合、页摘要和cursor绑定；不调用旧别名合并函数。
2. **领域PG：** 只用合成批次和身份投影；失败/处理中批次排除、晚到历史批次、替换导入、旧元数据缺字段、投影中断/版本改变、写回滚。网店选项查询不得访问NetshopRow，ERP/market选项不得扫描事实表；初始化作业的事实访问单独测试。
3. **读写隔离：** 生产reader URLConf注册、writer不注册；无签名/普通角色/有限scope/跨账号拒绝；新元数据表只授必要权限，无客户/原始payload泄露。
4. **分页与性能：** 超20身份完整续页、搜索缩小后游标失效、跨页源revision变化、单行/整页容量边界、空页与未就绪区别；目录达到设计上限时明确拒绝。保留SQL超时，合成性能不替代后续正式只读计划验证。
5. **工作台：** 账号切换/异步迟到、目录失败不删除用户问题、同名歧义不自动选择、精确ERP渠道联动、市场价格原筛选与动态价格带区别、缺源保留缺口、48/49展开边界；旧手填预览与未知提交按原body恢复不变。

上述选择器只解决「范围身份来自哪里」。事实覆盖、店铺去重UV、跨源业务口径、模型建议质量及正式采用仍按[剩余开发清单](AI_BUSINESS_REMAINING_ACCEPTANCE.md)分别验收。

## 网店最小片之后：工作台接入实施建议

本节以新候选 [netshop.analysis_options](../backend/netshop/analysis_options.py) 与 [Next 薄路由](../app/api/netshop/analysis-options/route.ts) 为准。前文是跨域总设计；网店最小片实际选择**独立 GET**，不新增 consumer operation。每项 `identity={platform,shop,dataset}`，另有 source/sourceDataset/dateMetadata/provenance，不是前文设想的整店 datasets 数组。一个店铺的多个数据集可能跨页出现，当前页绝不能当该店全部可用数据集。主线程已完成该候选的21项定向PG，本节的A/B/C已在第三十二批实现并通过组件、父级及旧工作台组合验证；以下保留其设计与验收依据。

### 小批次 A：固定前端协议与账号绑定的只读适配

建议新增 `lib/ai/business-scope-options.ts`（被动类型、规范 JSON/SHA 校验与选择合并），及独立 `tests/business-scope-options.test.ts`。核验 domain/schema、query/queryDigest、pageDigest、revision、20项上限、完整行/唯一optionKey、精确支持组合、日期合法性、`coverageVerified === false`、provenance含义及UTF-8容量；不相信响应里自报的 `optionKey` 而跳过结构/摘要校验。游标是不透明签名串，前端只沿服务端nextCursor翻页，不拆解/拼接或根据返回数猜偏移。新helper不复用要求“完整封存48源目录”的 mapping builder 校验器，因为此处是可搜索、可分页且可能超过48身份的候选列表。

**先补当前响应缺少的账号回显，再接UI。** 网店GET已由当前principal签名且游标绑定账号，但首响应本身没有工作台 `principalKey`；仅组件props/取消和请求前GET一次AI身份都无法排除会话已换B、父级仍显示A的竞态，页SHA也不证明账号。建议新增只读适配路由 `app/api/ai/business-plan/netshop-options/route.ts`：要求恰好一个64hex `expectedPrincipalKey` 查询字段，先以一次 `requireAppPrincipal`取得实时principal、核验无范围admin并计算实际key，**不符即403且零领域请求**。相符后，用同一个principal对象调用现有netshop service，并回 `{schemaVersion:"business-netshop-options-response-v1",principalKey,page}`；返回key只能来自该实时principal，不能回显客户端值。前端仍逐次比较返回key和当前父级身份。

适配层只剥离已严格校验的expectedPrincipalKey，其余网店query保留原始重复/未知参数交领域拒绝，不能通过转换object悄悄去重。page完整保留领域原响应及pageDigest，不改网店原GET，原非AI调用继续兼容。principalKey必须与 [工作台算法](../backend/ai_assistant/business_evidence.py)完全相同：SHA256规范JSON数组 `["business-workbench", email.lower()]`，用固定跨语言夹具核验；不要用显示名。路由不得返回email，不注册模型工具，不增加Django AI写接口。已有源服务成功revision门禁、no-store、Abort及签名查询原样传递保留。

适配路由、helper可以单独交付，只做单测，不挂页面。额外测试：A的expectedPrincipalKey遇实时B时403且零网店fetch；缺失/重复/伪造key拒绝；预检后账号变化返回另一个principalKey必须被拒收；错误/403不降级为空列表；匿名/有限scope/错误method被拒绝；pageDigest仍覆盖原完整页，不因外层身份回显改变旧摘要；原网店GET无需AI身份字段且行为不变。

### 小批次 B：独立网店来源选择组件

建议新增 `app/ai-business-scope-picker.tsx`，复用 bw 样式；props可为 `{principalKey,disabled,onSelect,onIdentityMismatch}`。`onSelect`仅在用户点击时返回 `{principalKey,revision,queryDigest,optionKey,identity}`，不提交证据、不生成UUID、不解析问题或修改日期。第一页显示20项、当前筛选和“历史已发布来源”的日期提示；platform/dataset/q筛选、下一页/上一页、重试/取消齐全。没有total就不画“已读全部店铺”的百分比。

- 组件按account+筛选条件维护请求代次、AbortController和游标历史；新筛选先清旧页，等待当前页成功再显示/选择，不能在新筛选标题下显示旧结果。上一页使用保存的原游标重新读，不将不同revision页拼起来。
- 账号变化、卸载、取消、切筛选都中止并隔离迟到响应；完整JSON解析和SHA计算之后也再验代次/账号。响应principalKey不符则清候选并通知父级刷新身份，不能自行把当前表单搬给新账号。
- 409过期/源revision变化时保留搜索文字，清当前可选页和游标历史，显示“来源有更新，请从首页重读”；不自动重选。只读重试可以显式进行，不触发任何POST。
- 页面显示精确平台+店铺+数据集。日期文案例如“该来源历史导入包络：7月1日—9月15日；不表示逐日完整”，主数据显示快照日期。首尾日期为null显示未知，不能转为当天或零。历史包络不能自动填写分析起止日期、勾选同比/环比或显示绿色全覆盖。
- 每个来源行独立“添加到分析范围”，不自动选择同店其他数据集。搜索结果没有某dataset不是该店永远不支持；跨页不能构造并展示一个假完整datasets集合。

组件验收可用独立esbuild+合成HTTP+真实Chrome：20+3来源分页，同店数据集跨页、同名跨平台、目录revision变化、账号切换及迟到、连续切筛选、坏摘要、未知日期、取消/恢复、手机无外溢。所有请求GET，无真实数据/模型。

### 小批次 C：只接工作台新增范围，不替换原手填与其他领域

仅修改 [工作台](../app/ai-business-workbench.tsx) 的“问题与范围”区域，放置独立“从历史导入选择网店来源”入口。保留现有精确平台/店铺/数据集手填、ERP渠道逐行输入、市场精确条件、日期与窗口；明确“ERP和市场范围仍需手动确认”。本批不支持自然语言自动匹配，也不把网店选择自动映射为ERP渠道。

`onSelect`由父级同步ref检查live、当前principalKey、locked/pending和事件绑定，然后对**最新**form做纯合并：同一精确platform/shop仅追加该dataset且去重，保留既有ERP渠道；不同身份新增一店，超过4店明确拒绝，不删旧店。仅初始空白占位行可以由用户这次点击替换，替换后dataset只包含本次明确选择，不能把默认promotion/master偷偷一起带入。若需覆盖非空身份，用另外明确的替换操作，不能用行序号回调覆写，因为删除前一店会令旧索引指向另一店。首版采用单个全局picker追加/合并，避免给现有index-key店铺行挂异步选择器。

每次添加/手工改动仍经过原 `edit()`：取消旧预览、撤销范围确认，之后重做完整v2预览。旧选项revision只是“曾选该精确身份”的UI提示，不能存成事实证明；新增表单事件不改变已有 request/evidenceRequest/analysisRequest JSON。超48来源交现有规划器完整显示并拒绝，不自动减少店铺、数据集或窗口。49来源拒绝与20候选分页分别验收。

`locked`必须覆盖整个picker和所有添加动作；创建结果未知时只重发已持久的原bodyJson/UUID，不读取picker当前选择重组payload。会话恢复不自动查选项后改写冻结请求，也不自动重复创建。读取错误和写错误继续分开，候选页读取成功不能清除原提交错误。未成功添加任何来源时，旧手工流程完全可用。

父级Chrome验收：选择精确来源→只增加对应dataset→用户自填日期/渠道→完整预览→确认采集；检查同店合并、4店上限、跨平台不合并、删除店铺后迟到选择、ERP/market字段不变、日期窗口不变、旧预览失效、未知提交刷新同body、当前账号绑定，以及候选目录故障时仍可手工填写。不得用组件单测替代父级完整请求断言。

这三个小批次可以在网店元数据PG通过后逐步实施，不依赖ERP或市场新投影先完成，也不提高48来源、64MiB/2000页、模型上下文或既有权限上限。候选选择只减少身份输入错误，采集后的完整性与业务口径核验仍是必需步骤。
