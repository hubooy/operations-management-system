# AI 经营分析：当前主线整合交付清单

核对日期：2026-09-25。范围为 `codex/ai-business-current-integration` 的当前整合工作树及已明确保存的候选验证记录；不是生产状态证明。目标沿用原五阶段：让系统结合店铺、市场、网店销售、ERP/B 端及财务，完成多维明细、同比环比、多个专业 Agent 协同诊断、调整规划和工程级 HTML/XLSX。

结论：保留已有分析、调度、证据和文件底座，当前主线兼容整合及推广词货专项的隔离完整链路已完成。默认关闭的新入口有真实五 Agent 调度、人工复核与 HTML/XLSX 多卷签名下载；市场深诊断、财务 v3 正式 Agent/文件、店铺独立总览及真实业务/模型/原生 Excel/生产采用仍未闭合。不能把“代码已合进隔离工作树”或合成测试通过写成五阶段全部完成。

2026-09-25 当前小阶段：另加显式 renderer 9 推广专项试用版，提供行动建议、来源范围、缺口对账及完整 HTML/XLSX/JSON 多卷交付；预算明确未交付，旧 renderer 7 保留。隔离 PostgreSQL 12 项、客户端分卷下载 35 项、备份纯校验 22 项与页面构建通过；0045→0046 升级、旧版文件字节/权限、双备份恢复及空数据回退演练通过 `.runtime/ai-pg-bd27e4980c95/business-promotion-trial-file-upgrade-evidence.json`。见[本阶段交接清单](AI_BUSINESS_HANDOFF_20260925.md)。

2026-09-25 续验：参考志高商用设备京东店的历史 30 天本期/前期推广提取文件各 281,759/293,336 行，逐日压缩摘要、行数和业务日核验通过；两期费用/展现/点击与参考 XLSX 相符。v2 整份证据 2,000 页/64 MiB 上限不足以完整封存该两期来源。E 盘从本期 7 天 61,543 行制作未人审的 renderer6 双格式来源预览，三卷/清单与静态行摘要核验通过，**不是** v9 正式发布。v9 试用证明升为 v2，增加表标题/说明/列声明摘要，正常发布及漂移拒绝的目标 PG 3 项 `.runtime/ai-pg-d52735ab6fbd/tests.log`、相关纯测 20 项通过。具体范围、来源版本差异及缺口见[志高试用验收记录](AI_BUSINESS_ZHIGAO_TRIAL_ACCEPTANCE_20260925.md)。

v4 大来源的下一内部候选已加[推广单段票据页重放](AI_BUSINESS_V4_SEALER_SEGMENT_REPLAY_CANDIDATE.md)：最多16页，复核原文字节/摘要、工具请求、游标、行数、收据链和0036段进度；第17页起须受保护前段回执校验。纯测试7项通过。当前没有受保护独立凭据、真实 claim/MAC 调用方、持久独立复核回执或财务段重放，输出固定 `candidateOnly=true`、`authorityVerified=false`，不能据此封存、派发 Agent 或发布文件。

后续隔离候选补[财务单段纯重放](AI_BUSINESS_V4_FINANCE_SEGMENT_PURE.md)与[0047 独立推广段回执](AI_BUSINESS_V4_SEALER_REPLAY_PROGRESS.md)：财务和推广纯测合计13项；0047 目标隔离 PG 3 项、旧票据 TRUNCATE 负例及0046→0047旧78表/renderer1—7字节/ACL、双备份恢复、空回退再升级通过。回执按当前 claim、来源根、最新 attempt、0036段和前段候选链精确绑定，专用角色仍 NOLOGIN，旧直接 seal 提交保持撤销。当前尚无真实受保护 sealer 进程、财务段持久回执、跨票据17页实际验收或最终同事务 seal+消费包装；不能把候选回执当作来源权威。

[独立执行器接线方案](AI_BUSINESS_V4_SEALER_EXECUTION_PLAN.md)固定一次领取后在180秒租期内可处理多个段、未知结果先读精确回执、过期后用新票据接续的顺序；专用角色激活/派生密钥采纳和真实来源封存尚未执行。当前 Office 可执行文件存在，但只读许可状态为 `NOTIFICATIONS`，不能据此算原生 Excel 已验收。

0048 再把[财务候选段回执](AI_BUSINESS_V4_FINANCE_REPLAY_PROGRESS.md)接入 0047 同一默认关闭账本，推广路径继续保留；隔离 PG 3 项、0047→0048 保持79表/旧renderer1—7字节和函数OID/ACL、前后备份恢复、空回退再升级通过。专用角色仍 NOLOGIN、正式 seal 仍关闭；财务候选回执不等于真实财报页的独立重放或来源权威。

0049 增加[claim 绑定的单来源窄读](AI_BUSINESS_V4_SEALER_SOURCE_BRIDGE.md)，补齐 0042 context/segment/page 未返回的真实查询、完成页/行/字节和检查点摘要；仅专用 NOLOGIN sealer 有 EXECUTE。隔离 PG 4 项、0048→0049 旧79表/renderer1—7字节与旧函数OID/ACL、前后备份恢复和空逆迁移通过；仍无真实登录凭据、执行器或 seal 权威。纯 MAC 校验另有两项用途分隔测试，只有调用方提供派生段密钥并另核当前 claim 才可用于独立重放。

0050 修复[claim 绑定回执读取](AI_BUSINESS_V4_REPLAY_READ_CAST.md)的 PostgreSQL `varchar(64)`→`text` 返回类型；隔离目标 PG 4 项与[默认关闭的单段编排](AI_BUSINESS_V4_SEALER_STEP_CORE.md)共测通过 `.runtime/ai-pg-a1a7fdb52846/tests.log`。0049→0050 的完整隔离升级/空回退/再升级、旧79表及 renderer1—7 字节、所有其他 AI 函数不变、READ OID/ACL/签名不变、前后独立备份恢复通过 `.runtime/ai-pg-7ddc07cd8f88/business-v4-replay-read-cast-upgrade-evidence.json`。单段编排只在测试中用合成来源、已领取 claim、派生段密钥写入推广/财务候选；专用角色仍 NOLOGIN，正式权限交接、跨票据17页、最终 seal+消费、真实业务规模及生产采用均未完成。

0051 修复[跨票据前段 claim 列歧义](AI_BUSINESS_V4_PRIOR_CLAIM_COLUMN.md)，让第17页可在首个180秒 claim 自然过期后由第二张票据续段；完整 owning 17页、0036两段持久证明、前段候选回读与幂等的一项隔离PG通过 `.runtime/ai-pg-6e739a683421/tests.log`。迁移与有回执禁回退三项 PG 通过 `.runtime/ai-pg-5743154be602/tests.log`；0050→0051 旧79表/renderer1—7字节、仅写函数体变化且 OID/ACL/签名不变、双备份恢复、空回退再升级通过 `.runtime/ai-pg-7033b21c9550/business-v4-prior-claim-qualification-upgrade-evidence.json`。父仍 `collecting`、候选非权威、专用角色仍 NOLOGIN，无最终 seal/Agent/生产采用。上段 0050 的“跨票据17页未完成”描述是该检查点当时状态，以本段为准。

下一候选仅新增[最终封存请求纯合同](AI_BUSINESS_V4_FINAL_COMMIT_CONTRACT.md)：复用现有 v4 正文规范与父 HMAC 目的分隔，固定 `commit-seal-v1` 请求摘要；8 项本地纯测试通过，未接数据库或生产。0041 已撤销专用角色直封 EXECUTE，0043 无消费写入函数，因此最终 seal+消费必须另有受保护同事务包装；纯摘要不等于票据、授权或正式封存。

0052 已新增[默认关闭的 seal+消费同事务包装](AI_BUSINESS_V4_COMMIT_CONSUMPTION.md)，只有 NOLOGIN 专用角色可调用；在 claim、版本化请求摘要、全部连续候选回执及来源根复核后才调用旧 seal 并写 0043 消费。隔离 PG 五项目标测试通过 `.runtime/ai-pg-929da4f9223d/tests.log`，撤权负例另有一项 `.runtime/ai-pg-d5e9d4b49403/tests.log`；0051→0052 旧79表/renderer1—7字节/所有旧 AI 函数不变、新函数精确权限与所有者、双备份恢复/空回退重做通过 `.runtime/ai-pg-de8865ae6bd3/business-v4-commit-consumption-upgrade-evidence.json`。数据库只能核 MAC 形状，真实父 HMAC 必须由未来受保护进程先验；没有 LOGIN/凭据、当期 authority 交接、正式 Agent/文件或生产采用。真实575,095行跨源重放与 180 秒票据内最终包装耗时未验。

[默认关闭的最终调用层](AI_BUSINESS_V4_FINAL_COMMIT_STEP.md)已以注入受限连接和派生父密钥完成真实 PostgreSQL 两项目标测试 `.runtime/ai-pg-e2bcd735e87a/tests.log`：一次性调用 0052、消费精确回读、原票据独立恢复、错误发行摘要在提交前拒绝。相关读取/回执/最终包装同库组合回归17项 `.runtime/ai-pg-e8139845286e/tests.log` 通过；但这些仍是隔离合成角色与来源，不能替代正式凭据、业务规模、真实报告 Agent 与双格式文件验收。

