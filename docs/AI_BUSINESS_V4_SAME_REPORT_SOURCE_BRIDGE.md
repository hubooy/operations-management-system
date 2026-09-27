# v4 大容量来源与同报告文件流：封闭桥接候选

当前 `AiReportRun` 固定 `reference-v2` 证据 ID、版本与封存摘要；13 表组合和推广分卷调用同一报告的 sealed-v2 Reader。该 Reader 每来源最多 2,000 页、64 MiB，不能用它声称已读完参考规模的 575,095 行推广数据。`AiBusinessV4Run` 是另一套独立账本；它的真实封存回执验证 v4 自身的页面、工具审计、修订和应用 HMAC，但回执明确 `reportGenerationSupported=false`，没有指向任一 `AiReportRun` 的外键或创建时承诺。

`backend/business_analysis/report_v4_source_bridge_candidate.py` 是默认关闭的纯合同。输入须含新报告**创建时**声明的 v4 run/计划、原 v2 报告快照/工作流/证据身份、同一 owner、原始日期、京东店铺及分析请求；还须含完整三期容量计划和最新 v4 封存回执。它重建三个推广窗口的日期、来源身份、修订、引用及期望日期摘要，要求所有来源修订与当前修订相同。结果只给未来持久绑定的候选字节摘要；`persistedSameReportLinkVerified`、`v4RowsReadableForReport`、Agent 引用、renderer 和发布均固定为 false。调用方能重新计算所有普通 SHA256，因此该纯合同本身不能证明报告创建时声明或回执来自数据库。

`backend/business_analysis/report_v4_promotion_stream_candidate.py` 接受上述候选、单窗口精确来源摘要与拥有方提供的页/审计迭代器。它在第一遍完整核对连续页、请求游标审计、响应原始摘要、行 ID、控制总额、期内全部日期和来源修订，第二遍才逐行交给私有 sink；第二遍或最终拥有方复验失败会 `abort()`，不留下完成回执。单来源上限沿用 v4 的 16,384 页与 2 GiB，未套用 v2 的 2,000 页/20 万行限制。当前测试仅为 30 行合成页；575,095 行只是 v4 容量计划的合成估算，未验证此新流的真实耗时或真实来源。

这个 stream 的 `pages_factory`、`source` 和 `verify_current` 仍由调用方提供，普通摘要都可重算；`complete()` 仅代表私有暂存完整。当前系统没有能从真实 sealed-v4 页、工具审计、HMAC 封存、同报告数据库绑定同时构造这些输入的拥有方 adapter，也没有允许它进入现有 v2 `sourceManifest` 的版本化 writer。不能把这份候选回执转换成下载权限。

隔离第三切片新增 `backend/ai_assistant/business_v4_report_stream_owning_candidate.py`，只在 `DJANGO_ENVIRONMENT=test` 且内部 writer/development 角色、显式 `enabled=True` 时运行。它复用 v2 报告拥有方核验与 v4 `verify_seal`（真实应用 HMAC/页/段/收据），从真实 `AiBusinessV4Chunk`、`AiBusinessV4ToolReceipt` 和工具审计按序构造双遍私有流；从封存正文读取收据链、从终段检查点读取数据页根。v2 报告与 v4 必须同管理员、京东同店、相同问题/请求维度/完整三期和原始日期，所有 v4 来源修订须仍为当前。每遍及最后复核报告与 v4 目录/封存；已记录但未覆盖全部日期的来源拒绝，不把缺日解释为零。

第三切片 adapter **没有**读取创建时 SQL 桥；成功只返回 `blocked_unbound`，13 表同报告、文件下载、Agent 引用及发布均 false。已存在的 sealed-v2 报告没有同事务 v4 意图，不能因这些业务字段碰巧相同而自动升级。隔离 PostgreSQL 目标用例核真实封存 ORM 页和工具审计、reader 对页表直接 `SELECT` 拒绝，并核无报告时入口失败；纯测试覆盖三期/跨店/日期/修订拒绝。真实 PostgreSQL 目标须由主整合任务串行执行，当前没有宣称真实 575,095 行可流完或已生成工程文件。

