# 完整经营分析容量：独立审查与后续实施

这是开发期多 Agent 独立审查及实施记录，不是正式采用的容量配置。旧协议保持 12 来源；候选 v2 工作台最多 48 来源，事实仍限 64 MiB/2000 页，多卷每卷最多 120 表。工作台必须完整展示超限来源并停止提交，不能默默缩小范围。下方初始审查表保留当时发现，以本段及批次记录为当前状态。

2026-09-17 第十六至二十四批已接通 v2 独立目录、采集封存、轻量多 Agent 报告、持久多卷交付及固定预算工作台。19 来源合成全链路产生 156 张表、两卷 HTML/XLSX 和完整 JSON 清单；预算有独立执行协议和容量预检。第二十五批新增只读分区商品关联接口，5001 行真实读取器合成链路及每源 30001 行纯计算通过，旧关联接口不变。新关联尚未接入报告和导出；目录支持 48 项不代表事实容量提高或模型上下文一定可容纳。下方初始审查与设计保留历史依据，以实施批次为当前状态。

## 已确认的相互依赖

| 环节 | 当前限制 | 完整任务的影响 |
| --- | --- | --- |
| 证据计划 | 12 来源；16000 UTF-8 字节 | 数据集×窗口×店铺分别计数。单京东店 promotion/sku/spu/b2b/ERP 三窗口及当前 master 为16来源，加一市场类目三窗口为19 |
| 证据存储 | 每任务64 MiB/2000页；每人256 MiB；全局2 GiB | JSON事实体积与XLSX压缩体积不同；不能因提高任务限额而通过倍乘顺带扩大用户/全局额度 |
| 数据库约束 | 计划16000字节、状态64 KiB、字节64 MiB；块序号≤2000、块≤128 KiB | 新容量必须明确版本，保持旧任务全部摘要和约束语义；不是只改Python常量 |
| 模型输入 | 工作流input≤8000字节；当前复制完整sources | 完整来源应改为固定证据引用与分页目录；不能把完整事实塞入模型 |
| 工具输出 | 证据摘要当前整份返回，38000字符边界 | 大来源目录须分页且绑定版本/摘要，不截断字段或完整性声明 |
| 商品匹配 | 双来源各≤5000行；当前内存列表/字典 | 大规模须流式分区及分页匹配，维持跨店、歧义、未匹配和退款金额核对 |
| 派生分析 | 每次工具分页重扫来源；最多25万组、临时盘256 MiB | 需要固定证据版本的派生结果复用，减少相同来源的反复扫描 |
| 文件 | 120表、单表100万行、单文件256 MiB；构建600秒 | 当前生成逻辑每个有三窗口的指标来源产生22张分析表。16来源完整单店至少131表；19来源约156表，尚未加预算表 |
| 游标 | 三个owning reader签名游标均1小时过期 | 暂停太久后无法直接续传；不能删除过期/版本检查或从首页混拼 |
| 报告快照 | DB限制32768字节，预算参数允许到48000字节 | 应预先核验最终快照或改用持久参数引用，避免走到数据库才失败 |

## 按依赖实施

1. **证据新协议。** 分离来源目录和逐源检查点，固定容量档位与集合摘要。可评估48来源/256 MiB/8000总页档位，但必须经合成压力验收后决定，不能先当作可用承诺。用户与全局配额独立定义。新增表同步最小角色、健康、备份、恢复和所有权清单。
2. **轻量输入和分页目录。** 新报告只携带证据ID/版本/摘要/来源数/问题；来源目录逐页完整返回并复验身份，旧工具调用保持兼容。不能借此扩大模型调用预算或允许任意来源。
3. **版本绑定派生结果。** 从完整封存事实流式建立分区，核对行数、金额、缺失数与摘要，完成后提供分页读取。大商品匹配保留唯一匹配、歧义、未匹配三类，不能跨店或复制销售金额。
4. **多卷文件协议。** 先确定完整表清单及行段，按表数、行数和字节边界拆成明确编号的HTML/XLSX卷。总目录记录全部卷与表摘要，分块身份显式包含卷号；不能把卷号混进旧sequence。旧renderer1/2/3保持原下载行为。
5. **受控续签。** 后台仅在原query、sourceRef、revision、lastId和持久检查点全部一致时续签下一游标；来源变化继续停止，不自动扩大日期或重放模型。

