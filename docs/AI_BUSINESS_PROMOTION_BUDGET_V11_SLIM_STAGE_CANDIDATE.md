# renderer 11 压缩 HTML 临时未发布候选

本切片把上一版已批准来源与固定预算的纯生成路径扩成独立 renderer 11：同一组表继续用现有多卷 writer 生成 XLSX，HTML 固定采用 `htmlPayloadVersion: 2` 的逐表 gzip 规范 NDJSON。renderer 10 的公开 `open_volumes` 保持原入口、原 schema、默认原 HTML 字节；renderer 1–9 不接收瘦身参数。v11 临时入口 `business_promotion_budget_v11_slim_stage_candidate.open_unpublished` 只有在 `AI_PROMOTION_BUDGET_V11_STAGE_CANDIDATE_ENABLED is True` 时才导入拥有方模块并读取同报告已批准五角色内容、封存来源与固定预算，前后复验后只在临时目录交付候选。当前配置未设置此开关；没有创建任务行、持久块、ready、路由、账号凭据或生产功能。

版本化完整清单使用 `rendererVersion: 11`，保留并核原 `promotionFileProof`、renderer 9 行动/来源证明和 renderer 10 `promotionBudgetProof`；新增 `promotionSlimProof`，锁原预算证明摘要、每卷文件 SHA 与字节数、`htmlPayloadVersion: 2`、压缩表摘要及浏览器门槛 `DecompressionStream:gzip` 与 `SubtleCrypto:SHA-256`。每卷 `htmlPayload` 逐表绑定原表片段键、行数、原 `rowDigest`、规范 NDJSON 字节数、gzip 字节数及 SHA-256。纯清单自洽不足以证明真实文件：临时拥有方在 yield 前重新打开每卷 HTML，核完整文件 SHA、惰性 JSON 目录、全部压缩/解压字节、规范行、列宽与逐表行摘要。任何宽度、损坏、缺卷或源根变化均拒绝候选，部分文件不进入持久或 ready 路径。浏览器能力字段只描述**要求**，不冒充目标浏览器通过。

合成小样本已验证 v11 的一卷预算文件与完整 manifest、固定预算 3 张公式页，以及 `file://` Chrome 离线读取/搜索和篡改 gzip SHA 拒绝，外部请求与页面脚本错误均为零。v10 前驱同形 3 行 HTML SHA `8f247be805cbb3aff19e5cca989bf6d1ccc6190ac35cf48aaf2f272ea795b7f8` 在本切片仍逐字节一致。v11 与 v10 的 XLSX 工作表、公式、关系和样式 XML 成员逐字节一致；XLSX 包内 `teruisi-manifest.json` 因版本化卷绑定不同，**整个 XLSX 文件 SHA 不宣称相同**。纯正反还覆盖 v11 证明缺失/改写、重签清单替换压缩 SHA 后由实际 HTML 复验拒绝、超宽拒绝以及 v10 不接受 v11 字段。合成文件：`E:\codex-artifacts\ai-business-v11-slim-stage-pilot-20260925-a`，其中有 `browser-evidence.json`。

**当前无法持久暂存或发布 renderer 11。** `0054_business_promotion_budget_file_staging.py` 的 `ai_business_file_bound` CHECK 只列 `1,2,3,4,5,6,7,9,10`；卷块 guard、紧凑清单 guard 的版本范围与 run/complete guard 也只认到 10。即便放开 CHECK，现有触发器仍不允许把 11 的块及 manifest 正常落库。`0057` 独立证明函数要求 `parent.renderer_version=10`，`0058` 原子发布/结果查询只选 v10，`0059` 当前下载回执与预算栅栏也硬判 v10；前端清单和文件服务未注册 11。不能把 11 冒充 10、复用旧证明角色或直接写 ready。

未来持久化最早需独立 `0065+` 迁移：冻结并版本化 0064 前驱，扩展 CHECK、卷块/清单/任务/完整性触发器且 **v11 仍只可 `staged_unpublished`**；设计受保护的实际 HTML 解压/行证明验收与独立 NOLOGIN 证明后，另行审核原子发布及下载窄回执/权限/前端兼容。须覆盖老 1–10 文件字节和函数 OID/ACL、真实角色正反、独立备份恢复/空逆迁移以及网络未知结果恢复。本切片没有申请或执行这些数据库改动。

这里的证明仍不是平台来源权威：合成行、测试预算与浏览器小样本不证明真实 30 天/环比/同比三窗口、市场、B 端或销售覆盖，也不代替 57.5 万 v11 原生 Office 复算与浏览器容量。既有 v10 大规模瘦身验收不能自动升级为 v11 的业务终验。生产与付费模型均未使用。

本地验证：相关纯合同回归 55/55、默认关闭且无 DB 的 Django `SimpleTestCase` 2/2、Node 旧 v10 下载与 v11 公共客户端拒绝 30/30、目标 ESLint、Python 静态编译及差异检查通过；未运行 PostgreSQL、迁移或生产浏览器。完整拥有方 `open_unpublished` 的真实角色/预算根前后核验仍需由主任务在独立 PostgreSQL 串行测试，不能用本轮纯夹具冒充。

整合分支已复核相关纯17项、Django无DB两项、Node34项与静态编译；仍未对 v11 durable 路径运行 PG，因为 0054/0057–0059 的正式SQL门禁会按设计拒绝 renderer11。后续必须在新迁移中独立完成暂存、证明、发布、reader和双备份恢复，再做真实角色验收。
