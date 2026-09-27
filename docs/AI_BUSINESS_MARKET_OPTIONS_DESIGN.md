# 市场权威来源选项：下一切片设计

2026-09-23 主线整合说明：当前主线已采用 `market.0005_filter_facet_indexes`，其四个筛选索引及模型声明必须保留。候选来源选项迁移顺延为 `market.0006_analysis_options`，明确依赖该 `0005`；升级/独立恢复演练以已应用 `0005` 的库为旧基线，备份与维护证据核对新的迁移回执及前置索引。下文 2026-09-17 至 09-18 的 `0004→0005`、批次和测试记录均指整合前的候选版本，保留作为历史证据；不能直接视为整合后 `0005→0006` 的验收结果。

2026-09-17。本文件是候选开发设计，**整体 owning 服务尚未实现或验收；已完成的独立纯合同见第10节**。依据当前 owning reader、导入与权限代码；未查询生产、运行 PostgreSQL、调用模型或变更服务。主线先收口第48–52批，再按本设计推进。纯合同容量边界测试不是已测数据库吞吐或正式规模承诺。

## 1. 选择市场先行

现有网店选项已经提供精确身份、有界分页和工作台账号封套。下一域优先市场，不同时展开 ERP：

- [market/import_service.py](../backend/market/import_service.py) 的 `validate_import_payload()` 从最多5000条导入行构造去重 `scope_json.ranges`，并检查提交的 scope 与这些范围完全相等。每项具有 `category/scope/rankingDimension/priceBandFilter/periodStart/periodEnd`。成功发布可提供目录索引的元数据来源。
- [sales/analysis.py](../backend/sales/analysis.py) 要求精确 `(platform,shop,channel)`，且原字段与 `platform_key/shop_key/channel_key` 相等、`is_business_row=True`。现有销售批次不含完整三元组；ERP 必须另外设计事实投影初始化，不能用页面别名、渠道配置或网店同名身份推导。
- [market/query.py](../backend/market/query.py) 的独立 facets 不能拼成真实组合，价格分类标签也不等于导出时的 `price_band_filter`。新 GET 不复用这些事实查询来发现身份。

本片只减少范围输入错误。不增加自然语言选范围、Agent 工具、付费报告启动、店铺去重 UV、预算来源或新事实覆盖证明。

## 2. 精确身份和日期语义

每条选项的身份固定为：

```json
{"platform":"京东","category":"精确类目","scope":"精确范围","rankingDimension":"SKU","priceBandFilter":"导出时原始筛选值"}
```

`rankingDimension` 允许 `SKU/SPU`，没有 shop。固定 `source=sourceDataset="market_daily_top"`。只纳入 `periodStart == periodEnd` 的合法单日榜范围，排除周/月榜；不将重叠区间拆成推测的逐日记录。

`priceBandFilter` 保留成功导入范围的原值。禁止用确认价格、动态价格带、“未确认价格”标签、当前商品售价替换；禁止构造独立 facets 的笛卡尔积。字符串按现有分析读取器的有界精确身份规则检查，不 trim 后悄悄改身份、不截断、不合并大小写或 Unicode 不同编码。候选项还须能够原样通过当前规划器与市场 reader 的支持性校验。

日期仅返回：

```json
{"kind":"published_import_envelope","firstDate":"2026-01-01","lastDate":"2026-09-16","snapshotDate":null,"coverageVerified":false}
```

它表示**历史成功发布的单日范围包络**，不是当前事实日期集合、所选区间完整性、每商品逐日完整性或全市场覆盖。替换导入、后续事实变化不应将此历史目录冒充当前快照。日期未知应明确 null；非法日期、倒置或损坏元数据不能静默变成 null。

TOP 排行始终为样本。市场选项不可作为现有预算目标；[business_budget.py](../backend/ai_assistant/business_budget.py) 当前预算目标仍限定本期 netshop promotion，不扩口径。

## 3. 最小持久索引与初始化

建议新增市场自有两类模型，命名在实现时最终冻结：