推广预算另有[renderer10 纯候选](AI_BUSINESS_PROMOTION_BUDGET_RENDERER10_CANDIDATE.md)：仅在同报告批准绑定与拥有方固定预算重算材料齐全时产出分配/情景/口径三表和三张可编辑试算页的预检输入；缺固定预算只列缺口。预算及分卷相关 38 项本地纯测试通过，尚未注册 renderer10、发布 HTML/XLSX 或做原生 Office 验收，不改变 v9 的 `budgetDelivered=false`。

[renderer10 临时 HTML/XLSX/JSON 多卷候选](AI_BUSINESS_PROMOTION_BUDGET_RENDERER10_VOLUMES.md)现已通过真实 owning 报告/来源与固定预算 `_roots` 的前后复核、预算三表或无预算缺口、可选三原生试算页、逐文件 SHA/完整清单校验；预算/分卷纯回归41项、隔离 PG 有预算/无预算/异常三项 `.runtime/ai-pg-ca0e72a2c458/tests.log` 通过。首次有预算路径的基础根与人审扩展根结构比较错误已修复。此版本仍未注册 DB ready 或公开下载路由，不能说预算正式交付；原生 Office 复算与真实业务规模仍缺。

市场 v2 第五工具前置为[0053 材料准入的暂停快照](AI_BUSINESS_MARKET_V2_ADMITTED_PAUSED.md)：保留0044/45旧 parked 报告和材料回执不变，另建同账号/selector/manifest 的 admitted report/flow；实际 allowedTools 仍空、model 空，数据库禁止节点、job、派发与结果。隔离 PG 三项目标 `.runtime/ai-pg-86461140037a/tests.log`、目录撤掉 job guard 的负例 `.runtime/ai-pg-7a6b5b162f07/tests.log`、旧 parked/材料/新 admitted 同库18项 `.runtime/ai-pg-edd8cc7dda38/tests.log` 通过；0052→0053 旧79表/renderer1—7字节/旧函数与停放材料行保持、新七函数七触发器精确、双备份恢复/空逆迁移重做通过 `.runtime/ai-pg-f8faa8ebc693/business-market-v2-admitted-paused-upgrade-evidence.json`。正式第五工具/五 Agent 仍须下一版本的工具目录、模型策略与数值引用证据，不可把 paused 快照说成已运行。

[市场第五工具未注册读取候选](AI_BUSINESS_MARKET_V2_FIFTH_READ_PREVIEW.md)能从 0053 admitted-paused 根重新核同账号/selector/0045 材料与封存来源，返回有界三表 summary/page/row 与精确数值引用基础；纯合同3项、隔离 PG3项 `.runtime/ai-pg-49dd0c30c191/tests.log` 通过。注入 job/provider 身份只是声明，`persistedRead=false`，没有真实派发或 Agent 已读证明。

[v4 三期日期包络纯候选](AI_BUSINESS_PERIOD_BOUND_PLAN_V1.md)另固定京东同店推广本期/前等长/去年同期的逐来源原始日期、解决后日期与预期业务日摘要；财报保持自然月，不做日摊分。18 项本地纯测通过，未改既有 v4 计划、数据库或 renderer；缺日与零日仍须拥有方证明，当前正式词货报告仍只接一个基期。

[renderer10 持久暂存候选](AI_BUSINESS_PROMOTION_BUDGET_RENDERER10_STAGE.md)已允许完整预算卷分块入库并以当前批准/预算根重建全字节验证，但数据库在文件行和完整分块两层永久拒 v10 `ready`。隔离 PG 四项目标 `.runtime/ai-pg-74e6f194d49e/tests.log`、目录撤权负例 `.runtime/ai-pg-3c349136f20c/tests.log`、0053→0054 旧79表/旧1—7文件/v9 OID ACL和ready拒绝、双备份恢复/空回退重装 `.runtime/ai-pg-755537cd7e40/business-promotion-budget-v10-stage-upgrade-evidence.json` 及旧 v9 多卷/签名下载八项同库 `.runtime/ai-pg-1e89d4206954/tests.log` 通过。仍无公开路由、正式文件下载或原生 Office 验收。

0055 的[v4 三期日期候选侧表](AI_BUSINESS_V4_PERIOD_PLAN_SIDECAR.md)在四来源完成、当前管理员与最新尝试下由数据库独立重算30天/闰日三窗口及预期日期摘要，始终不授权；隔离PG六项 `.runtime/ai-pg-ca5ae4f621c3/tests.log`、0054→0055旧79表/renderer1—7/旧函数权限保持、新第80表与双备份恢复 `.runtime/ai-pg-1bd88d32948d/business-v4-period-plan-upgrade-evidence.json` 通过。表清单已区分冻结79与当前80，备份22项纯回归通过。仍没有拥有方逐日零日证明或正式三基期Agent/文件。

0056 的[市场材料正式角色窄桥](AI_BUSINESS_MARKET_V2_ROLE_BRIDGE_0056.md)修复0053触发器/第五预览直接读取0045封闭侧表的问题，不给 reader/writer 整表权限。真实session_user角色下旧 admitted/第五预览十项 `.runtime/ai-pg-c50f832e0409/tests.log`、0055→0056旧80表和市场原行保持、两guard仅安全属性变化及新窄函数/双备份恢复 `.runtime/ai-pg-501877b11b13/business-market-v2-role-bridge-upgrade-evidence.json` 通过。实际 Agent/job/模型仍关闭。

0057 新增[预算 v10 发布前不可变证明](AI_BUSINESS_PROMOTION_BUDGET_V10_ATTESTATION_0057.md)，独立 NOLOGIN 角色只能在同尝试、同审批/预算根、持久卷描述符与完整JSON摘要一致时写一次，普通角色无表/函数写权；目标PG三项 `.runtime/ai-pg-35577d49eae2/tests.log`、0056→0057旧80表/renderer1—7/旧函数/ready语义保持、新第81表及独立恢复 `.runtime/ai-pg-1622c6faccd4/business-promotion-budget-v10-attestation-upgrade-evidence.json` 通过。数据库仍不解析 HTML/XLSX 语义，且 v10 `ready` 仍被0054双层拒绝；无正式发布、下载或原生Excel验收。

[0057 预算拥有方预检](AI_BUSINESS_V10_ATTESTATION_OWNING_PREFLIGHT.md)默认关闭，在当前管理员/批准/预算根下全量重读持久文件，生成不可变证明正文而不写库；隔离PG四项 `.runtime/ai-pg-571c024c5cbd/tests.log`（609.527 秒）、相关纯三项通过。它核持久XLSX容器SHA并逐条比对新渲染ZIP内容，不能说两次ZIP容器字节完全相同。

[0058 原子发布数据库门禁](AI_BUSINESS_PROMOTION_BUDGET_V10_PUBLISH_GATE_0058.md)已让独立 NOLOGIN 验证角色在同 run/attempt 0057证明、当前审批预算根及版本化请求摘要下同事务将 v10 staged→ready，并可按原摘要查询未知结果；普通 writer 直改仍拒。隔离PG两项 `.runtime/ai-pg-6646a50f5d94/tests.log`、0057→0058旧81表/renderer1—7/旧函数权限与v9拒绝保持、双备份恢复/空回退 `.runtime/ai-pg-423a50ee3e05/business-promotion-budget-v10-publish-upgrade-evidence.json` 及旧 v9 多卷/签名下载同库八项 `.runtime/ai-pg-c21f2c3f7847/tests.log` 通过。仍无受保护真实凭据、应用显式发布器、v10下载路由或原生Office验收，不把合成ready当正式交付。

0058 默认关闭的应用发布调用器已通过真实 NOLOGIN 角色四项隔离测试 `.runtime/ai-pg-e5a8482f4cf5/tests.log`，覆盖一次证明/发布/OUTCOME、禁用和预检漂移、响应丢失后不重试写入。它仍是注入连接的内部入口，没有正式登录凭据和公开发布路由。

[0059 预算 v10 下载窄栅栏](AI_BUSINESS_PROMOTION_BUDGET_V10_READER_FENCE_0059.md)按当前审批、证据、预算和文件身份重算版本化摘要；真实 reader/attestor 隔离 PG 3 项 `.runtime/ai-pg-27eda3e1c066/tests.log` 与 0058→0059 旧 81 表/旧文件函数冻结、前后恢复、空回退 `.runtime/ai-pg-2808dd63f241/business-promotion-budget-v10-reader-fence-upgrade-evidence.json` 通过。只提供窄 ready 回执，没有公开下载路由、正式凭据或原生 Office/真实规模验收。本小阶段[交接清单](AI_BUSINESS_HANDOFF_0059_20260925.md)记录下一步市场同 surface 五工具和实际下载接线。

[市场 v2 第五工具默认关闭目录候选](AI_BUSINESS_MARKET_V2_REGISTRY_GATE.md)只在显式新surface/双端flag下展示第五个只读工具；旧v1四工具静态目录不变。Django隔离路由3项 `.runtime/ai-pg-ec558dca39ce/tests.log`、新旧Node目录12项和本地构建通过。当前请求ID仅派生非持久声明，`persistedRead=false`、无真实job/模型。

