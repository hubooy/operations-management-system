# 京东商智商品明细 SPU 日数据下载与 API 导入

## 1. 目的与适用范围

本流程用于把京东商智“商品 → 商品明细 → SPU”的日维度数据下载到 `D:\谷歌浏览器`，校验后通过运营管理系统正式 API 导入为 `spu_daily` 数据集。

适用场景：补齐本月缺失的 SPU 日数据、重跑指定区间、接通或验证 SPU 导入入口，以及排查错误维度、合计行、中文乱码、日期缺失或重复批次。

相关实现：

- 下载程序：`tools/jdsz-product-detail-export.ts`
- 导入接口：`app/api/netshop/import/route.ts`
- 解析与校验：`lib/netshop/import-service.ts`
- 批次与明细存储：`lib/netshop/database.ts`
- 前端导入中心：`app/page.tsx`

## 2. 固定数据口径

| 项目 | 固定值或规则 |
| --- | --- |
| 平台 | 京东 |
| 店铺 | 所选注册表项的 `shopName` |
| 页面维度 | SPU，不是 SKU |
| 下载类型 | 分天下载 |
| 对比范围 | 不包含对比时间 |
| API source | `jd_sku_daily` |
| 入库 dataset | `spu_daily` |
| 日期上限 | 上海时区昨天 |
| 导入文件 | `.xlsx`，不允许 `.crdownload` |

运营数据查询前先遵守 `docs/OPERATIONS_DATA_QUERY.md`。优先调用 `teruisi_operations.get_data_freshness`；MCP 不可用且用户明确指定本机系统时，才使用本机数据库作为只读替代源。

## 3. 确定本月缺失日期

缺失日期必须依据 `spu_daily` 自身覆盖计算，不能仅参考通用销售数据截止日。

```sql
SELECT business_date, COUNT(*) AS row_count
FROM netshop_rows
WHERE dataset = ?
  AND platform = ?
  AND shop_name = ?
  AND business_date >= ?
  AND business_date < ?
GROUP BY business_date
ORDER BY business_date;
```

参数依次传入：`spu_daily`、`京东`、所选注册表项的 `shopName`、月初、下月月初。中文值必须参数化并以 UTF-8 或 Unicode 安全方式传递。

本月没有 `spu_daily` 时下载月初至昨天；已有连续覆盖时下载最后覆盖日次日至昨天。发现中间断点时按实际缺口处理，不凭记忆推断。

## 4. 下载 SPU 分天数据

在仓库根目录执行：

```powershell
npm run jdsz:product-detail-export -- --dimension SPU --start-date YYYY-MM-DD --end-date YYYY-MM-DD
```

程序使用专用持久化 Chrome 配置和 `D:\谷歌浏览器` 下载目录。日常默认以 `headless=new` 后台运行；只有登录失效、验证码、安全验证或店铺登录身份异常才打开当前店铺的独立可见 Chrome，并保持任务失败关闭、不自动重放确认或下载。首次登录使用 `--interactive-login`。不要改用独立登录状态的浏览器，除非用户明确要求。

### 4.1 创建任务前门禁

必须依次确认：

1. 页面是商品明细。
2. `SPU` 标签唯一且 `aria-selected=true`。
3. SPU 切换后的加载状态已结束。
4. 自定义日期起点具有未禁用的 `cell-start + cell-selected` 状态（CSS 类名可带动态哈希）。
5. 结束单元格的 `cell-end + cell-selected` 只能记录为诊断，不能作为成功依据；`cell-now` 也绝不是端点。
6. 页面“当前”日期回显与目标区间完全一致才可继续；若目标日带 `cell-disabled`，在任何点击前停止并报日期尚未开放，回显成功后不得再点结束日。
7. 下载弹窗唯一，包含“分天下载”和“不包含对比时间”。

单日区间也必须点击同一天两次，分别取得 `start` 和 `end` 状态。仅显示“当前：YYYY-MM-DD HH:mm:ss”或日历仍打开时不得继续，也不得用商品筛选区的“查询”按钮兜底。若弹窗只显示“下载设置”并提示最多 1000 行，这是实时汇总下载，不是离线分天任务；必须在写 manifest 和确认前停止。
8. 两个单选框的真实 `checked` 状态正确。

任一门禁失败，停止创建任务并保存失败截图。点击调用成功不等于页面状态成功。

### 4.2 下载中心门禁

- 创建任务前刷新下载中心并保存连续两次相同的同区间任务基线；首帧空表不能作为 baseline，真正没有历史任务时第二次空快照即可继续。提交后把唯一新增行的任务 ID（或“标题 + 创建时间”指纹）写入本地 manifest。
- 超时或进程重启后只按 manifest 接管原任务；找不到或匹配多行时停止，保留 manifest，禁止再建任务。
- 状态必须包含“已生成”。“生成中”即使出现“下载”文字也不能点击。
- 下载按钮必须唯一、可见且可用。
- `.crdownload` 存在时继续等待，禁止重复点击。
- 完整文件和临时文件都不存在时，最多重试一次下载点击。