1. `MarketAnalysisOption`：目录代次、精确四字段组合（平台固定）、历史单日首尾日期及规范行摘要；唯一键包含代次与完整身份。不得只凭短哈希合并身份，摘要冲突也须核原值。
2. `MarketAnalysisOptionsState`：单例或固定域状态，至少记录当前可读 generation、目录 digest、初始化是否完整、ready/building/blocked 状态和有界失败原因；记录当前构建身份及源版本围栏。ready 不能仅由“存在一些选项行”推导。

GET 只读这两类元数据及原市场 revision。不得读取 `MarketRankingEntry`、金额、商品明细、客户信息，或在每个请求展开全部批次 JSON。

### 历史初始化

独立 management command 按 completed 批次主键分块读取现有 `scope_json.ranges`，校验每个有界元数据对象和规范日期。它属于受控初始化作业，不能藏在 GET、Django 启动或迁移默认执行中。作业先记录源 revision，完整扫描和对账后，只有源围栏仍一致才原子切换当前 generation。

- 中断、源版本变化、漏批次、旧 scope 缺少真实范围或元数据损坏时，不发布部分目录。
- **历史初始化未完整完成，必须返回 `503 options_not_ready`，而不是200空页。** 若实现中使用更细的 `invalid_source_metadata/blocked_capacity` 原因，应保持“不宣称目录完整”这一状态。
- 对能证明为不支持的周/月榜可确定性排除；无法判定含义的旧元数据不能当作已完整排除。
- 不允许首次初始化临时回退全事实扫描。旧范围不足时，另立授权和资源有界的修复/重建任务；手填入口继续存在。
- 重建和清理必须限定本域、本代次；不能删除事实、导入批次或其他业务表。清理旧代次是独立受控动作，保留恢复依据，不在 GET 自动执行。

### 正常发布更新

在 `import_service` 的实际成功发布事务内，利用已规范的 ranges 更新历史目录。重复导入只按实际 publication 语义处理，不额外捏造新身份。失败/处理中批次不进入目录。

来源发布和目录更新须同事务一致；若采用“目录容量不足不阻塞业务导入”的产品策略，必须在同一事务将目录明确标记 blocked，之后 GET 停止提供当前可选页，不能继续冒充同步成功。不得吞掉索引失败后仍返回 ready。实施前固定该取舍并用失败回滚测试确认。

首版可评估1万身份、16MiB规范索引载荷的档位，但这只是建议。必须根据实际字符串上限、5000行单次导入范围上界、历史元数据量和合成压力确定最终常量；另定初始化暂存空间、分块行数、超时及代次保留数。载荷字节不等于 PostgreSQL 磁盘占用或进程 RSS。超限明确停用目录或拒绝发布，不取前N身份冒充全部。

## 4. 市场 revision 与目录 generation

两种版本不同：

- 市场全局 `revision_value()` 是 owning 域版本，非导入操作也可能改变它。
- 目录 generation/digest 证明选项索引的实际内容与初始化状态。

不能要求“索引建立时的全局 revision 永远等于当前 revision”，否则无身份变化的市场操作也会永久禁用目录。初始化提交可保守要求全局 revision 不变；正常读取则同时绑定**当前**全局 revision 与当前 ready 目录 generation/digest，前后复验任一变化均拒绝当前页并要求从首页重读。

游标不得仅绑定 generation 而忽略权限或查询；也不得拿全局 revision 代替目录完整初始化标志。建议响应保留原 `X-Market-Data-Revision` 格式，并在 provenance 中明确目录 generation/digest，不修改已有市场服务 revision 协议。

## 5. 独立 GET 合同

建议 `/api/market/analysis-options`，仅 market_reader/development 路由注册，writer 不注册 GET。不新增消费者 operation 或模型目录，避免扩大旧工具合同。

请求参数允许 `platform/category/scope/rankingDimension/priceBandFilter/q/cursor/limit`：