每一项先在独立候选环境实施并验收。需要覆盖16/19/35/48来源、49拒绝、2001页与64 MiB以上实际合成事实、5001/30001行匹配、131/156张表多卷无遗漏、暂停/取消/权限变化/审计失败回滚、旧任务及独立备份恢复。合成容量通过不等于正式数据库查询计划、真实经营口径或付费多 Agent 分析效果验收。

首次任务工作台可以先交付“输入问题、确认范围、预览覆盖、后台取数、人工启动分析”，同时明确以上阻塞。任意自然语言自动解析范围、自动规划来源与完整多店综合任务需要后续能力，不能把表单或关键词规则称为全部实现。

## 2026-09-17 精确实施设计：证据 v2 与轻量工作流

本节保留初始设计及兼容约束，部分已由第十六至十八批实现，以顶部实施状态和当前代码为准。目标先解决来源目录结构和模型输入容量，不顺带放大事实存储、文件或模型额度。

### 代码核验得到的兼容约束

- `backend/ai_assistant/business_evidence.py` 的 v1 创建将完整来源写入 `plan_json`，`request_digest=digest(plan)`，同一 owner/clientRequestId 复用该摘要；创建时的 expectedPrincipalKey 不进入计划。新版工作台的 analysisRequest 才预检 8000 字节 workflow 输入，旧调用允许此前合法的更大来源计划继续创建/重放。
- `migrations/0014_business_evidence.py` 锁定 run 的 plan_json、身份和创建时间；chunks 不允许更新/删除；run 终态不允许更新，普通更新 version 必须加一。迁移不能通过回填 v1 plan/state 来“转换格式”，也不能改已应用的 0014。
- `business_collection.py` 直接从 plan.sources 找未完成来源，从 state_json 读取对应 verifier；后台和人工 collect 共用提交逻辑。append chunk、verifier、stored_bytes、run version 与审计必须继续在同一 mutation 事务。
- `business_reports.py` 创建 input 复制全部 sources；`business_budget.py`、`business_export.py` 也直接读取 plan.sources 和 state_json。只改采集器会使封存后的计算和导出失效。
- `workflows.admission()` 保存整个 `ai_agent` 目录的名称列表及 digest，执行时先重新 admission 并逐项比较，再做经营报告工具筛选。`lib/ai/django-edge.ts` 执行桥还按 surface 的完整实时目录核验 policyDigest。因此在原 ai_agent surface 增加工具或修改原条目的 schema/allowedSurfaces，可能使旧已持久任务以 executor_policy_changed 停止，不能声称旧任务无影响。
- `lib/ai/tool-execution-runtime.ts` 同时限制总调用数、单工具调用数和累计时间；`tool-registry-contract.ts` 普通工具输出最多 40000 字符，只有精确后台 business_collection 专用工具可达 131072。此设计不放宽这些门禁。
- 当前没有名为 `backend/ai_assistant/tool_execution.py` 的模块；实际持久 Agent 派发在 `workflows.py`，跨 Worker 执行在 `transport.py` / `lib/ai/django-edge.ts`，通用执行约束在上述 TS runtime/contract。

### A. 最小新数据结构与版本选择

建议只新增一个 `AiBusinessEvidenceSource` 模型/表 `ai_business_evidence_sources`，每个 v2 来源一行，兼容现有 run 和 chunk 表。

| 字段组 | 建议字段 | 不变量 |
| --- | --- | --- |
| 固定目录 | run_id、source_key、ordinal、domain、query_json、query_digest | 唯一(run,source_key)、唯一(run,ordinal)、唯一(run,domain,query_digest)；全部创建时固定，禁止更新/删除 |
| 逐源检查点 | checkpoint_json、version、page_count、stored_bytes、row_count、finished | 初始空；保存 PageReconciler 及现有 metadata；源更新与 run version CAS、分块插入一并提交；finished 后不得继续更新 |
| 时间 | created_at、updated_at | created_at 不变，更新时间只随成功检查点推进 |

v2 run 的 plan_json 继续不超过 **16000 字节**，仅包含 schemaVersion=`business-evidence-v2`、sourceCount、catalogDigest、capacityProfile、collector 及 analysisRequest。plan 不含 sources。v2 run 的 state_json 保持小型聚合状态，如已完成来源数/总页数/总行数和封存目录结果摘要，不承载逐源游标。run 现有 stored_bytes 仍表示事实 chunk 字节，不能悄悄改成包含其他内容。

