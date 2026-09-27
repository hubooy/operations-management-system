# 财报证据内部读取桥接候选

2026-09-24。Django `finance_reader` 新增签名 `POST /api/finance/business-evidence/page`，只转发到已实现的 `finance.business_evidence_page.read_page`。入口要求当前无范围限制管理员；请求仅含精确 `query/offset/afterId` 和续页原始 `expectedSourceRef/expectedRevision`，8192字节以上拒绝。返回规范 JSON 原字节，完整成功响应最多38,000 UTF-8字节，`Cache-Control: no-store`，短响应版本头由已复验的完整财务 revision 得出。`finance_writer` URLConf 不含此路由，接口不写数据库。

Worker 侧 `lib/django/finance-service.ts` 仅对固定路径允许已签名的 reader POST，继续禁止同路径 writer 调用。`lib/ai/business-finance-source-page.ts` 是独立内部适配器：仅 `business_collection` 面及无范围限制管理员可调用，校验查询、页边界、完整 revision 回显、来源摘要和38KB响应；转发取消信号。本片最初**未登记**到中央 `tool-registry`、模型工具目录或浏览器公开 API，旧财务 `analysis/consumers/query` 和既有工具定义保持原样。后续仅 `business_collection` 的中央内部工具注册见 `AI_BUSINESS_FINANCE_V3_SIGNED_COLLECTION.md`；仍无模型或浏览器入口。

新后端路径可读取财报行，但 v3 证据仍未采集：`ai_assistant.0028` 的 SQL 保持 v3 chunk/checkpoint/seal/report 禁止。签名应答也不等于持久页链、Agent 已读或跨来源同刻快照。未来 collector 必须加载可信 v3 目录、逐页保存/校验真实行 ID、完整 revision 与 month→batch，受共享容量和CAS约束，最终重建纯 `FinanceEvidence` 后才能封存。

验证：Django 路由测试覆盖签名管理员、正文篡改、受限身份、错误字段与 writer 路由缺失；TS 定向测试覆盖固定 reader 端点及签名、角色/面隔离、回执来源/版本/范围/容量。正式系统和真实财务数据未操作。