- platform 若提供只能为京东；其余身份过滤为精确原值，q 为最多100字符的搜索提示，不改变结果身份。
- limit 省略或文本 `20`；重复、未知、空参数及非规范类型明确拒绝。
- 查询文字按身份上限校验并拒控制字符/代理字符。cursor 是不透明签名串，给定长度上限，不能接受客户端自报 last tuple。

按完整四元组使用 PostgreSQL `C`/纯测试 `BINARY` 一致排序和 keyset `LIMIT 21`，返回20条，不做全库 COUNT。新索引必须支持相同排序与谓词；q 搜索的最坏计划另测，不放宽原SQL超时。每页唯一性由完整身份保证，下一页沿签名游标，不先加载全目录到 Python/浏览器去重。

签名游标1小时，独立 salt，绑定：规范查询、principal 邮箱/角色/scope摘要、当前 market revision、目录 generation/digest、固定页长、上一完整身份。过期/变更返回409，不能自动从首页与旧页混合。

响应可以沿现有 `business-analysis-options-v1` 结构新增严格 market 域分支：`domain/revision/query/queryDigest/items/pagination/pageDigest/limitations`。每项包含 `optionKey/identity/source/sourceDataset/dateMetadata/provenance`；optionKey 根据完整规范身份重算，pageDigest 覆盖除自身外完整规范页。最终字段必须先冻结再实现 TS 校验，不为兼容市场而放宽网店验证器。

完整响应不超过38000 UTF-8字节，单项/整页超限明确拒绝，不截断名称、行或元数据。只有 ready 目录在当前筛选确实无命中时才合法返回200空列表。无权限403、未就绪503、损坏/游标过期409、无命中200必须可区分。

## 6. 权限和前端接线

领域接口复验实际 `role=admin && scope is None`，不借市场普通页面的宽权限。沿当前签名身份和 reader URLConf；没有签名、旧权限、有限scope均不能查询目录。生产角色最小授权：reader 对新元数据仅 SELECT，writer 仅有索引维护所必需的权限；不因 `FOR UPDATE` 误给控制表 UPDATE，不使用 SECURITY DEFINER 绕过权限。

建议后端完成后，再新增 `app/api/market/analysis-options/route.ts` 与精确 service allowlist。AI 工作台另加 `/api/ai/business-plan/market-options`，采用现有网店封套策略：一次取得实际登录 principal，严格 expectedPrincipalKey，账号不符时零领域请求；同一 principal 签名转发，并保留原页完整摘要。未知/重复参数不得在薄桥转成 object 时被去重。

独立 helper/picker 核验域、身份、版本、完整SHA和分页。父级只在用户选择并重读确认后向最新表单的 markets 添加该完整组合，不改日期、窗口、店铺或 ERP 渠道；最多7个市场组合及展开48来源门禁仍取当前规划器，超限完整拒绝。保留手填、未知写入冻结body、账号切换取消、409重读和迟到响应隔离。

## 7. 文件范围与开发顺序

| 切片 | 具体文件范围 |
| --- | --- |
| 市场投影及历史初始化 | 新 `backend/market/analysis_options_projection.py`、management command；修改 `backend/market/models.py`、`import_service.py`；当前迁移止0004，可提议新增0005，实施时确认名称与依赖 |
| 只读选项接口 | 新 `backend/market/analysis_options.py`、`tests/test_analysis_options.py`；修改 `views.py`、`urls.py`，增加投影/初始化专用测试 |
| 数据库边界 | `tools/django-market-service.ps1` 的真实表清单/grants；`backend/teruisi_backend/health.py` 的结构与角色检查；相应工具测试与隔离PG验证 |
| 备份和历史兼容 | `tools/postgres-consistent-backup.py`、`tools/django-postgres-maintenance.ps1`，及当前实际快照清单；`backend/market/management/commands/migrate_market_from_d1.py`、`retire_market_d1.py` 的历史边界需逐项审查 |
| 薄桥和工作台 | `lib/django/market-service.ts`，新增市场GET/AI账号封套路由、market专用helper与picker测试；最后最小修改工作台父级，不重写网店picker或手工规划器 |