目录摘要建议绑定协议名及按固定 ordinal 排列的 `{key,domain,queryDigest}`；queryDigest 来自规范 query。创建、读取目录、封存和导出均验证 query_json 与 queryDigest。最初一次创建仍可接收有界 sources 数组，服务端规范化后同事务插入 run 与全部 source 行；**不能把数组原样塞回 plan_json 或 workflow input**。

首个实现档建议取名 `catalog-48-facts-v1`：最多 48 来源，事实仍为 **64 MiB/2000 总页**。这是来源结构扩容，不能宣传已经支持完整 48 来源大规模分析。后续 256 MiB/8000 页须另做容量档位、额度及多卷验收，不修改旧档。v2 只接受显式新协议选择，不因 len(sources)>12 自动升级；无新选择字段的请求固定走现有 v1 路径。

新增 source.query_json/checkpoint_json 分别建议 4096/32768 字节上限，并限制目录全部 query 合计 128 KiB。该数值为待测试的初始方案，不是实测容量。新增目录和检查点会占用额外持久空间：须在 quota 函数中单独统计 v2 元数据字节，再与事实字节一起计入 owner/global 总量；不能只在界面显示事实 stored_bytes 而漏算资源消耗。原 v1 单任务64 MiB、owner256 MiB/global2 GiB和4个在途任务额度保持原值；这意味着v2实际可用事实额度可能因元数据占用而更早用尽，应明确显示。

创建事务必须有延迟一致性门禁，提交前 source 行数和连续 ordinal 与 plan.sourceCount 一致；目录行 insert 只能指向 v2 collecting run，禁止 v1 有目录行；禁止目录删除，固定列不可更新。数据库门禁能验证结构和计数，Python 负责规范 JSON 摘要算法，不能假定正式环境已有 pgcrypto。源检查点更新须锁定父 run，拒绝已终态/取消父任务。数据库不允许通过旧 writer 任意追加 v2 来源后继续封存。

### B. 保持 v1 字节、幂等和读取不变

1. 新 `business_catalog.py` 统一提供 `protocol(row)`、`iter_sources(row)`、`source(row,key)`、`checkpoint(row,key)`、`summary(row)`、`directory_page(row,offset,limit)` 与 `verify_sealed(row)`。v1 仅解析原 plan/state；v2 查询新表。协议未知立即失败关闭。
2. `create()` 先做身份及 expectedPrincipalKey 检查。**无显式协议字段**完全保留旧验证和 plan 构造；新 analysisRequest 的既有一致性校验仍保留。v2 校验 sources 后建立小 plan 和目录，幂等摘要绑定小 plan（其中 catalogDigest 覆盖所有来源），不包含随机 run ID，也不包含 expectedPrincipalKey。
3. 原 clientRequestId 对应 v1 不能因升级代码变成 v2；原始请求重交仍走 v1，返回同一记录。显式改版本、query、窗口、问题、目录顺序协议或容量档位时，同一 ID 必须冲突，不能复用旧源行。
4. 不把规范化 v1 计划的排序、缺省 window、autoCollect=false 或 analysisRequest 默认值回写旧记录。原未带默认项的摘要继续逐字节保持。
5. 旧 mapping/detail/chunk 响应保持原语义。v2 detail 返回紧凑 manifest 与目录分页入口，不假装返回完整 sources；必须有明确 schemaVersion。原工具只读 v1，不能让旧模型工具在未修改说明的情况下收到另一种目录结构。

### C. 采集提交、封存与所有读取者的最小接入

`collect()` 仍只读取一页；加载 source 和 checkpoint 改经适配层。后台 collector 对 v2 按 ordinal 找第一个 unfinished source，不加载全部检查点。当前16步/25秒调度、每页30秒、120秒认领和取消/暂停/重试规则不变。

成功提交顺序：重新查真实权限 → CAS父run → 校验source版本 → 配额检查 → 插入原immutable chunk → 更新该source检查点与计数 → 更新run聚合计数和version → 同事务审计。若任一步失败，chunk与两级状态全部回滚。任意迟到提交必须同时败于run或source CAS，不以source仍未完成为由跨过暂停/取消。

