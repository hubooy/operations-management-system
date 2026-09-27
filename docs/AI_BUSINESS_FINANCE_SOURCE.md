# 月度财报证据源与店铺 UV 边界

2026-09-18。只读代码核查与纯合同候选；未查询正式数据、未运行 PostgreSQL、未接公共 API、模型工具或经营报告。存在代码和字段不代表当前店铺已导入有效财报。

## 已有来源与可复用部分

| 来源 | 本地证据 | 实际粒度与限制 |
| --- | --- | --- |
| `finance_lines` / `FinanceLine` | [模型](../backend/finance/models.py)、[导入](../backend/finance/import_service.py) | 月 × `section` × `scope_key` × `subject_name` 唯一；`section=summary/kingdee`，范围为事业部/组/店铺。保留 `metric_key`、范围名称/分组、金额分及比率基点的 NULL、原文、来源行数、合计标志。没有订单、SKU、客户或日粒度。 |
| `finance_months`、`finance_import_batches`、`finance_data_revisions` | 同上 | 当前每月对应一个批次；完成导入在同事务按月替换旧行，发布完成状态与数据 revision。批次有规范内容摘要、原文件摘要及发布状态 token。旧事实行不作为历史快照保留，证据采集必须固定 revision 并封存实际行。 |
| 财报解析器 | [parser](../lib/finance/parser.ts)、[规范导入](../lib/finance/normalized-import.ts) | 月份工作表中的经营汇总与金蝶科目；人民币元转整数分，比例转基点。已识别销售额、退货、实际销售、发货/退货成本、包材、大/小毛利、运营/工资/其他费用及利润等。空单元格可能根本不生成行，不能解释为零。 |
| `finance_targets_scoped` | [模型](../backend/finance/models.py)、[年度进度](../backend/finance/annual_progress.py) | 月/年/项目目标，平台+店铺+品类身份；是规划目标，不是财务实际。首片不混入来源事实。 |
| `GET /api/finance/analysis` | [路由](../backend/finance/urls.py)、[views](../backend/finance/views.py)、[analysis](../backend/finance/analysis.py) | 无范围限制的已认证用户，经 finance reader 签名与前后 revision 栅栏读取。最多选择24个月，月份选项120、店铺500、平台100，部分费用/目标列表存在截断标志；可显式选择平台和稳定店铺复合键。是页面视图，不是无损证据。 |
| 既有 AI 财报问数 | [page-data-tools](../lib/ai/page-data-tools.ts) 的 `projectFinanceAnalysis`；[工具目录](../lib/ai/tool-registry.ts) 的 `get_finance_page_data` | 投影并限制费用/店铺等数组，不含完整账本、封存链及逐行引用。保持原工具定义和摘要，不冒充新来源。 |
| 通用逐行数据集 | [manifest](../backend/system_datasets/manifest.json) 的 `rows_finance_lines/months/import_batches`；[reader](../backend/system_datasets/reader.py) | 只向无范围限制管理员提供受限列/条件、加密游标和字段截断元信息；游标绑定账号/查询，未固定 finance revision，不能直接当跨页一致的财报证据采集器。 |

页面 `_metrics` 会把缺金额归零、用默认零计算部分比率或推导利润；新证据不能反向从此页面还原原始空值。经营总计与店铺汇总必须选一层，不能把事业部、分组、店铺相加；`summary` 的销售费用与 `kingdee` 的销售费用也是两份口径，不是两笔费用。

另有必须披露的既有导入边界：`parser.buildDimensions` 的店铺 `scopeKey` 由店名生成，`aggregateLines` 以 section/scopeKey/科目合并，未包含 group；不能据此保证跨组同名店尚可拆分。解析器还可能按明细重算金蝶销售费用合计。新合同读取的是**现有规范化账本值**，不是原文件单元格的重新审计；`source_row_count` 与合计标志照实保留。以后 owning 接入不能猜平台别名或从已合并金额恢复跨组拆分；需要源文件层单独核验有歧义的身份。这次没有改变导入口径。

## 本片已实现的纯合同

