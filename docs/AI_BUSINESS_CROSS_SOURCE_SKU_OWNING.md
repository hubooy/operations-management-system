# 三窗口 SKU 并列：封存 v2 拥有方候选

`backend/ai_assistant/business_cross_source_sku_materials.py` 提供默认关闭的内部 `prepare(report_id, source_keys, principal, enabled=True)`。同一已封存 v2 综合报告必须固定单店、三个比较窗口、当前 master 和 mapping pair。入口使用拥有方 `Reader.info` 固定所有被选来源，逐窗口调用既有 `business_cross_source_daily_materials.prepare` 完整回放 ERP、商智原生 SKU/SPU 与推广页；未选来源显式为 null。三份日材料、报告绑定、计划摘要与来源修订必须一致，然后由已版本化的 `cross_source_sku_window_compare` 再核每日 ERP 已分配 SKU 加未分配池、原生 SKU、推广明确 SKU 加缺身份桶的行/金额守恒。返回前重读报告/账号和全部所选来源检查点，变化即拒绝。

结果按 ERP 已匹配 SKU、商智原生 SKU、推广三列族并排，金额不跨域相加；ERP 歧义/未匹配事实留在店铺每日未分配池，缺明确推广 SKU 是不可行动独立桶。当前 master 不能证明历史 ERP SKU 归属，故 ERP SKU 的跨期差额与增长率为 null；商智与推广仅在自身来源、实体和指标覆盖完整时允许比较。`sameReportAuthorityVerified=true` 仅表示这三份材料绑定同一封存报告，不表示原生平台签名、Agent 已读或正式文件权威；总 `authorityVerified=false`、`agentReadPersisted=false`、`registeredRenderer=false`。

本候选最多接收三份日材料合计 64 MiB、完整结果 64 MiB；无公共路由、模型或自动执行。它**只适用于 v2 的小规模完整封存**：每来源最多 2,000 页/20 万行、整个证据另有 64 MiB 门禁。参考推广本期 281,759 行、前期 293,336 行合计 575,095 行无法进入此路径，不能拆成 TOP 样本冒充全量。后续须另建 v4 三期版本化封存/页 Reader 与日报合同，再进行真实三期和 20,000 SKU/50,000 对照行容量验收，之后才考虑 Agent 引用与 HTML/XLSX。

隔离 PostgreSQL 目标 `ai_assistant.test_business_cross_source_sku_materials`：合成正例复用真实 sealed v2 Reader 与 mapping pair，核三窗口缺源、同报告摘要及无跨域加总；退款加第二个当前 master SKU 造成 M1 多义，确认退款与全部 ERP 金额仍在未分配池；负例覆盖默认关闭、错报告/账号与返回前撤权。纯比较、日材料与 ERP 回卷相关测试作为前置。未修改旧 renderer 或生产数据。

整合隔离 PostgreSQL 四项 `.runtime/ai-pg-abfeabc3ca34/tests.log`（16.641 秒）及相关纯16项通过。首轮歧义退款用例错误要求整份结果没有任何已匹配 SKU 行，而夹具另有合法已匹配事实；现精确断言新增退款 5,000 分未进入任一已匹配 SKU、全部仍在未分配池，业务实现未因测试调整。未做参考57.5万行或正式文件验收。