[市场 v2 同 surface 五工具预览目录](AI_BUSINESS_MARKET_V2_FIVE_TOOL_CATALOG.md)已合入：四个新别名复用正式已发布角色包、推广拥有方分析/预算/词货路径，第五项保持原有界市场候选；双端开关默认关闭，旧v1目录不变。首轮真实PG揭出旧screening-profile reader误接原推广报告，修复后隔离reader角色3项 `.runtime/ai-pg-759ad2f0271c/tests.log` 和未发布包精确拒绝1项 `.runtime/ai-pg-81e89178091e/tests.log` 通过；新目录/第五工具Node8、旧v1目录/工具Node16、Django路由2、ESLint与本地构建通过。仍 `persistedRead=false`，无Agent job/provider本人已读、真实规模12秒或执行profile。当前[交接清单](AI_BUSINESS_HANDOFF_MARKET_V2_FIVE_TOOLS_20260925.md)列出下一阶段。

[v4 推广页容量测量候选](AI_BUSINESS_V4_PROMOTION_PAGE_CAPACITY_CANDIDATE.md)逐页核规范原字节、行宽/页包络、控制与日期/游标，再用既有 v4 上限作诊断复算。独立对抗审查发现低估可被丢失、游标自证及行/控制字段过宽；修成 v2 后 `v4Measurement=null`、`capacityPlanMeasurementAvailable=false`，不能直接喂计划，相关纯10项通过。[默认关闭的拥有方容量接线](AI_BUSINESS_V4_PROMOTION_OWNING_CAPACITY_BRIDGE.md)双遍重放当前持久页及内部成功审计，仍不授予上游签名/计划/报告权限，隔离PG待验；当前真实三期页和180/600秒规模未测，575,095 历史行只作风险估计。[预算 v10 默认关闭分卷下载候选](AI_BUSINESS_PROMOTION_BUDGET_V10_DOWNLOAD_CANDIDATE.md)逐片前后核0059窄回执、文件身份、字节和SHA，客户端复核整卷及最终账号；新旧Node75、真实reader PG4（含撤权）、旧v9 PG5、ESLint和首次构建通过。首轮PG夹具缺既有reader角色GRANT，补正式provision后通过，生产权限未扩大。页面仍不开放v10下载，真实预算/Office/规模未验。

后续验收更新：v4 拥有方容量接线隔离PG4项 `.runtime/ai-pg-77ea697e7c14/tests.log` 通过，仍只是合成三期与内部签名审计，真实57.5万行未测。[预算v10页面分卷候选](AI_BUSINESS_PROMOTION_BUDGET_V10_PAGE_CANDIDATE.md)在双端开关精确启用时显示清单/逐卷SHA与进度取消，默认关闭；新旧Node79、目标ESLint和默认关闭构建通过，未生产启用/视觉/Office验收。[0060市场五工具暂停执行档案](BUSINESS_MARKET_V2_EXECUTION_SNAPSHOT.md)真实PG5项及0059→0060旧81表/旧函数/文件/双备份恢复通过；[0061 SQL-owned context证明](BUSINESS_MARKET_V2_CONTEXT_PROOF.md)真实PG2项及0060→0061旧81→82表/权限/双备份恢复通过。Agent job/model仍硬拒，后续同job/provider真实已读与数值引用未实现。

继续验收：[0062同job/provider已读门禁](BUSINESS_MARKET_V2_READ_RECEIPT_CANDIDATE.md)当前必须拒虚构job，PG2及0061→0062旧82→83表/双备份恢复通过；[0063五Agent执行前置计划](BUSINESS_MARKET_V2_EXECUTION_PLAN_V3.md)零付费/零派发，PG3及0062→0063旧83→84表/双恢复通过；[0064隔离五角色合成持久链](BUSINESS_MARKET_V2_SYNTHETIC_VERTICAL_0064.md)各有真实通用job/provider/tool行，PG3及0063→0064旧84表/精确守卫版本化/双恢复通过，所有结果仍 `externalProviderCalled=false`、`persistedRead=false`。真实模型价格与人工费用上限尚无权威来源，未进行付费调用；[纯费用包络](AI_BUSINESS_MARKET_MODEL_COST_ENVELOPE.md)默认不授调用，3项通过。

[0065模型费用待核需求账](BUSINESS_MARKET_V2_COST_LEDGER_0065.md)以当前五Agent计划和模型版本重算 CNY token 最坏需求，物理保持预留0/状态pending/调用关闭；隔离真实角色PG2项 `.runtime/ai-pg-20df4bcf7ab3/tests.log` 及0064→0065旧84表/文件/旧函数权限与双备份恢复 `.runtime/ai-pg-21f2c2323009/business-market-v2-cost-upgrade-evidence.json` 通过。费率SHA与批准SHA只作待核声明，不能证明实际价目、汇率或人工授权，也未调用模型。完整阶段验收边界见[完成门槛清单](AI_BUSINESS_COMPLETION_GATE_20260925.md)。

[v11持久但未发布暂存](AI_BUSINESS_PROMOTION_BUDGET_V11_DURABLE_STAGE_CANDIDATE.md)由0066精确依赖0065，只准当前批准预算报告生成slim HTML与XLSX完整块链后进入 `staged_unpublished`；真实writer隔离PG3项 `.runtime/ai-pg-8b34c361d408/tests.log`、旧v10真实reader4项 `.runtime/ai-pg-718e1d69e665/tests.log`、旧v9多卷5项 `.runtime/ai-pg-2215c3d8ef06/tests.log` 和0065→0066旧85表/文件/函数权限/双备份恢复 `.runtime/ai-pg-2555a7bf63a8/business-promotion-budget-v11-stage-upgrade-evidence.json` 通过。数据库仍双层硬拒v11 ready、公开控制与下载。[市场付费前门禁](BUSINESS_MARKET_V2_PAID_PREFLIGHT.md)纯5/隔离PG1通过，通用Agent每次provider/tool前识别市场家族；0065预留0使五合成job也不能被当付费模型请求。[同报告13表纯组合与成对预览](AI_BUSINESS_REPORT_COMPOSITION_OWNING_PREVIEW.md)纯相关17及隔离PG3项 `.runtime/ai-pg-f7246bac2aae/tests.log` 通过；仍限v2小数据/无真实Agent权威。

交付/商品关系方面，[v10瘦身HTML纯候选](AI_BUSINESS_PROMOTION_BUDGET_V10_SLIM_HTML_CANDIDATE.md)默认不启用，合成57.5万行[静态及Chrome验收](AI_BUSINESS_PROMOTION_BUDGET_V10_SLIM_575K_ACCEPTANCE.md)得28.66MB HTML、42.70MB XLSX、浏览器大表2.7秒/约236MB JS堆，但不是客户数据或Excel原生复算；[v11临时未发布候选](AI_BUSINESS_PROMOTION_BUDGET_V11_SLIM_STAGE_CANDIDATE.md)另锁压缩证明，现行DB只认v10，不能暂存/ready。关键词×搜索词×计划×单元×匹配×跟单SKU已有[纯/拥有方v2](AI_BUSINESS_ATTRIBUTED_SKU_RELATION_CANDIDATE.md)与[单窗v4纯候选](AI_BUSINESS_ATTRIBUTED_SKU_RELATION_V4_CANDIDATE.md)，后者合成本期281759和前期293336行各自守恒；[v4拥有方](AI_BUSINESS_V4_ATTRIBUTED_SKU_OWNING_CANDIDATE.md)PG3通过，但Agent/renderer未接。[财报自然月与B端缺源证明表](AI_BUSINESS_FINANCE_B2B_SOURCE_PROOF_CANDIDATE.md)纯3/拥有方PG5通过，始终不同报告根、不能跨域加总。[SKU三期候选](AI_BUSINESS_CROSS_SOURCE_SKU_WINDOWS.md)撤去未验证历史ERP SKU增长率，保留原生SKU/推广可比规则，相关纯11项通过。真实来源/同报表权威、Office许可和最后HTML/XLSX业务终验仍缺。

> 2026-09-24 当前检查点：本页下方较早的逐项表格和历史检查点保留了开发当时的状态；以此段和“最新组合验证”为推广链路的现状。最终组合测试正在收束，未标记生产采用。

与用户参考成品的逐表差距和四个后续纵向验收切片见[参考推广诊断差距清单](AI_BUSINESS_REFERENCE_PARITY_GAPS.md)。参考 XLSX 实际 30 表、HTML 26 张可检索表；完整原始/原生表存在不等于跨来源归属诊断已完成。

参考差距的第一条数据底座继续补齐：单店 ERP/原生 SKU/SPU/推广三期来源有未注册的[精确选择合同](AI_BUSINESS_CROSS_SOURCE_KPI_PLAN.md)，参考推广源 575,095 行超过现有 v2 每来源 2,000×100 行上限会明确标不支持。ERP [逐事实归属](AI_BUSINESS_ERP_FACT_ASSIGNMENT.md)按当前 master 的完整唯一 SKU+SPU+品类归属，多义/不完整/未匹配保留源行和金额；[五层逐日回卷](AI_BUSINESS_ERP_FACT_ROLLUPS.md)分别对店铺、品类、SPU、SKU、未分配池按 11 项有符号 ERP 指标守恒。纯测试 7+9 项通过；[逐日并列候选](AI_BUSINESS_CROSS_SOURCE_DAILY_COLUMNS.md)与[封存内读材料](AI_BUSINESS_CROSS_SOURCE_DAILY_OWNING.md)已将 ERP、商智SKU/原生SPU、推广分来源对齐，缺值/缺日和逐日守恒独立核验，隔离 PG 5 项 `.runtime/ai-pg-818803e7d020/tests.log` 通过。尚无跨域金额合计或正式 Agent/renderer。

