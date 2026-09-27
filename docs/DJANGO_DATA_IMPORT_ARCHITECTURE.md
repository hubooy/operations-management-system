# Django 数据导入架构契约

## 1. 适用范围

本契约用于把现有 Worker/D1 业务域逐个迁移到 Django/PostgreSQL。它定义可复用的安全不变量，不要求所有业务域共享表、进程、锁或写入所有者。

本契约最初用于月度财报，现已复用于网店、市场和商品经营，并用于库存管理候选域的完整实现与镜像验收。各域保留现有 React/Next.js 前端和公开 API 契约，不以迁移后端为由改写页面或形成第二套业务口径。

跨域 BI 等纯读模型是本契约的特例：它不拥有上游事实，不得为了“领域独立”复制销售、ERP、库存等权威数据，也不得创建虚假的 writer 或第二套业务 revision。它必须用独立最小权限 reader、组合源 revision、前后采样一致性和只写审计收据的迁移流程证明可用。

## 2. 固定组件边界

```text
浏览器
  -> 公开 Worker：真实鉴权、权限、上传边界、Excel 解析、HMAC、有界传输
  -> Django domain writer：二次校验、请求幂等、范围锁、原子发布、审计、回查
  -> PostgreSQL domain tables：唯一权威事实与历史审计
  -> Django domain reader：有界查询、scope、revision
  -> 公开 Worker：稳定响应契约
```

- Worker 不保存已迁移领域的事实、批次、指纹或写入状态。
- Django writer 不直接暴露给浏览器或外网。
- 临时文件/分片不是事实源；成功只能由 PostgreSQL 完成批次与落库回查证明。领域可以选择 PostgreSQL 有界暂存或既有对象存储，但不得因此形成第二写入所有者；已经退役对象存储的领域不得保留可达旧路径。
- 每个精确业务范围任一时刻只有一个写入所有者，迁移期间禁止长期双写。

## 3. 领域隔离

每个领域必须具有独立的：

- Django app 和 migration；
- 数据表、revision 和批次身份；
- 数据库 writer 角色或等价数据库级写权限边界；
- scope head、owner token 和写请求 receipt；
- readiness 与迁移/切换证据；
- 备份核对清单和恢复验收。

财务写入故障不得阻断销售总览、渠道或品类查询。财务迁移不得改变销售 write authority、销售表、ERP 主数据 authority 或 D1 销售 retirement 对象。

销售域的原始分片字节、owner、摘要和生命周期固定由 PostgreSQL 管理，生产代码不得读写 R2；这不授权删除市场图片、库存、工作流等其他领域仍使用的全局 R2 binding。

商品经营是组合读模型，不拥有上游销售、ERP 或库存事实。其 PostgreSQL reader 只读销售、ERP 主数据和库存的现有 PostgreSQL 权威；库存投影固定排除 `刷刷仓`，失败不得回查 D1 或阻断上游事实发布。SKU 快递费率、商品域批次、幂等、上传和 revision 才属于商品经营写侧。

库存正式切换前继续由 D1 单写，Django 库存目标只用于迁移和影子验证。正式切换后，PostgreSQL 拥有分仓库存、库龄、备货计划、库存设置、导入幂等/审计、分片和 revision；商品经营改由有界 inventory consumer 获取版本化完整投影。库存 reader/writer 只读销售需求和 ERP 参照，不得修改上游事实；库存故障不得改变其他领域 authority。

## 4. 公开请求与内部鉴权

公开 Worker 必须从 `requireAppPrincipal()` 得到真实 principal，验证角色和数据 scope 后，重新生成短时 HMAC 信封。签名至少绑定：

- 版本、Unix 时间和 request ID；
- HTTP 方法、精确路径和原始 query string；
- 请求正文 SHA-256；
- 规范 principal（email、displayName、role、scope）。

Django 拒绝未知字段、过期或未来签名、正文摘要不一致、角色不符、scope 不支持、重定向、超大请求和非受控来源。内部签名头不返回浏览器。

## 5. 解析与规范化

