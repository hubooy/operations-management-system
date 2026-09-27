# 推广来源逐页读取与 13 表分卷接线候选

`business_report_composition_promotion_stream.prepare()` 默认关闭。它只接受现有 13 表同源预览的报告摘要，重新读取当前管理员、`AiReportRun`、workflow、sealed-v2 证据、mapping plan 和所有选中来源 revision。推广来源必须是该报告选中的京东 promotion 来源。

入口通过同一封存证据的 `Reader.pages` 完整逐页读取两遍：第一遍计算完整规范行根摘要，第二遍把 `(sourceKey, rowIndex, 完整来源行 JSON)` 的连续迭代器交给有界分卷暂存器。两遍均由 Reader 复核页序、请求游标、来源修订、日期窗口、页摘要、末页与控制汇总；每页发出进度检查点，但当前进度是观测信息，**不是断点恢复许可**。文件暂存前后重核同一报告及来源身份；没有模型、Agent 工具、公开下载或发布。

本接线证明真实拥有方迭代器可以进入上一切片的 HTML/XLSX 分卷流程，不把合成 Table 当作来源。纯测试覆盖同报告完整页流、关闭入口、错报告和来源修订漂移；隔离 PostgreSQL 目标测试使用真实 sealed-v2 报告，已写，等待整合任务串行运行。测试与接线均未改动正式数据或预算迁移。

## 容量阻点

- `business_evidence.py` 的 v2 收集写入对整个 run 限 2,000 页/64 MiB；`business_collection_continuation.py` 的续页准备也限相同额度。
- `business_sealed.Reader.info/pages` 读时再次限制单来源不超过 2,000 页/64 MiB。`cross_source_kpi_plan` 与推广关系纯层按 100 行/页推得最多 200,000 行；13 表基础投影另限 50,000 行/32 MiB。因此参考 575,095 行不可能从当前 sealed-v2 同报告读出。
- 独立 v4 采集支持每来源最多 16,384 页/2 GiB，但它属于另一套 `AiBusinessV4Run`，目前没有把它与 13 表 `AiReportRun` 绑定的正式报告来源目录；v4 封存验证仍声明 `reportGenerationSupported=False`。把独立 v4 run 当成此报告的推广来源会错绑店铺、窗口与权限，当前入口明确不接受。

完成真实规模前，须版本化建立 v4 报告/来源桥：同报告、同店、三期日期与选择、封存/应用签名、来源修订与逐页内部审计在数据库中固定，并给出可恢复的页级已读/暂存进度；随后重新验真实行宽、截止时间、源总量、HTML/XLSX 与 Agent 引用。当前不能声称 575,095 行已进入报告。
