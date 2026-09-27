# 市场 v2 停放报告预览候选

此接口仅供管理员核对同一已封存词货 v2 报告中的京东市场样本。它不会准入材料、启动 Agent、调用模型、生成正文或文件。两端 `ai_reader` 与 `ai_writer` 的 `TERUISI_DJANGO_AI_MARKET_V2_PREVIEW_ENABLED` 均默认 `false`；只有受控设置为 `true` 才开放对应读写端。此开关不等于正式市场报告上线。

`POST /api/ai/market-v2-parked-reports` 使用现有签名身份、请求 ID 和正文 SHA。正文固定为 `schemaVersion=business-market-v2-parked-create-v1`、`clientRequestId`、`sourceReportId`、`marketSelector`；selector 的价格带、市场来源键和双观察日由已有已验证来源目录及用户明确选择提供。服务器完整复核已封存来源和三张市场类型表后，只创建 `paused/market_material_not_admitted` 报告与空工具工作流。相同 `clientRequestId` 只能重放同一正文；签名写回执与报告写入同事务完成。

`GET /api/ai/market-v2-parked-reports/{id}/preview` 返回三表摘要；添加 `view=price_band_summary|price_band_members|rank_entry_exit&offset=0&limit=20` 返回一表至多 20 行。`offset` 为规范十进制 0—200000，`limit` 只能为 20。每次请求均在完整封存来源页与三张市场类型表重放之后取样；取样前后重验当前账号、停放报告、来源根、空节点/任务/文件。GET 无业务持久写入，也不依赖 0045 的独立 attestor sidecar。响应最多 38 KB，超出整体拒绝，不截断。

创建响应含 `item` 与 `replayed`；预览响应含 `tables`、可空的 `page`、观察日覆盖、来源材料摘要及以下固定标识：`materialAdmitted=false`、`agentReadPersisted=false`、`actualAgentBound=false`、`authorityVerified=false`、`renderer=false`、`requestMaterialReplayed=true`。价格带汇总与成员同源，不得相加；市场 TOP 样本不代表全市场，也不证明本店 SKU、ERP 或 B 端归属。预览不能用作正式 Agent 已读回执或 HTML/XLSX 交付依据。

本候选仅在隔离工作树开发。正式采用仍需独立材料准入、Agent 持久读证、人审、renderer8、真实业务与文件验收；生产未设置此开关、未部署。
