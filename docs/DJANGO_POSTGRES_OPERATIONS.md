# Django/PostgreSQL 持续备份与隔离恢复

本文定义本机 Django/PostgreSQL 共享权威库的日常逻辑备份、完整性复验、隔离恢复演练和保留策略。它补充各业务域的一次性切换备份，不改变销售、ERP 主数据、财务、网店、市场、商品经营、库存、运营事务、客服和 BI 的既定权威边界，也不提供 D1 回退入口。

## 1. 不停服边界

- 日常备份只连接已经运行且身份核验通过的 `127.0.0.1:5432/teruisi_sales`；不会启动、停止或重启 PostgreSQL、任何 Django reader/writer、Worker、n8n 或其他模块。
- 备份使用 PostgreSQL exported snapshot，把表行数、Django migrations、动态 revision 和写入权威证据与同一个 `pg_dump` 快照绑定。证据必须覆盖库中全部销售、ERP、财务、网店、市场、商品经营、库存、补货、运营事务、客服和 BI 表；ERP 证据包含 `erp_reference_write_authority`、货品/组合事实、两个 scope head、当前批次、迁移 run 与 ERP revision。authority 尚未激活的候选域只能表达候选状态，不能声称已切换。备份期间的新写入不会造成半新半旧的归档。
- 恢复演练不在生产 PostgreSQL cluster 内创建、覆盖或删除数据库。它在 `55432–55999` 的显式空闲回环端口和独立数据目录启动临时 PostgreSQL 17，恢复完成后比较内容证据，再停止临时进程并删除该次临时数据目录，只保留脱敏结果和日志。
- `Status`、`Verify` 和不带 `-Execute` 的 `Prune` 不创建备份、不删除数据，也不改变服务状态。
- 权威库未运行、端口/进程/数据目录身份不符、归档或摘要变化、恢复内容不一致时全部失败关闭；维护工具不会为了完成任务而接管进程或切换数据源。

受控 operator 位于部署目录：

```powershell
$maintenance = "D:\teruisi-runtime\django-sales\app\tools\django-postgres-maintenance.ps1"
```

源码副本不能直接操作 runtime。先按现有不可变部署流程验证、部署，再从上述受保护副本执行。

## 2. 日常备份

```powershell
& $maintenance -Action Backup -Execute
```

成功结果包含 `backupDirectory`、`manifestSha256`、`dumpSha256`、`contentSha256` 和 `serviceStateChanged=false`。最终目录固定为：

```text
D:\teruisi-runtime\django-sales\backups\postgres-daily\daily-YYYYMMDDTHHMMSSZ-<12 hex>
```

每个已发布目录只能包含：

- `teruisi-sales.dump`
- `backup-manifest.json`
- `backup-manifest.json.sha256`

工作目录先以 `.incomplete` 身份生成并完成 archive list、文件 SHA-256、manifest 和同部署复验，之后才原子发布。失败的未发布目录只会在精确身份与路径校验后清理。

建议调度在上海时区低峰每日执行一次；只有在首次人工备份、Verify 和隔离恢复演练全部通过后才能启用调度。调度失败只能告警，不得自动启停在线服务。

## 3. 独立复验

```powershell
$backup = "<精确 daily-* 备份目录>"
$manifestSha = "<64 位小写 manifest SHA-256>"

& $maintenance `
  -Action Verify `
  -BackupDirectory $backup `
  -ApprovedManifestSha256 $manifestSha
```

复验要求备份目录是固定根目录的直接子目录，目录名与 manifest `backupId` 完全一致，不包含重解析点或额外文件，并重新计算 manifest/dump SHA-256 与 `pg_restore --list` 条目数。未传批准摘要时可做本地只读巡检；恢复演练必须传批准摘要。

## 4. 隔离恢复演练

先选择一个当前没有监听器的允许端口，并生成一次性 12 位小写十六进制 `RehearsalId`：

```powershell
& $maintenance `
  -Action RestoreRehearsal `
  -BackupDirectory $backup `
  -ApprovedManifestSha256 $manifestSha `
  -RehearsalId "012345abcdef" `
  -RehearsalPort 55432 `
  -Execute `
  -ConfirmedIsolatedRestore
```

演练必须验证：

1. 临时 PostgreSQL 只绑定批准的 `127.0.0.1:<RehearsalPort>`；
2. 监听进程、PostgreSQL 可执行文件和 `-D` 数据目录属于本次演练；
3. restore 使用 `--single-transaction --no-owner --no-privileges` 且最多运行 30 分钟；
4. 恢复后的全部 `sales_*`、`erp_*`、`finance_*`、`netshop_*`、`market_*`、`product_*`、`inventory_*`、`replenishment_*`、`workflow_*`、`customer_service_*`、`bi_*` 和 `django_migrations` 行数，migration 清单、revision 与 write authority 的内容摘要与备份快照完全相同；
5. 生产数据库和在线服务状态均未改变。

脱敏结果保存在：

```text
D:\teruisi-runtime\django-sales\rehearsals\postgres-restore\restore-<RehearsalId>\
```

如果临时进程无法确认停止，工具会保留数据目录并标记 `requires_manual_review`，不会强删或终止身份不明的进程。

### 受保护 AI 候选的只读预检

在讨论采用 0067–0072 前，可从已核验的部署副本显式调用 `& $maintenance -Action ProtectedAiPreflight`。此动作只读取角色目录、迁移收据、私钥表所有权与当前备份身份的 SELECT 权限，不读取密钥行或运行备份。当前结果固定为 `blocked`，原因码指出缺失的角色/权限以及尚未实现的 owner/ACL 归档和加密策略。若数据库已经含受保护迁移，日常备份会在 `pg_dump` 前拒绝；含这些迁移的归档会在隔离演练 `initdb` 前拒绝。原日常备份与恢复参数不变。正式使用受保护迁移之前，须另行完成特权迁移/备份身份、加密归档与异集群恢复验收。

## 5. 保留与清理

默认保留至少最近 30 天并且至少保留 7 份已完整验证的成功备份。先只生成计划：

```powershell
& $maintenance -Action Prune -RetentionDays 30 -MinimumSuccessfulBackups 7
```

确认计划后才可执行：

```powershell
& $maintenance `
  -Action Prune `
  -RetentionDays 30 `
  -MinimumSuccessfulBackups 7 `
  -Execute `
  -ConfirmedPrune
```

清理只扫描 `backups\postgres-daily` 下名称精确匹配 `daily-*` 的直接子目录。切换备份、恢复结果、attestation、forward-recovery、retirement 证据、无效/被篡改目录和其他模块目录都不在删除范围。每个候选在执行前再次核验 manifest；删除审计保存在 `audits\postgres-backup-prune`。

## 6. 发布与回退

维护 operator 和 Python helper 属于 Django runtime app 的不可变部署内容。发布前必须通过 PowerShell 5 解析、专项单测和完整回归；发布时不得顺带启动、停止或重启服务。发布后先执行 `Status` 和一轮人工 `Backup → Verify → RestoreRehearsal`，确认无服务状态变化，再单独审批每日备份调度。

代码回退只回退 operator/helper 版本，不删除已经生成的备份或演练审计。生产数据恢复仍是独立审批操作；本工具不会自动把备份覆盖回生产库。

进程崩溃恢复、desired-state fencing、主动 readiness 监控和本地告警 outbox 见 [`DJANGO_RUNTIME_SUPERVISION.md`](DJANGO_RUNTIME_SUPERVISION.md)。守护只处理本 Django runtime，不把备份失败或数据分歧转化为自动重启。
