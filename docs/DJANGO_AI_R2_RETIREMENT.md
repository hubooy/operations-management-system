# AI 图片 R2 退役

## 范围与状态

本次只覆盖本机 AI 助理的 `ai-space/` 图片命名空间。已于 2026-09-06 完成本机正式发布、系统验收与终态回查；市场图片、运营事务附件及全局 R2 binding 继续保留。

2026-09-06 零点的只读预检结果：AI 对象数、字节数、multipart upload 和 part 均为 0；共享桶其余 38,050 个对象的元数据摘要为 `d16d43eb121f3b2ef413994ef77ab067595b07846196f7239a147c70b0e7beff`。停服后及发布后的终态回查均与此水位、保留摘要一致；没有历史图片需要搬运，也没有删除共享桶对象。

## 存储与失败处理

- 新增 Django migration `0005_postgres_image_payload` 与 `ai_space_asset_payloads`，AI 自有表由 44 张增加到 45 张。历史 39 张表和原 D1 采用摘要不变。
- 图片字节、资产元数据、任务成功状态、派发结果在同一 PostgreSQL 事务提交。单图最多 6 MiB，仍校验 PNG 结构、像素数、尺寸、字节大小和 SHA-256。图片生成在事务外执行，入库前再次验证租约、取消状态及 principal。
- payload 以资产 ID 一对一关联，仅允许 reader SELECT、writer SELECT/INSERT；数据库执行 authority fencing、不可变字节保护和延迟完整性校验。元数据缺少 payload、大小或 SHA 不一致不能提交。
- 预览/下载继续通过原公开 API 并执行 owner/scope 校验。Worker 内部桥仅保留授权、工具目录、只读工具执行和数据集操作；旧 `storage_get/put/delete` 不再可达，Python 调用也会在联网前拒绝。
- 没有未发布的外部对象；取消、失败或过期租约不发布图片，也不重复付费派发。旧清理队列只清理终态队列记录，不能删除已发布资产或字节。
- 本次采用空资产水位；migration 遇到已有资产会拒绝应用。R2 只读证据工具遇到任意 AI 对象或未完成 multipart 会拒绝退役，不提供删除或丢弃内容的回退路径。
- 正式备份闭合清单包含新 payload 表。恢复仅使用 PostgreSQL 备份/WAL/PITR 或前向修复；不恢复 AI R2 读写路径。

## 已完成的隔离验证

- AI PostgreSQL 33 项测试通过，包括发布事务回滚、拒绝字节篡改/删除、元数据摘要约束和禁止重复付费。
- 最小权限 reader/writer 的 21 项 HTTP 检查及 10 项数据库权限负向检查通过。合成 PNG 由真实最小权限 writer 保存，经真实 reader 下载，恢复后的 45 表内容摘要一致；没有真实供应商调用。
- R2 证据工具 3 项测试通过，覆盖其他前缀保留、任意对象/upload/part 阻止退役、歧义数据库拒绝和源文件不变。
- 前端 1,855 项：1,832 通过、0 失败、23 跳过；其中 3 项构建产物检查在构建完成后补跑，全部通过无跳过。其余 20 项为既有退役夹具跳过。完整构建、19 项 HTML 检查、18 项相关 Node 测试通过。
- lint 为 0 错误、10 项既有警告；全局 tsc 仍有 160 项既有错误，不能称为全局类型检查通过。

## 正式操作顺序

先保存正式 PostgreSQL 备份并验证成功，再按已批准维护窗口停止 Worker 和全部 Django 应用进程。将候选提交部署到受保护 Django runtime，应用新 migration、重新授予 AI 最小权限角色，并验证空 AI R2 水位。Worker 按精确 plan SHA 激活新的 immutable successor，回读并重绑登录启动项。恢复所有服务后核验 23 个 readiness、公开图片入口、旧内部存储动作拒绝及 AI 空命名空间；共享对象保留摘要必须一致。最后制作包含新表的正式备份并完成独立恢复演练，持久化正式采用证据。

`tools/ai-r2-retirement-evidence.py` 只读 Miniflare 元数据；没有清理或写库动作。任何非空 AI 对象都应先暂停正式切换并另做保全迁移。

## 正式采用证据

- 代码提交：`bc9dd36fdc9931889895bf2b448f90782293718f`。
- Worker release：`20260905T161941Z-2f1ddff054f33ff5`，manifest SHA-256：`d132f96fc826ad42b1cf6c87fcb77c394fe125a0a882906a46b2fc14f9423ec9`；登录启动项已验证绑定到此版本。
- Django app 指纹：`a2ca23385daf8bb0e8f6b7dc8512f43803ca1a9cf7138fd14c10cc53ae00556f`；migration `0005_postgres_image_payload` 已应用，最小权限已重新核验。
- 23 个 reader/writer/BI 端口健康，12 个公开 AI 读取入口通过；同一有效 HMAC 的工具目录请求成功，旧 `storage_get/put/delete` 均返回 403。
- 新结构正式备份：`daily-20260905T162938Z-8fa556de4423`，manifest SHA-256：`9e584650bad5c1d3aeb056fd4f7ce6c86a77a43a2b552c9eb5d444b732bb6e1c`。
- 已在独立端口 55448 完成恢复演练，生产数据库未被改写；源与恢复内容摘要均为 `106870f5a70797d4f03d08c9cb48534f907572f748b36423d1abe94f2045054a`。演练实例已清理，正式备份及审计保留。
- 受控守护已恢复 running/healthy；没有调用付费供应商或发送外部消息。

完整证据见 [AI_R2_RETIREMENT_VALIDATION.json](AI_R2_RETIREMENT_VALIDATION.json)。本次用户授权沿用当前任务已批准的本机维护窗口，范围仅为 AI R2 退役；全局 R2 继续供其他业务域使用。