封存 v2 时核对全部source完成、sourceCount及ordinal连续、每个source的queryDigest、PageReconciler结果、分块计数/字节及run总数。生成按固定顺序绑定每个sourceRef/evidenceDigest/rowCount的封存摘要。报告引用固定evidenceVersion、planDigest、catalogDigest及封存摘要；后续只读计算仍重新核验相应完整chunk链，目录摘要不能替代事实页验证。

迁移过渡时必须一次接完下面的读取者，或明确拒绝尚未支持v2的操作，不可产生半支持任务：

| 文件 | 最小改动 |
| --- | --- |
| `backend/ai_assistant/business_models.py`、`models.py` | 新模型及唯一约束/import；不改v1模型字段语义 |
| `backend/ai_assistant/business_catalog.py`（新） | 双协议适配、目录摘要/分页、逐源状态、封存验证及元数据额度 |
| `backend/ai_assistant/business_evidence.py` | 显式v2创建分支；collect/finish/listing/detail/chunk/analysis/reconcile使用适配；v1响应保持 |
| `backend/ai_assistant/business_collection.py` | 通过适配选来源与检查点，commit回调据v2聚合状态判定可封存；审计只摘要 |
| `backend/ai_assistant/business_reports.py` | 来源范围从目录适配取得；新增reference-v2工作流输入模式、绑定摘要、新工具集合和图说明 |
| `backend/ai_assistant/business_budget.py`、`business_export.py` | 替换直接plan.sources/state读取；继续逐页校验、精确范围与固定报告版本 |
| `backend/ai_assistant/business_files.py` | binding中绑定新证据协议/封存摘要；旧renderer交付不改；不支持的v2完整多卷必须创建前拒绝 |
| `backend/ai_assistant/business_diagnosis.py` | 通常可继续经analysis_table；增加v2引用链回归，不放宽现有引用验证 |
| `backend/business_analysis/planning.py`、`ai_assistant/business_planning.py` | 显式按版本返回v2小plan字节/轻量input字节及完整来源数；来源未缩小，旧预览保留 |
| `backend/ai_assistant/views.py` | 新有界reader目录路由，严格query及owner/version/digest校验；不启动模型 |
| `lib/ai/django-route.ts`、`lib/django/ai-service.ts`、`app/api/ai/…` | 显式路由/读写进程白名单；保持1MiB请求、2MiB普通响应界限，不自动扩大 |
| `app/ai-business-workbench.tsx` | 新版目录分页、容量档位及真实来源状态；旧任务回显和账号切换门禁保持 |

### D. 轻量输入与旧工具目录兼容

建议保持最终报告格式 `business-report-v1`，仅对新v2证据创建的报告增加不可变 `inputMode:reference-v2`、`toolSurface:business_agent_v2` 和相应摘要字段。旧snapshot不补默认字段，旧图和输出结构不重写。input控制在现有8000字节内，字段仅为证据ID/版本/planDigest/catalogDigest/封存摘要/sourceCount/question，以及可选reportId、budgetPlanDigest和小型预算总额；绝不含来源数组/预算对象数组。

**工具隔离是本次兼容设计的必要部分。** 不能直接给原ai_agent目录新增分页参数或新条目后承诺所有旧DAG正常恢复。建议新增 `business_agent_v2` surface，使用独立稳定名称的目录工具、分析表工具、可选预算工具，条目仅绑定该surface；原工具条目（包括allowedSurfaces）保持不变。新条目仅复用原可信handler/计算逻辑，不自动开放其他系统工具。

