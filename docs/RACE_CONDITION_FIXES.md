# 维护、周报读取与 Prompt 版本并发修复

状态：2026-09-15 经用户明确批准停服，三项修复已在本机生产采用。源码 `0452a15e`，Worker/helper `20260915T081612Z-730408eeaa9cbc1c`，Django manifest SHA `3ecceb7a0ba67f175b4513a751349c169ba1f303016181a6d3bd43b4434cc931`。详见 [生产核验记录](evidence/race-fixes-production-20260915.json)。

源码按优先级分别提交：生命周期 `128829dc`、周报读取 `b5b578b1`、Prompt 版本 `2303b043`。验证证据见 [候选核验记录](evidence/race-fixes-candidate-20260915.json)。

## 1. 完整生命周期与持久维护

### 日常停机与重启

`operations-system-service.ps1` 完整 Stop 现在只调用唯一 Worker 引擎的 `Stop -IncludeBackend`；引擎在同一把 Worker mutex 内完成网页停止、后端停止和回执返回。完整重启调用同一引擎的 `RestartFull`，在同一次持锁内依次停止 Worker、停止 Django、启动完整系统。热重启继续保留后端。

Worker 生命周期争用立即拒绝，不再排队 30 分钟后自行启动。Django 顶层 Start/Stop 在同一进程、同一线程持续持有服务 mutex，覆盖核心服务及全部域；子域复用可重入锁。ACL 的原有一次完整审计与有界上下文复用保持不变。后端停止失败时不会继续启动。

### 跨多个命令的部署窗口

完整 Stop 只保证该命令内的互斥。部署、HardenAcl、版本切换等多步维护必须使用持久维护标记：

```powershell
$maintenanceId = [Guid]::NewGuid().ToString("N")
& .\tools\worker-local-service.ps1 -Action EnterMaintenance -MaintenanceId $maintenanceId -Json
# 经受控发布流程完成 DeployApp / HardenAcl / successor 激活和所需校验。
& .\tools\worker-local-service.ps1 -Action ExitMaintenance -MaintenanceId $maintenanceId -Json
& .\tools\operations-system-control.ps1 -Action Start -Json
```

EnterMaintenance 会停止整栈，属于需明确停服授权的操作。执行前仍必须暂停派发、排空在途业务、完成发布前备份独立恢复；维护标记不能代替这些要求。n8n 自身不由这些命令停止或恢复。

标记保存在受保护 Django runtime 的 `run/system-maintenance.json`，绑定 runtime 和 32 位小写操作 ID。设置和结束标记仅从 Worker 引擎进入，以 Worker → Django 的固定锁顺序在同一线程完成；不将持有 mutex 的身份委托给子进程，也不新增可绕过 Worker 锁的 Django 维护写命令。已审查源码入口使用同目录 Django 库，immutable Worker 使用已部署库；库只读取当前部署 manifest 并执行既有 ACL/进程门禁，不替换运行代码。

标记在停机前持久保存。停机失败、操作者进程退出或 Windows 重启后保留；不靠超时自动解锁，也不因为 owner PID 消失就删除。相同 ID 可继续进入，另一 ID 拒绝接管。启动、热/完整重启、Django 域启动和 supervisor 的 Start 均在各自持锁区间拒绝活动维护标记；文件损坏、路径重解析或 runtime 绑定异常失败关闭。

生产 DeployApp/RollbackApp 要求维护标记、Worker/helper 端口已释放，且 Worker supervisor 进程回执已由受控 Stop 清除；仅端口空闲不能排除 supervisor 正在恢复子进程。继续执行原有全部停机、文件摘要和恢复门禁。ExitMaintenance 要求精确 ID、部署与 ACL 完整、后端及 Worker 均已停止；它仅结束维护，不启动服务。运行中不能提前解除维护。

只读检查：

```powershell
& .\tools\worker-local-service.ps1 -Action MaintenanceStatus -Json
```

### 首次采用与回退限制

