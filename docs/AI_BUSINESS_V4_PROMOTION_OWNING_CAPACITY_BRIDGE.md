# v4 推广页容量的拥有方内部接线候选

`business_v4_promotion_capacity_owning.measure_window`、`measure_three_windows` 默认关闭、没有公开路由、计划任务或生产取数调用。每个窗口先使用现有 `business_v4_promotion_replay.inspect` 对同一 v4 run/source 的持久页、内部 HMAC 工具成功审计、精确参数/游标、账号、店铺、修订与终页控制汇总做完整第一遍重放；第二遍仅以 `AiBusinessV4Chunk.payload_json` 的**数据库持久规范 UTF-8 字节**逐页测量，不把 ORM 解析后的 DTO 再序列化成“原页”。第二遍重建每次请求参数摘要并比对对应工具审计，按相同 receipt ID、调用 ID 与原页摘要重算收据链，必须等于首遍证明。返回前再次核管理员、父任务和各来源版本/检查点。

三期入口要求一个 v4 固定计划里的京东同店 current/previous/yearAgo 完整窗口，使用已版本化的日期包络逐一选精确 sourceKey，不按商品行猜店名。三期推广页/行/字节合计只证明推广部分；自然月财报、ERP、商智 SKU/SPU、B端、市场和店铺区间 UV 的容量仍各自未验。此实现不触发 owning 工具的新下载，而是读取已持久保存的页与对应成功审计。

首遍工具审计是**系统内部签名请求及成功收据**，不是京东平台对原始 HTTP 响应的独立上游签名；数据库 `payload_json` 也不是网络原始响应字节。因此结果可标 `internalSignedToolAuditBound=true` 和 `databasePersistedCanonicalBytesMeasured=true`，但 `rawUpstreamHttpBytesVerified=false`、`upstreamSignatureVerified=false`、`v4PlanMeasurementAvailable=false`、`sealerOrReportAuthorityGranted=false`。纯测量 v2 的 `v4Measurement` 保持 null，避免把未获来源权威的容量诊断直接塞进可显示 `runCapacitySupported=true` 的 v4 计划。

两次扫描分别按现有 600 秒及本桥 1,200 秒页间计时，三期入口有 3,600 秒页间边界；迭代器阻塞、数据库驱动预分配和整个临时目录/Excel 文件峰值不因此获得硬保证。实际 575,095 行与第三个同比窗口尚须在同一当前拥有方修订下跑完整数据，量出真实页大小、双遍耗时及 180 秒 claim 可完成性。隔离 PostgreSQL 目标 `ai_assistant.test_business_v4_promotion_capacity_owning` 包含三期真实合成采集、错签名请求、证明链漂移、账号撤权和默认关闭；本提交不声称这些 PG 用例已运行。

整合隔离 PostgreSQL 四项 `.runtime/ai-pg-77ea697e7c14/tests.log`（1.090 秒）通过。该测试使用少量合成页，证明代码接线和拒绝路径，不是当前店铺 575,095 行的规模、真实平台响应签名、ERP/财务/B 端覆盖或最终 HTML/XLSX 验收。