文件：[finance_source.py](../backend/business_analysis/finance_source.py)、[测试](../backend/business_analysis/test_finance_source.py)。

```python
build(rows, *, query, revision, months, batches,
      analysis_period=None) -> FinanceSource
FinanceSource.manifest  # 独立副本
FinanceSource.page(offset=0)  # 独立副本；只能使用实际返回的 nextOffset
```

- `query={months:[YYYY-MM,...], scope:{scope_key,scope_type,scope_name,group_name}}`：精确一个财务范围，1—24个按升序连续自然月，不推测网店/ERP别名。跨店由以后固定计划分别选择独立来源。
- `revision={revision,source_digest}`：来自 `FinanceDataRevision` 的原标量投影；整数与64位十六进制摘要严格校验。纯层不证明其真实性。
- `months=[{month,batch_id,status}]`、`batches=[{id,status,content_hash,raw_file_hash,published_state_token}]`：只投影已发布选定月份和其唯一批次；缺月允许成为缺口，未完成、重复、未引用或缺批次拒绝。历史批次若缺有效摘要，后续 owning 层应明确拒绝作为此来源，不填造摘要。
- `rows` 单次迭代每行读取 `FinanceLine` 的明确16字段投影（含 id、month、section、metric_key、subject_name、scope四字段、value_type、amount_cents、rate_bps、raw_value、source_row_count、sort_order、is_total；以源码 `ROW_FIELDS` 为准确清单）。生产接入应由 owning reader 按 `id` 升序提供。输入顺序参与链摘要，不能把乱序结果解释成同一制品。
- 每行保留源值并增加绑定 revision+整行内容的 `rowId`。重复持久 ID、重复业务身份、越范围、超安全整数、未知字段、非法类型或迟到异常均使整个构建失败，不返回完成清单。
- manifest `coverage` 按月披露12项核心经营指标：`present / missing_month / missing_subject / ambiguous_subject / non_numeric / missing_value`；只有唯一且有值的对应 summary 科目才展示值。同一指标多科目不自行相加，金蝶费用不回填 summary。金额与比率保持原值，不累计比率、不推算利润或增长率。
- `allRequestedMonthsPublished` 只表示所选月份有完成发布元数据；`allCoreMetricsPresent` 只表示12项指标存在。二者都不证明经营数据完整，`sourceAuthorityVerified=false`、`businessCoverageVerified=false` 固定，不能用于授权或模型已读证明。
- `analysis_period` 可选 `{startDate,endDate}`；与所选完整自然月首末日完全相同才为 `exact_full_months`，其他为 `different_or_partial_months`。闰年按真实月末计算。没有按天分摊，没有把“最近30天”转换成一个月，也不自动补充月份。
- 每页最多100条、完整规范 JSON 含摘要不超过128 KiB；返回能完整容纳的行前缀与实际 `nextOffset`，不截字段。整源最多100000行，最终 manifest+所有页不超过64 MiB，超限整源拒绝。是序列化容量，不承诺进程峰值内存为64 MiB；当前纯构建保留有界完整源。
- 固定元数据在开始迭代前复制，返回页和清单均从不可变 JSON 字符串读出；调用方修改输入/返回 DTO 不改变已准备结果。此对象不是安全令牌，未来 owning 层仍须持有可信身份、验证来源并复核摘要，不接受公开 DTO 恢复为已授权对象。

## 后续最小实施顺序（尚未实施）