ERP 五层逐日材料随后绑定到封存 v2 integrated report 的固定 mappingPlan 与 sales/master pair，按真实 Reader 全页重算并核对前后账号/报告，产生五张临时类型表及 NDJSON/摘要。隔离 PG 4 项 `.runtime/ai-pg-3a3ced803741/tests.log`、更新后的纯 assignment+rollup 10 项通过；仍不进入正式 Agent/renderer，也不混合其他域金额，见[内部 ERP 材料说明](AI_BUSINESS_ERP_ROLLUP_OWNING.md)。大来源扩容的分期方案见[v4 大规模证据设计](AI_BUSINESS_LARGE_EVIDENCE_V4_PLAN.md)；物理账、京东推广本期采集与内部重放候选已接，后续封存、Agent、文件和生产采用未完成。

## 最新组合验证

| 链路 | 当前状态与证据边界 |
| --- | --- |
| 推广词货五 Agent | 新 `screening-promotion-v1` 精确来源、上期/去年同期基期、四工具、每节点读取与数值引用、三专业角色→独立复核→报告、人审均已接真实持久运行路径。隔离 PostgreSQL 假提供者/假工具完整五 Agent→批准→文件测试通过，日志 `.runtime/ai-pg-c60392cc0a76/tests.log`；签名工作台详情 4 项、签名创建/人审/下载组合亦已通过。真实付费模型和业务判断尚未验收。入口由默认关闭的 `AI_PROMOTION_AGENT_RUNTIME_ENABLED` 控制。 |
| 工程文件 | renderer 7 将封存原始/原生事实和推广分析表拆成完整 HTML/XLSX 多卷；19 来源、两卷、表/行/摘要同源测试通过 `.runtime/ai-pg-54ff6919563b/tests.log`。持久分块、续传、重建、取消及签名公开创建/下载正反例通过 `.runtime/ai-pg-e763642a09fd/tests.log`、`.runtime/ai-pg-3e571dcc7d65/tests.log`、`.runtime/ai-pg-1b8f8be6165c/tests.log`。公开下载现用完整六节点、五任务、审批时序及任务生命周期栅栏，READY 后篡改拒绝测试通过 `.runtime/ai-pg-c929e0b51153/tests.log`。旧 v4/v6 文件回归 14 项通过 `.runtime/ai-pg-512f146f2433/tests.log`。原生 Excel 开启/重算仍待有效 Office 环境。 |
| 市场、财务 | 市场已到封存绑定只读页、区间价格带与精确两日观察的内部签名 Reader、三张完整数据材料；正式五 Agent/文件尚未纳入这些派生表。财务已有权限最小化 owning reader、签名内部读取、v3 计划/目录、纯续读检查点；0030/0031 允许财务和日来源分别签名续读与 CAS 落地，0032 给每页绑定不可变成功工具回执。0033 可内部封存并重新验证整份混合 v3，但无公开路由/自动调度或 v3 报告；新旧 PG/65→66 表独立备份恢复通过。内部追加限 64 页，跨源非原子快照、缺日/缺月均披露。新版比较规则保持纯候选，旧报告口径不变。 |
| 兼容与采用 | 0025—0034 独立迁移/备份恢复、旧 AI 表及文件协议保护已验证；旧 screening HTTP/文件 13 项通过 `.runtime/ai-pg-d18f9a5db5de/tests.log`。上次全 Node 2543 项通过、20 项既有跳过、0 失败，v3 内部读取改动另有 PG 25 项、Node 14 项及构建通过；暂停后主线新增一笔导入链提交，需重新整合并跑受影响回归。主检出目录未修改，生产未部署、未调用付费模型。 |

2026-09-24 追加：0030 finance-only 物理账本与只读重放已接入隔离工作树，24 项相关 PG、0029→0030 独立备份恢复通过；在这个检查点父 v3 仍为 `collecting/manual`，签名页 append 尚未开放，见[财务物理账本](AI_BUSINESS_FINANCE_V3_PHYSICAL_LEDGER.md)。推广工作台已要求从封存目录选精确京东本期来源与可选基期；新报告查看页按真实节点显示进度，在完整内容出现前标“未核验”，分别见[工作台与查看页](AI_BUSINESS_PROMOTION_WORKBENCH.md)。最终新迁移与完整五 Agent→人审→双格式交付同跑 2 项 PG 通过 `.runtime/ai-pg-d46e344e0d9a/tests.log`。报告查看页改动后的全 Node 组合回归尚未重跑。

随后财务签名读取→单页 CAS 追加的内部函数已接，相关 PG 26 项、Node 58 项通过；仅 `business_collection` 无范围管理员可使用，最多 64 页，无公开路由/自动调度，父 v3 和日来源仍不封存，见[签名采集边界](AI_BUSINESS_FINANCE_V3_SIGNED_COLLECTION.md)。市场新纯候选把区间价格带与两单日观察明确拆开：所有来源仍共享原始分析区间，单日对比从各自完整封存多日页筛出，缺日不作出榜；旧+新纯测试 18 项通过。新增内部 owning Reader 从同报告封存多日页重算观察表并提供有界分页/精确行读取，签名内部 GET 与 TS 只读适配已接；新旧市场路由隔离 PG 14 项 `.runtime/ai-pg-1e5dbc26c59d/tests.log`、目标 Node 3 项通过。未加入公开代理、Agent 目录或 renderer，见[市场候选](AI_BUSINESS_PROMOTION_MARKET_CANDIDATE.md)。

市场数据材料随后增加 v2 组合版：同报告区间价格带与两明确观察日的进出榜生成三张完整类型表/NDJSON，分别固定当前/基期来源、表/行摘要与缺日期 null；旧+新纯测试 20 项、隔离 PG 3 项 `.runtime/ai-pg-a79c6b3f8a8a/tests.log`。仍是未注册数据材料，不等于有五 Agent 判断的正式 HTML/XLSX。

市场数值引用另有未注册候选合同与内部重算器：按实际报告/job/角色、同一来源与行摘要提取区间价格/样本排名，空值和缺日不能当零，市场样本不能归入本店、ERP或B端销售；纯测试 4 项、隔离 PG 2 项 `.runtime/ai-pg-23edc022e6b8/tests.log`。候选明确 `agentReadPersisted=false`，尚无市场 v2 profile 的实际持久 dispatch/result 或本人读取证明，不能直接作为五 Agent 正式判断或人审证据。

v3 混合来源增加 0031 日页物理门禁、共享可信目录和日来源只读完整块重放；财务 inspect 已改用共享目录，先财务后日数据、先日数据后财务两种顺序均通过。相关新旧 PG 13 项 `.runtime/ai-pg-a08d60f64bac/tests.log`，0030→0031 前后独立备份恢复、旧 1–7 文件字节和权限、财务来源不变与带日事实逆迁移拒绝见 `.runtime/ai-pg-adc3232aff44/business-v3-daily-pages-upgrade-evidence.json`。随后日来源签名首取与真实末块续读的内部单步推进已接，相关新旧 PG 14 项通过 `.runtime/ai-pg-db0442497197/tests.log`；无公开路由或自动调度，真实跨进程/过期游标/规模仍待验。**整个混合父任务封存、Agent 和文件仍未接入。**

0032 为新 v3 财务/日来源的每个事实块增加成功中央工具审计的一对一不可变回执；审计响应摘要、原 UTF-8 块、账号、工具、来源和页序共同核验，并在完成检查时再次由 owning Reader 重放该目标来源。旧 0030/0031 直接写入的事实保留，但无回执不能称为已核验来源。最终新旧组合 PG 51 项 `.runtime/ai-pg-5498f45ad42c/tests.log`，0031→0032 独立备份恢复见 `.runtime/ai-pg-587ab1357f1d/business-v3-tool-receipts-upgrade-evidence.json`：65→66 张表、旧 1–7 文件字节和权限不变、新表最小权限与带回执逆迁移拒绝。正常来源响应没有上游独立数字签名，此回执证明受信 Worker/HMAC 工具调用链，仍不构成整份 v3 证据已封存或业务质量验收。最新 Node 全量回归正在重跑；前次 2 个失败为旧 65 表夹具，已修并有目标测试通过。