京东任务文件名不包含 SPU/SKU 维度。同日期的两种任务会重名，因此最终文件必须改为包含 `_SPU_` 的名称，例如：

```text
701455_商品明细_SPU_离线_不包括对比时间_分天下载_2026-07-01_2026-07-19.xlsx
```

## 5. 工作簿校验

导入前验证：

- 文件非空且大小稳定。
- 前三列表头是 `时间`、`SPU`、`SPU名称`。
- 第二行可能为区间“合计”，但不能作为日明细导入。
- 明细日期集合与目标区间逐日一致。
- 每个明细行拥有有效日期和 SPU。

工作簿总行数通常等于：表头 1 行 + 合计 1 行 + 日明细行。API 的 `rowCount` 应只等于日明细行数。

## 6. API 接入约定

正式接口：

```text
POST /api/netshop/import
Content-Type: multipart/form-data
```

| 字段 | 值 |
| --- | --- |
| `file` | SPU 日数据 XLSX |
| `source` | `jd_sku_daily` |
| `platform` | `京东` |
| `shopName` | 所选注册表项的 `shopName` |
| `expectedDataset` | `spu_daily` |
| `expectedStartDate` | 本次目标起始日，`YYYY-MM-DD` |
| `expectedEndDate` | 本次目标结束日，`YYYY-MM-DD` |
| `note` | 本轮用途和区间 |

服务端必须：只依据精确 SPU 表头识别为 `spu_daily`；校验其与 `expectedDataset` 一致；排除 `SPU=合计`；把 `时间`写入 `business_date`、`SPU`写入 `spu_id`；逐日校验目标区间无缺日和越界日；用“数据集 + 平台 + 店铺 + 日期 + SPU”自然键合并重叠文件，并以文件哈希保证同批文件幂等；返回日期范围、行数、重复数和警告数。

### 6.1 中文编码

客户端必须使用 UTF-8 multipart。不要在可能使用本地代码页的命令行中直接拼接中文参数。可使用支持 UTF-8 的 HTTP 库，或用 Unicode 码点构造平台、店铺和备注。

导入后回查：`platform=京东`、`shop_name=所选注册表项的 shopName`，且中文备注无乱码。

### 6.2 本地运行时请求体上限

路由的单文件上限是 25 MiB；本地 Vinext 开发服务也必须配置相同的 Server Action 上限：

```ts
// next.config.ts
experimental: { serverActions: { bodySizeLimit: "25mb" } }
```

默认 1 MiB 限制会在路由解析前直接返回纯文本 `413 Payload Too Large`。此时没有批次写入：先确认服务已按新配置重启，再仅重传同一份已完成工作簿一次；不得重新下载或绕过正式 API。

## 7. 导入成功门禁

接口响应至少满足：

```text
HTTP 201；幂等重复时 HTTP 200
ok = true
status = imported 或 duplicate
batch.source = jd_sku_daily
batch.dataset = spu_daily
batch.status = completed
batch.warningCount = 0
batch.dateMin / dateMax = 目标起止日期
```

首次导入时，`insertedCount` 应等于实际日明细行数；同文件重复上传必须返回 `duplicate`，不得再次写入。

## 8. 数据库只读回查

用批次 ID 参数化查询：

```sql
SELECT source, dataset, platform, shop_name, status,
       row_count, inserted_count, duplicate_count, warning_count,
       date_min, date_max, note, completed_at
FROM netshop_import_batches
WHERE id = ?;
```

```sql
SELECT business_date, COUNT(*) AS row_count,
       COUNT(DISTINCT spu_id) AS distinct_spus
FROM netshop_rows
WHERE last_import_batch_id = ?
GROUP BY business_date
ORDER BY business_date;
```

```sql
SELECT COUNT(*) AS aggregate_rows
FROM netshop_rows
WHERE last_import_batch_id = ?
  AND spu_id = ?;
```

最后一个参数传 `合计`，结果必须为 0。逐日行数总和必须等于批次 `row_count`，日期数必须等于目标自然日数。

## 9. 错误批次修复

如果错误文件或“合计”行已经入库：

1. 先只读查询并记录批次 ID、`source`、`dataset`、状态和关联行数。
2. 确认关联行的 `first_import_batch_id` 与 `last_import_batch_id` 都等于该批次，避免删除被后续批次更新的数据。
3. 在单个事务内按精确批次 ID删除关联行，再删除批次。
4. 任何行数或批次属性与预期不一致时回滚并停止。
5. 修复解析器并运行测试。
6. 重新通过正式 API 导入，再完整回查。

禁止按日期、来源或数据集做宽范围删除。

## 10. 测试与交付

至少执行：

```powershell
node --test tests/rendered-html.test.mjs
npm run build
```

最终报告包含：数据来源、截止日期、平台/店铺/数据集筛选、下载区间、文件路径与大小、下载点击次数、工作簿行列数、API 状态码、批次 ID、导入/重复/警告行数、日期覆盖、合计行数，以及测试和构建结果。

只有下载完成时不得声称已导入；API 成功但未回查数据库时不得声称日期完整。
