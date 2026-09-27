# 全系统分组搜索

顶部搜索与 AI 工具 `search_system_data` 复用 `lib/search/global-search.ts`。14 个分组全部通过 Django 有界只读 consumer 获取 PostgreSQL 权威数据；Worker 只负责鉴权、签名、参数校验、分组调度和结果投影，不再读取 D1 表清单、执行旧 SQL 或回退旧库。数据库字段白名单、查询和 scope 由各 Django 业务域维护。

## 当前覆盖

| 分组 | PostgreSQL 权威来源 | 字段白名单摘要 | 导航模块 |
| --- | --- | --- | --- |
| 货品主数据 | `erp_product_master` | 编码、名称、规格、条码、品牌、品类、供应商 | 货品详情 |
| 销售订单 | `sales_order_lines` | 订单号、线上单号、ERP 商品编码、网店规格编码、商品名称、店铺、平台 | 销售分析 |
| 京东 SKU/SPU 与网店商品 | `netshop_rows` + `netshop_import_batches` | 各店各数据集最新已完成权威批次中的 SKU、SPU、商品编码/名称、店铺、平台、数据集；原始推广历史不重复参与商品搜索 | 网店分析 |
| 库存记录 | `inventory_import_batches` + `inventory_stock_lines` | 系统当前已完成库存快照中的商品、仓库、仓型、规格、品牌、品类；排除刷刷仓 | 库存管理 |
| 库龄数据 | 最新 `erp_reference_import_batches` + `erp_inventory_age_lines`、最新 `inventory_import_batches` + `inventory_age_metrics` + `inventory_stock_lines` | 当前权威批次中的商品、仓库、品类、规格、库龄、7/30 日销量；排除刷刷仓 | 库存管理 |
| 组合装关系 | `erp_combo_items` | 父件/子件编码和名称、组合数量 | 货品详情 |
| 备货计划 | `replenishment_plan_items` | 商品、仓库、状态、原因 | 库存管理 |
| 市场 SKU | `market_master_identities` + `market_ranking_entries` | 当前身份投影指向的最新 SKU、商品名、品牌、品类、口径 | 市场分析 |
| 细分品类标注 | `market_sku_annotations` | SKU、细分品类、一级品类 | 市场分析 |
| 客服会话 | `customer_service_conversations` | 授权角色可按顾客、客服、商品和会话号匹配；只有显式 `group=customer_service` 时才扫描聊天正文，响应只含会话最小摘要，不返回正文 | 客服分析 |
| 财务/目标 | `finance_lines`、`finance_targets` | 授权角色可按科目、范围、月份等匹配；不返回 `raw_value` | 销售分析 |
| 运营事务 | `workflow_tasks` | 标题、内容、分类、负责人、店铺、状态、优先级 | 运营事务 |
| 导入批次 | 已登记的销售、库存、ERP、网店、市场、财务、客服、SKU 快递费率批次表 | 批次号、文件名、来源、状态以及对应店铺/平台字段 | 数据导入 |

Django 配置、服务、权限、响应或来源版本异常时，该分组返回 `available: false`，不会阻断其他分组。后续新业务表需要显式加入覆盖清单和测试；这样可以审计哪些数据能够被顶部搜索和 AI 读取。

## 边界与分页

- 关键词 2—80 个 Unicode 字符；`%`、`_` 和反斜杠会转义为字面量。
- 每组默认 4 条、最多 8 条；总计默认 48 条、最多 50 条。越界参数直接拒绝，不会静默扩大查询。
- API 支持 `page`、`limit`、`totalLimit`，以及单分组分页的 `group`。各域通过有界分页返回 `items/total/truncated`；正常响应的 `totalExact=true` 表示来源返回的精确总数，服务异常或超时不会伪报精确空结果。导入批次按销售、财务、网店、商品经营、库存、客服、ERP、市场的固定顺序跨源分页，并校验计数/数据页的来源 revision 与 total。
- 默认多域搜索最多同时执行 3 组查询，并受 2 秒组查询总截止时间约束；到达截止时间后不会启动后续组。截止后会取消尚未完成的 Django 请求。响应通过 `deadlineExceeded` 与 `timedOutDomains` 披露本次不完整域，调用方可指定单一 `group` 重试。
- 大表仍采用服务端 `LIMIT/OFFSET`，不会一次性返回全表。客服聊天正文只在显式客服分组中执行受限 `LIKE` 扫描；当体量继续增长，应迁移到独立 FTS 索引。`netshop_rows.raw_json` 等大 JSON 未纳入模糊搜索，避免一次搜索扫描不必要的原始载荷。
- 网店商品搜索只连接每个 `source + dataset + platform + shop` 最新的已完成批次，并通过批次所有权复合索引读取事实；京东/天猫推广历史由推广聚合域负责，不再让同一商品的逐日推广行拖慢全局商品检索。
- 库存、库龄和市场搜索分别复用系统当前完成批次或最新身份投影；不会为了返回少量搜索结果重扫全部历史快照，并始终保留 `刷刷仓` 业务排除。
- `product_shipping_rates` 不作为独立模糊搜索域；规格代码仍通过货品主数据定位，最新已完成批次的快递费率由商品经营汇总和 `get_product_performance` 有界只读工具返回。
- 金额统一为人民币分。库存与市场结果使用匹配实体最新一条快照，避免把历史数量或价格误当成当前值。
- `viewer` 不可查询客服、财务和目标域，且其余域的 `amountCents` 一律脱敏。客服域只开放给 analyst/operator/admin，财务与目标只开放给 analyst/admin，导入批次只开放给 operator/admin。
- 非空 `principal.scope` 通过签名信封传给 Django，在各领域查询中落实：库存/库龄/备货按仓库，销售按渠道或平台，网店按平台，财务/目标/事务按范围或店铺；没有兼容范围维度的域对受限主体关闭。模型参数中的身份或角色不会参与授权。

## 明确排除的敏感与管理数据

架构审计将以下表保持在搜索白名单之外：`app_users`、`ai_models`、`ai_channels`、`system_settings`/`ai_system_settings`、`ai_conversations`、`ai_conversation_messages`、`ai_tool_audit_logs`、所有上传分片/对象键/结果 JSON、模型密钥与渠道密钥、市场标注 Prompt/任务/验证/本地代理令牌、导入警告与原始 JSON。业务导入批次只返回文件名、来源、状态和时间，不搜索哈希、对象键、警告 JSON 或原始载荷。

本地直连采用显式三重门控：只有开关明确启用、部署环境声明为 development，且构建运行时同时确认开发模式时才允许；生产构建强制关闭。搜索层始终接收并执行 `requireAppPrincipal` 返回的真实主体。

## AI 注册

工具名称、描述、Schema、角色、风险、scope 能力、handler 与审计策略只在 `lib/ai/tool-registry.ts` 声明一次。OpenAI、Anthropic、MCP、`/api/ai/tools` 和执行路由都从该数组生成；`lib/search/ai-tool.ts` 仅保留业务 handler。运行时在 handler 前执行完整的 JSON Schema 子集校验。所有工具（包括只读工具）都必须先成功写入 `started` 审计才会执行 handler，并在成功或失败后写入结束审计；任一步审计不可用都 fail-closed，调用方不会收到业务数据。

源码边界检查、无 D1 构建和正式发布门禁见 [`DJANGO_AGGREGATE_CUTOVER.md`](DJANGO_AGGREGATE_CUTOVER.md)。