本机已完成首次采用，后续维护直接使用上方协议。本次首次引导中，源码合并使受保护入口与旧 head 摘要不一致；在保存已审查新版副本并持有生命周期锁后，精确恢复旧 head 的入口字节，通过旧版本校验，再由独立源码工作树的新控制器进入维护。正式 successor 激活安装新版入口并恢复与 main 一致。该引导不允许修改旧 release、降低摘要门禁或把候选源码当成已部署版本。

旧版脚本不会理解新维护标记。因此首次采用须在已授权的维护窗口协调其他任务与旧启动入口，完成 Worker/Django 两侧升级及启动绑定；不能宣称只合入 main 或只升级一侧就已修复生产竞态。旧版排队中的控制器亦需在窗口前检查。首次安装之后，新协议保护后续维护窗口。

不得通过删除维护文件来修复启动失败。先检查本次 ID、部署清单、端口和停机证据，再通过新控制器结束维护。回退到不认识维护标记的历史控制器不具备本修复，应单独评估与协调。

## 2. 新品销售周报读取

周报两路读取共享 AbortController 和当前请求身份，30 秒期限包括响应体读取。新请求、切周、卸载使旧请求失效；只有当前请求可以更新数据、错误及 loading。切周立即清空原表格，避免日期已切换但旧表格仍可导出。

load 回调保持稳定，保存/学习/启停监控完成后的刷新读取最新周 ref，避免写请求开始时的旧闭包在返回后重新读取旧周。取消只作用于读取，不取消或重放写入。日期与销量口径不变。

## 3. 市场 Prompt 创建

`_create_prompt` 以原子事务覆盖版本分配、插入与审计。PostgreSQL 在 `MAX(version)` 前按类目取得事务级 advisory lock，空类目也锁定；同类目请求依次分配版本，不同类目互不等待。保留 `(category, version)` 唯一约束。审计失败回滚记录与版本分配并释放锁。

没有数据库迁移、权限扩展或生产数据回填。SQLite 开发环境保留原数据库语义；并发验收使用真实隔离 PostgreSQL。

## 验证入口

- `tests/system-lifecycle-race.test.ts`：Windows PowerShell 5.1/7 的隔离跨进程互斥、各域阶段持锁、维护 ID/损坏拒绝、完整重启顺序与失败停止。
- `tests/followup-request-race.test.ts`：执行真实加载回调，延迟合成响应验证旧成功、旧失败、loading、最新日期刷新、超时和卸载。
- `backend/market/tests/test_prompt_concurrency.py`：真实 PostgreSQL 同类目首次/已有版本并发、不同类目并行及审计回滚。
- `tools/market-annotation-postgres-rehearsal.py`：隔离市场回归、迁移漂移检查，以及真实 reader/writer 最小权限探针（含新 Prompt 版本分配）。

生产上线后另行核验 Running/Ready/exact_release、有效启动绑定、守护与接收器、部署代码摘要及发布后备份/E 盘归档。测试不发送消息、不执行真实模型调用。

2026-09-15 候选阶段验证结果：全量 Node 2196 项中 2170 通过、22 跳过，4 项因共用启动函数提取后仍定位旧代码块而失败；已更新定位并保留原安全顺序断言，相关 62 项、Worker release 42 项和最终双 PowerShell 维护测试 2 项均复测通过。隔离 PostgreSQL 69 项、真实 reader/writer 探针（包含 Prompt v2 分配）、构建和 20 项渲染检查通过；lint 为 0 错误/11 条原有警告，446 个后端边界模块无违规。该候选验证阶段未改动生产，无迁移或消息发送。

生产采用补充：维护中实际 Worker Start 与已部署 Django StartFinance 均拒绝，精确 ID 结束维护后总控启动成功。最终 Running/Ready/exact_release、全部域就绪、守护 healthy、钉钉 connected；两周周报与市场标注读取均 200，周报快速切周后日期与表格一致，三份正式前端资源逐字节匹配。发布前备份独立恢复、发布后备份 Verify 及前后 E 盘三文件归档通过；59 条迁移清单不变。维护期间 n8n 周报检查 2186/2187 因 PostgreSQL 停止失败，2188 在恢复后自然成功，未人工补跑或重启 n8n。未通过生产写入、模型调用或消息发送测试竞态。
