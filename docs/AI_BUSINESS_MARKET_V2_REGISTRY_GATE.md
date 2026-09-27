# 市场 v2 第五工具目录门禁候选

本切片把 `get_business_promotion_market_v2` 定义为**独立且默认关闭**的只读候选。新 surface 为 `business_agent_screening_promotion_market_v2`，候选 profile 为 `business-agent-screening-promotion-market-admitted-v2`。中央旧 `aiToolRegistry` 数组保持原样；仅当 Worker 环境显式 `AI_MARKET_V2_AGENT_RUNTIME_ENABLED=true` 时，内部 edge 为新 surface 组合第五工具目录。v1 `business_agent_screening_promotion_v1` 的四工具顺序、内容和目录摘要不变。Django 独立环境变量 `TERUISI_DJANGO_AI_MARKET_V2_AGENT_RUNTIME_ENABLED` 默认 false，任一端关闭均不能获得候选数据。

新 entry 只允许无范围管理员，风险为 read_only、direct、12,000 ms、每次最多 8 调用、工具完整结果最多 38,000 字符。参数包含精确 admitted 报告 ID、市场上下文/材料摘要、允许角色 `market_b2b` / `independent_review` / `report`，以及互斥的 summary、page 或 row 模式。现有 TS 通用 JSON Schema 校验器不执行 `oneOf`，因此专用 handler 与 Django 纯合同再次验证模式专属字段，混入页/行参数或冒用其他角色即在取数前拒绝。

edge 只在开关开启时接受新 surface、公布其单项目录并允许候选执行；handler 自身再次查开关和角色，签名向 reader 进程发送固定 POST `/api/ai/market-v2-tool-candidate/{requestId}`。Django 校验签名请求 ID、独立开关、reader 角色及精确参数，然后从该请求 ID 派生**仅供预览的非持久 job/provider 声明**，交给已有 0056 窄权限与三表拥有方重放适配器。返回必须为 `persistedRead=false`、`sameJobProviderPersisted=false`、`registeredTool=false`，不能把请求 ID 或 provider call ID 冒充真实派发回执。38k 边界包括外层 `{ok,toolName,data}` 工具信封，宽结果整次拒绝，不截断。

即使两端开关打开，0053 数据库仍拒绝该报告的节点、Agent job、第五工具派发与结果，当前并无业务流程使用新 surface，也不会因这个候选触发模型调用。真正同 Agent/provider 的持久读取、数值引用与五角色诊断须在后续独立激活迁移、真实目录/模型策略、回执重放和人审中完成；本候选不可称市场分析已上线。

2026-09-25，Node 候选目录/handler/edge 测试 **4/4**、旧词货四工具回归 **8/8**、变更文件 ESLint 通过。Python reader 路由目标 `ai_assistant.test_business_market_v2_tool_candidate_route` **3/3** 通过，证据 `.runtime/ai-pg-ec558dca39ce/tests.log`；它只以测试内的已核身份替身检验签名请求 ID 与默认关闭逻辑，不把替身等同完整 HMAC 联通或真实模型调用。首轮测试夹具把 viewer 的 `current_principal` 误模拟为无条件通过且误用 JsonResponse `.json()`，修正后复测通过；生产门禁未放宽。完整构建、真实 HMAC 联通、参考规模及生产仍未验收。无付费模型、生产迁移或服务操作。
