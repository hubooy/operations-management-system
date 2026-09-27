# 经营分析开发暂停点

**2026-09-18已收到用户“继续工作”并恢复开发。以下是9月17日历史暂停快照，不代表当前执行状态；最新状态见 `AI_BUSINESS_REMAINING_ACCEPTANCE.md` 及各批候选证据。**

2026-09-17，用户明确要求暂停，当时停止开发及测试，等待恢复指令。

工作区：`D:\运营管理系统-ai-business-analysis`；分支：`codex/ai-business-analysis-stages`。最近已提交并推送 `140d1ba7`，包括第50–51批固定筛查真实五 Agent 调度、原子 HTTP 写入及完整文件交付；第47–49、52、53批也已推送。未合并 main、未发布、未操作生产业务数据或服务，也未调用付费模型。

## 已验证

- 新流程三条子任务绑定拒绝测试及实际五 Agent → 人工批准 → 正式 HTML/XLSX → 全部分块下载 SHA 校验，共4项通过：`.runtime/ai-pg-c5493384642b/tests.log`。
- 新签名 HTTP/文件创建恢复和提交回滚共6项通过：`.runtime/ai-pg-88a1fa8ad32e/tests.log`。
- 旧报告与并发33项、旧关联与预算9项通过：`.runtime/ai-pg-5d4fadbdda37/tests.log`、`.runtime/ai-pg-bacdf983e5fb/tests.log`。
- 正式合成 HTML 35表的搜索、CSV、桌面及390px检查通过，根 Agent 已目视：`.runtime/screening-formal-files/browser-evidence.json`。
- 其他分组验证及首次失败后的修正记录在已提交的执行、文件、工作台候选证据中。原五阶段尚未达到全部验收条件。

## 暂存但未提交的工作

1. **市场选项第54批**：owning projection、只读分页、实际 AppUser 撤权检查、Unicode casefold搜索、候选 `market.0005` 两表、导入同步、权限/健康/备份配套已落盘。代码静态检查通过，11项 `market.tests.test_analysis_options.MarketOptionsOwningTests` 尚未执行。只允许恢复后在隔离 PostgreSQL 验证；不得执行正式迁移。准备建库前复核 harness 对新表、序列及 AppUser 五列最小授权；补独立复审、升级和恢复、旧市场回归及文档。
2. **关键词×明确推广SKU第55批**：新纯模块和15项纯测已通过；真实 TypeScript 归一化导入→Python投影的合成边界测试正在补充，最终结果需回读。尚未接封存 owning 服务、工具或文件；不能据此声称完整词货诊断已完成。原顶层 skuId 的模糊首匹配可能取到触发/跟单SKU，本片明确不用其兜底；同角色别名冲突仍需后续处理。
3. **新筛查报告首屏**：`business_export.py` 新增经营摘要优先、技术元数据后置，约12行变化；仅编译通过，尚未重新执行文件集成和视觉检查。保留旧协议路径。
4. **原生 Excel 验收工具**：`tools/business-native-excel-helper.ps1`、`tools/business-native-excel-rehearsal.py` 尚未提交、最终工具复审未完成。仅用私有实例和 `.runtime` 合成副本；禁止附着用户已有 Excel。

## Excel 实机发现的未修复缺陷

初始15位置参数 Open 的失败含验收脚本 COM 编组问题；改成3参数后进一步定位到产品兼容性问题。单一私有 Excel 实例四方对照：原始工作簿打不开；仅补 `teruisi-manifest.json` 的 JSON ContentType 可打开、180条公式重算和2341个值核对通过；仅改ZIP32仍打不开；ZIP32加JSON类型通过。证据：`.runtime/native-excel-de5932d5a5a5/result.json`。所以不能把问题归因于 ZIP64，也不能宣称现有 XLSX 实机验收已完成。

正式 writer 尚未修复。恢复时先完成版本设计：新单文件/多卷都应声明 JSON 类型，保留旧固定版本、已持久文件、摘要及旧尝试语义；初步考虑新渲染版本5/6，需要对应候选迁移与真实 PostgreSQL文件围栏回归，方案尚未实施。不要仅修改测试副本后报告产品已修好。

该对照轮 `Quit` 后自有 PID15268残留，独立 Agent 已按原记录的 PID、路径和开始时间精确终止并确认退出，证据 `post-helper-cleanup.json`。暂停时根 Agent 再查没有 Excel 进程。源文件 SHA 未改变；未来测试仍须独立实例所有权证明和精确清理。

## 恢复顺序

先读本暂停点、当前 git status 和相关差异；保留全部未提交工作。先修 Excel 文件版本并完成原生重算，再集成市场11项隔离测试/权限/恢复，收口词货纯合同与真实投影测试，补新摘要双文件检查。主 Agent 独占隔离 PostgreSQL端口55485，不让子 Agent 并发创建测试库。继续区分工程合成通过、真实模型判断、真实业务对账与正式采用。
