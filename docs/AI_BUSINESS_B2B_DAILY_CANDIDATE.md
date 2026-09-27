# 京东 B 端逐日与期间候选

`business_analysis.b2b_daily_candidate.prepare_candidate` 是纯计算、未注册的候选；仅接受调用方提供的固定 v2 来源目录、完整 owning Reader `info` 与所有分页，不查数据库、不调用 Agent、不生成文件。返回 `authorityVerified=false`、`registeredRenderer=false`。`catalogDigest` 重建匹配只证明所给目录内部一致，**不证明目录已封存或账号有权读取**；正式接入须由 owning 服务复核报告、主体、封存摘要和来源权限。

已查代码：`netshop.analysis.SOURCES['b2b']['京东']` 精确映射 `jd_b2b/b2b`，读取时按平台、店铺、本期/前期/去年同期日期过滤，并提供修订、游标、首页控制汇总、来源页和日期覆盖。五个原生指标为 `paymentCents`、`paymentQuantity`、`productDayVisitors`、`pageViews`、`reportedOrders`；字段未出现时 owning 页给 `null`，而非从 `NetshopRow` 的默认整数零推断真实零。商品日访客不能当店铺去重 UV。

候选重建目录及三个窗口的比较日期，只允许显式选择该店该窗口唯一的 `jd_b2b/b2b` 来源；若目录内已有匹配来源，不能声明缺源。选中来源则逐页核对修订、精确过滤、源 Ref、游标链、单页和总容量、源行内容摘要唯一、控制指标与日期覆盖。每个自然日保留 `observed_rows`、`partial_metric_coverage`、`date_not_covered` 或 `selected_no_records`；各指标独立汇总，已知值只是已知行小计。没有目录匹配项时返回 `catalogue_only_missing_source` 和 `null`，其含义仅是**提供的目录内未安排**，不证明数据库中绝无 B 端数据；选中来源但范围内零行也不等于店铺历史 B 端销量为零。

仅 B2B 同指标、两期日期和字段均完整、基期为正时给差额及增长率（bps）。`b2bIncludedInErpSales` 与 `b2bIncludedInPlatformSkuSales` 固定为 `unknown`；ERP/商品销售占比及 B 端增量固定 `null`，B2B/ERP/推广/平台商品金额绝不相加。是否重叠需要同店同区间、订单/渠道定义与源端对账，不能用“企业购”名称或商品类型推断。`jd_b2b` 通用导入未像 SKU/SPU 日源那样强制业务日期和连续范围，正式验收还需源文件表头、缺日及历史导入核对；本候选只分析 owning Reader 对所选窗口实际返回的事实。

合成纯测覆盖精确店/窗口、完整 B2B 金额比较、单指标 `null`、无源目录缺口、缺日、零基期、跨店、越界日、源 Ref/内容篡改和重复行哈希。未读取真实客户行、启动导入或改变公共 API。