0033 增加内部混合 v3 父封存与封存后复核：只在全部日/财务来源 finished、逐来源 owning 全页重放、逐页成功工具回执、父与来源 CAS/额度复验后一次写入版本化 seal；缺日只表示未观察到来源行、缺月只按真实发布批次披露，跨领域不是原子快照。旧公开 v1/v2 证据列表与详情不读内部 v3。新旧 PG 47 项 `.runtime/ai-pg-c86c116fe0c0/tests.log`、封存后防追加/删回执 PG `.runtime/ai-pg-a0b5b9c13b60/tests.log`、0032→0033 独立备份恢复 `.runtime/ai-pg-a21dc9023a99/business-v3-parent-seal-upgrade-evidence.json` 通过。仅有数据库 `sealed` 状态不足以授权报告，必须调用内部 `verify` 重建规范全文；v3 报告/Agent/HTML/XLSX 仍关闭。

封存后的 v3 新增**只读、未注册**报告准入候选：内部调用 `seal.verify`，把日事实与财报 `monthly_context` 分开固定到精确来源修订、收据链和缺口，禁止将月财报摊入 SKU 或把可能重合的 ERP/B端/广告成交金额直接相加。纯测 2 项和与旧 v2 证据合跑的隔离 PG 19 项 `.runtime/ai-pg-97fcf7803331/tests.log` 通过；未创建 AiReportRun、工作流、Agent、模型调用或文件，见[报告候选](AI_BUSINESS_V3_REPORT_ADMISSION.md)。

0034 在独立 append-only 表里持久化**暂停的 v3 报告意图**，不复用旧 AiReportRun/工作流图：封存引用、日事实、月度背景、五专业角色与人工复核节点、固定暂停原因均由新版本协议绑定。新旧 PG 39 项 `.runtime/ai-pg-aa321362bd5a/tests.log`，66→67 表和旧 renderer1–7 字节/权限的独立备份恢复 `.runtime/ai-pg-fcbb9e349bae/business-v3-report-intent-upgrade-evidence.json`，维护 Node 12 项及备份 Python 10 项通过。没有真实 Agent、模型或文件任务；意图不能当作已交付报告，见[暂停意图说明](AI_BUSINESS_V3_REPORT_INTENT.md)。

v3 暂停意图现有内部**签名只读来源桥**：首个目录页完整 `seal.verify`，后续 10 分钟用途限定 HMAC 句柄按当前账号版本、意图、封存摘要与 sourceKey 逐块核摘要/回执，返回前复验权限；不用每页全量重扫。旧公开代理与模型工具目录均未加入，读取发生在已有权限的 AI writer 进程且不执行写入。新旧 PG 25 项 `.runtime/ai-pg-4f4189ff06be/tests.log`、Node 14 项、纯合同 2 项和构建通过。**当前桥上限 8 MiB/64 页，超限整次拒绝且不截断；尚无持久 Agent 本人阅读回执，参考整店规模不能据此验收。** 见[内部来源读取边界](AI_BUSINESS_V3_SOURCE_READ.md)。

暂停后已合入主线 `4ad06070` 的导入链状态说明，相关 Node 20 项和构建通过；主 checkout 其他未提交文件未触碰。大规模证据的[纯 v4 容量合同](AI_BUSINESS_V4_CAPACITY_CONTRACT.md)按测量行宽保守预估单源16,384页/2GiB和整任务65,536页/8GiB，纯测4项，测量本身未获来源授权。[0035 独立物理账](AI_BUSINESS_V4_LEDGER_FOUNDATION.md)新增四表，旧67→新71表、renderer1–7字节/权限、前后独立备份恢复 `.runtime/ai-pg-dbb34f6ac662/business-v4-ledger-upgrade-evidence.json` 与新旧PG43项 `.runtime/ai-pg-20a1422c63e4/tests.log` 通过。[京东推广 v4 内部单页采集](AI_BUSINESS_V4_JD_PROMOTION_COLLECTION.md)只接受本期精确来源，已覆盖真实过期游标续读、中断恢复和父/来源计数门禁；新旧相关 PG 32 项 `.runtime/ai-pg-17da9476df8d/tests.log` 通过。父任务封存、Agent和文件均未接入。错误基期仍须未来拥有方完整重放才可获得业务权威，不把工具审计或物理完成冒充上游签名及业务验证。

[京东推广完整词链候选](../backend/business_analysis/promotion_relation_v2.py)另将关键词、搜索词、计划、单元、匹配方式及推广/触发/归因三种 SKU 身份分离保留，纯测试与旧词货测试共 47 项通过；仍受 v2 每来源 200,000 行限制，未接正式 Agent 或 renderer。

[跨来源三窗口店铺对照候选](AI_BUSINESS_CROSS_SOURCE_WINDOW_COMPARE.md)已将逐日并列材料按来源、指标分别给出本期、前期及去年同期的数值、缺口与保守增长率，纯新旧测试 20 项通过；[SKU 三窗口候选](AI_BUSINESS_CROSS_SOURCE_SKU_WINDOWS.md)另逐行核身份、来源分列及逐日未分配/缺 SKU 桶守恒，新旧纯测 18 项通过。两者均未接正式 Agent/文件，SKU 非连续观察的增长率保守留空。[京东推广 v4 完整重放候选](AI_BUSINESS_V4_PROMOTION_REPLAY.md)逐页核原始字节、来源身份、审计收据、控制总额和覆盖，异常字段与 32 KiB 检查点拒绝；更新后 0034→0035 备份恢复 `.runtime/ai-pg-97419d905834/business-v4-ledger-upgrade-evidence.json` 通过。中央内部采集审计现只保存完整参数摘要，重放用前页长签名游标重建并核每次请求；v4 与旧 v3 收据 PG 23 项 `.runtime/ai-pg-aa75fe8eec65/tests.log`、Node 全量 2,546 通过/20 既有跳过/0 失败及正式构建通过。上游独立签名、父封存、Agent、报告均未开放。

[v4 推广三窗口精确选择候选](AI_BUSINESS_V4_PROMOTION_WINDOWS.md)按固定计划区分本期、环比、同比；可选基期缺源或未请求时不补零、无数值比较，错店/错期/重复来源拒绝。相关纯测 16 项通过。同一 v4 run 的三窗口京东推广来源现可分别逐页采集、过期游标续读和完整重放，跨窗口旧版本、错基期/审计窗口拒绝；相关隔离 PG 22 项 `.runtime/ai-pg-7320d93f8faa/tests.log` 通过。尚无父任务封存或跨窗口正式结论。

[v4 完整推广词链流式候选](AI_BUSINESS_PROMOTION_RELATION_V4_STREAM.md)可分别处理三种窗口的关键词、搜索词、计划/单元/匹配及三种 SKU 角色，单源理论上限 16,384 页/2 GiB，三视图按来源逐项守恒；新旧纯测试 28 项通过。高分组合成 5 万行的分组/守恒约 65.3 秒、主 SQLite 文件约 674 MiB，不能线性外推参考 575,095 行。临时主 SQLite 文件与输出有硬上限，但整个临时目录峰值及真实耗时未验，当前仍是未注册候选，不把调用方提供的重放 proof 当权威封存。

[品类/SPU 三窗口候选](AI_BUSINESS_CATEGORY_SPU_COMPARE_CANDIDATE.md)完整重算 ERP 五层回卷中品类/SPU 对已分配 SKU 的逐日守恒，并把商智原生 SPU 独立保留；相关纯测 21 项通过。ERP 映射来自当前 master，历史归属未验证，故各期数值可展示但 ERP 品类/SPU 跨期差额和增长率留空；原生 SPU 仅在同 ID、每日可见、指标无缺值且正基期时比较。无正式 Agent/文件。

[京东 B 端逐日/三期候选](AI_BUSINESS_B2B_DAILY_CANDIDATE.md)只按精确 `jd_b2b` 来源重放本域完整页、日期覆盖与五项指标，缺源仅说明所给目录未安排来源；相关纯测 24 项通过。B 端与 ERP/平台商品销售包含关系未知，跨域占比、增量和合计均留空。[店铺总览与区间 UV 静态缺口](AI_BUSINESS_JD_SHOP_OVERVIEW_UV_GAP.md)确认现有通用导入缺专用字段、业务日和同店同日冲突规则，需先核平台字段与脱敏表头后才能做可信来源。

[v4 财务自然月内部采集与重放](AI_BUSINESS_V4_FINANCE_MONTHLY_COLLECTION.md)已独立于旧 v3 的页/字节上限，逐页绑定成功工具请求摘要、月度批次、完整行链和 32 KiB 检查点；新旧财务与账本隔离 PG 20 项 `.runtime/ai-pg-501999af600c/tests.log`、纯测试 9 项通过。财报只作自然月背景，不按日摊至 SKU；v4 父仍 collecting/manual。市场方向的[同报告封存准入候选](AI_BUSINESS_PROMOTION_MARKET_ADMISSION.md)从已封存推广报告自行重建两市场来源覆盖和双观察日，隔离 PG 4 项 `.runtime/ai-pg-3aacb9f7435b/tests.log` 通过；未读完市场行、未派发专业 Agent、未生成市场版文件。

财务容量估算现与拥有方 **100,000 行**上限对齐，100,001 行的候选计划明确不支持；相关纯测试 13 项通过。独立审查还指出：若持财务 writer 权限绕过正常导入直接改已读事实而不推进修订，页链可混合不同事实快照。正常导入会推进修订，但数据库尚无普遍的写入-修订强制门禁；因此 v4 分段证明和父封存不能据当前版本宣称财务来源权威。市场另有[五 Agent v2 运行纯合同](AI_BUSINESS_PROMOTION_MARKET_RUNTIME_V2.md)，固定本人市场摘要/页/行读取和数值引用要求，旧图不变，纯测 15 项通过；尚无注册派发、持久本人回执或正式文件。

