# AI 经营分析续工交接：市场 v2 五工具预览目录

日期：2026-09-25。整合分支 `codex/ai-business-current-integration`；完成本小阶段后暂停。本轮仅在隔离工作树与数据库验证，未修改正式数据库、工作流或服务，未调用模型或启用双端开关。

## 完成与证据

- 新市场 surface 在 Worker `AI_MARKET_V2_AGENT_RUNTIME_ENABLED=true` 与 Django `TERUISI_DJANGO_AI_MARKET_V2_AGENT_RUNTIME_ENABLED=true` 同时满足时，中央签名 edge 才列出并转发五项只读预览工具。旧词货 v1 四工具静态目录、顺序及摘要保持；所有新结果固定 `persistedRead=false`、`sameJobProviderPersisted=false`，没有 Agent job/派发/结果行。
- 前四项经 0053 admitted-paused → 0044 parked → 0045/0056 窄材料声明回到同账号原推广报告。角色包仅读正式发布页；分析读原推广报告拥有方材料；无固定预算返回 `unavailable_no_fixed_budget`/null；词货复用原拥有方只读路径。首轮真实角色测试发现误用旧 screening-profile reader，修复后目标三项 `.runtime/ai-pg-759ad2f0271c/tests.log` 通过；未发布角色包精确拒绝单项 `.runtime/ai-pg-81e89178091e/tests.log` 通过。
- 新目录/第五工具 Node 8 项、旧词货目录/工具 Node 16 项、Django 路由 2 项、变更文件 ESLint、Python 静态检查和 `npm run build` 通过。独立代码审查未发现默认开关、跨报告授权或候选已读状态的直接绕过。

## 下一步

1. 以当前真实五工具目录的固定顺序及摘要另建**版本化市场执行 profile**，保留 0053 admitted-paused 不可变及旧词货 v1 四工具不变。数据库当前硬拒节点、job 与派发，不可仅开前端 flag 就声称 Agent 已执行。
2. 为真实市场五 Agent 建同一 job/provider/tool 派发与结果持久回执，并让数值引用只接受对应 Agent 本人已读材料；预览的请求 ID 和 `roleClaim` 不能替代此证明。
3. 对参考大表测量拥有方读取的 12 秒/38–40K 容量。当前截止只能在同步读取前后拒绝，无法立即中断读取中的旧查询；需有界取消或数据库查询时限后再开放执行 profile。
4. 继续预算 v10 正式受保护连接/分卷下载、三期权威来源及最终 HTML/XLSX 同报告验收。参考 30 天推广 575,095 行、Excel 许可证和真实 ERP/B 端归属仍是独立门槛。

续工进展：分析页与词货的封存逐页/临时分组已接合作式截止，纯13项及隔离真实角色/旧词货回归共13项通过；角色包重建、固定预算及单条慢查询仍不能保证12秒内中断。此检查点不改变默认关闭状态或 Agent 已读结论。

此目录是默认关闭的**可调用预览**，不等于真实多 Agent 深度诊断或最终工程文件已交付。完整技术边界见[五工具说明](AI_BUSINESS_MARKET_V2_FIVE_TOOL_CATALOG.md)与[总状态](AI_BUSINESS_INTEGRATION_STATUS.md)。
