# 网店过期游标接入实际 v2 采集器：最小实现设计

2026-09-18，网店、ERP 销售与市场三域候选均已接中央采集工具与实际 v2 检查点；没有改变旧游标或持久协议，也没有部署正式服务。以下设计描述保留原问题与实现边界，以本节验收结果为当前状态。

## 当前实现与验收

新reader GET `/api/netshop/analysis-records/continuation`、collection-only工具 `get_business_netshop_continuation_page`、AI `business_collection_continuation` 及 `_collect_v2` 已接通。仅v2网店有未完检查点时启用；工具未就绪失败关闭，旧v1/其他域/首页走原路径。准备读取真实末块及原检查点，原字节SHA、100页长、lastId/nextCursor/sourceRef/revision/范围严格一致；落页仍使用原cursor和父/来源CAS、额度、审计回滚。账号五列含version前后复验。netshop reader安装增加最小五列SELECT并纳入健康检查，无业务写权。

- 网店入口9项及原过期reader8项，隔离PG合计17项通过：`.runtime/ai-pg-a3f68ea917d3/tests.log`。
- 真实AI账本12项通过：`.runtime/ai-pg-1e45ad8c9b02/tests.log`；跨进程transport由测试替换，其余调用实际新owning入口。覆盖过期/未过期、暂停恢复、丢失响应重读、ABA、取消、并发只落一次、审计回滚、容量、版本错位、末块损坏。首次11项中10通过，1项Mock链夹具错误，日志`.runtime/ai-pg-83fd99259a98/failure.log`保留，修复夹具并新增有效cursor后通过。
- 原v1/v2采集与证据回归31项通过：`.runtime/ai-pg-5d6d709b7a41/tests.log`。
- 桥接/注册/原工具/销售退役边界相关Node75项通过：`.runtime/continuation-related-node.log`。新工具7项包括独立HMAC、全JSON恰131072字节/多1拒绝、三种版本错位及非collection拒绝。旧47工具规范字节和96旧工具目录投影摘要保留；实际collection目录明确新增一个工具。
- 构建通过：`.runtime/netshop-continuation-build.log`，相关ESLint通过。不是生产HTTP或真实模型验收；销售/市场续读仍缺。

后续销售和市场切片使用独立工具 `get_business_sales_continuation_page`、`get_business_market_continuation_page` 及各自 reader GET。两域拥有各自 salt、参数、版本和事实查询，不复用网店签名公式。销售要求真实存在的 `sales:erp` 双版本；市场保留精确平台、类目、范围、榜单维度及价格带。三域 continuation 工具都只向 `business_collection` 的无范围管理员开放，模型和聊天目录保持为空。

- 销售/市场 owning、原 reader 回归共 26 项通过：`.runtime/ai-pg-46a3d40a14f7/tests.log`。
- 三域 AI 账本新增销售/市场 6 项通过：`.runtime/ai-pg-8b7513a8f51e/tests.log`。首轮 22 项中销售夹具没有显式建立双版本而被正确拒绝，市场子案受遗留 queued 状态影响；失败日志 `.runtime/ai-pg-ffc09154f7e4/failure.log` 保留，修正合成夹具后通过，未放宽实现。
- 原 collection v2/evidence v2 共 24 项通过：`.runtime/ai-pg-1074fa9129cc/tests.log`；5001 行身份任务 6 项通过：`.runtime/ai-pg-ea24349de185/tests.log`。
- 三域 schema/目录/桥接相关 Node 43 项通过：`.runtime/three-source-continuation-node.log`；销售与市场固定 GET 薄桥及既有服务 31 项通过：`.runtime/other-continuation-edge.log`。
- 三域组合构建通过：`.runtime/three-source-continuation-build.log`，相关 ESLint 通过。

以上仍是隔离 PostgreSQL 与合成跨进程调用。生产的多进程 HTTP、超一小时真实暂停、服务重启后恢复及正式规模耗时未执行；不能据此宣称长任务生产验收完成。

## 当前真实调用链与故障信息

当前调用依次经过：

