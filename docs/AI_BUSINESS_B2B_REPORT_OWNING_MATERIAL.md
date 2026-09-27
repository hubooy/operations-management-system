# 已封存报告内的京东 B 端逐日材料

`ai_assistant.business_b2b_report_material.prepare` 是集成型 v2 报告的内部只读候选，不注册公共路由、Agent 工具、模型调用或文件渲染器。调用者传报告 ID 和**精确京东店名**；服务先复核当前账号、报告/工作流快照、封存证据与目录，再从同一目录自动选择该店 `current/previous/yearAgo` 的 `jd_b2b/b2b` 来源。任一窗口未选 B2B，仅返回 `catalogue_only_missing_source`：它证明该**已封存报告目录**没有安排该窗口，不证明数据库、平台或其他报告没有 B 端事实。

已选来源必须通过 `Reader.info` 和全部 `Reader.pages` 的页链、修订、身份、控制总额及字节核验；随后再由纯 `b2b_daily_candidate` 独立复算固定比较日期、精确 sourceRef、逐日字段、期间汇总和同指标比较。当前内部桥总计最多 2,000 页、200,000 行、64 MiB 完整选中 B 端页，结果最多 640 KiB；任一超限整份拒绝，不截断。返回前再次复核当前账号、报告、工作流与封存版本。

结果含三窗口完整逐日/期间表及来源或目录缺口摘要。B 端与 ERP 净销售、平台商品销售的包含关系固定为 `unknown`，跨来源占比和增量固定为 `null`，金额不跨域相加；商品日访客不解释为店铺去重 UV。输出虽经过本报告的 owning Reader 重放，仍为未注册候选（`authorityVerified=false`、`registeredAgentTool=false`、`registeredRenderer=false`），不能单独授予 Agent 已读、正式诊断或工程文件发布权威。

隔离 PostgreSQL 合成验收覆盖三窗口实源金额/日行与增长、同报告目录缺源而库里另有 B 端行、跨店/跨账号拒绝、容量拒绝，以及返回前撤权。没有读取真实客户行或改动生产。