先冻结身份/目录状态/发布策略，再完成后端索引+GET+受限角色和恢复验收；随后桥/helper、独立组件、父级分别验证。不要同时建设ERP投影或自然语言解析，以免无法明确单域完成条件。

## 8. 备份恢复与旧 D1 边界

新表归 market，不扩AI数据库权限。备份清单、结构健康和角色验证必须包含它们；恢复后须核 generation、目录摘要、初始化状态及来源围栏，不能只凭表存在返回 ready。

旧备份和旧D1导出缺新表属于历史版本，不能直接视为损坏；它们通过原版本结构/摘要验收后，升级新schema得到 **not_ready** 状态，再执行独立完整初始化。**旧D1还原或添加空表绝不自动ready**。历史 scope 元数据不足时保持未就绪，不自动扫业务事实弥补。新备份独立恢复要证明旧业务表摘要不变、新目录摘要与状态一致、reader/writer权限仍隔离。

逆迁移或代次清理不得悄悄丢弃已经使用的目录依据；具体逆迁移阻断条件与恢复流程在迁移实现前冻结。不要把候选迁移/初始化设计视为生产迁移、维护或历史重采授权。

## 9. 最小验收清单

- 精确组合：同类目不同scope、SKU/SPU、原始priceBandFilter独立；空值/控制符/长Unicode/同名不合并；日期非法拒绝，周/月榜排除。
- 初始化：完整空源、部分完成、缺批次、坏旧scope、并发导入、事务回滚、进程中断、容量超限；未完整初始化始终not_ready。
- 发布：completed才纳入、失败/处理中排除、重复与替换导入、目录故障不冒充同步成功；非导入全局revision变化可重新读取目录，不永久禁用。
- 分页：20+身份跨页无漏无重、C排序、查询变化/跨账号/权限变化/过期/篡改游标；响应UTF-8边界和空页语义；GET无事实表SQL、无写入、无模型或远程业务调用。
- 实际角色与恢复：真实restricted reader读取成功、writer无GET、reader禁止写；新旧备份独立恢复、旧D1升级not_ready、初始化后摘要核验；不只mock权限函数。
- 前端：账号切换、取消、连续筛选、坏页、迟到选择、409重读、日期不变、精确组合逐项添加；目录故障仍可手填；7市场/48来源展开及超限拒绝，不以20项分页代表来源上限。

合成 SQL/容量测试不替代正式只读执行计划和真实历史元数据验收。完成本片只能声明“市场精确来源选项可选择”，不能声明全市场数据完整、自然语言范围已理解或五阶段全部完成。

## 10. 已实现的独立纯合同边界（尚未接入服务）

候选新增 `backend/market/analysis_options_contract.py` 与 `test_analysis_options_contract.py`，不导入 Django、不访问数据库、不注册 URL、不更新导入、目录或权限。上述 owning 服务、迁移、历史初始化、签名游标、备份及工作台仍是设计，不能因纯测试通过宣称已经可用。

接口固定如下：

- `normalize_completed_scope(scope, status=...)`：接受 `completed` 标签及导入规范精确两键 scope；逐项检查全部非空、规范排序且去重的六键 ranges，返回 `authorityVerified:false/entries/rangeCount/excludedNonDailyRanges`。只合并单日范围，相同身份日期取最小/最大，合法非单日范围显式排除。`completed` 字符串本身不证明真实发布；实际调用者必须从 owning 批次取得并复验。
- `normalize_identity()`：精确五字段 `platform/category/scope/rankingDimension/priceBandFilter`，京东、SKU/SPU，不去空格、不做 Unicode 归一或价格别名；未知字段拒绝。
- `merge_entries(existing,incoming)`：完整、稳定排序的历史身份包络合并，返回独立 DTO，不在失败时发布前缀。`directory_digest(entries)` 只对已校验的整个 DTO 列表计算规范摘要，不证明目录已经初始化完整。
- `normalize_query()` 与 `make_page()`：仅验证纯查询及完整20项以内页面。固定 `business-analysis-options-v1/domain=market/authorityVerified=false`；顶层 `directoryGeneration/directoryDigest` 让空页也绑定这两个输入，每项 provenance 再含 generation/digest。所有日期 `coverageVerified=false`；`pageDigest` 覆盖除自身外完整页。revision、generation、目录摘要和不透明 cursor 都只是调用者提供并经过格式检查的值，未来 owning 层仍须核其真实来源、当前权限及签名。纯层没有 ready 标志。