- `workflows.admission()` 增加只供服务端调用的surface参数；通用任务默认ai_agent。新经营报告创建通过内部参数选择v2 surface，不把surface作为通用公开请求可指定字段。
- 新工作流运行时，从已持久报告snapshot解析工具profile；新子Agent通过所属workflow的报告继承。数据库尚未创建报告时的初始admission使用受信内部参数，workflow+report创建仍同一事务；异常回滚不会留下无profile的可运行任务。
- `workflows.py` 重新admission、工具execute_tool的surface和 `business_reports.restricted_entries/validate_call` 必须一致选择该profile。旧任务仍用ai_agent，原allowed_tools_json/tool_policy_digest不更新；原模型/权限/工具变更继续按既有策略失败关闭。
- `lib/ai/tool-registry-contract.ts`、`tool-registry.ts`、`django-edge.ts` 的surface枚举/目录与桥接校验已显式接入；`lib/ai/business-evidence.ts`增加新目录handler。实际新工具只接受runId与可选offset，不接受模型提供的版本/摘要或页长；页长固定20。版本、目录摘要及封存摘要由服务端固定快照绑定，每次新模型派发前重验持久目录回执。新分析表另加38000 UTF-8字节限制；旧工具仍保留原字符限制和摘要。
- 目录工具返回小型全局摘要＋当前页完整来源（缺日/金额口径详情可按单source继续读），total/returned/nextOffset齐全。默认每页10来源，最多20；48来源仅需5次目录调用，仍在现有单工具8次以内。若最宽单来源元数据导致一页超限则按字节减少实际returned，nextOffset用实际数量；单来源本身超限时拒绝并转精确详情分页，不截断字段。不能保证所有48来源最坏情况下5次调用，需预检/测试及明确“未读完整目录”的状态。
- `lib/ai/tool-execution-runtime.ts`、`tool-registry-contract.ts` 的调用、取消、审计、输出限制均保持，新增负向测试验证两种surface隔离。不要让模型通过传入surface或任意runId读取其他任务。

预算snapshot的32768字节DB上限仍需单独预检。轻量input不会自动解决48KiB预算计划塞入snapshot的问题；首批v2可明确拒绝超出最终snapshot容量的预算请求，随后再设计不可变参数引用，不临时抬高DB限额。

### E. 新迁移与恢复路径

建议在当前0018之后新增下一号迁移（实际编号以落地时最新head为准），不修改0014–0018。

1. 创建source表/索引/约束，安装运行authority写栅栏、固定身份不可变、父任务状态/CAS关联、目录创建完整性门禁。v1 run/chunk现有字节/序号约束全部保留；初档仍64MiB/2000页，无需扩大它们。
2. 同步 `database_contract.py` 的MODELS/READ_TABLES/WRITER_PRIVILEGES，新表writer仅SELECT/INSERT/UPDATE，禁止DELETE；来源固定列由触发器保护。`table_manifest.py` 与 `health.py` 核验表、索引、constraint、trigger，AI表数由60变61（前提落地前没有其他新表）。
3. 更新 `tools/ai-domain-snapshot.py` 的退役旧域快照排除新运行表，及升级工具中新旧表集合。旧版56/60表备份继续按其迁移版本验证，不要求旧备份凭空包含新表。
4. `tools/ai-business-evidence-upgrade-rehearsal.py` 扩展0013→…→新迁移：预置v1 collecting/manual/paused/sealed/cancelled与旧renderer1/2/3；前后完整摘要保持；新v2数据、源状态和chunks独立dump/restore后验证。
5. 逆迁移必须先确认无任何v2 run/source记录及依赖报告；有则拒绝，不删除新目录或抹去版本字段。无v2时可回到原结构。未经采用，不能把隔离迁移结果说成正式数据库已具备该能力。

### F. 最小测试矩阵与交付分批

第一批为模型/迁移/目录adapter，第二批为collect和全部读取者，第三批为轻量报告及独立tool surface，第四批为工作台切换和端到端。**完成前几批不应向正式入口开放v2创建**；通过受控内部测试分支即可。文件多卷和更大事实额度是后续独立批次，不能因目录v2已完成而解除120表文件限制。

### G. 持久多卷设计（第二十批已实现并经候选验证）

独立审查确认不能只把纯多卷 renderer 接到旧任务：旧 chunk 唯一键没有卷号、数据库 ready 门禁固定一对 HTML/XLSX，完整追溯清单也可能超过 run 的 128 KiB。第二十批新增 renderer 4 与 `0020_business_volume_files` 独立不可变卷分片表；卷 1—100 保存 HTML/XLSX，卷 0 保存最多 16 MiB 的完整 JSON 清单。旧 renderer 1/2/3 和旧分片表不回填或改写。新表同步权限、就绪、备份与逆迁移拒绝条件；当前仅在隔离候选环境验证。

