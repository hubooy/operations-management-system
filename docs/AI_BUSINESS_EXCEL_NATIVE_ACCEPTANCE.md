# Excel 原生兼容性候选验收

2026-09-18。本文只记录候选分支的合成验证，不代表生产采用、真实模型判断或经营效果验收。

## 实机发现及修复

Excel 路径为 `C:\Program Files\Microsoft Office\Root\Office16\EXCEL.EXE`，文件版本 `16.0.19127.20800`。首次脚本使用十五个位置参数调用 `Workbooks.Open`，连原生对照工作簿也失败，属于 COM 调用问题；改为三个参数 `Open(path,0,true)`，使用默认正常加载、禁外链更新和只读打开。

同一个私有实例的四方对照证据在 `.runtime/native-excel-de5932d5a5a5/result.json`：

| 合成文件 | Excel 打开与重算 |
| --- | --- |
| 原始文件 | Open 1004 |
| 仅补 JSON ContentType | 通过，180 条公式、2341 个值 |
| 仅重打包 ZIP32，XML 不变 | Open 1004 |
| ZIP32 并补 JSON ContentType | 通过，180 条公式、2341 个值 |

产品缺陷是 `teruisi-manifest.json` 缺少 OPC 内容类型声明，不能归因于 ZIP64。新单文件 renderer 5、新多卷 renderer 6 声明 `application/json`；已有 1–4 版不改字节或迁移文件内容。新建任务选择 5/6，旧任务恢复及下载继续使用持久行中的实际版本，不能借重试改版或复用旧任务 ID。

原生预算复算另发现 Excel `MOD` 对大整数商返回 `#NUM!`。最大预算与基期等天数恢复案例中，`QUOTIENT` 正常。新 5/6 的公式版本 2 改为 `n-QUOTIENT(n,d)*d`；只有已校验的非负整数及正除数可进入计算，分配分子不超过 `10^12`、约分后积不超过 `99999999999999`，乘减结果小于 `2^53`。数学结果、预算上界、空值和高精度拒绝规则均未放宽。旧公式版本 1 保留原 `MOD` 字符串。

原生脚本的 `SpecialCells` 在该 COM 环境误报没有公式，已改用 Excel 自己的 `SUMPRODUCT(--ISFORMULA(...))` 和 `SUMPRODUCT(--ISERROR(...))`。私有控制工作簿验证一条公式、故意的 `1/0` 错误和清除后的零错误，不能通过吞掉 1004 来判定无错误。参数写入显式转为 double/string/bool，避免 PowerShell JSON 值的 COM 包装转换错误。

## 当前结果与未通过项