- 迁移既有领域时优先保留已经验证的 Worker/TypeScript Excel 解析器，避免同时改变模板解析和数据所有权。
- Worker 输出有版本的规范化业务行和解析告警；Django 必须再次校验类型、范围、稳定身份、行数和业务约束。
- 金额使用人民币分，比率使用明确的整数基点或领域固定单位；禁止浮点金额写库。
- 文件名、原始行号、行顺序、工作簿元数据和对象键顺序不得影响业务内容指纹。
- 未声明字段、无效签名、解析失败、表头错误、范围错误和未经证明的零行业务集合必须失败关闭。

## 6. 两层幂等

### 6.1 请求幂等

写请求 receipt 绑定 `request_id + actor + method + path + raw_query/query_sha256 + body_sha256`。同一身份、query 和正文可安全重放原响应；相同 request ID 携带不同 query 或正文必须冲突拒绝。即使 DELETE 等请求没有正文，也不能省略 query 绑定。处理中请求只能由有效 claim token 完成，迟到 owner 不得覆盖新状态。

### 6.2 业务幂等

业务判重固定为：

```text
domain + exact business scope + canonical normalized content
```

原文件 SHA-256 只用于签收和审计，不能单独决定 duplicate。业务字段、权威行集合或精确范围任一变化都必须创建新批次。

## 7. 范围锁与 fencing

- 可能重叠的月份、日期或快照按稳定业务基域串行抢占，不把整段范围文本直接作为互斥域。
- owner 是随机且唯一的导入尝试 ID，不是确定性批次号。
- scope head 保存固定长度 state token、generation、owner、当前批次和更新时间，并可 O(1) 读取。
- 事实发布前重新锁定 scope head、写 authority 和请求 receipt；在同一数据库事务中安装提交栅栏。
- 只有 owner、generation 和原 state token 仍匹配才能发布、失败释放或完成。
- 超时接管必须验证状态没有推进；旧 owner 之后的写入、清理和完成全部失败。

## 8. 原子发布与回查

同一事务内完成：

1. 锁定有效写 authority、请求 receipt 和 scope head；
2. 创建或更新 processing 批次；
3. 在精确业务范围内完整替换事实集合；
4. 写入尝试、内容指纹、告警和来源；
5. 推进领域 revision 和 scope state token；
6. 完成批次并清除 owner。

提交前/后必须核对批次、范围、行数、内容摘要和 revision。失败不得留下部分事实，也不得让失败释放覆盖前一个已发布 state token。

## 9. 历史迁移

迁移工具固定采用：

```text
dry-run -> 人工批准精确 run ID -> apply -> verify-only
```

- dry-run 只读源，输出解析版本、源身份、表/范围行数、完整规范摘要和 revision，不写业务表。
- apply 在目标事务内重新核对同一材料并单次消费批准；材料变化、批准复用或模式省略均零业务写入失败。
- verify-only 独立比较源/目标事实、批次、审计、摘要和 revision。
- 早期历史批次缺失现行内容指纹，或旧指纹与当前已发布权威事实不一致时，只能从该批次当前拥有的完整月份和事实行按现行规范确定性重建；必须保留原始哈希（如有）并新增带固定版本和原因的迁移审计，不能伪造为原始导入审计或静默沿用失真的指纹。
- 切换前暂停该领域写入，执行最终迁移和公开 API 契约核对；其他业务域继续运行。

## 10. 单写切换与恢复

切换前，旧后端是唯一 writer；新后端只做迁移和影子读取。切换时必须：

1. 暂停精确领域写入；
2. 生成并校验两侧备份；
3. 执行最终迁移和 verify-only；
4. 发布 Worker 薄适配；
5. 验证公开读写、权限、幂等和回查；
6. 原子确认 PostgreSQL writer authority；
7. 恢复该领域用户操作。

PostgreSQL 接收第一笔新权威写入后，旧 D1 不再是无损回滚目标。恢复只能使用兼容应用版本、PostgreSQL 备份/WAL/PITR 或经审批的前向数据修复。D1 领域对象只有在稳定观察和独立退役审批后才能受控退役。

## 11. 领域特例

### 11.1 财务域

本批迁移以下对象：

