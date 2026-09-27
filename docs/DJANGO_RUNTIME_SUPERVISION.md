# Django 本机运行守护与主动健康监控

本文定义 `D:\teruisi-runtime\django-sales` 的进程崩溃恢复和主动健康监控。守护范围只包括该 runtime 的 PostgreSQL、全部已启用 Django reader/writer 和 BI reader；不得启动、停止、重启或接管 Worker、n8n、京东、天猫及其他模块进程。

## 1. 自动恢复边界

守护每 15 秒读取一次受控 `Status -Json`。只有下列两类状态可以自动调用既有 `Start`：

- PostgreSQL 监听器不存在，且没有任何外来/身份异常端口或进程；
- PostgreSQL 身份和 readiness 正常，但至少一个经过核验的 reader/writer 或 BI reader 受管进程已经停止。

以下状态只生成告警，不自动重启：

- PostgreSQL 或任一已配置 Django 端口被外来或无法核验身份的进程占用；
- PostgreSQL `max_connections` 低于完整 Django/BI 运行栈所需的 `120`，或普通角色连接槽已经耗尽；
- 当前 Windows 启动周期内的 PID receipt 损坏、PID 复用、身份变化，或出现未登记受管进程；
- runtime ACL 不符合契约；
- PostgreSQL 仍监听但未 ready；
- 进程都在运行，但 reader/writer readiness 失败；
- 任一领域 authority、revision、摘要、行数或 startup marker 陈旧/分歧；
- 状态探针本身失败。

允许恢复的失败必须连续出现两次，并在真正执行前再次探测；分类或 desired-state 发生变化立即取消。15 分钟窗口最多尝试 3 次，退避为 15、30、60 秒。守护只调用受控 `Start`，从不自动调用 `Stop`，也不绕过现有服务 mutex、进程所有权、authority、权限、migration 和 readiness 门禁。

Windows 重启会终止全部本机进程，但旧 receipt 可能保留，而数值 PID 可能在登录启动期间被其他短进程复用。operator 只有在 receipt 中的进程创建时间、启动时间和 receipt 文件最后写入时间三项都严格早于 Windows 当前启动时间时，才把它认定为“上一次开机遗留记录”。此时只删除旧 receipt，绝不接管或终止当前占用该 PID 的进程；随后仍由端口、进程候选、authority 和 readiness 门禁重新判定是否可启动。任一时间证据缺失、无效、位于当前启动周期，或文件在本次开机后被改写时，继续按所有权异常失败关闭。

## 2. 显式期望状态与停止竞态

`tools/django-local-service.ps1` 的显式 `Start` 成功后写入 `desiredState=running`；显式 `Stop` 在停止组件前先写入 `desiredState=stopped`。守护在期望状态缺失、损坏或不是 `running` 时失败关闭。

自动恢复把当前 desired-state 文件 SHA-256 作为 fencing token 传给服务 operator。服务 mutex 内必须再次确认相同 SHA-256 和 `desiredState=running`，之后才可启动。因而：

- 如果人工 `Stop` 先取得 mutex，后到的自动恢复看到 fence 已变化并拒绝启动；
- 如果自动恢复先取得 mutex，后到的人工 `Stop` 会等待恢复操作结束，再写 `stopped` 并停止整套服务；
- 守护不能在维护窗口中与人工停止互相争抢、反复拉起。

## 3. 主动状态与告警

最新状态保存在：

```text
D:\teruisi-runtime\django-sales\monitoring\django-runtime\state.json
```

健康状态最多每 5 分钟写一次心跳，状态变化、恢复尝试和失败会即时写入。告警按“严重度 + 代码 + 状态摘要”在 30 分钟内去重，并写到：

```text
D:\teruisi-runtime\django-sales\monitoring\django-runtime\alerts\pending\
```

告警不包含凭据、路径、用户标识或业务数据。根据系统通知规则，外部消息只能由名称精确为“志高助手”的唯一企业机器人发送到标题精确为“测试群聊”的唯一群；发送前还必须动态唯一核验群、机器人及安装可用状态。因此 runtime 守护本身不保存 webhook、robotCode 或群 ID，也不直接发送消息；它生成 `pending_local_outbox`，由具备动态身份核验能力的监控任务消费。身份无法唯一确认时保留本地告警，不回退个人单聊。

## 4. 受控启用

仓库脚本必须先通过不可变部署进入 runtime；部署清单必须包含 `django-runtime-supervisor.ps1`，不能只部署 one-shot service operator。启用不会重启业务服务，但前置要求 PostgreSQL、全部已启用 Django/BI 进程及其 readiness 全部健康：

```powershell
$supervisor = "D:\teruisi-runtime\django-sales\app\tools\django-runtime-supervisor.ps1"

& $supervisor -Action Probe
& $supervisor -Action Arm -Execute
& $supervisor -Action InstallStartup -Execute -ConfirmedStartupReplacement
```

`InstallStartup` 只允许把现有的受控 one-shot Django 登录启动项替换为受控 supervisor 启动项；遇到未知快捷方式时拒绝覆盖，写入或回读失败时恢复原快捷方式。它不启动、停止或重启任何业务服务。

安装后若需要在当前登录会话立即启动守护，可由操作者隐藏启动：

```powershell
Start-Process powershell.exe `
  -WindowStyle Hidden `
  -ArgumentList @(
    "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
    "-File", $supervisor, "-Action", "Run", "-Execute"
  )
```

`Run` 使用全局 singleton mutex 和绑定 PID、creation time、命令行、operator 路径/内容摘要的 receipt。重复实例、PID 复用或 operator 变化都失败关闭。循环内部错误会形成脱敏告警并继续监控，不会因单次探针异常退出。

从 PowerShell 7 启动 Windows `powershell.exe` 时，不能让子进程继承仅适配 PowerShell 7 的 `PSModulePath`；否则 `Microsoft.PowerShell.Security\Get-Acl` 可能无法加载。启动子进程前临时将当前进程的 `PSModulePath` 设为 `[Environment]::GetEnvironmentVariable('PSModulePath', 'Machine')`，启动后在 `finally` 恢复原值，不修改用户或系统环境。隐藏启动时将 stdout/stderr 重定向到 runtime 日志，并回读 `Status` 的 `supervisorProcess=running`、`health=healthy`；只取得 PID 或成功安装快捷方式不能当作守护已运行。

## 5. 查看、停用与代码回退

```powershell
& $supervisor -Action Status
```

计划维护时直接执行现有服务 `Stop`；它会先把 desired-state 设为 `stopped`，守护最迟在一个 15 秒周期内自行退出，再由服务 operator 停止受管组件。若只想停用自动恢复而保持当前服务运行：

```powershell
& $supervisor -Action Disarm -Execute
```

回退到不包含 supervisor 的旧应用包前，必须先 Disarm、确认 supervisor process 为 `stopped`，再恢复 one-shot 登录启动项：

```powershell
& $supervisor `
  -Action RestoreOneShotStartup `
  -Execute `
  -ConfirmedStartupReplacement
```

上述恢复只改登录启动项，不启停业务服务。完成后才能按既有停服门禁执行 `RollbackApp`。监控状态与告警审计不得因代码回退而删除。