根记录只存不超过 128 KiB 的紧凑目录、绑定、attempt、卷数、各文件字节/摘要/分片数及完整清单摘要。详细表片段、行区间和源表证明保存为 JSON 文件。单文件 256 MiB、任务 1 GiB、用户 2 GiB、全局 8 GiB 不自动增加；旧 attempt 的分片继续占用额度。所有临时输出共用整批高水位，为 JSON 预留 16 MiB，防止多个合格文件合计耗尽磁盘。TableSpool 每逻辑表 100 万行门禁在第二十批仍保留；大逻辑表流式拆分尚需独立容量验收。

第一版只承诺整批恢复：完整清单已暂存则按原 attempt 重验后发布；部分暂存则新 attempt 重建整批，旧片不可变。缺卷、错误片、权限变化、暂停/取消和旧 worker 写入均须阻止整批 ready，不声称卷级续算或按字节动态拆卷。创建须显式选择 volumes 并绑定当前账号；新增按卷下载和清单下载路由。页面一次由用户选择下载一个文件，校验分片和整文件摘要，再复验权限、attempt 与交付摘要，旧任务仍显示原两个按钮。

- 兼容：同一旧请求创建/重放字节完全一致；含/不含analysisRequest、window省略、标准/bulk及自动/手动都覆盖；旧大于8k但小于16k计划继续重放；v1字段不得自动排序或补值。
- 新目录：16/19/35/48来源小数据通过，49拒绝；超query单行/目录总字节拒绝；缺一目录行、重复ordinal/key/query、目录digest错、向v1插source、向已开始/结束run添source均失败；未知协议/profile拒绝。
- 原子性：双worker同run/source、目录插入中异常、chunk后checkpoint前异常、审计失败、pause/cancel后的迟到页、权限scope改变、owner不匹配、expectedPrincipalKey换账号均不残留部分提交。
- 额度：恰好/超过旧事实64MiB、2000页、owner256MiB/global2GiB；新source元数据计费纳入共享额度；并发创建/append不能通过先读总量后独立写突破边界。大字节测试使用真实合成payload，不能只patch常量宣称通过。
- 封存：总行数/控制金额、sourceRef/revision、query digest、页哈希、源顺序、finished标志与run汇总都核对；缺来源、缺尾页或不完整字段不得被目录摘要掩盖。
- 工作流：48来源和1000字中文问题的实际input仍≤8000；新轻量input没有sources；旧ai_agent目录序列化字节/hash不变，新surface工具不能在旧surface使用。模型配置变化、工具定义变更仍失败关闭；不迁移/重放未知provider dispatch。
- 分页：目录请求错误version/digest、越界offset、byte-limit自适应、单项超限、跨owner、迟到旧页面、未读完不得全量声明。分析/预算/诊断/导出均能经adapter处理v1和v2，同一合成事实结果一致。
- 运行约束：编译/纯单测先行；隔离PG由协调Agent串行启动并跑真实角色负向探针及备份独立恢复。相关Node注册表/边界/工具审计与前端分页测试通过后再跑必要整合检查，不调用付费模型补齐测试结果。

本设计的接受条件是：新目录允许更复杂的精确来源范围，旧证据和旧Agent任务兼容，输入/响应不截断，失败保留原证据。它**不等于**真实历史数据齐全、完整多店多卷已交付、5000行商品匹配已扩容或长期游标续签已实现；这些仍按前文分项开发和验收。

### H. v2 固定预算引用与独立执行协议（第二十二、二十三批候选已接通）

本节保留设计依据；第二十二批完成参数底座，第二十三批完成独立执行协议、三个工具、逐 Agent 读取证明、实际容量预检及预算文件交付，第二十四批接通首次工作台目标选择与只读试算。`budget.normalize()` 允许最多 100 个目标、规范 JSON 最多 **48000 UTF-8 字节**，报告快照继续为 **32768 字节**，因此参数存于独立不可变记录而非内联 snapshot。现有 v1/v2 snapshot 原始字节、摘要和旧工具目录保持不变，不回填参数引用，不修改已应用迁移。以下建议值已由候选代码及隔离边界验证采用，不代表生产启用；参数可存储也不等于模型上下文可容纳。

#### H.1 固定参数存储、绑定与独立配额