- 月度财报批次、月份和财报行；
- 导入尝试、内容指纹、scope head、请求 receipt 和 finance revision；
- 财报导入历史和财报分析 reader API；
- 与财报分析同一一致性边界内的经营目标、版本和删除审计。

现有 Worker 财报解析器继续负责 Excel 模板解析；Django 接收有版本的规范化数据。公开 `/api/imports/finance`、`/api/finance/analysis` 和 `/api/finance/targets` 契约保持兼容。财务 reader/writer 的不可用不得影响非财务销售 API。

### 11.2 商品经营候选域

商品经营迁移以下对象：

- SKU 快递费率批次、当前完整费率事实和历史导入记录；
- 导入尝试、内容指纹、单一 scope head、写请求 receipt 和 product revision；
- PostgreSQL 原始分片会话/字节和有界过期清理；
- 最新库存完整投影及原子激活 control；
- 商品汇总、AI 和全局搜索 consumer；
- 历史迁移、双侧 authority、系统 smoke 和 D1 retirement receipt。

Worker 继续解析 `SKU累计` XLSX，Django 接收 `product-shipping-rates-normalized-v1` 定点规范行。商品 reader/writer 使用 `8041/8042` 独立进程和数据库角色。D1 库存事实不在本次迁移范围；`0100` 只能退役商品快递费率表及其共享命名空间，必须保留库存表、库存上传和其他域共享行。完整门禁见 [`DJANGO_PRODUCTS_MIGRATION.md`](DJANGO_PRODUCTS_MIGRATION.md)。

### 11.3 库存管理候选域

库存迁移以下对象：

- 分仓库存和库龄事实、批次、内容指纹、attempt、scope head 与 revision；
- 备货计划、库存运营设置、写请求 receipt 和 PostgreSQL 原始分片；
- 库存总览、库龄、京东入仓、系统成本、AI、全局搜索和商品经营投影 consumer；
- 真实 D1 冻结副本历史迁移、双侧 authority、系统 smoke、备份与 D1 retirement receipt。

Worker 继续解析库存/库龄 XLSX，Django 接收版本化规范行并二次校验。库存 reader/writer 使用 `8051/8052` 独立进程和数据库角色。库存正式切换时的 D1 退役只能清理库存事实和库存占用的共享命名空间，并已证明当时的 ERP 及其他域批次、指纹、attempt、scope head、上传和 retirement receipt 均被保留；ERP 后续仅通过独立 `0110` 完成终态退役。完整门禁见 [`DJANGO_INVENTORY_MIGRATION.md`](DJANGO_INVENTORY_MIGRATION.md) 与 [`DJANGO_ERP_REFERENCE_MIGRATION.md`](DJANGO_ERP_REFERENCE_MIGRATION.md)。

## 12. 必测负向路径

- 相同内容改名、换序和重复提交；
- 同月份内容变化和多月份部分重叠；
- 两个管理员并发写入；
- 请求成功但 Worker 丢失响应；
- owner 超时接管与旧 owner 迟到；
- 发布中数据库错误和进程中断；
- 非管理员、受限 scope、HMAC 篡改和正文替换；
- 源材料在 dry-run 与 apply 之间变化；
- 备份损坏、恢复目标指向正式数据库或清单被篡改；
- 财务服务失败时销售总览、渠道和品类仍正常。
- 商品 reader/writer 失败时销售、ERP 和库存权威不变，且商品路径不回查 D1；
- 库存投影缺页、乱序、owner 过期、调用方伪造摘要和分页期间 revision 变化；
- 商品 D1 退役保留库存域、其他共享导入行和既有 retirement receipt。
- 库存迁移对同日历史重导只发布最后完成批次事实，同时保留全部历史批次和迁移排除审计；单批次重复业务键必须拒绝。
- 库存 authority pending/retirement 必须同时识别新旧库龄 scope key、`inventory_age:` 批次关联；其完成证据必须证明在后续 ERP 独立退役前保留了 ERP 共享行。
- 库存 reader/writer 失败时销售、ERP、财务、网店、市场和商品经营 authority 不变，库存路径不得回查 D1/R2。
