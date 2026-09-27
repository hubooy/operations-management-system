# n8n 无控制台启动

2026-09-11 晚间已恢复本机 n8n：确认并修复 Windows PowerShell 5.1 将普通原生 stderr 警告升级为终止错误的问题。计划任务现使用独立运行目录 `D:\teruisi-runtime\n8n-service\20260911-daf263377b07`，首页、健康和就绪检查均为 200，21:20 自然周报检查成功且为 `not_due`。2026-09-12 同步最新主线后重新验证，聚焦测试 6/6、全量单元测试 2109 通过/23 跳过/0 失败、lint 0 错误，并已合入 `main`；生产继续复用此前受控采用的相同源码字节，无需重复中断 n8n。部署期间修正包装脚本编码曾中断过期重试 1006，该执行现为 `crashed`，未手动重放；丽丽店原商品导出失败仍需单独处理。详见 [晚间恢复证据](evidence/n8n-native-stderr-recovery-20260911.json)。

2026-09-11 已在本机受控采用服务启动与命令节点两处修复。10:30 自然触发的新品周报检查 execution 956 成功，观察到其 cmd/PowerShell 子进程，未新增 Windows Terminal/OpenConsole；服务健康、13 条启用工作流及工作流/Webhook/owner 摘要回查通过。详见 [采用证据](evidence/n8n-no-console-production-20260911.json)。

`TERUISI-n8n-Service` 继续使用当前用户的交互登录身份，保留登录触发、失败每分钟重试、最多 999 次、忽略重复实例和不限运行时长。交互身份供原有浏览器自动化及当前用户 DPAPI 使用，不切换 SYSTEM，也不改变全局 Windows Terminal 设置。计划任务允许在电池供电时启动并继续运行，避免笔记本短暂的 AC/电池切换向 n8n 发送停止信号；回环监听、单实例和失败重试边界不变。

后台服务和失败重试是正常行为，弹出空白终端不是必需行为。直接把 `powershell.exe -WindowStyle Hidden` 作为计划任务动作，仍会先创建控制台并可能激活 Windows Terminal。

## 启动与退出

`tools/n8n-launcher/NoConsoleLauncher.cs` 编译为 Windows GUI 子系统程序，本身没有控制台。它通过 `CREATE_NO_WINDOW | CREATE_SUSPENDED` 创建固定系统路径的 Windows PowerShell，在恢复执行前将进程加入 `KILL_ON_JOB_CLOSE` Job Object。PowerShell 和原生子进程不创建控制台；包装程序等待 PowerShell 退出并原样返回退出码，供现有计划任务判断失败重试。停止包装程序或计划任务会结束所属进程树，避免孤儿 n8n 和重复监听。

Windows PowerShell 5.1 会把 `2>>` 重定向收到的原生 stderr 转成 `NativeCommandError`；当启动脚本使用 `$ErrorActionPreference='Stop'` 时，普通 Node 警告也会使脚本退出，继而关闭整个 Job 进程树。`tools/n8n-native-process.ps1` 使用无窗口原生进程和两个并行字节流直接追加日志，保留警告、UTF-8 和实际退出码。新日志为 `.runtime/n8n/service.stdout.utf8.log` 与 `service.stderr.utf8.log`；旧 UTF-16 日志保留，不混写。用户目录修改的早期假设已被后续退出推翻，不作为本次修复依据。

仅接受一个已存在的绝对 `.ps1` 路径；无效参数返回 64，创建进程或所有权失败返回 70，不弹消息框。n8n 的环境、回环监听、端口检查和 stdout/stderr 日志仍由原 `tools/start-n8n-service.ps1` 管理。启动前错误可通过计划任务 LastTaskResult 排查。

## 安装和验证

在隔离 worktree 先执行：

```powershell
& .\tests\n8n-no-console-launcher.test.ps1
& .\tools\install-n8n-no-console.ps1 -BuildOnly -InstallRoot '<独立临时目录>'
```

测试使用合成脚本和临时计划任务，验证 GUI 子系统、PowerShell/原生子进程的控制台句柄为零、中文空格路径、成功/失败退出码、无控制台输出、参数拒绝、任务持续运行及停止时无孤儿。临时计划任务在 finally 中清理，不调用 n8n。

用户授权本机采用后，在已验收源码执行：

```powershell
& .\tools\install-n8n-no-console.ps1 -ProjectRoot 'D:\运营管理系统'
```