建议新增不可变 `AiBusinessBudgetPlan` 参数表，并为 `AiReportRun` 增加 nullable、受保护的预算外键。旧报告保持 NULL；新预算报告的外键和 snapshot 中的 `budgetRef` 必须指向同一参数记录。参数表保存规范 plan_json、planDigest、owner/scope，以及 evidenceRunId、evidenceVersion、evidencePlanDigest、catalogDigest、sealedDigest、analysisRequestDigest 和 calculatorVersion。报告 snapshot 只增加固定的 `budgetRef={schemaVersion,id,planDigest,bindingDigest}` 与 reportId，workflow/job 输入也仅携带固定引用，不复制目标数组、预算参数或结果全文。

`analysisRequestDigest` 对已封存请求的规范值计算，缺省时明确绑定 NULL，不在旧证据中补写默认值。预算 bindingDigest 同时覆盖上述证据、请求、参数摘要和计算器版本；目标的 sourceKey、dimension、rowIndex、rowId 仍由共享 `Reader` 和完整分析表重新核验。现有“本期推广来源、相同日期、同一店铺不混重叠来源或聚合维度”约束保留，不接受模型提供的摘要作为权威。

参数容量使用独立的 `budget-parameters-v1` 档位，建议初始候选值如下；实施时须通过真实边界及并发测试后才能宣称生效，不隐含提高事实或文件配额：

| 计费项 | 建议硬上限 | 计费规则 |
| --- | --- | --- |
| 单条参数 | plan_json 48000 字节，固定绑定元数据规范 JSON 4096 字节 | 都按实际 UTF-8 字节核验；不只计算参数摘要 |
| 单 owner | 全部参数与绑定元数据合计 8 MiB，最多 200 条 | 生效、历史替代版本和失败报告保留记录均计入 |
| 全局 | 全部参数与绑定元数据合计 64 MiB，最多 2000 条 | 跨 owner 累加，不能用不同账号或新请求键绕过 |

行数门禁同时约束小参数造成的行与索引增长；字节额是应用计费口径，不等于 PostgreSQL 物理磁盘占用。检查与插入必须在同一事务并持有全局参数配额互斥，不能以并发的“先查询再插入”突破限额。额度耗尽则拒绝新预算版本；本切片不提供自动删除、覆盖或按时间免计历史参数的路径。未来保留策略须另做引用安全、审计与独立恢复设计。

预先分配参数 ID、报告 ID；参数、workflow、报告及审计在同一 mutation 事务提交，任何失败均不留下孤立参数或可运行但无绑定的任务。相同 owner/clientRequestId 的未知回执使用原完整请求重试并返回原记录，不能换 UUID 自动重建。参数修改创建新参数行及新报告，`previousReportId` 只允许同 owner、同封存与分析范围引用，不更新旧报告或旧参数。nullable 外键应禁止删除被引用参数，数据库还须校验新预算 profile 必有参数、参数身份与报告一致、快照引用与外键一致。

#### H.2 新工具目录与每个 Agent 的读取证明

建议新增 `business-agent-budget-reference-v1` profile 和 `business_agent_budget_v1` surface，分别新增预算专用的来源目录、分析表、预算情景三个工具名称，复用既有可信 handler 与确定性计算。**不得给现有两条 v2 工具追加 allowedSurfaces，也不得把预算工具加入原 v2 或 ai_agent 目录**；这会改变已有持久任务的目录摘要。profile 只能由报告创建服务内部选择，公开通用工作流和模型参数不能指定。

`workflows` 的 admission、运行时 surface、图、输入绑定、工具过滤、调用验证和 preflight 必须同时接通新 profile。新预算工具输出按 **38000 UTF-8 字节**限制，沿完整目标行前缀分页；若按字节减少返回条数，nextOffset 使用实际数量。单个目标超限时拒绝，不截断字段。既有预算 TS handler 的字符数限制不能直接当作新工具的字节保证；原工具本身保持原行为与定义摘要。

全部五个 Agent 仍须独立、顺序读完本任务的来源目录；promotion、independent_review、report 还须分别读完固定预算全部页。新预算回执证明从本 job 的持久 dispatch/result 有界重建，核验原始参数与结果摘要、report/证据/预算绑定，并与服务端重新计算的完整预算页逐项比对。失败回执不计覆盖，缺页、重复、乱序、跨 job 或重算摘要后的伪造页均拒绝；unknown 不授权重放。不要复用旧预算只累计 rowIndex 的宽松覆盖检查。模型输出完成、人工复核、content 与文件发布前都再次核验这些证明。