[市场三表内部只读工具预览](AI_BUSINESS_PROMOTION_MARKET_TOOL_PREVIEW.md)在每次读取前由服务器完整重放已封存市场来源并核三张 NDJSON 表，再有界返回摘要/20 行分页/精确行，返回前复验报告和账号；隔离 PG 4 项 `.runtime/ai-pg-eb46d2b08fb0/tests.log` 通过。角色参数目前只是纯合同声明，尚未绑定真实 Agent job/dispatch/result，因此 `agentReadPersisted=false`，不能据此认为市场专业 Agent 已读。

两域写侧缺口现有隔离候选：`finance.0003` 令财务事实/月/批次写入必须同事务推进修订，`finance.0004` 禁止修订与摘要回退；`netshop.0003` 对商品行/批次同样要求同事务修订并阻断回退。正常财务导入、D1 受控二次重灌、两店网店导入及重复/失败路径的相关 PG 分组 57、16、68 项通过；finance.0002→0003→0004 与 netshop.0002→0003 的旧事实独立升级、前后备份恢复、权限/行摘要保持、空库回退与有事实/活动 writer 禁回退通过 `.runtime/ai-pg-e008c2ab040e`。备份与健康检查已按迁移版本验证 marker、触发器、函数及最小权限，真实 catalog PG 7 项通过；未生产采用。源修订更晚并不自动使旧封存页链失效，但必须标历史快照，不能冒称最新或跨域原子。

`ai_assistant.0036` 为同一已完成推广+财务任务追加可恢复的16页段 HMAC 候选，父状态仍 `collecting/manual`；隔离 PG 17 项、71→73 AI 表与旧 renderer1–7 字节/ACL 独立升级恢复通过。分段自身不是正式封存；后续需窄权限 seal_writer、完整段/账号/门禁复验与新状态迁移，且还需真实规模、模型、Excel/HTML 和生产验收。

0037 增加窄 SECURITY DEFINER 只读锁源准入，0038 增加默认 `NOLOGIN` 的独立 seal_writer、唯一 seal 表和数据库原子封存状态门禁；普通 AI writer 伪 seal/status 被拒。0038 相关 PG 20 项、旧73→新74 AI 表及 renderer1–7 字节/ACL、前后独立备份恢复通过；测试里的随机十六进制 MAC **只证明数据库角色和事务门禁**，不是应用签名。后续[内部 verify_seal](AI_BUSINESS_V4_SEAL_VERIFICATION.md)对规范正文真实 HMAC、全部分段链、账号/目录/来源重新核验，随机 MAC 拒绝，纯3项、隔离 PG19项通过；当前仅 AI writer 内部可调用，独立 seal_writer 受保护登录/单次命令、AI reader 用途限定句柄、正式 Agent/报告/文件和真实575,095行容量仍未完成。生产未采用。

内部 `verify_seal` 后续加锁外完整原文重算：逐chunk核原始UTF-8 SHA/字节与收据，末尾才短锁财务/网店修订复核账号、目录与真实seal；隔离 PG 22 项通过。高权限改页原文但保留旧摘要的负例会拒绝，扫描中其他店合法导入可变为 `historical_revision` 而不长时间阻塞；单次全链超过600秒返回 `verification_requires_resume`，尚无跨调用可恢复读取证明。seal_writer 目前无LOGIN、无独立日常凭据，也没有读取完整验证账本的最小权限，不能直接复用该 AI writer 内部验签器来执行封存。

0039 已为默认 NOLOGIN 的 seal_writer 增加仅供 v4 验证账本的读能力：账号和成功工具审计须通过绑定 run/attempt/segment 的窄 SECURITY DEFINER 函数，原始审计参数不返回；相关隔离 PG 与旧74表、renderer1–7字节、ACL的独立前后备份恢复通过。当前五张 v4 物理表仍是**跨 run 全表 SELECT**，不等于单任务行级隔离；正式激活独立凭据前须改同run窄流/RLS。该角色仍无LOGIN、无受保护单次sealer程序，AI reader也无可用的签名封存读取句柄。

0040 已撤五张 v4 物理表的直接 SELECT，改为同 run/attempt/source 的固定窄流；单页在数据库内重核原文 SHA、收据来源/时间链、成功审计并拒绝重复页/收据/段。隔离 PG 27 项通过（其中6项历史0039直接读测试按迁移边界跳过），0039→0040、旧74张AI表/renderer1–7字节/ACL及前后备份恢复通过。`runs/sources` 元数据仍可跨 run SELECT，三只窄流函数仍是无票据参数，角色仍默认 NOLOGIN；0040不代表可安全激活独立凭据或用于正式报告。

0041 只增加短期票据发行和一次领取，迁移同时撤销 seal_writer 对旧无票据读页/读段/读上下文、直接封存及锁源探针的 EXECUTE；角色仍默认 NOLOGIN，不能读取或封存。票据和领取表对普通 AI reader/writer 与专用角色均无直接表权限；库内只存随机 nonce/claim 的用途分隔 SHA，来源根显式拒绝重复 ordinal。隔离相关组合 PG 76 项（26 项历史路径跳过）与补强后定向 PG 23 项（7 项历史路径跳过）通过；0040→0041 旧74→新76表、renderer1–7字节、ACL、前后备份恢复及空表逆迁移通过。票据不能替代应用请求认证，180秒领取租期尚无参考规模证明；带票据逐页读取、续租恢复、原子消费、受保护 sealer CLI 与 AI reader 签名句柄仍待开发，生产未采用。

市场 v2 另增加同一封存报告的三张类型表临时材料：完整重放选定封存页链，核来源身份、游标、原文字节摘要和三表行类型；相关隔离 PG 3 项随上述组合测试通过。它只证明 v2 自身封存页链，**没有** v3/v4 的逐页独立签名工具收据；`authorityVerified=false`，尚未进入正式 Agent 派发、人审或 renderer8。

0042 在保持 seal_writer 默认 NOLOGIN、旧无票据读取和直接封存均不可执行的前提下，新增 claim-token 绑定的上下文/分段/单页读取。每次读取前后复核短租期、账号、最新尝试、来源根和财务/网店写入修订防护；只能返回该任务的原始页，仍没有消费/提交封存、续租、独立CLI或AI reader句柄。财务 v3 同时新增从一个已封存报告意图完整重放自然月材料的内部候选，缺月/空值不填零，比率仅比较基点差额，不向日/SKU摊分；超过现有64页/8MiB读取桥容量整份拒绝。相关合并隔离 PG 49 项（13 项历史接口跳过）通过；0041→0042 保持76表、旧 renderer1–7 字节、ACL与前后独立备份恢复，空 claim 逆迁移通过。正式 Agent/HTML/XLSX 和真实业务验收仍未完成，生产未采用。

0043 新增默认不可提交的消费回执表和新 seal 同事务必须有消费回执的延期约束；旧 seal 行保留，但当前内部验签因无回执明确拒绝。验证前及最长600秒原文扫描后的最终短锁均核消费回执函数与触发器的固定定义，避免高权限维护漂移误报权威。专用角色仍 NOLOGIN、旧直接 commit 无 EXECUTE，没有应用验 MAC 的提交包装。隔离目标 PG 32 项（8 项历史跳过）和0042→0043旧76→新77表、旧 renderer1–7 字节、双备份恢复与空回执逆迁移通过；纯备份19项、维护Node14项通过。市场v2停放报告0044只持久化数据库可核对的封存源根/选择/固定图，真实工具列表为空、流程paused/`market_material_not_admitted`、0 Agent/0模型；隔离 PG 7 项（含伪造来源空值）、0043→0044保持77表/旧 renderer1–7 字节/角色权限/前后独立备份恢复与空逆迁移通过，纯备份20项、维护Node14项通过。正式三表材料准入与 Agent/文件仍缺，两者都未采用生产。

0045 为停放市场报告追加唯一、不可变的三表材料回执候选：独立 attestor 身份仍 NOLOGIN、无正式凭据/CLI/只读桥；普通 AI reader/writer 无该表 DML 或提交函数 EXECUTE。内部准备须先从同一封存来源完整重放三张类型表并退出最终权限/封存复核，再形成候选；数据库只核材料与报告/来源/摘要的绑定和一致性，不把 SHA 当作上游数字正确性的独立证明。隔离 PG 目标10项（含角色模拟、并发幂等、变造/跨报告/撤权）通过；0044→0045 旧77→新78表、旧 renderer1–7 字节、角色权限、前后独立备份恢复及空回执逆迁移通过，新侧表备份门禁在真实隔离 PostgreSQL 回读。纯备份21项通过。仍无正式材料准入执行器、Agent/模型/文件或生产采用。

