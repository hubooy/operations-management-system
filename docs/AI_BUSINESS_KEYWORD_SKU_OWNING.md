# 关键词 × 明确推广 SKU：封存服务候选

2026-09-18，本片独立于已提交的第55批纯合同（`dcff291`）。新增两个文件：

- `backend/ai_assistant/business_promotion_keyword_sku.py`
- `backend/ai_assistant/test_business_promotion_keyword_sku.py`

内部接口 `table(report_id, source_key, view, principal, *, baseline_key=None, checkpoint=None)` 复用已有 `_load/_revalidate` 验证实际报告、工作流、owner/scope、固定输入与封存证据；只从完整 Reader 目录和 `info.expected` 获取来源身份及核对证明。两个来源的 `Reader.pages` 全部耗尽、纯聚合核验完毕后才允许读取结果，context 正常退出后再次复验当前权限与固定绑定。page/read_row 封套仅在退出成功后返回。

`page(report_id, params, principal, *, checkpoint=None)` 接受 sourceKey/view/可选 baselineKey/严格整数 offset/固定 limit20。`read_row` 额外要求精确 rowIndex/rowId。两者返回 `business-promotion-keyword-sku-response-v1`，完整封套不超过38,000 UTF-8字节；分页仅缩减完整行前缀，重算实际 nextOffset/pageDigest，不丢单行字段。未公开 HTTP/工具、未注册报告维度、筛查或文件导出。

纯表 `authorityVerified=false` 保持原样。服务额外 authority 只证明所选来源的完整遍历和报告绑定，`entityDailyCoverageVerified=false`、`productMasterIdentityVerified=false`。关键词或明确推广SKU缺失时保留金额核对桶及 `identityCoverage`、`identityQualified=false`、`missingIdentityFields`；不使用通用SKU/触发SKU/跟单SKU补身份，不能据缺身份桶指令具体商品调整。

根任务已运行隔离 PostgreSQL 标签 `ai_assistant.test_business_promotion_keyword_sku`，9项通过，日志 `.runtime/ai-pg-bb40b6c35b23/tests.log`。测试通过真实数据库 JD 规范事实、实际 owning reader、采集与封存；不是 AST 元数据替身。覆盖两视图与基期金额守恒、generic TRIGGER角色隔离、全缺列显式缺失、精确引用、账号/范围/错误基期、尾部错误、退出撤权、原取消异常与临时目录清理、宽中文完整分页、封存绑定/事实篡改，以及只读阶段不查询业务事实库、不写数据、不调用模型。

该 PG 夹具直接植入隔离规范事实；完整真实上传导入、正式账号数据、生产发布、付费模型及原五Agent消费新维度仍未验证。实际TS导入器→后端纯投影边界由第55批的独立合成测试覆盖；不能把两者描述成生产导入验收。旧推广 owning 模块、旧八维 JSON 和55纯算法没有改动。
