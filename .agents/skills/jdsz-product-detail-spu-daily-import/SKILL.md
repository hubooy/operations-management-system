---
name: jdsz-product-detail-spu-daily-import
description: Download, validate, and import JD Business Intelligence product-detail SPU daily data into the TERUISI operations system. Use when asked to fill the current-month missing SPU date range, select SPU and custom dates on jdsz.jd.com, create or reuse a safe download, save and verify the XLSX in D:\谷歌浏览器, connect or use the /api/netshop/import endpoint, prevent aggregate-row or Chinese-encoding corruption, or verify the spu_daily batch after import.
---

# 京东商智 SPU 日数据下载与导入

## 必读规则

先完整阅读：

- [`docs/OPERATIONS_DATA_QUERY.md`](../../../docs/OPERATIONS_DATA_QUERY.md)
- [`docs/京东商智商品明细SPU日数据下载与API导入.md`](../../../docs/京东商智商品明细SPU日数据下载与API导入.md)

把文档作为详细操作与故障处理参考；以下步骤是强制执行顺序。

## 工作流

1. 调用 `teruisi_operations.get_data_freshness`。连接不可用且用户明确指定本机运营系统时，才以本机数据库作为只读替代源，并在结果中说明。
2. 从 `netshop_rows` 的 `dataset='spu_daily'` 日期覆盖确定本月缺失区间。不要用通用销售截止日代替 SPU 覆盖。
3. 目标结束日默认是上海时区昨天；不要下载今天未完整的数据。
4. 显式执行：

   ```powershell
   npm run jdsz:product-detail-export -- --dimension SPU --start-date YYYY-MM-DD --end-date YYYY-MM-DD
   ```

   日常默认 `headless=new`；首次登录或人工排障增加 `--interactive-login`。只有登录/验证码/安全验证/店铺登录身份异常会打开当前店铺可见 Chrome，任务仍失败关闭且不得自动重放确认或下载。

5. 创建任务前验证：SPU 标签唯一且 `aria-selected=true`、日期起止状态和页面回显正确、下载弹窗唯一、真实单选状态为“分天下载”和“不包含对比时间”。单日区间必须连续点击同一天两次以分别建立 `start/end`；仅显示“当前：时间戳”或日历仍打开都不算成功。不得点击品类/渠道“查询”强行关闭日历；仅含“下载设置/最多 1000 行”的弹窗是实时汇总下载，必须在写 manifest 和确认前停止。
6. 创建任务前刷新下载中心并确认 baseline 连续两次快照一致；首帧空数组不能视作已加载，真正无历史任务时第二次空快照即可继续。在最终确认前落盘 SPU manifest，提交后补入唯一新增行的任务 ID 或“标题 + 创建时间”指纹。若 manifest 仍为 `submitting`，只接管创建时间紧邻 manifest 的唯一同标题行并补写指纹；找不到或出现歧义时停止，禁止重新创建。SKU 使用独立 manifest，二者不能互用。
7. 将最终文件名明确标记为 `SPU`，避免与相同区间的 SKU 文件混淆。
8. 检查工作簿：第二列标题必须是 `SPU`，第二行可以是“合计”，但该行不得写入数据库；日期必须逐日覆盖目标区间。
9. 默认通过正式接口自动导入（用 `--no-auto-import` 可只下载，`--base-url` 可覆盖地址）：

   - endpoint：`POST /api/netshop/import`
   - multipart `source`：`jd_sku_daily`
   - `platform`：`京东`
   - `shopName`：所选注册表项的 `shopName`
   - `expectedDataset`：`spu_daily`
   - `expectedStartDate` / `expectedEndDate`：本次下载的目标区间
   - 预期 `dataset`：`spu_daily`

10. 使用 UTF-8 安全的 multipart 客户端。禁止用可能按本地代码页传参的命令拼接中文字段；必要时使用 Unicode 转义构造字符串。
    本地 Vinext 服务还必须把 `experimental.serverActions.bodySizeLimit` 配置为 `25mb`，与路由上限一致；收到路由前的纯文本 413 时重启服务后仅重传同一已验证文件一次。
11. 接口成功后只读回查批次与明细，验证：`status=completed`、日期完整、逐日行数之和等于批次行数、`spu_id='合计'` 为 0、平台和店铺无乱码、警告数为 0。

## API 未接通时

在现有架构内补齐，不另建平行导入系统：

- 后端复用 `app/api/netshop/import/route.ts` 与 `lib/netshop/import-service.ts`。
- `jd_sku_daily` 只能根据精确的 `时间 / SPU / SPU名称` 表头识别为 `spu_daily`，文件名不能决定维度。
- 前端导入中心分别提供 SKU/SPU 日数据入口，表单仍提交 `source=jd_sku_daily`，并提交期望数据集和目标日期区间。
- 历史列表按 `dataset='spu_daily'` 显示为“京东店铺 · 商品 SPU 日数据”。
- 为 UI 接线、数据集识别和“合计”行排除增加回归测试。

## 写入门禁

任一条件不满足时停止导入：

- 工作簿表头不是 `时间 / SPU / SPU名称`。
- 日期范围或日期集合不完整。
- API 返回的 `dataset` 不是 `spu_daily`。
- API 返回 `warningCount > 0`、`status=rejected` 或无法读取批次。
- 中文平台、店铺或备注出现乱码。

如果错误批次已经写入，只能在只读查询确认批次 ID、来源、数据集和关联行数后，精确删除该批次关联行与批次记录，再修复解析器并重新走 API。不得按日期或来源做宽范围删除。

## 成功报告

报告：

- 本机替代源或 MCP、数据截止和筛选条件。
- SPU 起止日期、文件绝对路径与大小。
- 下载任务是否复用、实际下载点击次数。
- 工作表数、行列数、日期覆盖。
- API 状态码、批次 ID、导入/重复/警告行数。
- 数据库回查的日期数、行数、合计行数和中文字段状态。
- 测试与构建结果。

不要在仅下载后声称已导入；不要在未回查数据库时声称导入完整。
多店铺运行时使用 `--store-key` 选择 `config/jd-store-accounts.json` 中的店铺；全部顺序执行可用 `npm run jd:all-stores -- --mode all`，仅 SPU 用 `--mode spu-daily`。执行器不会保存或代填密码。