本片固定并纯测：每批最多5000 ranges、合并目录最多10000个身份及16MiB规范 JSON、每页20项/38000 UTF-8字节。16MiB指规范载荷，不是 Python RSS、PostgreSQL索引或临时初始化磁盘。宽身份可能先触发字节上限，不能承诺10000个最长字符串身份都可容纳。整页包括摘要、版本、provenance 和 cursor，超限全部拒绝，不自动缩页或截断身份。

与旧代码的差异已明确保留：导入 `validate_import_payload()` 日期只校验形状及先后，可能存在旧 `2026-02-30`；纯目录要求真实日历日期。旧导入文本可能包含 Cc/Cs 字符，新目录拒绝这些字符及非规范首尾空格。因此旧批次可以在导入层曾合法、在新目录层却不可用；这必须让历史初始化失败或标记 not_ready，不能静默丢弃之后声称目录完整，也未修改旧导入和分析 API 的接受范围。

纯测试直接抽取现有导入规范化和 analysis.validate 的原函数 AST 做格式对照（不启动 Django/数据库），覆盖合法真实导入形状及旧坏日期差异；另验证5000/5001范围、10000/10001身份、7000宽身份触发16MiB、页面恰好38000字节与多1字节拒绝、非单日排除、缺失/空/乱序/重复历史范围、原始价格筛选独立、深层/循环类型拒绝、摘要绑定及别名隔离。这些是合成纯合同证据，不是历史数据库完整性、权限、性能或生产容量验收。

## 11. 第54批 owning 候选实现（2026-09-18，隔离验收待完成）

本节记录第54批已落盘候选代码，替代第3节中尚未定稿的存储实现选择。第53批纯合同保持原样。未部署、未执行正式迁移、未初始化正式目录，也未接入市场选择器前端或新模型工具。

### 存储与实际发布路径

候选 `market.0005_analysis_options` 依赖 `0004_projection_sync_fencing`，新增 `market_analysis_options` 和 `market_analysis_options_state`。采用**唯一当前索引 + 单例 state 的 generation**；没有多代索引保留。重建或导入同步在同一事务原子替换当前索引并更新 generation/digest/count/bytes。GET 前后复验 state 和全局市场 revision，拒绝混合两代数据。

`analysis_options_projection.prepare_rebuild(principal)` 只允许事务外调用，读取真实 completed 批次元数据，逐批完整核验；最多10000批、原始 scope 总64MiB、单批16MiB，并继续受每批5000范围、目录10000身份/16MiB规范载荷约束。范围数不能大于该批成功行数，sourceType须等于批次原值，completed_at须存在。任何坏批次、缺失范围、超容量或前后 revision/权限变化均失败，不返回可发布的部分结果。

准备结果是进程内 `PreparedProjection`，不能从客户端 JSON 恢复。`publish_rebuild(prepared,principal)` 复验实际写入 authority、真实账号与准备绑定，取得原 `MarketDataRevision` 行锁后核验来源 revision，才在同一事务发布全部索引和 ready 状态。此内部函数不是公开管理 API，也没有自动执行的 management command；当前发布/初始化只能由后续受控 owning 调用，不能向浏览器暴露准备胶囊。

