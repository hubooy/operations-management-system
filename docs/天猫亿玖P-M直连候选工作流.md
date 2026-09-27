# 天猫亿玖 P/M 直连现行工作流

## 结论与边界

`automation/n8n/tmall-yijiu-direct-pm-candidate.workflow.json` 因历史兼容保留文件名，现已作为志高亿玖专卖店的现行仓库模板。P（推广商品报表）和 M（货品主数据）使用浏览器登录态下的受控接口方式；A、B、C、共享 helper 认领、同一 n8n execution ID、店铺隔离、导入幂等和落库回查不变。

该文件是同一 workflow ID 的现行替换版本，不是第二条业务流水线：

- workflow ID 仍为 `M4xY8kQ2vR6sT9pC`；
- 仓库状态固定为 `active=false`；
- 只允许 `tmall-yijiu`，并要求 P/M 都携带 `X-TERUISI-TMALL-CANDIDATE-PROTOCOL: yijiu-direct-pm-v1`；
- 本机 n8n 只允许最新发布版本处于 active；旧版本保留历史审计，但不得承接新的定时或恢复 execution；
- 不能和旧版同时激活，不能单独运行 P 或 M，不能绕过 n8n 直调 helper。

同事提供的 macOS/Claude 归档仅作为只读的端点与任务节奏参考。归档代码未被执行，也没有照搬它的 Cookie 文件、配置、导入或断点行为；当前实现使用 TERUISI 已有的 Windows 独立 Chromium、DPAPI 登录守护、店铺注册表、活动清单和导入回查。

## 现行流程

```text
定时 / 手动完整运行
        ↓
领取共享 helper（workflow key + execution ID + tmall-yijiu）
        ↓
A 计划单个目标日并核验亿玖登录身份
        ↓
B 下载并校验生意参谋商品日 XLS
        ↓
C 单次导入并回查商品日覆盖
        ↓
P 直连创建商品报表 → 唯一 taskId 轮询 → ZIP 校验 → 单次导入回查
        ↓
M 每日到期？──同日已完成→ not_due → 关闭亿玖 Chromium
        │
        是
        ↓
捕获出售中首屏 MTOP 列表模板 → 20 条分页核总数
        ↓
itemId 排序分批 → 每批基线/提交/唯一记录/下载/商品集合校验
        ↓
合并一个权威 XLSX → 单次导入回查 → 下一到期日推进到次日 → 关闭浏览器
```

## P：推广商品报表

现行节点请求 `POST http://127.0.0.1:5791/promotion-direct-v1`。helper 复用亿玖独立 Chromium 的浏览器 Cookie 存储，并从 `bpcommon.alimama.com/commonapi/report/async/findPage.json` 的本轮真实请求 URL 临时取得 `csrfId` 和 `loginPointId`。两者不写活动清单、n8n 或日志。

创建报表的业务口径固定为：

- 起止日期为 A 计划的同一个业务日，`splitType=day`；
- `fieldType=all`；
- `unifyType=last_click_by_effect_time`，`effectEqual=15`；
- 场景为 `onebpSite`、`onebpSearch`、`onebpDisplay`、`onebpStarShop`；
- 维度为 `promotion + campaign`，即商品与计划；
- 报表为 `item_promotion`，不使用同事旧样例中的 `unifyType=zhai`、`subPromotionTypes=[ITEM]` 或 `promotion + date`。

安全与恢复约束：

1. 调用创建接口前原子写入 `report_submitting`。如果 HTTP 响应丢失或结果不明，清单保留在未决状态，后续自动执行禁止再次创建任务。
2. 创建成功必须返回唯一 `taskId`。轮询和取下载地址都只按该 ID，不靠文件名、最新时间或列表首行猜测。
3. OSS 临时链接只允许 HTTPS `*.aliyuncs.com`，只在内存使用，取得后立即下载，不写清单。
4. ZIP 不得超过 25 MiB，必须有 ZIP 魔数，并继续复用现有导入器校验店铺、单日日期、业务行、商品身份与哈希。
5. 导入接口返回成功后，还要重新读取推广覆盖，确认目标日存在，才把 P 标记完成。
6. 原 UI 版 P 若存在 `report_submitting`、`report_submitted`、下载或导入中的活动清单，直连现行流程拒绝接管。