安装程序编译到固定运行目录 `D:\teruisi-runtime\n8n-launcher\<源码 SHA256>`，先导出 `task-before.xml`，仅更换精确匹配旧命令的任务 Action，并把 `DisallowStartIfOnBatteries` / `StopIfGoingOnBatteries` 固定为关闭，再回读验证 Principal、Trigger 和其他 Settings 均未变化。它不会自动启停 n8n，不修改工作流、数据库或其他服务。已存在发布目录、正在运行的 n8n 任务和不匹配的任务动作均拒绝覆盖。标准输入输出绑定有效 NUL 句柄，避免 PowerShell 在调用网络查询或原生程序时自行分配控制台；Job 句柄禁止继承。

更换动作不改变已经运行的进程。受控切换前确认没有 new/running execution、helper 空闲；waiting execution 必须另行评估恢复时间。备份 n8n SQLite 时使用 SQLite backup API，不直接复制正在写入的数据库。对经过 PID/创建时间/命令行确认的旧控制台发送 Ctrl+C，让 n8n 正常收尾；确认旧进程退出且 5678 释放后，从同一计划任务启动新入口。回读 healthz、回环监听、唯一进程树、无新增可见终端/控制台窗口、工作流版本/active 和 Webhook 摘要。Windows 内部不可见 conhost 进程不等于控制台窗口。禁止通过演练触发业务下载或导入。

若验收失败，先确认本任务持有的新进程已停止，再从 `task-before.xml` 取原 Action 通过 `Set-ScheduledTask -Action` 恢复，保留当前其他任务设置。备份和发布目录保留作为回滚证据；不要覆盖或删除 n8n 业务数据。

## 工作流命令子进程

新品周报的正式 Schedule Trigger 每 5 分钟检查一次发送条件。n8n 2.32.7 的 Execute Command 实现使用 `shell: true, detached: true` 且未设置 `windowsHide`，因此服务启动无窗口后，该命令仍可能另外弹出 PowerShell。

`tools/n8n-command-no-console.mjs` 只替换两个精确匹配的 spawn 调用：Windows 不使用 detached，命令及取消时的 taskkill 都设置 `windowsHide: true`。保留 stdout/stderr、退出码、Windows taskkill 进程树取消、POSIX 进程组及业务命令。工具拒绝未知、重复或部分补丁；已完整采用时幂等。`--apply` 必须先验证正式任务停止且 5678 无监听，再备份原始模块与 SHA 并写入回读。示例：

```powershell
$module = Join-Path $env:APPDATA 'npm\node_modules\n8n\node_modules\n8n-nodes-base\dist\nodes\ExecuteCommand\ExecuteCommand.node.js'
node tools/n8n-command-no-console.mjs --check $module
# 受控停止 n8n 后执行；不更新或触发工作流。
node tools/n8n-command-no-console.mjs --apply $module 'D:\teruisi-runtime\n8n-launcher\vendor-patches'
```

启动脚本每次只读 `--verify`，防止 n8n 升级覆盖补丁后悄悄恢复弹窗。升级后须重新审查当前模块，再显式采用；未知版本或补丁缺失时失败关闭，不自动修改安装包。取消此功能时，受控停服后恢复原模块并同步恢复启动校验，不允许仅删除补丁导致持续启动失败。

## 2026-09-12 模块环境修复

天猫维护窗口曾直接从 PowerShell 7 启动 n8n，继承的 `PSModulePath` 只包含 PowerShell 7 模块目录。新品周报的 Execute Command 实际启动 Windows PowerShell 5，因而找不到 `ConvertTo-SecureString`，execution 1060–1064 失败；只读复现和仅使用系统/Windows PowerShell 模块路径的探针均证明凭据密文没有损坏。

在 0 个 new/running/waiting execution、helper 空闲并完成 SQLite 在线备份后，受控停止精确旧进程树，改由已安装的 `TERUISI-n8n-Service` 无控制台计划任务启动。新进程只监听 `127.0.0.1:5678`；未修改凭据，未重发新品周报，未重启 PostgreSQL、Django、Worker 或 helper。重启后的自然新品周报 execution 1065、1080、1089、1097 均成功，确认 Windows PowerShell 模块环境和原 DPAPI 解密链已恢复。私有运行回执位于 `D:\teruisi-runtime\tmall-store-isolation-20260912\n8n-clean-environment-restart.json`，重启前 SQLite 备份为同目录 `n8n-before-clean-environment-restart.sqlite`。