实际 `import_market_payload()` 成功分支在 `bump_revision()` 后、原事务提交前调用 `synchronize_import(scope,revision)`。锁序为 revision→options state，和显式重建一致。原目录未初始化或已 blocked 时，新批次不会自动宣称历史完整。原目录 ready 时先核全部有界元数据及目录摘要，再合并新批次原范围；坏范围、损坏目录或超容量使该目录同事务变为 blocked，不将新来源静默遗漏，也不拒绝原来合法的业务导入。其他数据库异常仍导致原事务回滚；未吞掉写入失败。同步只处理目录元数据，但当前实现在导入事务内完整替换最多10000条索引，正式时延尚未测量，不能将其表述为恒定耗时或已达到生产性能目标。

### 只读 GET 与实时授权

`GET /api/market/analysis-options` 只在 market_reader/development 注册，market_writer 不提供该 GET。读取仅访问新索引/state、市场 revision 和 AppUser 的五列授权元数据；不读取市场事实、历史批次、销售明细或 AI 表。每次读取前后均验证实际 active/admin/scope=None，并绑定账号 version；无真实 AppUser、停用、撤权、受限范围或本地保留身份没有记录时返回403，不回退免登身份。

参数为第5节严格字段集，limit省略或字符串20。精确身份筛选与四字段 keyset 使用 COLLATE C，SQL LIMIT 21，返回最多20项，不在 Python 加载全目录。搜索使用规范持久 `search_casefold` 与 `q.casefold()` 的 C contains，避免 PostgreSQL icontains 与 Python Unicode casefold 对 ß/İ 等处理不一致；字段之间使用查询不允许包含的换行分隔，防止跨字段拼接伪匹配。输出的原始身份不做大小写归一。每项回读会核原列、规范 entry JSON、行摘要和搜索投影完全一致。

1小时签名游标绑定精确查询、实际账号邮箱/role/scope/version、当前市场 revision、generation、目录 digest、limit20以及最后一项完整身份。账号变更403，游标/查询/来源代次变化409，未初始化或blocked503，只有完整初始化后的真实无命中为200空页。非导入引起的全局 revision 变化只作废旧游标，不永久禁用已建立目录。

owning 成功页将纯 DTO 的 `authorityVerified` 改为 true 并重新计算 pageDigest；它仅表示**该次已授权历史目录读取**，所有 dateMetadata.coverageVerified 仍为 false。最终实际 HTTP JSON 使用同一规范序列化，完整响应≤38000 UTF-8字节，超限整页拒绝，无名称截断或部分前缀成功。

### 部署、备份与权限配套文件

- `tools/django-market-service.ps1`：reader对两表仅SELECT；writer对索引SELECT/INSERT/UPDATE/DELETE及其id序列USAGE，对既有单例state仅SELECT/UPDATE。两角色新增 access_control_users 的 email/role/status/scope/version 五列SELECT，无整表用户权限、权限写入或AI表授权。
- `backend/teruisi_backend/health.py`：检查新表字段、选项索引与约束，以及真实五列SELECT权限；未初始化目录不等于整个市场服务不健康。
- `tools/postgres-consistent-backup.py` 与 `tools/django-postgres-maintenance.ps1`：只在备份实际迁移含0005时要求两新表及0004依赖；旧0004备份缺两表继续合法，出现新表却无0005依据则拒绝。
- `market/management/commands/migrate_market_from_d1.py`：真实D1 apply完成业务数据迁移时，同事务清除旧PG-only选项索引，并把state置not_ready；不从D1导入推断新目录已经完整。未来显式初始化仍须全量历史元数据核验。
- 已初始化或有选项数据时拒绝直接逆迁移；需要独立明确的受控重置。当前没有提供自动清理/重置命令。

### 当前验证状态与未完成项

暂停前无数据库检查已完成：Python编译通过、Django system checks为0、migration state autodetect无market漂移。专用隔离测试标签 `market.tests.test_analysis_options.MarketOptionsOwningTests` 共11项已经收集，包含真实导入→初始化→两页GET、无事实SQL/无写入、Unicode搜索、撤权/跨scope/本地身份拒绝、游标版本/过期、初始化失败、同步回滚、损坏索引和真实受限reader/writer最小权限。暂停时这11项尚未执行；2026-09-18恢复后的实际结果见下方复测记录，现已全部通过。测试代码存在本身不代替真实角色验收。