**100 个目标只是参数存储上限，不是模型可分析范围的承诺。** 新 preflight 必须按实际参数及真实工具页编码，计入每个 Agent 的完整目录页、所需预算页、至少一个分析表响应、依赖节点输出和工具 schema；检查单工具额度、总调用数、轮次、192 KiB transcript 和当前模型上下文。沿用现有上限，不增加调用额度或删除已读页来通过；不能容纳的合法存储参数必须在付费调用前明确拒绝。不同文字长度、目标数及模型配置可能得出不同可用范围。

#### H.3 最小代码、迁移与恢复依赖

| 位置 | 后续最小变更 |
| --- | --- |
| 新预算模型及下一号 migration | 参数不可变、精确字节与行数约束、报告 nullable FK、同身份及引用一致性门禁；不修改 0012 或已应用迁移 |
| `database_contract.py`、`health.py`、`table_manifest.py` 与域备份/升级工具 | 新表最小 SELECT/INSERT 权限、禁止 UPDATE/DELETE、约束/trigger 就绪检查、独立 dump/restore 和版本化表清单；旧备份按原版本验证 |
| `business_budget.py` 及新的参数存储适配 | 复用 `resolve()` 的 Reader/计算器；`for_report/read/preview` 按固定引用读取、重新验证参数与封存绑定，旧内联预算分支不变 |
| `business_reports.py`、`workflows.py` 及新预算回执模块 | 原子创建、新 profile/图/轻量引用、实际上下文预检、逐 Agent 回执证明和完成/复核/content 门禁 |
| `lib/ai/tool-registry-contract.ts`、`tool-registry.ts`、`django-edge.ts`、`business-evidence.ts` | 新 surface 与三个独立工具条目、精确路由/权限、字节有界预算页；原工具和旧目录摘要保持 |
| `business_export.py`、`business_files.py` 及多卷持久服务 | 复用既有预算表、HTML 离线预算、首卷原生 Excel 预算；每次创建/恢复/发布重验参数记录，文件绑定覆盖 budgetRef 和参数绑定摘要 |

逆迁移必须在执行任何 DROP/移除 FK 前确认：**无任何新参数行、无非 NULL 预算 FK、无新预算 profile 报告**。任一存在即原子拒绝，不能为回退删参数、清空外键或改写 profile。独立恢复后，报告缺参数行、参数摘要变化、计算器版本不支持或证据绑定变化均失败关闭；不得回退为“无预算报告”或接受模型重新补参数。

#### H.4 建议分批与验收

先完成“参数表、原子固定引用、确定性读取/试算、迁移与恢复”，保持新 Agent profile 禁用；再完成“独立工具目录、回执证明、实际 preflight、报告与多卷导出集成”。两批均在候选环境测试，不因持久表已建立而开放尚未完成的预算分析入口。

- 容量：大于 32768 且不超过 48000 字节的合法计划保存到独立参数表，同时最终报告快照仍不超过 32768；精确边界、超过一字节、中文/转义文本、owner/global 字节与行数上限以及并发竞争。
- 绑定和原子性：跨 owner、证据版本/目录/封存/analysisRequest/目标行身份篡改，参数与 FK/snapshot 不一致，审计或 workflow 创建失败全回滚；未知创建回执同请求重试，新旧预算报告并存不互相覆盖。
- 读取证明：预算页字节自适应、单行超限、缺页/乱序/重复/伪造且重算摘要、跨 job、failed/unknown，以及完成、人工复核、content、文件恢复后的重新核验。
- 模型预检：用实际最大参数与真实预算分页验收不同模型；超出单工具、总调用、轮次或上下文上限时零付费派发，不以合成 100 目标存储通过冒充完整分析可用。
- 兼容与恢复：现有工具条目、全部旧 surface 目录摘要及现 v2 snapshot 字节黄金回归；升级前后旧报告不变，新增参数独立备份恢复，存在参数或新 profile 时逆迁移拒绝。
- 文件：同一固定预算的报告内容、HTML、Excel 和完整多卷 manifest 使用同一 planDigest；预算仅首卷，后续卷和独立清单保持完整，缺参数或绑定改变不发布部分文件。