正式接线仍需单独版本化并做隔离 PostgreSQL 升级/备份恢复验收：

1. 在**新报告创建事务**中记录 append-only、`report_id` 主键且 `v4_run_id` 唯一的同报告桥；绑定不可变的报告快照、工作流输入、v2 seal、v4 plan/seal、三期来源 key/query digest/ref/revision、财报来源与创建时管理员版本。应由同事务预签意图对应的报告 `AFTER INSERT` 触发器写入，禁止独立的事后 `INSERT`；已存在的 `reference-v2` 报告不能追补。`reportSnapshotDigest` 应在插入后由数据库计算并存入桥表，避免把自身摘要嵌进 snapshot 造成循环。若修改报告快照协议，必须另立版本，不静默改变旧报告。
2. 报告创建应用先运行现有 `verify_seal` 的全页/HMAC 校验；在写事务中锁住并重核其 `run_id`、seal 摘要、目录版本、来源修订与管理员版本，再由数据库拥有方触发器写桥。数据库无法仅凭普通 SHA256 证明应用 HMAC，应用预检与 SQL 当前态栅栏两者都必需。`ai_writer` 只获新报告创建入口所需权限，不能直接 `INSERT/UPDATE/DELETE` 桥表；`ai_reader`/Agent 只经固定 `SECURITY DEFINER` 只读函数核当前报告/桥/来源，不能写桥、调用签名入口或通过客户端传入 `reportId` 加 `sealedDigest` 自证。旧函数 OID/ACL 与 renderer 1–11 保持兼容。
3. 新 sealed-v4 来源 reader 要在报告开始、每卷和结束复验桥与来源，逐页重放原始字节、连续序号、工具审计、行数/控制总额、修订和封存根；从真实 seal 正文读取 `receiptChainDigest`，从完整检查点读取 `evidenceDigest`，再构造当前纯流所需摘要。把真实行迭代器接到**新版本**私有分卷 writer，沿用现有逐卷 HTML/XLSX 回读算法与失败中止规则。当前 v4 的 `inspect` 仅面向 collecting 父任务，`verify_seal` 只返回紧凑证明，均不是可直接使用的 sealed-v4 报告行流。
4. 13 表基础报告与大容量推广卷须引用同一桥摘要及同一报告意图；若原 v2 推广选源与 v4 数据范围不同，要显式版本化其替代/并列语义，不能把 v4 行冒充已经封存的 v2 行。金额口径仍分别标示，财报按自然月背景，不按日分摊或跨域相加。
5. 隔离环境验证跨店、跨报告、错三期、修订漂移、旧封存、篡改页、重复/缺页、角色越权、半写卷回滚；用真实来源和 Office/浏览器完成 575,095 行容量与 HTML/XLSX 一致性验收，之后才考虑注册 Agent/renderer 与受控发布。

0071 隔离候选已实现创建时绑定表/触发器和只读 `SECURITY DEFINER` 函数；非超级用户正反、完整升级及备份恢复仍待执行。当前 adapter 的 `blocked_unbound` 不能通过把布尔值翻转或补一个普通 SHA256 变成 `linked`；须由 SQL 回执与重新核验的真实 HMAC 同时授权新版本报告 reader。新分卷 writer 应有独立 renderer 版本与文件权限迁移，不沿用 sealed-v2 的 `sourceEvidenceDigest`。

0071 的对象与权限边界：`protected_business_v4_report_link_intents` 保存同事务受限意图，`protected_business_v4_report_source_links` 以 `report_id` 为主键/FK，`v4_run_id` 唯一/FK，保存报告创建事务中的 v2 seal、v4 plan/seal、报告快照/工作流摘要、三窗口及财报来源目录摘要、创建者版本和创建时间；仅找到同事务意图的报告 `AFTER INSERT` 触发器可写，拒绝 UPDATE/DELETE/TRUNCATE 和旧报告回填。报告 writer 不能直接写桥表，reader/Agent 不能直接读 v4 chunk/receipt/seal 表；只读 definer 函数按当前管理员、报告/桥、最新封存及来源版本返回窄页流回执。由于当前 adapter 使用 ORM 直接读取真实 v4 表，正式接线还须实现独立受限读取身份或固定 SQL 页函数，并验证普通非超级用户正反行为。对旧 `AiReportRun`/旧 renderer 函数的表结构、OID、ACL、字节及备份恢复执行升级门禁；0071 不应同时开启 Agent、renderer 或下载。