在此基础上新增默认关闭的**市场样本只读预览**：当前管理员从同一封存目录选双市场窗口、观察日和价格带，签名 POST 只创建 0044 停放报告；签名 GET 每次完整重放拥有方三表，仅返回摘要或指定表最多20行，并在前后复核账号、封存根及 `paused/market_material_not_admitted`。Next 与 Django 均须显式打开各自开关；前端明确显示 `materialAdmitted=false`、`agentReadPersisted=false`、`authorityVerified=false`，不读取0045侧表或启动Agent/模型/文件。隔离后端旧+新组合 PG 10 项、前端目标 Node 20 项、正式构建通过；真实业务规模、浏览器视觉与生产启用尚未验收。


## 四种状态的含义

| 状态 | 判定标准 |
| --- | --- |
| 已接代码 | 存在真实调用路径；同时说明只到纯计算、内部 reader、Agent，还是已到完整报告。仅有模块文件不等于产品入口可用。 |
| 合成验证 | 记录已执行的合成、隔离数据库或浏览器验证及版本范围；旧候选结果是历史证据，不能代替当前组合回归。 |
| 仍需开发 | 尚未实现的合同、服务接线、产品交互或恢复能力，有具体完成条件。 |
| 真实验收 | 使用真实业务来源、真实模型、原生应用或受控采用才可闭合的事项；合成测试不能替代。 |

## 按用户目标逐项核对

| 能力 | 已接代码 | 合成验证 | 仍需开发 | 真实验收 |
| --- | --- | --- | --- | --- |
| 店铺、品类、SPU、SKU、基础关键词/搜索词 | [results.py](../backend/business_analysis/results.py) 已有八个原生维度；网店、ERP、市场 reader → 固定来源目录 → 持久封存 → 分析表的链路存在。店铺维度是所选事实聚合。 | 旧候选已有 19 来源全链路及 156 表导出等记录，见[剩余验收清单](AI_BUSINESS_REMAINING_ACCEPTANCE.md)。当前整合需按受影响链路复验。 | 补下面单列的独立店铺总览、财务和新增派生分析；不能把商品事实的店铺聚合冒充平台独立总览。 | 至少两店、多渠道、本期/基期完整与缺源两组业务对账；确认商品身份、退款、缺日、类目和历史归属。 |
| 同比、环比与截止日 | 旧 [contracts.comparison_periods](../backend/business_analysis/contracts.py) 仍为“前等长周期 + 上年同期夹月底”，现有来源和报告沿用原合同。新增 [comparison_rules.py](../backend/business_analysis/comparison_rules.py) 仅为纯日期规则候选。 | 旧日期/比较测试保留；新规则有独立测试文件，本清单不把文件存在当作当前全链路已验证。 | **新规则尚未注册到计划、reader、证据、Agent 或文件。** 需以新版本固定规则 ID、完整日期和摘要，同时保留旧报告含义。销售页面上月同期与 AI 前等长周期须明确展示。 | 同一业务问题实际日期和增长率对账；45 天滚动同步不保证 30 天环比所需历史或同比覆盖，缺失不能补零。见[比较规则说明](AI_BUSINESS_COMPARISON_RULES.md)。 |
| 市场、ERP/B 端及网店来源选择 | 三域精确选择器已接[工作台](../app/ai-business-workbench.tsx)。ERP 按平台/店铺/渠道精确取数；网店 `b2b` 有独立来源；[映射计划](../backend/business_analysis/mapping_plan.py) 已支持 ERP 与网店主数据关联。 | 旧 ERP 选择器 16 项组件、8 项父页面及相关 Node 验证记录保留；本轮市场/ERP 目录升级恢复见下节。 | 自然语言给出权威身份候选的辅助入口尚缺；ERP 目录失效后的显式重建已有，尚非自动后台重建。 | 正式目录初始化、实际身份和日期覆盖、查询执行计划及规模；核清 B 端与总销售的包含关系，推广归因成交、平台销售和 ERP 销售不能直接相加。 |
| 关键词 × 明确推广 SKU、计划/单元/匹配上下文 | [关键词 owning](../backend/ai_assistant/business_promotion_keyword_sku.py) 与 [内部 GET 适配](../backend/ai_assistant/business_promotion_runtime_tools.py) 已接；已有严格分页和行引用读取。基础关键词能力并非从零缺失。 | 旧 owning 9 项 PG、内部 reader 新旧路由合计 11 项 PG 通过，见[接线说明](AI_BUSINESS_PROMOTION_RUNTIME_INTEGRATION.md)及[证据](evidence/ai-business-keyword-sku-owning-candidate.json)。 | **内部 reader 不等于 Agent 和文件接入。** 仍需新 profile/工具目录、每节点读取回执、服务端数值引用核验、诊断及新版本双格式全量表。缺明确推广 SKU 仍留缺口，不用跟单 SKU 替代。 | 真实词货费用及基期对账、专业 Agent/独立复核建议质量；天猫已合并的计划粒度不能凭京东实现声称可恢复。 |
| 市场价格带、进榜/出榜与自家商品关联 | 基础市场事实 reader 已接。新增 [market_dynamics.py](../backend/business_analysis/market_dynamics.py) 已有价格区间分组与明确单日对单日进出榜的纯计算。 | 旧候选 11 项纯测试记录见[市场派生说明](AI_BUSINESS_MARKET_DYNAMICS.md)。 | **市场派生纯计算尚未接 owning 服务、Agent 或报告。** 需真实封存来源与权限绑定、有界服务、数值引用及文件；竞品到自家 SPU 的可核验映射仍缺。 | 核对精确类目/榜单粒度/价格带和观察日期；TOP 样本缺席不等于零销量，样本金额不等于全行业规模。 |
| 月度财务与利润/费用分析 | [财务 owning 内存源](../backend/finance/business_analysis_source.py) 已从实际模型按自然月、精确 scope 和 month→batch 链读取；五列账号最小权限与健康检查已接候选安装路径。 | 旧纯合同 18 项、owning 与权限合计 16 项 PG 记录见[财务来源](AI_BUSINESS_FINANCE_SOURCE.md)和[owning 验证](AI_BUSINESS_FINANCE_OWNING_SOURCE.md)。 | **尚无持久财务证据、跨进程采集恢复、Agent 和文件完整链路。** 需独立版本的来源合同/封存、月同比环比及全量表。现返回 `persistentEvidenceVerified=false`。 | 自然月财报和日经营窗口并列核验；保留缺月、缺科目、null、源比率合并限制。不把财报总计和明细、经营汇总与金蝶科目重复累加，不用财报反推 SKU 利润。 |
| 独立店铺总览和去重 UV | 既有 `jd_shop_overview → trade_overview` 导入入口存在；当前[经营分析 SOURCES](../backend/netshop/analysis.py) 尚不含此独立来源。 | [静态核对](AI_BUSINESS_JD_SHOP_OVERVIEW_UV_GAP.md)已确认当前通用导入不保证业务日、专用指标或同店同日唯一性；商品访客已明确为 `productDayVisitors`。 | 可信原字段合同、店铺×日期身份、重叠导入去重/冲突和来源接线仍缺；若无平台区间去重数据，只能披露不可得。 | 日去重 UV 多日相加不是区间去重 UV；商品日访客也不能替代。实际字段和平台导出对账尚待执行，不能从当前未查生产推断无原始数据。 |
| 多 Agent 协同、诊断与调整规划 | 既有五角色、三个并行专业节点、独立复核、汇总、逐任务读取证明、结构化动作、预算和人工复核已有运行路径；[screening tools](../backend/ai_assistant/business_screening_tools.py) 当前分派 native/mapped。 | 旧合成五 Agent → 人工批准 → 文件生成记录存在；这证明工程调度和约束，不证明真实模型理解能力。 | 新关键词/市场/财务视图须进入相应角色包、引用核验和完整交付。自然语言范围建议、跨请求派生复用尚缺；复用应按实测重复扫描成本决定范围。 | 按[六卡](AI_BUSINESS_DIAGNOSTIC_ACCEPTANCE.md)验证事实、错误因果、遗漏、冲突和动作；每条建议含对象、前提、指标、观察期、责任与回退。开发 Agent 协作不等于系统 Agent 质量验收。 |
| 长任务暂停、续读与恢复 | 网店、销售、市场 continuation reader、collection-only 工具及 AI 真实末块/检查点已接；原 CAS、额度和签名来源绑定保留。 | 旧三域 owning/真实 AI 台账、旧采集与 5001 行任务隔离验证见[续读证据](AI_BUSINESS_CURSOR_RUNTIME_INTEGRATION.md)。 | 既有三域无需重复开发续读底座；新增财务来源须另接对应持久协议。源变化后仍应停止，不混合新旧页。 | 真实多进程 HTTP、超过一小时暂停、重启后恢复及正式规模耗时未验收；模型未知结果不因取数恢复而自动重放。 |
| 工程级 HTML/XLSX 与预算 | 同源完整表、互动 HTML、可编辑预算、多卷持久文件、下载摘要、旧版本恢复已接；新 renderer 5/6 保留历史 1—4。 | 旧新版 36 表五 Agent 合成交付及浏览器记录；本轮文件 0024→0025 独立升级恢复见下节。 | 新增推广/市场/财务表尚需版本化加入完整文件，不能只在模型摘要里提到；最终组合的同源同数和规模回归仍需完成。 | 原生 Excel 历史 50 案中 49 通过、1 案许可过期中断；另 8 项预算边界与完整新 v6 打开/复算仍待有效许可。当前许可未重新检查，不声称现仍过期或全部通过。见[原生验收](AI_BUSINESS_EXCEL_NATIVE_ACCEPTANCE.md)。 |
| 最终交付与采用 | 当前主线改进与旧候选在隔离工作树整合；生产尚未采用本经营分析整合版本。 | 下节列出的隔离结果仅覆盖其明确范围；正在执行或无最终摘要的测试不计通过。 | 收束旧协议兼容、迁移/权限/备份、构建和受影响链路测试；形成精确待交付版本与采用/回退步骤。 | 真实业务、模型费用与规模、参考报告业务深度、正式工作台/文件下载及受控采用分别关闭；不以通过测试总数代替完成。 |

