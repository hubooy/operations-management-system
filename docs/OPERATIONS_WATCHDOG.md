# 运营管理系统独立看门狗

Windows 计划任务 `TERUISI Operations Watchdog` 在当前用户登录时、以及每分钟独立触发一次。执行文件安装在 `D:\teruisi-runtime\operations-watchdog`，不依赖 Codex 对话存活。任务使用原 Windows 用户与普通权限；现有 DPAPI 和钉钉身份仍要求用户已登录。电脑关机、注销或 Windows 计划任务服务停用时不提供检查。

2026-09-20 修复每分钟空白终端弹窗：计划任务最外层使用 `tools/watchdog-launcher/NoConsoleLauncher.cs` 编译的 Windows GUI 启动器，以 `UseShellExecute=false`、`CreateNoWindow=true` 和重定向标准流创建 PowerShell。从进程创建时就不分配控制台，不能只依赖 PowerShell 的 `-WindowStyle Hidden`，后者在 Windows Terminal 接管时仍可能先弹窗。启动器等待直接检查进程并回传退出码，消费输出但不记录正文；不使用 kill-on-close job，避免结束看门狗时连带终止其恢复的业务服务。安装记录绑定脚本、启动器源码及二进制摘要；已有任务只替换受核验的 action，保留触发时间、身份和设置。

## 检查与恢复

2026-09-20 已增加市场业务的被动观测：市场 reader 只记录榜单/筛选真实读取的结果、耗时和时间，独立看门狗最多每 5 分钟读取一次，不为监控执行全表查询。连续业务失败仅告警，不重启存活服务；没有近期样本时为 unknown，已打开的业务故障必须获得新成功样本才闭合，不能因记录过期当作恢复。通知身份核验失败保存固定阶段/原因，每事件最多 3 次、间隔至少 15 分钟复核；sending/unknown/sent 不重发。正式采用及限制见 [市场查询与看门狗优化](MARKET_QUERY_WATCHDOG_RECOVERY.md)。

每轮通过现有控制器核验 12 个后端组件、Worker 的不可变版本和进程归属、Django supervisor 的实际状态；另查首页、Worker live/ready、helper HTTP 和 3000/5791/5432 监听。内部健康端点必须返回约定 JSON，不能把任意 HTTP 200 当作就绪。HTTP 请求有超时和响应体上限，状态子进程限 60 秒；正常页面访问不等待看门狗。全栈检查比单次 HTTP 探测耗时更长，以独立运行的实际耗时为准。

第一次失败只保存证据，连续第二次失败才进入恢复判定。恢复调用 `tools/worker-local-service.ps1` 的原唯一 Start 引擎，复用已就绪服务，顺序为 Django/PostgreSQL → Worker/helper → Django supervisor；没有新的 Stop/Restart、构建、部署或业务补跑入口。

恢复在同一线程持有原 Worker 生命周期 mutex，锁内重新检查持久维护门与 desired-state 的 SHA-256。supervisor 启动还持有原 Django mutex 并复验其运行意图。失效回执由既有控制器按原规则处理：已验证且所属进程不存在时才可清理；端口外来、PID 复用、回执损坏或归属不明一律拒绝接管。单纯 readiness 降级不会强制重启存活服务。

每 15 分钟最多 3 次恢复尝试，间隔至少 5 分钟；预算在尝试前落盘。失败关闭的身份异常只告警。人工完整 Stop 与持久维护期间安静跳过；干净的 Worker-only Stop 保留后端时只提醒，不自动逆转。需要完整维护时继续使用 EnterMaintenance/ExitMaintenance，不能删除维护标记来触发恢复。

首次检查、维护退出后的下一轮、显式 Start 后的下一轮及故障恢复，都要求相隔至少 5 秒的两轮完整健康快照通过；中途出现失败或运行意图变化，不记为恢复成功。运行时间超过一分钟时计划任务跳过重叠实例，不叠加恢复进程。恢复阶段不强杀启动进程树；若启动本身长时间不退出，需要人工检查，不能为了让下一轮运行而中断业务子进程。

## 通知与证据

连续两次确认异常后，告警由 dws 动态核验指定组织中的本人“邓伟章”及唯一“志高助手”，仅向本人单聊发送，不向群或其他账号降级。消息包含首次异常时间、恢复结果、当前状态和是否需要人工处理。发送前持久预留，远端结果不明记 unknown，不自动重发；身份或授权失败记录 identity_failed。正常运行和维护跳过不发消息。通知预检使用真实身份查询和 dry-run，不能据此声称已验收客户端真实收件。

- `state.json`：最后检查、最后健康、连续失败、恢复预算和通知状态。
- `latest.json`：最新脱敏组件、端口和 HTTP 检查快照，覆盖更新。
- `evidence/<事件>-before.json` / `-latest.json`：故障前后快照、最近固定类型 Windows 事件的时间/编号/来源，以及 PostgreSQL 中断和恢复标记。
- `alerts/<事件>.json`：通知持久记录，不保存人员 ID、机器人 ID、密钥、消息原文或业务数据。

故障证据不被正常心跳覆盖，不自动删除。Windows 事件与数据库恢复记录只能帮助定位，不能单凭时间接近推断谁终止了服务。

## 操作

```powershell
# 从已审查源码安装/更新独立看门狗；不发布 Worker/Django、不停止业务服务。
pwsh -NoProfile -File .\tools\operations-system-watchdog.ps1 -Action Install -Execute

# 一次观察检查，不触发恢复或真实通知。
pwsh -NoProfile -File .\tools\operations-system-watchdog.ps1 -Action Check

# 查看独立任务与最后检查状态。
pwsh -NoProfile -File .\tools\operations-system-watchdog.ps1 -Action Status

# 只核验本人/机器人及通知参数，不发送消息。
pwsh -NoProfile -File .\tools\operations-system-watchdog.ps1 -Action TestNotification
```

自动检查执行受保护目录内的副本，并核验安装摘要。源码修改不自动进入已安装脚本，更新必须重新安装并验证任务 action、登录触发器、每分钟触发器、脚本摘要及至少两次独立任务成功。测试使用 `tests/operations-system-watchdog.test.ps1` 中的隔离目录和合成进程；禁止通过杀死正式服务验收恢复能力。

无控制台验收另运行 `tests/watchdog-no-console.test.ps1`：核对可执行文件 GUI subsystem、子进程控制台句柄为零、长标准流无死锁、子进程退出码和无效参数拒绝。安装后还需观察实际计划任务连续运行期间没有新建 WindowsTerminal/OpenConsole；不能仅凭任务退出码 0 宣称弹窗已解决。更新前禁用下一次看门狗调度并等待当前检查自然结束，不中断其进程树；重新安装会恢复调度。
