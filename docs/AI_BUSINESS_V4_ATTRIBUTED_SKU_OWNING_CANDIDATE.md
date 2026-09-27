# v4 跟单 SKU 关系拥有方只读候选

`backend/ai_assistant/business_v4_attributed_sku_relation.py` 增加默认关闭的内部 `page`、`read_row`，无公共路由、Agent 工具、renderer 或生产开关。请求须显式给出 current/previous/yearAgo 三个不同来源 key，均属于同一 v4 run、同一固定计划、京东店铺和原始分析日期；每次仅处理选中窗口，其他两个只核持久目录身份，绝不把同源事实或不同关系视图金额加总。

所选窗口先由既有 `business_v4_promotion_replay.inspect` 完整重放并检查持久事实块、请求游标和内部成功审计。第二遍复用 `business_v4_promotion_capacity_owning._receipt_pages` 的规范原文字节及精确请求审计核对，将完整页流交给新版纯关系读取器；该流必须耗尽并通过同一收据链尾检，之后还要对三来源、计划版本、账号和来源修订逐项终栅栏。结果包含三来源目录、选中来源的重放证明/收据链、纯关系表摘要与独立 `bindingDigest`；响应不超过 38,000 字节，过宽单行拒绝，页缩短时保留正确 `nextOffset`。默认 `enabled=False`，传入的 `sourceRef` 或自称证明不能直接授予访问。

边界：这仍是 `collecting/manual` 的 v4 完成来源候选，不是数据库封存或京东上游独立签名。`sourceAuthorityVerified=false`、`agentReadPersisted=false`、`registeredRenderer=false`，其他两窗口的完整重放也标为 false。单窗口双遍+纯分组外层仅有 1,500 秒页间合作式期限；同步 DB 迭代器阻塞、全系统临时盘和真实 575,095 行拥有方性能仍须目标环境验收。三窗口逐页数值比较、商品主数据和正式 HTML/XLSX 均未接入。

隔离 PostgreSQL 目标：`ai_assistant.test_business_v4_attributed_sku_relation`。合成正例使用现有 v4 真实签名请求/持久收据夹具完成三份各 101 行来源，验证只读一份、行/费用守恒、有界页、精确行及三来源绑定；负例覆盖默认关闭、重复/缺失/外来窗口、错误精确行、末页字节改变与中途账号撤权。PG 由整合任务串行运行；本独立提交仅做静态和纯回归，不使用客户数据、生产或付费模型。旧 v2 词货与推广 SKU 接口源码未改。

整合隔离 PG 三项 `.runtime/ai-pg-313ee0fbcb16/tests.log`（1.783 秒）通过，纯相关17项与静态检查通过。此处 PG 仅用三窗各101行合成持久页，不代表参考57.5万行的拥有方性能，也不授予封存/Agent/文件采用。
