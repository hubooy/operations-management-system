# v4 财务自然月来源：内部单页采集与完整重放候选

`business_v4_finance_collection.advance` 仅从已固定的 v4 `finance / monthly_context` 来源领取精确 `months`、`scope`、`analysisPeriod`。首次从 `offset=0, afterId=0` 调用现有签名 `get_business_finance_source_page`；后续只用该来源的持久检查点和最后一块真实页重建 `nextOffset`、`nextLastId`、`sourceRef`、完整修订。每次只取一页，不接收调用方提供的页面，不扫描此前所有页。工具审计必须保存本次完整请求参数的规范 SHA 和规范响应 SHA，才可在父任务与来源版本 CAS 下原子追加事实块、收据、来源检查点及父计数。

v4 财务状态是独立版本，沿用现有财报拥有方的自然月、完成批次、精确范围、真实行 ID、页/行摘要、缺月、原生汇总与金蝶明细、空值与显式零语义。旧 v3 状态的 1,999 页/64 MiB 限制保持原样；v4 状态使用单来源最多 16,384 页/2 GiB 与整任务最多 65,536 页/8 GiB，并按 38,000 UTF-8 字节限制每个财报页。包含 v4 外壳与纯状态的整个持久检查点在写块前须不超过 32,768 UTF-8 字节，与 0035 数据库门禁一致。超限整步拒绝并保留原检查点；容量计划仍是测量候选，不是实际来源权威。

`business_v4_finance_replay.inspect` 对已完成的单一财报来源逐块重建状态、月份覆盖和核心指标原值，逐页核对不可变收据、成功工具审计与从前页真实偏移重建的完整请求参数摘要；最后复核账号、父目录、来源修订及计数。输出的是自然月 `monthlyContext`，缺月或缺科目不补零，来源比率不相加，财报金额不摊到日或 SKU，也不与 ERP/B 端/推广归因额相加。即使这个来源完成，父任务仍为 `collecting/manual`；`upstreamSignatureVerified`、`sealed`、`persistentEvidenceVerified`、`reportGenerationSupported` 都为 false。

本片没有公共路由、调度、Agent、模型、报告或文件输出。拥有方页的账号/批次/修订逐次复核与本系统的 HMAC 工具审计不是财报上游的独立数字签名；跨域非原子快照、其他来源完整性、整目录封存以及真实大容量运行尚需后续验收。最初复审发现绕开正常导入直接改已读取事实而不推进修订的混合快照风险；隔离分支后续新增 finance.0003 写入-修订同事务门禁和 finance.0004 不可回退修订，并通过隔离测试/旧数据升级恢复。正式环境尚未采用这两条门禁，且分段证明不能替代正式封存或真实财务来源对账。