1. `ai_assistant.business_collection._advance` 取得任务、复验后台创建人，在事务中将任务置为 reading 并递增父版本。
2. `business_evidence.collect` 将 v2 任务交给 `_collect_v2`。它用 `business_evidence_store.catalog/source_record/checkpoint` 读取实际目录与检查点。
3. `transport.execute_tool` 经签名 POST `/api/ai/internal/edge` 请求 Worker 中央工具 `get_business_source_page`，surface 固定 `business_collection`。
4. `lib/ai/business-source-page.ts` 经 `lib/django/netshop-service.ts` 对 netshop reader 签名 GET `/api/netshop/analysis-records`。AI Django 不直接读网店表，也不能直接 import 网店模块调用来代替跨进程边界。
5. `_collect_v2` 在网络返回后进行范围与 PageReconciler 校验，再在 AI mutation 内锁父任务和来源行，将新 chunk 与检查点原子保存。

现在不能在 AI 侧收到 409 后直接续签：

- `netshop.analysis.read_page` 捕获 `signing.BadSignature`；其子类 `SignatureExpired` 也转为同一个 `invalid_cursor/409`，只在 Python `__cause__` 中保留具体异常。
- `DjangoNetshopServiceResponseError` 的 `upstreamCode` 保留该代码，但 public code 会变为 `conflict`。
- `tool-registry-contract.ts` 对普通 PublicApiError 返回 `tool_execution_failed`，只有 RegistryToolError 能保留明确代码。
- `datasets._result` 再把这类错误变为 `service_unavailable/503`。`business_collection._advance` 通常按既有策略重试，达到三次后暂停。故此前“invalid_cursor 直接暂停”的概括不够精确。

本片不更改这条旧错误映射，不解析中文错误文本、不将所有 409/503 认作过期。

## 建议采用的最小纵向切片

仅增加一个后台只读工具和一个 netshop reader 路由：

- 工具建议名：`get_business_netshop_continuation_page`。
- owning 路由建议：GET `/api/netshop/analysis-records/continuation`，仅注册到 `netshop.urls.read_patterns`；writer 角色应返回 404，非 GET 返回 405。不新增 `app/api/netshop/...` 浏览器代理。
- 工具只允许 `business_collection`、无范围限制管理员、direct、read_only。它不出现在任何模型、聊天、MCP 或沙箱目录中。
- `_collect_v2` 仅在 domain=netshop、已有非空检查点、未完成且存在原 expected_cursor 时调用新工具。网店首页、sales/market、旧 v1 均保持原工具与行为。
- 新工具一次请求直接进入上述路由；路由自己判断原游标是否确实过期。不先调用旧工具失败再跨层猜测原因，也不增加每页正常请求次数。

输入为原来源 query 的 `platform/shop/dataset/startDate/endDate/window`、固定 `limit=100`、原始 `cursor`，以及 AI owning 从真实检查点导出的 `expectedSourceRef`、`expectedRevision`、`expectedLastId`。工具 schema 拒绝额外字段，身份、日期、cursor、整数与摘要有界。新路由拒绝重复参数，参数解析不得覆盖原 query。固定 pageSize 为当前采集合同的 100，不能随故障调小或扩大。

这些 expected 字段是额外一致性约束，不是 AI 检查点授权凭据。网店不接受调用者自述的 `authorityVerified`、runVersion 或 proposal 替代真实签名；是否有资格推进某个任务，仍由 AI owning 的任务归属、检查点和最终 CAS 决定。新路由只读，不为调用者保存续签 token 或修改 AI 台账。

## owning 路由对“未过期/明确过期”的处理

1. 校验签名 principal，并在读取前取得真实 `access_control_users` 的 email、role、status、scope、version；要求 active admin、scope=NULL。保留现有 `analysis_cursor._actor` 不允许本地管理员回退的规则，两条分支均使用真实用户检查。
2. 在 `analysis.CURSOR_SALT` 下调用 `signing.loads(cursor, max_age=3600)`，并严格限制载荷恰为 `{binding,lastId}`。须先通过签名；bool 不能当整数。核对载荷与 expectedSourceRef/expectedLastId，核对当前 revision 与 expectedRevision。
3. 未过期：调用原 `analysis.read_page(spec, 100, cursor)`。它继续自行计算包含 masterBatch 的 binding、按源主键读取并做前后 revision fence。
4. 只有捕获到 `signing.SignatureExpired` 才进入 `analysis_cursor.read_expired_page`；该函数再次核验原签名与实际过期条件，在同 salt 下生成仅供本次读取的临时 token，并调用原 `read_page`。坏签名、不同 salt、未知字段、绑定变化一律拒绝。
5. 存在签名检查到实际读取之间刚好越过 3600 秒的竞态。正常分支仅当原 `read_page` 抛出的 `NetshopApiError` 的 code 为 `invalid_cursor` 且**直接 cause 为 `signing.SignatureExpired`** 时，允许调用上述过期专用函数一次；后者仍重新核验。不能因所有 invalid_cursor 或所有 BadSignature 回退。
6. 返回前统一复验 sourceRef、sourceRevision、当前 revision 与真实用户五字段快照。返回原规范页及 X-Netshop-Data-Revision，保持 no-store；不添加可能改变旧页规范的续签字段。