1. **财报 owning reader**：新增独立 `finance/analysis_records.py` 与专用测试，精确月份+财务scope查询；实时无范围限制管理员、签名 reader、前后完整 revision、完成 month→batch 链、身份/有界分页/游标绑定与过期拒绝。不复用 line_search 的模糊检索和文本裁剪，不取客户/订单；现表已有数据与权限，不预设必须新建事实表。需要实际 PG 验证读取/导入竞态和跨店边界。
2. **只读协议与封存扩展**：现 `evidence_v2.normalize_sources` / `business_evidence.TOOLS` 仅允许 sales/netshop/market。必须显式新版本或独立新增协议，保留旧目录/工具摘要；固定财报自然月与日事实窗口的并列关系，加入封存、恢复、行/金额单位及空值证明。当前64 MiB/2000页及48来源限制不自动扩容。不能只在旧 enum 添加 finance 就声称完整兼容。
3. **派生与诊断**：先做同scope、同科目、完整自然月的月同比/环比；缺月/缺科目/零或负基期给明确状态，原比率与加权重算分开。税额/含税规则当前没有可证明的独立字段，财报记账日期与 ERP 发货/销售日期、推广归因窗口必须并列披露，不把不同来源相加。财报没有商品/订单/客户维度，无法证明某 SKU 退款导致店铺毛利变化，也无法据其去重 B 端与零售。
4. **报告与文件**：新来源全量表、来源目录、比较及缺口加入固定新报告协议、候选包/五角色阅读证明、精确数值引用和多卷工程文件；实际 context preflight 决定容量。另做真实模型对退款/成本/费用归因边界与行动建议质量的验收，不用纯计算通过代替。

下一片建议先做第1项；不需用户逐批确认常规开发。真实来源采用、付费模型及生产变更仍按主任务已有边界分别验收。

## 店铺总览/UV 的有证据缺口

并非完全没有店铺总览导入入口：`jd_shop_overview → trade_overview` 已在 [normalized-import](../lib/netshop/normalized-import.ts) 和 [Django writer](../backend/netshop/import_service.py) 枚举中。[既有本地操作文档](../自动导入-京东自营网店数据(1).md) 写的是交易概况**分天下载**。事实与原文可以存入 `NetshopRow.metrics_json/raw_json`，但没有发现进入当前完整经营分析的独立店铺总览来源：

- [netshop.analysis.SOURCES](../backend/netshop/analysis.py) 及 [analysis_options](../backend/netshop/analysis_options.py) 只含 promotion/sku/spu/b2b/master，未含 trade_overview。
- [netshop.query.overview](../backend/netshop/query.py) 返回数据集行数、日期包络、最新批次；不是店铺经营指标总览。
- 通用 typed `visitors` 投影只认 `visitors/商品访客数`，没有已核验的交易概况表头合同。不能把不存在的标准字段零值当真实零访客。
- 非商品日的通用 `sourceRowKey` 含文件哈希、原行号和内容摘要；不是严格的店铺×日期身份，重叠文件可以留下不同记录。先需要实际表头/指标语义、日期完整性、重复与冲突规则的合成合同；不能直接 sum 所有历史导入行。
- 即使以后获得可信**店铺日 UV**，多日求和仍不是所选区间去重 UV；区间 UV 需要平台明确针对同一完整区间的去重总览，或合规可用的去重机制。目前没有证据表明此能力已存在。不可用商品日访客替代，不能收集客户原始身份来临时补洞。

因此，当前可优先推进的是已有月度财报的有界来源；店铺 UV 先保持缺源声明。未访问生产，不能声称任一店铺现在有或没有 trade_overview 原始数据。

## 本片验证

独立复核后18项纯测试通过（`python -m unittest business_analysis.test_finance_source -v`，`PYTHONPATH=backend`）；夹具用既有 `finance.tests.factories` 并执行真实 `_line_model` AST 获得 ORM 标量形状，没有初始化 Django 或数据库。覆盖空值/零值/负值、缺月与缺scope、同指标歧义、区段和身份隔离、闰月与部分月份、实际UTF-8页前缀、一次消费、迟到重复/额外字段、字节/行限制、摘要绑定及副本隔离。新增复核包括 summary/kingdee 同科目并存不相加、源端已合并比率原值与行数保留且不伪造跨组拆分、零行页仍校验完整页字节上限。后续 owning、持久封存、付费模型和真实财报质量均未在此验收。

复核确认：现解析器 `aggregateLines` 还可能相加重复列的 `rateBps`。因此核心指标的 `present` 只表示规范化账本有唯一有值科目，不能证明源比率可用于跨平台比较；`source_row_count > 1` 不足以区分合法重复汇总与跨组同名误合并。后续诊断必须保留此质量限制，不能只检查 `allCoreMetricsPresent` 就认定财报有效。
