# 关键词—搜索词—计划—跟单 SKU 关系候选

`backend/business_analysis/promotion_attributed_sku_relation.py` 在完整京东推广规范页上新增两种**未注册的纯视图**：`keyword_attributed_sku`，以及 `keyword_searchterm_plan_unit_match_attributed_sku`。后一视图按计划、单元、匹配、关键词、搜索词和源明确的 `attributedSkuId` 分组；缺身份桶保留费用与行数，不猜一个商品。两视图各自对来源行数和费用全量回卷，彼此及现有“明确推广 SKU”视图都是同一来源的不同投影，不能相加。

它复用既有京东推广页身份、页链、来源总额、日期覆盖、临时分组容量与合作式取消；基期只允许同店同范围的 previous/yearAgo。缺跟单 SKU、缺词或缺计划等行不做可行动的唯一商品归属，也不把 `attributedSkuId` 改称 `promotedSkuId`、`triggerSkuId` 或主数据 SKU。源投影中的多个同角色原表别名冲突仍未由本层核验；广告归因金额不能当 ERP 净销售、B 端增量或因果效果。

纯层 `authorityVerified=false`、`agentReadPersisted=false`，不自带 owning 报告绑定、Agent 工具、五 Agent 数值引用或 HTML/XLSX 注册。需要同一封存报告的拥有方全页复验，之后仍须记录 Agent 本人同任务读取，才可进入有审查的诊断表和 renderer。合成真实投影测试与旧推广 SKU 回归共 19 项通过，覆盖两视图各自守恒、缺身份、环比、精确行 ID、末页篡改和取消；没有使用客户数据或生产服务。

续工新增未注册的 [拥有方只读接线](../backend/ai_assistant/business_promotion_attributed_sku.py)，复用同报告封存 Reader、当前管理员复验与精确推广来源选择，完整读取后才返回当前页或精确行的报告绑定；仍不授予 Agent、商品主数据或报告覆盖权威。隔离 PostgreSQL 三项 `.runtime/ai-pg-66dfcdcf4b4e/tests.log`（15.144 秒）通过，覆盖费用回卷、旧视图角色分离、错账号/撤权与末页篡改；默认无公开路由、模型或文件发布。