另一实现方式是提取一个私有签名分类器供新路由复用，但必须证明旧公开 GET 的错误、salt、max_age 和成功页字节语义不变。最小首版优先独立路由，避免修改旧 reader。

## 从真实末块构造续读输入

在 `_collect_v2` 中，调用网络前新增 AI 内部准备函数；不读取前端声明的 lastPage，不复用公开 `chunk()` 的可分页切片。

| 字段 | 权威来源及验证 |
|---|---|
| runId / runVersion | 当前已授权 `AiBusinessEvidenceRun`；使用 claim 后当前版本，不能用末页写入时版本 |
| 原目录及 query | `store.catalog(initial)` 重建并验证的 v2 目录；显式 window，不移动日期 |
| sourceVersion / checkpointJson | 当前 `AiBusinessEvidenceSource.version/checkpoint_json` 原始持久字节；`store.checkpoint` 校验计数，再验证恢复字段 |
| 最后 chunk | 精确 `run_id + source_key + sequence=page_count` 查询；使用完整 payload_json，不是 rows slice |
| payloadDigest | `policy.digest(payload_json)` 重新计算原始 UTF-8 字节 SHA，与持久 payload_digest 比较；不得先 JSON.parse/reserialize 再冒充原字节 |
| sourceRef / expectedLastId / cursor | verifier.source_ref、verifier.last_id、verifier.expected_cursor；与真实末块 sourceRef、最后 items 的 rowId、pagination.nextCursor 逐项相等 |
| expectedRevision | checkpoint.metadata.sourceRevision，同时等于完整末块 sourceRevision |
| 页长/未完成状态 | 末块 pagination.limit=100、hasMore=true、非空 items；检查点未 finished、page_count>=1、未耗尽既有容量 |
| 用户快照 | 当前真实 active admin、scope=NULL、权限 version；调用前后比较，不能只检查角色在两次读取中都叫 admin |

准备函数还必须验证末块 pageEvidence.sha256/rowCount、所有 rowId 为有界正整数且严格递增、最后 rowId 与 checkpoint 相等、来源筛选与原 query/window/periods 一致；确认末块确为此来源的末块且无更大 sequence。不要把 lastId 当累计行数，主键空洞是合法的。

查询只读取一个完整末块及固定数量元数据；可先取数据库 octet_length，超过 131072 字节即拒绝，再读内容。检查点原字节最多 32768 字节，目录沿既有最多48来源上限。调用前后用父/来源版本、原 checkpoint 字节建立一致性快照；不能为准备动作读取全部64MiB事实或建立第二事实副本。

已有 `business_analysis.cursor_renewal.prepare/validate` 可用于结构对照，但其 `authorityVerified=false` proposal 不能替代以上真实读取，也不应让 AI 进程解码网店签名密钥。最小运行接线不要求发布或持久化该 proposal。

## 落页、并发和失败规则

新页仍由原 `_collect_v2` 校验 filters/periods、sourceRevision 和完整页摘要。调用 `verifier.consume(page, request_cursor=原 verifier.expected_cursor)`；临时 fresh token 不进入 AI checkpoint，也不改写旧 chunk.nextCursor。

现有 mutation 必须保留：真实权限复验、父 select_for_update 与 expectedVersion CAS、status=collecting、来源 select_for_update、source version 与原始 checkpoint_json 精确比较、2000总页/64MiB及用户全局额度、append chunk 后原子更新检查点、审计失败回滚。新增实际用户 version 快照复验，避免撤权再授予的 ABA；检查 paused/cancelled 的父版本变化不能被迟到响应覆盖。

首版不改 `business_collection.control(resume)`：它继续只将调度置 queued，真正续读在下一次 `_collect_v2` 中按当前版本重新准备。响应丢失可重读原源只读页，最后只有一个 CAS 成功插入；不能重发已执行的模型、通知或业务写入。

