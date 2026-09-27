# v3 财报签名分页的内部单步采集

2026-09-24。财报分页工具 `get_business_finance_source_page` 只登记在中央工具目录的 `business_collection` 面：仅无范围限制管理员、只读、直接执行，每页财报事实不超过38,000 UTF-8字节，含工具信封最多40,000字符。模型工具定义在该面固定为空；AI 聊天、普通 Agent、工作台公开查询均无法看到或调用它。Worker 使用现有内部签名凭据向 `finance_reader` 的固定 POST 路径取页，财报 reader 以真实账号、完整 revision、月→批次及精确行数前后复验。不增加 AI writer 对财务业务表的数据库授权。

内部 `business_finance_collection_v3.advance_finance_source(run_id, source_key, expected_version, principal, request_id)` **不接收页面参数**。它先从真实 v3 来源目录和不可变 AI chunk 重建当前财务检查点，从检查点拿精确查询/偏移/末行/来源版本；在 AI 写锁外通过带政策摘要的中央工具签名请求拿一页，要求工具审计成功，并用纯 `finance_collection_state.consume` 逐字段、行原值、摘要、批次及边界校验。随后在原 AI mutation 事务中锁住父任务和财务来源、再验当前账号及双版本 CAS、检查用户/全局额度，按 chunk→source checkpoint→parent 字节/版本的顺序原子追加。任何来源变化、权限变化、未知/失败工具回执或并发抢先推进都会拒绝本次追加，不重放未知模型调用。成功后再次从不可变 chunk 重建来源结果。

首片最多64个财务数据页，避免每页完整重放旧页造成近2,000页时的平方级扫描；超限保持当前任务和检查点，不能截断后标完成。单个财务来源可 `finished=true`，父 v3 任务仍为 `collecting/manual`，日来源零页、父 sealed 与五 Agent/报告/文件继续关闭。当前 `persistentEvidenceVerified=false` 表示物理页与纯校验尚未组成整份可用业务报告，且真实来源质量/跨来源同刻性仍需后续验收。

验证范围：中央目录旧条目及96个旧投影摘要保持原样；新工具只在内部面可见，伪造调用方 page 字段在 schema 层被拒，签名 reader 只到固定只读端点。隔离 PostgreSQL 要覆盖真实两页推进、错误 source/role、页面自洽伪造但 rowId 错误、跨页 revision 变化、父/source CAS 抢先以及旧 v1/v2 目录回归。此候选没有公开路由、调度启用、生产部署或付费模型调用。
