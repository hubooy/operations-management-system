# 系统实时数据集 API

系统提供 **243 个数据集：219 个记录数据集 + 24 个分析数据集**，覆盖 12 个领域。2026-09-08 已在本机正式采用，配套 Worker、Django、12 个领域 reader 列级授权及 AI 对话工具均已上线；版本、备份恢复和验收记录见 [本机采用证据](evidence/system-datasets-production-20260908.json)。本记录不代表远程或云端发布。

记录数据集直接查询各域现有 Django/PostgreSQL 权威记录，支持连续分页；分析数据集复用已有业务口径和聚合工具。不复制事实、不建立第二写入源、不接受 SQL，不新增业务表或迁移。完整列表见 [覆盖清单](SYSTEM_DATASETS_COVERAGE.md)，逐列契约在 `backend/system_datasets/manifest.json`。

## 覆盖与权限

覆盖销售、ERP、财务、网店、市场、商品经营、库存、运营事务、客服、用户权限、AI 助理和 BI。清单核对这些 app 的全部 221 个模型：219 个记录来源，1 个重复 ERP 消费者模型归并到权威数据集，1 个已退役 ERP bridge 模型明确排除。包括事实、批次、版本、导入状态和审计；读取原始记录时必须按状态判断是否为已发布事实，不能把暂存行或失败批次直接计入经营指标。

每一列均有开放定义或排除原因。凭据、加密内容、原始客户聊天、客户标识、对象存储键和文件字节不开放给通用 AI 查询；图片、附件和分片只提供允许的元数据，字节仍走原有受控接口。业务 JSON 和本人 AI 内容按显式字段清单开放；结构内容中凭据键递归脱敏，URL 去除用户信息、查询参数和片段，深度超过 12 显示 `[depth-limited]`。这套 API 不等于数据库备份或无损文件导出。

219 个记录数据集仅允许**无数据范围限制的管理员**。私有 AI 会话、消息、记忆、产物和任务进一步按当前本人及父记录所有者过滤。其他角色继续访问原有分析数据集中符合其角色和数据范围的部分。沿用真实认证，未知、停用或权限变化的账号失败关闭，不创建通用密钥或管理员身份。

## HTTP 与 AI 入口