0071 隔离候选实现采用更严格的**同事务意图**：`ai_v4_issue_report_link_intent` 仅由 AI writer 在将创建报告的事务中调用，意图表的 `report_id` 使用延期外键，独立提交没有报告会失败；已存在报告不能签发意图。`ai_report_runs` 的 AFTER INSERT 触发器仅在同一个 `txid_current()` 找到意图时，核真实 v2 封存、v4 封存、当前来源修订、同店三期原始日期、分析请求及报告快照，再写只追加 `protected_business_v4_report_source_links`。没有调用方注册，因此迁移后两个表应为空、普通报告创建行为不变。受限 AI reader 函数仅返回身份候选，`appHmacVerified`、`authorityVerified`、Agent、renderer、下载均为 false。应用 HMAC 的 `verify_seal` 和当前 SQL 回执未来必须同时复核，不能凭 SQL 里的64位摘要单独授权。

受限 AI writer 没有也不应获得 `ai_report_runs` 的直接 `INSERT`。0071 增加 `ai_v4_create_report_from_link_intent` 窄 `SECURITY DEFINER` 函数：仅在当前事务已签精确意图、账号及工作流一致、当前 AI 写入 authority epoch/cutover 匹配且快照为固定 integrated-v2 无预算协议时写入报告；原报告全部 BEFORE/AFTER 守卫继续执行，`session_user` 仍是 writer。直接表插入继续拒绝；普通报告创建路径与表级 ACL 不改。隔离 PG 目标要求正例通过此函数，错店/错日期在 0071 报告身份守卫失败，并证明直接 INSERT 被数据库拒绝。

窄创建函数在等待意图/工作流前先对 `ai_write_authority` 第1行 `FOR SHARE` 持锁，提交前不释放；即使维护切换正持有更新锁，也会先等其结束后核新状态。原写入栅栏只在 `current_user=ai_writer` 时运行，SECDEF 内的 `current_user` 是函数 owner，所以函数自身在真正 INSERT 前再次核 status、epoch、cutover。隔离 PG 负例在签发意图后改变事务内 cutover，必须由该 authority 守卫拒绝，不能因已签意图继续写报告。

两张 SQL-only 表统一使用 `protected_business_v4_report_*` 前缀：正式备份器只把 `ai_` 前缀表与 ORM `AI_TABLES=89` 做精确目录比较；`pg_dump --format=custom` 未限定表也未排除表，仍会把这两张受保护表写入全库备份。AI 健康检查另调用 `0071.verify_catalog`，核其列/触发器/函数/ACL，不把它们混入 ORM 清单。隔离 PG 同事务正例使用明确标注的**合成 SQL 身份向量**（测试内跳过 v4 页/HMAC 守卫，只验证0071原子绑定），并要求读回 `appHmacVerified=false`；跨店、错日期和旧报告回填负例均须拒绝。此正例不能替代真实 v4 封存或客户数据测试。

实际迁移文件为 `0071_business_v4_report_source_link.py`，依赖 0070 两张票据表；隔离 PG 目标用例覆盖旧报告无法补绑、reader 无直接表读、默认空表。`tools/business-v4-report-link-upgrade-rehearsal.py`要求原 0069→0070 双备份恢复回执，再做 SQL 全物理清单 91→93 张、正式 ORM `AI_TABLES` 保持89张并等于 `database_contract.MODELS`、旧函数 OID/正文/ACL、renderer 1—7 文件字节、前后独立恢复及空逆迁移重装。PG 目标与升级脚本尚待主整合任务串行执行；若任何 SQL 语义/权限门禁不通过，本候选不得被视为完成或可采用。

0071 占用迁移号但没有应用调用方，不创建报告或文件、不修改线上开关；离线与纯测试不能替代隔离 PostgreSQL 权限、升级恢复或客户数据规模验收。