## 当前整合已取得的证据

2026-09-24 补充：工作台已接“从问题生成范围建议”，只用当前账号通过三个来源选择器验证的精确身份，建议日期、比较窗口和数据目的须由用户点击确认，随后仍经过原预览。15项纯测试、7项合成浏览器验收通过，见[范围建议说明](AI_BUSINESS_QUESTION_SUGGESTIONS.md)与 `.runtime/business-question-workbench-ui/evidence.json`；上表“自然语言辅助入口尚缺”是旧检查点。模糊店铺和类目、未导入日期依旧披露缺口。

2026-09-23 补充：市场价格带和进出榜已新增封存报告绑定的内部只读服务，7 项隔离 PostgreSQL 测试通过，日志 `.runtime/ai-pg-c074849f3aa6/tests.log`。下表“市场派生纯计算尚未接 owning”是本服务实现前的快照；当前剩余 Agent 工具、数值引用、双格式文件和竞品到自家 SPU 的可验证映射。新比较规则也已有11项纯测试，但尚未注册进计划、来源或报告；旧封存口径维持原版本。

随后市场内部只读路由及旧 screening 路由合跑11项隔离 PostgreSQL 测试通过，见 `.runtime/ai-pg-8f22cb2594bd/tests.log`；市场与本店商品的精确SKU/SPU候选映射另有8项纯测试，始终待人工确认，未自动归因市场销量或历史归属。Agent 工具、诊断引用和文件接线仍待开发。

推广关键词×明确SKU增加独立五角色纯候选合同（11项）与两张完整NDJSON材料（6项隔离PG，见 `.runtime/ai-pg-b7e9e0fe2330/tests.log`）。材料完整、保留缺身份桶并证明两种分组费用不可相加；尚未持久发布成HTML/XLSX，也未进入实际五Agent调度与读取回执。最终Node整库回归在新增工具前为2509通过、20既有跳过、0失败；新工具相关48项Node与最后一次构建通过，最终组合仍需更新后的完整回归。

随后从真实封存报告、工作流与筛查根准备新profile只读候选，3项隔离PG通过，见 `.runtime/ai-pg-4fa15d233537/tests.log`。候选明确 `registered=false`，当前不能启动新五Agent；[迁移边界](AI_BUSINESS_PROMOTION_MIGRATION.md)要求先固定新报告与工作流的持久创建形状，再做0026数据库guard和renderer7，旧报告协议不被重解释。TS类型检查尚有原223行基线错误，新增工具的类型错误已消除，日志 `.runtime/ai-integration-types-current.log`。

2026-09-24 新检查点：已在隔离工作树完成 `ai_assistant.0026_business_promotion_profile`、内部原子报告创建和持久来源复核；新profile可用预分配意图发布并回读真实规则筛查页。0025→0026 的独立升级、旧新备份恢复、旧65张AI表和renderer 1—6文件字节保持验证见 `.runtime/ai-pg-216c0adcdbf4/business-promotion-profile-upgrade.json`。创建、持久绑定、迁移及旧筛查存储组合测试见 `.runtime/ai-pg-2f0a62ae7162/tests.log`；两张词货表的完整NDJSON→工程表校验含6项纯测试。另有内部只读文件材料适配器，草稿材料仍不等于已发布HTML/XLSX。**新profile尚未接入实际五Agent派发、独立读取回执、诊断引用、人工复核及renderer 7；正式文件入口继续关闭。** 本检查点不是生产采用或真实模型质量验收。

推广新profile现已接入共享工作流调度的精确分支：只完成有租约的规则筛查和持久页链复验，随后停在 `promotion_admission_not_registered`，不创建Agent或调用模型。完整调度路径1项隔离PG通过，见 `.runtime/ai-pg-41fd23b2a223/tests.log`；四工具准入候选及旧筛查回归41项见 `.runtime/ai-pg-ff6a55233218/tests.log`。这一停靠状态需要后续独立准入与恢复路径，不能当作五Agent已运行。

后续候选已补四工具的实际读取适配、单Agent持久回执、词货数值逐行重算、五角色答复结构校验、运行中最终回答校验与必读内容容量测算。它们保持未注册；容量测算不预留可选词货调用，最终回答 token 不证明模型来源或完成态报告。对应独立PG：前三工具/词货回执/数值引用11项 `.runtime/ai-pg-cc1d2a68f1ea/tests.log`，单Agent完整阅读4项 `.runtime/ai-pg-7bd455d2d0cf/tests.log`，诊断 `.runtime/ai-pg-d18c24435acc/tests.log`，执行4项 `.runtime/ai-pg-99e35d335700/tests.log`，必读容量4项 `.runtime/ai-pg-93245b143cbc/tests.log`。数据草稿两张全量词货表已用既有writer生成同源HTML/XLSX，5项PG见 `.runtime/ai-pg-d3bd7ced3b6b/tests.log`；仍不是含五Agent判断的正式文件。0027仅允许renderer 7暂存并拒绝ready，独立升级恢复证明旧1—6字节与权限不变，见 `.runtime/ai-pg-debffe9408d2/business-promotion-file-guard-upgrade.json`。正式Agent派发、完成态内容/人审和renderer 7发布依旧待开发。

下列日志位于本隔离工作树 `.runtime`，是在当前主线整合过程中取得的结果。它们不覆盖此后尚未完成的新协议接线，也不代表正式业务数据验收。

| 项目 | 已核验结果 | 本轮证据 |
| --- | --- | --- |
| 相关数据库回归 | `Ran 35 tests in 72.205s`，`OK`。 | `.runtime/ai-pg-55113f58053b/tests.log` |
| 市场目录迁移与独立恢复 | 候选改为 `0006_analysis_options`，依赖主线 `0005_filter_facet_indexes`；40 张旧市场表摘要、主线四个 facet 索引保留；无历史自动初始化；独立恢复后 owning 页一致。 | `.runtime/ai-pg-5fa9374a1a75/market-options-upgrade.json` |
| ERP 目录迁移与独立恢复 | `sales.0009→0010`；17 张旧表摘要、旧备份兼容及独立恢复后 owning 页一致；不把目录包络当作连续覆盖。 | `.runtime/ai-pg-11e44660770d/sales-options-upgrade.json` |
| 文件协议升级与独立恢复 | `0024→0025`；65 张 AI 表旧行摘要、旧 1—4 文件及暂停 v4 恢复保留；实际 writer 新 5/6 发布、reader 写入与错误 epoch 拒绝。 | `.runtime/ai-pg-bdc164cc191d/business-file-opc-upgrade.json` |

文件升级证据明确标注 `syntheticLedgerOnly=true`、`realReportAuthorizationExercised=false`、`nativeExcelExercised=false`。不能据此宣称本轮已经复验完整五 Agent 报告授权或原生 Excel。旧 2,390 项单测通过属于此前候选组合，当前全量回归须以主线程最终日志为准。

## 接下来的五项优先顺序

1. **先收束当前主线兼容整合。** 保留 45 天滚动导入、市场索引/缓存/观测、新维护与启动流程；复验新增最小权限和健康检查、旧报告/文件字节与迁移恢复。新日期规则先维持纯候选，完成版本设计后再接入，避免旧证据被新规则重解释。
2. **完成关键词 × SKU 的完整纵向接线。** 内部 reader → 独立新 profile/中央工具 → 节点读取回执 → 服务端数值引用 → 诊断/人工复核 → 新版本 HTML/XLSX。以两张明确全量词货表和旧版本兼容为可验收切片。
3. **并行补市场深诊断与财务来源。** 市场从纯计算接真实封存 owning；财务从有界内存源接持久证据。共享版本与文件接入由主线程协调，避免同时改同一合同。UV 先完成可验证来源/去重合同，拿不到区间去重源时保留明确缺口。
4. **完成新旧链路组合及规模验证。** 两店、多期、多渠道、完整/缺源变体，核验金额、引用、失败恢复、资源与双格式同数；再决定是否需要跨请求持久派生缓存。补权威身份上的自然语言范围建议。
5. **集中做真实质量和采用验收。** 有明确范围与费用条件后执行真实模型六卡和业务对账；取得有效 Office 条件后补原生文件验收；最终准备具体采用/回退结果。正式发布、付费模型、消息或投流动作不由此清单自动授权。

每项完成时更新对应四栏和精确证据，保留失败及修复记录。原五阶段只能在对应开发接线和验收条件逐项关闭后完成，不再用开发批次数、表格数量或 Agent 数量作为完成率。