网络/部署暂时不匹配沿既有有限重试策略；新的 owning 签名错误或源版本变化至少保持失败关闭，不能吞错后从首页接续。若后续希望明确显示“来源变化，需新任务”，可为**新工具**添加限定 RegistryToolError 与 AI 专用解包，不全局放宽 datasets._result。本次最小正确续读不依赖该错误显示优化。

## 注册、权限和版本清单

| 位置 | 最小修改或核验 |
|---|---|
| `lib/ai/tool-registry.ts` | 新 schema/handler、admin/unscoped_only、read_only、仅 business_collection；完整审计，最多一次续读调用 |
| `lib/ai/tool-registry-contract.ts` | 当前131072字符例外硬编码只认 `get_business_source_page`；把新确切名称纳入同样严格的 surface/role/risk 条件，不能给所有 read_only 工具扩大容量 |
| `lib/ai/django-edge.ts` | business_collection 已在 surface allowlist；不增加新 surface/内部 action。核验新名仅经过中央注册表 |
| `lib/ai/business-source-page.ts` 或独立新 handler | 新 handler 使用真实 principal、取消信号、固定 owning path及131072 UTF-8字节上限；不修改旧 handler |
| `lib/django/netshop-service.ts` | 新路径加入精确 STATIC_PATHS；只允许 reader GET，不扩 writer POST；验证版本响应头与页 sourceRevision 一致 |
| `backend/netshop/urls.py` / 新 view | 仅 reader GET，真实 HMAC、完整参数与用户复验；无浏览器公开代理 |
| `business_evidence._collect_v2` / 新准备模块 | 只有v2网店已存在未完检查点使用新工具；catalog必须包含此能力，缺失失败关闭，不能降级到绕过检查点的本地调用 |
| runtime grants / readiness / rehearsal | `analysis_cursor` 读取真实 AppUser五列，须验证 netshop_reader 的 SELECT(email,role,status,scope,version)；当前 `_validate_netshop_schema` 未核这些列。若缺少则补最小列授权和健康检查，不授予用户表写权或 AI 事实表读取权 |

现有 `aiToolSurfaces` 已含 business_collection，provider工具输出函数对该 surface 返回空列表，继续保持。AI tool audit 的 tool_name/surface 为文本字段；本次检索未发现新工具名需要修改的数据库枚举。`ai_assistant.0019_business_source_directory` 的数据库触发器却**精确约束** collector 为 `{version:1,surface:business_collection,pageSize:100}`；因此不要为了代码适配改 collector.version，否则将牵涉持久协议、迁移和旧文件重放。

这个切片可保留 v2 header、原 fact page schema、cursor salt/payload、旧 checkpoint 格式和现有表；原则上无需新增数据表或修改已应用迁移。若新增权限须采用既有受控授权安装途径并在隔离实际角色验证。工具注册表 policy digest 自然变化，任务本次从实时 catalog 得到新摘要；不能改旧审计。

正式采用仍需 Worker 与 netshop/AI Django 一致的候选代码和权限。代码在同一个仓库不表示已在各独立进程上线，不能只测 development 混合 URLConf。此文档不授予部署、重启、补跑或模型调用权限。

## 实施顺序与验收

1. 网店 reader 新路由及最小权限：真签名未过期/明确过期两页、坏签名/不同盐/查询/limit/revision/masterBatch变化、真实角色 reader200/writer404/POST405、用户中途撤销或version变化。保留现有公开 GET 测试及8项内部续读测试。
2. 中央工具与桥：目录唯一、OpenAI/Anthropic schema一致、handler存在、所有非collection surfaces拒绝、新路径精确签名/无重定向/取消/字节与时限/审计失败；不扩大其他工具容量。
3. AI owning 适配：真实末chunk和checkpoint准备、损坏原字节/末行/计数/目录拒绝、父和来源CAS、权限ABA、取消/暂停/并发推进、超额度与审计回滚，均用隔离PostgreSQL。
4. 端到端：真实原签名过期 → 后台tick → 新工具 → owning reader → 一页原子保存 → 完整封存与旧页重放对账。先覆盖一次响应丢失再恢复，验证旧chunk字节不变、无重复行、无额外事实/模型调用、未改变日期与页长。旧v1、sales/market及无checkpoint首页均维持原路径。

本设计只完成网店事实游标恢复的可实施范围。市场/销售事实、来源选项目录游标和真实模型长任务仍分别处理，不凭本切片宣称全部长任务恢复完成。