后续独立授权前置切片使用 `business_analysis/report_v4_authorization_preflight_v1.py` 与 `ai_assistant/business_v4_report_preflight_owning.py`。纯合同将 0071 当前链接、真实 v4 `verify_seal` 回执/封存正文、三期原日期及逐窗口完整业务日、每个来源 ref/revision/页行计数与 v2 报告快照逐项核对；财报必须是精确 `shop:京东:<店铺>` 的自然月范围，事业部或未知映射直接拒绝。拥有方 adapter 只在隔离 test/development 显式开启，读取受保护链接并在最终返回前重跑应用 HMAC/报告/链接核验。当前旧 seal 的 `reportGenerationSupported=false` 是不可翻转的版本约束，即便上述材料齐备，结果仍为 `blocked_legacy_v4_report_authority`，13表引用、Agent、分卷发布与下载全为 false。纯/拥有方测试使用合成输入，不是新的生产读取路由。

下一阶段须先产生**新版本**的报告可用 v4 封存协议与真实拥有方签名/角色链，并建立财报店铺身份的独立拥有方证明；不能改写旧 seal 的 false、旧 v2 报告或 0071 身份候选。之后要在新报告版本内把真实 sealed-v4 三期逐页迭代器、13 表摘要与分卷文件绑定同一 SQL/HMAC 根，建立失败中止、最终重核、Agent 引用及下载权限的独立发布协议。真实 575,095 行、两店隔离、HTML/XLSX 逐表与原生 Excel 验收仍为必要终验；本切片不占 0072 迁移号。

新一轮可报告 seal 准入候选由 `business_analysis/v4_report_seal_admission_v2.py` 和 `ai_assistant/business_v4_report_seal_admission_owning.py` 组成，不占 0073 迁移号。仅在隔离 test/development 且显式开关开启时，拥有方先调用原 `verify_seal` 全链 HMAC/原页核验，再对三期推广真实 chunk/工具审计各做双遍逐页读取，用 O(1) 内存摘要 sink 验证连续页、请求游标、每个业务日及来源修订，最后重跑 HMAC 与目录复核。财报 scope 不是精确 `shop:京东:<店铺>` 或有缺月时在读推广页前拒绝；即便 scope 名义精确，现有 `shopIdentityMappingVerified=false` 仍缺独立财报拥有方证明。旧 seal 规范硬性要求 `reportGenerationSupported=false`，本候选只返回 `unavailable_report_capable_seal_not_issued`，不签新 seal、不让旧 0071 报告/13表/分卷/Agent/下载获得权限。纯测试不接受调用方伪造财报映射、缺页、缺日或把旧 seal 的 false 翻成 true；拥有方测试核三窗口执行与最终HMAC重检。

要跨过此阻断，需先定义独立的**新 seal 版本**和保护密钥/签发身份，在真实 v4 页段与三期完整日期、财报权威店铺映射证明上发一次性受限签名，并有 SQL 守卫、消费回执、角色目录、备份恢复及非超级用户端到端测试；旧 seal 行和 0071 候选链接不修改。还需新版本 13 表/分卷 writer 把该新 seal、逐页来源根与同报告 SQL 绑定逐项联结，最后由真实 575,095 行来源、双店隔离和工程 HTML/XLSX/原生 Excel 验收。当前没有可验证的财报映射证明或新版本签发器，不能把本次完整扫描称为报告可用授权。

独立 signer 准备切片检查了现有密钥边界：`business_v4_seal_hmac` 从部署内的 `DJANGO_INTERNAL_SECRET` 派生旧父封印用途 `teruisi:business-v4:parent-seal:v1\0`；`v4_final_commit_step` 需要已认证、受保护的 sealer 连接及一次性 ticket/claim，旧 `evidence_seal_v4.read` 强制 `reportGenerationSupported=false`。因此新版本不能复用旧 `sign()`、旧用途、旧正文或旧 seal 行。`business_analysis/v4_report_signer_preparation_v1.py` 只声明不同的新用途摘要和未来证明清单，永远返回 `canonicalSignableBody/newKeyId/newBodyMac=None`、`signerAuthorized=false`；`ai_assistant/business_v4_report_signer_preparation_owning.py` 仅在隔离 test/development 显式开启，亲自调用三期 HMAC+双遍拥有方后返回阻断，测试核旧 signer 不会被调用。未写密钥、凭据或签发代码，不占 0073。