以下路径相对系统同源地址：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/ai/datasets?page=1&pageSize=20&domain=sales` | 分页发现可访问数据集，参数均可省略 |
| GET | `/api/ai/datasets/{dataset}` | 字段、类型、单位、排除原因、权限和查询 schema |
| POST | `/api/ai/datasets/{dataset}/query` | 正文 `{"query": {...}}` |
| GET | `/api/ai/tools` | 中央 AI 工具目录 |
| POST | `/mcp` | 既有 MCP `tools/list` / `tools/call` |

目录默认每页 20 个、最大 50 个，按 `hasMore` 翻页；`total` 为权限及领域筛选后的数量。详情和查询不接受查询字符串。POST 沿用同源检查，提供精确同源 `Origin` 或 `Sec-Fetch-Site: same-origin`。外部客户端必须接入现有认证；客户端自报 email、role、scope 或内部签名头不是身份。

三个中央 AI 工具：

- `describe_system_datasets`：传 `page/pageSize/domain` 发现目录，或传 `dataset` 读取详情。
- `query_system_dataset`：传 `dataset`、`queryJson`，返回来源数据、水位和审计关联。
- `get_system_dataset_records`：有界记录来源工具，传同样的参数，由所属领域 reader 执行。

`queryJson` 是符合 schema 的 JSON 对象字符串，不是 SQL 或代码。系统 chat/Agent 和既有 MCP 从唯一中央注册表发现工具。MCP 沿用现有 Bearer 配置、服务身份及 12 秒请求预算，本次不配置凭据或连接。

## 系统内 LLM 对话接入

系统内“AI 助理 → AI 对话”沿用 `POST /api/ai/chat`。Django 对话服务把当前账号可用的中央工具传给模型；模型可依次发现数据集、读取字段、发起查询，再使用工具结果生成回答。系统提示明确分页、单位、数据截止日期和截断口径，不要求用户手写 API 参数。

数据集查询结果可生成聊天表格和既有 CSV 产物：只取最多 50 行、12 个安全标量列，保留来源工具及截断标记；目录元数据不会生成业务表格。长内容、凭据类列及其他不满足原产物安全规则的字段不进入表格。模型仍能根据有界工具结果回答，不能把部分页解释为完整数据。

模型返回参数的传输预算依据中央 schema 中 `queryJson` 的长度上限计算，兼容 16000 字符及 JSON 转义开销；业务层继续独立执行 16000 UTF-8 字节限制，其他工具保留原 8000 字节边界。通过协议夹具验证 OpenAI-compatible 和 Anthropic 的真实解析与 tool-result 循环、签名聊天入口、数据库读取、表格生成、请求重放、未授权工具及审计失败路径；未调用真实付费模型。该接入随本次候选代码采用，当前生产尚未更新；独立第三方 LLMchat 应用仍需明确其地址和认证配置。

## 连续读取记录

在已登录的同源页面中先查询目录及 schema，再选择字段：

```javascript
const catalog = await fetch('/api/ai/datasets?page=1&pageSize=50&domain=erp_reference').then(r => r.json());
const schema = await fetch('/api/ai/datasets/rows_erp_product_master').then(r => r.json());
const query = { columns: ['product_code', 'product_name', 'brand'], pageSize: 50 };
let cursor;
do {
  const response = await fetch('/api/ai/datasets/rows_erp_product_master/query', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query: { ...query, ...(cursor ? { cursor } : {}) } })
  });
  if (!response.ok) throw new Error(`查询失败：${response.status}`);
  const { data } = await response.json();
  console.log(data.rows); // 在此处理本页，不要用单页行数代表全量
  cursor = data.nextCursor;
} while (cursor);
```

AI 调用示例：

```json
{"name":"describe_system_datasets","arguments":{"dataset":"rows_sales_order_lines"}}
```

```json
{"name":"query_system_dataset","arguments":{"dataset":"rows_erp_product_master","queryJson":"{\"columns\":[\"product_code\",\"product_name\"],\"filters\":[{\"field\":\"brand\",\"op\":\"eq\",\"value\":\"志高\"}],\"pageSize\":20}"}}
```

记录查询支持 `columns`（最多 50 列，默认前 12 列）、`filters`（最多 8 条 AND 条件）、`pageSize`（1–100）、`cursor`、`textOffset`、`textLimit`。操作为 `eq/gte/gt/lte/lt/in/isnull`；`in` 最多 50 个值，`isnull` 使用布尔值，结构字段不支持通用值比较。数值和日期按字段类型传入，不接受嵌套字段路径或表达式。

分页按唯一键递增，不限制总页数。游标加密、有效 30 分钟，绑定本人、权限、数据集版本、字段、筛选和文本窗口参数，修改这些条件必须从第一页重查。每页实时读取（`live_per_page`），持续写入时不保证跨页原子快照。超过 JavaScript 安全范围的整数、Decimal 和 UUID 返回字符串。

长文本或 JSON 默认每个单元格最多 2000 字符，`textLimit` 最大 8000。`cellWindows["行号.字段"]` 返回编码、总字符数、偏移和 `nextOffset`。续读单个记录时，用其唯一键作 `eq` 筛选、仅选所需列、设置 `textOffset=nextOffset` 并重新查询；不要复用绑定旧窗口的游标。`encoding=json` 的分段拼接后再解析。减少列数或页大小可避免响应超限。

## 24 个分析数据集

| 领域 | 数据集 ID |
| --- | --- |
| 销售 | `sales_summary`、`sales_category` |
| 库存 | `inventory_health`、`inventory_age`、`inventory_inbound`、`inventory_guangdong`、`replenishment_plans` |
| 商品 | `product_performance` |
| 网店 | `netshop_catalog`、`netshop_products`、`netshop_product_daily`、`netshop_promotion` |
| 市场 | `market_overview`、`market_sku_trend`、`market_brands`、`market_price_bands`、`market_pending_review` |
| 财务 | `finance_analysis`、`finance_targets` |
| 客服 | `customer_service` |
| 运营事务 | `workflow_tasks`、`workflow_operations`、`workflow_launch_projects`、`workflow_templates` |

分析数据集保留各自参数及分页契约；例如 `sales_summary` 接受 `{"range":"custom","startDate":"2026-09-01","endDate":"2026-09-07"}`。销售分析工具与页面的起止日期均包含当天，上例覆盖 9 月 1—7 日；单日填写相同起止日。2026-09-09 的适配修复在销售汇总工具边界将结束日加一天，再传给既有左闭右开的 Django consumer，不能由调用方再加一天；此前单日拒绝和多日少算末日是缺陷。`sales_category.trend` 保留 Django 的对象结构（`items/returned/truncated/granularity/categoryLimit`），不是直接的数组。固定选择器由服务端注入，调用方不能覆盖。沿用上海业务日界；金额按字段单位解释，人民币分不能当元，网店访客不能当去重店铺 UV。查询仍须遵守 [业务数据查询规范](OPERATIONS_DATA_QUERY.md)。

## 响应与边界

销售品类分析提供 `categoryOptions.items/total/truncated`，沿用权威接口在当前账号与日期范围内的有界品类候选，最多 200 个。未确认品类时先仅传日期和 `limit=1` 获取候选，再将实际名称放入 `categories`。`productQueries` 只精确匹配完整货品名称或编码，不支持品类词/名称片段的模糊包含；错误筛选返回零行不能作为该品类没有销量的证据。

外层包含 `schemaVersion/dataset/source/requestId/queriedAt/freshness/dataCutoffDate/data`。`freshness` 为查询前读取的销售/库存水位，只代表这两个域；未知截止日期为 `null`。AI revision 不是业务版本或快照令牌。

记录结果在 `data` 中包含 `rows/returned/total/hasMore/nextCursor/truncated/truncatedFields/cellWindows/sourceDomain/consistency`。`total=null`，不做无界 COUNT，按 `nextCursor` 续读。分析结果保留原工具的 JSON、覆盖和截断标记，不根据截断行重算总计。

查询对象最多 16000 UTF-8 字节，记录来源响应最多 34000 字符，外层最多 39500 字符及 140000 字节。来源只读事务有 8 秒 SQL 超时、领域请求最多 10 秒；数据集网络调用共用 28 秒预算，每进程最多 2 个执行槽。AI 查询单次最多 30 秒、每轮最多 2 次。繁忙返回 429；参数、权限、签名、审计或来源异常失败关闭，不返回缓存。响应 `Cache-Control: no-store`。取消会中止边缘等待，已发起的有界只读调用可能完成正常审计。

Worker 只做鉴权、签名和薄路由。Django AI 层通过中央执行桥保留工具审计；记录由固定内部签名路径 `/api/ai/dataset-records` 进入所属领域 reader，来源配置不由客户端传入。此路径不是公开 Worker API，不直接暴露 Django 端口。

## 隔离验证与采用

没有新业务 schema、服务、写权限或 authority。**新增列级 SELECT 权限**：各域现有角色 provisioning 调用 `system_datasets.permissions.grant_columns`，按清单配置本域 reader，不扩大为整表授权，不给 AI reader 授予其他域表。重配先撤除这些表上旧列级 SELECT 再按清单重建，保留原有领域整表授权及其他域权限；readiness 检查列授权完整性。

测试覆盖所有模型/字段、全部记录表查询、游标连续分页与篡改/跨账号拒绝、本人隔离、长内容续读、签名、边界及中央注册表一致性。`tools/verify-system-datasets-mirror.py` 固定仅接受 `fixture_owner@127.0.0.1:15468/datasets_test` 隔离库，用 12 个无登录列级 reader 验证 219 个数据集全部字段可读、重复配置幂等、写入及排除字段拒绝。开发测试未使用生产数据；发布后通过真实本机管理员公开 API 有界回查，证据只保存计数、状态和摘要，不保存业务行。

本机已按现有受控流程部署完整 Django 代码、更新全部 12 个 reader 列授权并验证 readiness，采用配套 Worker。线上核验完整目录、全部 219 个记录来源各一条有界查询及拒绝路径；受限角色和本人隔离由隔离测试验证，未创建生产测试账号。聊天入口已核验数据集工具发现，默认文本模型已配置；OpenAI 兼容和 Anthropic 协议的查询、工具结果和表格回传由隔离协议夹具验证，发布验收未调用付费供应商。参数、权限、体积和来源异常分别返回 400、403、413、503，权限不可见的数据集返回 404。缺少代码或授权时来源失败关闭。恢复继续使用当前 PostgreSQL 架构的兼容或前向修复，不恢复 D1 或旧 bridge。