- 最终前端构建通过；全量Node 2410项中2390通过、20项既有跳过、0失败/取消（299.807秒）。全库类型检查223行既有错误与第52批逐字一致，无新增；497个生产边界模块无违规。迁移0025的一个空白行保留在精确前驱SQL字符串内，用于逐字逆函数恢复，不作格式化改写。
- 58 项纯计算、导出及迁移合同测试通过：`.runtime/excel-opc-compatibility/pure-tests.log`。
- 41 项单双文件/多卷下载 Node 测试通过，包含 4/6 跨版本清单拒绝。
- 将 `HEAD` 原 writer 与原预算公式实际载入，固定 ZIP 时间，1/2/3/4 四组 HTML、XLSX 和证明逐字节一致：`.runtime/excel-opc-compatibility/legacy-bytes.json`。另有旧 4 筛查总览顺序、新 6 摘要优先的纯回归。
- 根任务执行 19 项真实隔离 PostgreSQL 文件服务及旧恢复测试通过，耗时 81.675 秒：`.runtime/ai-pg-53104740e6cc/tests.log`。包括旧 1/2/3 实际生成下载，旧 3/4 暂存后暂停/恢复不重新渲染，新 5/6 不复用旧 ID，旧文件 SHA 保持。5/6 新 guard 与原 4 guard 的数据库拒绝测试亦已通过；这不替代原生 Excel。
- 最新原生轮共 50 案，**49 案通过、1 案未通过**：48 个预算案例加一个 renderer 6 简表，核对 2080 个值，Excel 错误单元格为零。包括 100 目标、最大预算、缺失/零值、混合成交口径、精度缺口及恢复、内存修改参数和无效参数。证据 `.runtime/native-excel-5db64c136ae8/evidence.json`。
- 未通过的 `blank_total` 在执行 `ClearContents` 时收到本机 Excel “使用该应用程序的许可已经过期”错误，未执行该案的后续断言。已停止进一步原生操作，没有修改许可或换方法绕过限制。不能称 49 个预算案例全部通过。
- 原先另外 8 个独立预算边界、最新完整 renderer 6 正式合成交付的原生打开/复算仍待完成；此前旧 35 表对照通过不等于新完整交付已验收。恢复原生验收需有效 Excel 许可，不重用用户正在打开的工作簿。
- 独立 `0024→0025` 升级及 dump/restore 通过：`.runtime/ai-pg-3e91a50962f9/business-file-opc-upgrade.json`。65 张旧表摘要、1–4 文件字节、暂停的 4 版任务恢复保持；真实 writer 发布 5/6、reader 写入拒绝、错误 epoch 拒绝、原 grants、无新行逆迁移/再升级、有新行逆迁移拒绝以及恢复后完整清单复验均通过。该演练报告行仅为存储夹具，不冒充实际报告鉴权。首次演练因合成 authority 缺少合法终态字段而触发 `ai_invalid_authority`，记录保留于 `.runtime/ai-pg-54ca4845fea3/failure.log`；补齐夹具后复跑，没有关闭或放宽 guard。
- 另一条真实合成五 Agent 调度、人工复核及正式 renderer 6 文件链路通过（1 项，88.061 秒）：`.runtime/ai-pg-0f33e3640a7c/tests.log`。产物为 `.runtime/screening-formal-files-v6/` 的 36 表报告；Chrome 核验 119 行、经营摘要优先、搜索、CSV、手机无横向溢出，通过且无脚本错误或外部请求：该目录 `browser-evidence.json`。这是合成模型回执与真实服务链路测试，不是付费模型或原生 Excel 验收。

## 可复用隔离方法

[Python 监督器](../tools/business-native-excel-rehearsal.py) 只接受仓库 `.runtime` 内的合成文件，先校验 ZIP、关系和公式白名单，再复制到新的 `.runtime/native-excel-*/copies`。它不打开源文件，运行前后验证源和副本 SHA，输入副本设为只读。

[STA helper](../tools/business-native-excel-helper.ps1) 使用 `CoCreateInstance` 创建隐藏 Excel。先记录已有 PID，再通过新实例 Hwnd 证明 PID、可执行路径和开始时间属于本轮，之后才设置禁宏、禁事件、禁链接更新及隐藏属性。它不附着 ROT、没有枚举或关闭已有用户工作簿；每份副本不保存地关闭，只对证明属于本轮的实例 `Quit`。

监督器有总时限和事件无进展时限；超时或 `Quit` 后残留，只能在 PID、路径、开始时间全部匹配时终止该私有进程。无法证明身份就记录失败，不按进程名称批量结束。保存所有失败轮证据，不能只展示成功对照。最新原生轮 PID 50424 正常退出、所有源和副本 SHA 不变，未强制终止；早期对照轮的精确残留清理单独留在该轮 `post-helper-cleanup.json`。

许可阻断后仅作两项脚本收口：控制工作簿明确使用 `xlWBATWorksheet`，独立旧夹具兼容新增基期精度提示。这两项只通过语法检查，未再次运行原生 Excel。

许可恢复后可从新的合成输出目录运行：

```powershell
python tools/business-budget-excel-rehearsal.py .runtime/excel-opc-v5-budget --renderer-version=5
python tools/business-native-excel-rehearsal.py --formal .runtime/<新正式合成文件目录> --budget .runtime/excel-opc-v5-budget
```

不要把路径改为真实业务工作簿。脚本会拒绝 `.runtime` 之外的输入；它没有报告发布、数据库写入或业务修改能力。