## M：货品主数据

现行节点请求 `POST http://127.0.0.1:5791/product-master-direct-v1`。亿玖持久节奏固定为每日一次；完整成功后把 `nextDueDate` 推进到下一上海日，失败不推进。同一业务日已经完整成功后再次定时/CLI执行会返回 `not_due`；n8n 手动完整运行仍可通过原强制 M 请求头明确重跑。

M 不保存 Cookie 文本。helper 先导航到“商品 > 我的商品 > 出售中”，在导航前监听真实首屏 MTOP 请求，只接受：

- Host：`h5api.m.taobao.com`；
- API：`mtop.tmall.sell.pc.manage.async`；
- 内层只读路径：`/tmall/manager/table.htm`；
- 首屏 `current=1` 且 `pageSize=20`。

分页使用页面真实的 `tab/filtertab/filter/table` 模板，只修改页码；每页都要求响应回显 `pageSize=20`，`total` 在整轮保持一致，最多 100 页，最终 itemId 数必须等于 total 且跨页不重复。

唯一允许的写类 MTOP 路径逐字固定为：

```text
/tmall/manager/batchFastEdit.htm?optType=batchExportItem&action=submit
```

函数不接受 `optType`、`action` 或替代路径参数，n8n 与店铺配置也不能改变它。这样同接口族的上架、下架、删除或其他批量编辑动作没有可达参数入口。

商品按 itemId 排序后每 20 个一批，逐批串行：

1. 提交前读取导出记录并保存全部 record ID 基线；
2. 写入 `submitting` 后才调用固定导出路径；响应未决时禁止重提；
3. 只接受基线之外恰好一条新记录，并核对该批行数与安全创建时间窗；多个新记录、行数不同、任务失败都停止；
4. 完成记录必须有受控 `excel-tmall-item.oss-*.aliyuncs.com` HTTPS 链接；
5. 每个 XLSX 都要精确核对商品 ID 集合等于该批 itemId；
6. 恢复执行会重新读取全部出售中商品并比较模板摘要、商品摘要和分批摘要，变化时不继续补批；
7. 全部分批文件通过后，复用现有合并器生成一个唯一商品数等于出售中总数的权威 XLSX，只调用一次货品导入并完成落库回查。

原商品管家或 UI 逐页 M 只要还有活动清单，直连现行流程一律拒绝接管，避免两种协议同时创建任务。

## 发布与版本门禁

每次调整仍按以下顺序受控发布：

1. 在隔离环境通过单元测试、lint、现行 JSON 确定性生成和差异检查。
2. 备份 n8n 当前 workflow ID `M4xY8kQ2vR6sT9pC` 的已发布版本，确认当前只有一条 active 亿玖流程。
3. 先发布包含两个直连路由的 helper 代码；不得先让 n8n 指向不存在的路由。
4. 只做无业务动作的登录态、路由鉴权和活动清单冲突预检；不得用单节点执行测试导出。
5. 确认现有 P、M 没有业务动作阶段的活动清单。
6. 将现行模板作为同一 workflow 的新版本导入，逐项核对 A→B→C→P→M、上海时区、11:00、同一 execution ID、店铺键和直连协议头。
7. 在明确的维护窗口停用旧发布版本后再启用最新版本，保证任何时刻只有一个 active 版本。
8. 从 n8n 启动一次新的完整 execution；核验 P 的 taskId/ZIP/批次/覆盖和 M 的 total/分批/合并文件/唯一导入批次。不得直接调用两个 helper 路由。

## 故障恢复

旧 UI 版和旧直连版本只保留历史审计，不作为回退运行路径，也不得重新激活。若最新版本进入 `report_submitting`、M 批次 `submitting/submitted/downloading` 或导入阶段，必须按同一版本的活动清单核对平台任务与已导入批次；修复后只能由 n8n 创建新的完整 execution，不能切回旧版本重建任务。