财报现有店铺 `scope_key` 的结构是 `shop:<平台>:<店名>`（例如 `shop:京东:同名店`），本切片将前两轮保守的 `shop:<店名>` 候选校验修正为京东平台限定形式；这仍是文本结构匹配，不能证明财报与网店主数据身份一一对应。没有独立财报拥有方映射表/签名、已发布月份与店铺身份的稳定外键，当前 signer 准备合同拒绝所有调用方自称的映射 witness。要真正签新 seal，还须先建立这些证明、保护新用途密钥及签发专用身份、一次性消费账，并在非超级用户真实角色下验证跨店、旧 seal 重放、未知提交与并发修订负例；随后才可进入真实 575,095 行及工程文件终验。

下一份无迁移事务协议候选位于 `business_analysis/v4_report_seal_transaction_protocol_v1.py` 和测试专用 `ai_assistant/business_v4_report_seal_transaction_owning.py`。后者在同一调用中分别走 0071 同报告拥有方与三期 HMAC/双遍逐页拥有方，再用不同用途的 signer 准备合同约束同一 report/run、旧 seal、三期日期与来源根。候选将未来操作固定为 `issue→claim→sign→同事务commit+consume`；财报映射证明不存在时，`signableBody/ticketId/nonceHash/claimHash/MAC` 全为 null，任意 issue、重复 claim、越序 commit、unknown 后直接 retry 或消费事件都拒绝。普通 SHA256 可由调用方重算，这些字段只是审查未来 SQL 账本的形状，不能作为签发授权。无 0073 迁移、密钥加载或持久写入。

未来受保护签发最少需要单独版本化的新用途密钥提供方和非超级用户专用 signer 身份；SQL-owned 只追加 ticket/claim/new-seal/consumption 账需锁定 0071 创建时链接、v4 旧封存 HMAC、三期每窗口真实页根、财报拥有方映射摘要、actor/key 版本及一次性 request digest。新seal提交与消费须同事务并有可证明的精确恢复回执，未知提交只按原 ticket 查询，不重发签名或 commit。旧 0052 的 `ai_business_v4_seal_tickets/claims/consumptions` 只消费旧父 seal，不能改其 schema/用途或把旧 `reportGenerationSupported=false` 行回填为 true。完成该层后，仍须新 13 表/分卷协议、真实 575,095 行来源与原生 Office 验收。

隔离新版本 seal 正文向量定义在 `business_analysis/v4_report_capable_seal_v2_isolated.py`，仅含 `testVectorOnly=true`、`authorityVerified=false` 的规范字段：0071 report/v4身份、旧v2/v4 seal、三窗口逐页根、原日期、财报 sourceRef/revision、独立映射 proof digest、专用新用途与 key ID。测试每轮用 `secrets.token_bytes(32)` 生成两把不同随机 key，在测试代码内分别对财报映射与新正文做用途隔离 HMAC；旧父 seal 用途验新 MAC 失败，错角色、缺页根、缺月份、跨店、旧 seal 正文均拒。纯合同只验合成字节形状，`mapping_mac_verified` 为测试注入布尔值，可被调用方伪造，因此始终返回 `protectedFinanceOwnerVerified=false`、`protectedReportSignerVerified=false`、`reportCapableSealIssued=false`。它不持有真实 key、不向数据库写入 seal，也不开放 renderer/下载。

正式签发仍缺两条无法由测试向量替代的证据：财报拥有方从已发布自然月与店铺稳定身份独立产生并签署一对一 mapping proof；新用途 signer 以独立受限角色/密钥对真实同报告三期页链和该 proof 签发，并由受保护 SQL 一次性 issue/claim/同事务 commit+consume、unknown 精确回读。候选 SQL 应另建版本化只追加账与新seal表，禁止复用或更新旧0052账、禁止先签后补映射；需非超级用户正反、备份恢复、漂移和旧seal重放测试，才能考虑 13 表与分卷使用。