根任务已统一完成隔离PostgreSQL、旧market回归、0004→0005升级/备份独立恢复和真实角色验收，详见下方日志。正式历史元数据是否足以初始化、正式执行计划/导入耗时、工作台选择器及真实业务覆盖均未验收；本片不声明五阶段全部完成。

### 第54批首次隔离结果与修正记录

2026-09-18，根任务执行11项隔离 PostgreSQL：9通过、2失败，耗时30.173秒，日志 `.runtime/ai-pg-727c937310ba/failure.log`。失败原因已定位，不能将首轮写为通过：

1. writer 路由断言的测试只 reload 子URLConf，根 `include()` 仍捕获此前reader的 urlpatterns 列表。测试现同时 reload 子与根URLConf并恢复，额外断言writer路由集合无analysis-options；实际冷启动角色注册未改。
2. 旧导入替换循环用 `scope` 表示一项榜单标签，覆盖了同函数此前的完整规范scope对象。新同步改为显式传 `normalized['scope']`，并断言真实导入调用收到完整对象、目录保持ready、两项精确身份均保留。原event/fingerprint使用该既有变量的历史行为未在本片更改，避免无关摘要变化；这项旧shadowing问题应单独评估。

两处修正编译和差异检查通过，随后完整11项重跑及独立升级恢复均已通过，见下方记录。实际受限reader/writer测试也在首轮其余9项中通过；该单项结果本身不替代完整回归。

### 第54批隔离复测更新

2026-09-18，根任务复跑新 owning 11 项全部通过，31.040 秒，日志 `.runtime/ai-pg-a3e180821043/tests.log`；旧 `market.tests.test_api` 与 `market.tests.test_analysis` 共22项全部通过，0.917 秒，日志 `.runtime/ai-pg-c3b0fde09a41/tests.log`。这些结果覆盖实际受限 reader/writer 权限，不是用纯合成 DTO 代替授权验收。前述首轮失败记录仍保留。

独立升级/恢复首轮在完整备份收集前发现合成库缺 sales/erp revision 控制夹具，日志 `.runtime/ai-pg-14ca2344ae68/failure.log`。候选 harness 已补齐各域空库控制前提、市场迁移记录 ID 格式及原生 PostgreSQL 工具显式连接绑定，未削弱备份 collector；根任务随后重跑通过，实际证据见下段。

独立市场 GET 边缘转发已经写入候选代码，仍只转发完整 owning 协议；工作台授权封套及来源选择器 UI 尚未接入。正式历史初始化、正式性能、真实业务覆盖和生产采用均未验收。

### 第54批升级恢复与边缘转发最终候选证据

根任务隔离重跑 `.runtime/ai-pg-05136156c352/market-options-upgrade.json` 为 passed：market.0004→0005 前后40张旧市场表行摘要一致；新两表不自动初始化，显式 owning 重建发布1项合成身份，初始化后逆迁移明确拒绝；旧0004备份依据继续通过。独立 pg_dump/pg_restore 后，全部备份表 evidence、市场旧新表行摘要及重新切换 Django 数据库后的真实 owning 页完全相等。该夹具使用规范成功历史批次元数据，验证目录迁移/恢复，不能冒充正式历史数据完整性或事实日期覆盖。此前 sales/erp revision 缺失属于完整备份夹具前提遗漏，已补足各域控制状态而非降低 collector 校验。

新9项边缘转发与旧10项市场/签名回归共19项通过，日志 `.runtime/batch54-market-edge.log`；4个相关 TypeScript 文件 lint 通过。已实现固定只读 GET 桥与完整参数签名、reader路径、权限、取消、38KB响应及 revision 头体一致校验；工作台授权封套和选择器 UI 尚未接入。以上均为候选隔离验收，未执行正式迁移、正式历史初始化、生产发布或付费模型调用。
