---
name: jdsz-product-detail-sku-daily
description: Download and verify JD Business Intelligence product-detail SKU daily data from jdsz.jd.com. Use when asked to fetch an operations-system missing date range, select SKU and a custom date interval, create or reuse a JD download-center task, prevent duplicate downloads, save the workbook to D:\谷歌浏览器, diagnose slow or stuck JD SKU downloads, or verify the downloaded XLSX date coverage.
---

# 京东商智商品明细 SKU 日数据

## 使用入口

优先运行仓库内的确定性程序，不要重新手写浏览器步骤：

```powershell
npm run jdsz:product-detail-export -- --start-date YYYY-MM-DD --end-date YYYY-MM-DD
```

脚本位置：`tools/jdsz-product-detail-export.ts`。

需要理解事故背景、耗时或人工排障时，阅读 [`docs/京东商智商品明细SKU日数据下载.md`](../../../docs/京东商智商品明细SKU日数据下载.md)。

## 执行流程

1. 先按 `docs/OPERATIONS_DATA_QUERY.md` 确定运营管理系统本月缺失的准确日区间。
2. 显式传入 `--start-date` 和 `--end-date`；只有确认需要整月时才使用默认范围。
3. 让程序先复用最近一小时的本地同区间完整文件。
4. 本地没有文件时，只接管本程序为该 SKU 区间保存了 manifest 指纹的任务；任务标题不含 SKU/SPU，禁止凭最近同名行猜维度。
5. 没有 manifest 时，先刷新下载中心并确认同名任务基线连续两次快照一致后再创建；首帧空数组不能作为已加载，真正无历史任务时第二次空快照即可继续。把唯一新增行的任务 ID 或“标题 + 创建时间”指纹持久化。
6. 下载完成后验证文件非空、无 `.crdownload`、日期覆盖完整。

## 创建任务前硬门禁

按以下顺序检查，任一项失败立即停止并保存截图：

1. SKU 标签唯一且 `aria-selected=true`。
2. SKU 切换后的数据加载已经结束。
3. 点击起始日后，单元格出现 `jmt-date-picker-calendar-cell-start`。
4. 点击结束日后，单元格出现 `jmt-date-picker-calendar-cell-end`。
5. 页面“当前”日期回显与目标起止日期完全匹配。
6. 当前弹窗唯一，且包含“下载类型”“分天下载”“不包含对比时间”。
7. 两个单选框的真实 `checked` 状态均正确。

商品明细页的公告可能在主页面可交互后延迟数秒才挂载，关闭控件也可能先显示、后完成事件绑定。只能处理唯一含“公告图片”的弹层，并等待同一公告的唯一 `Close`/`.close-modal` 控件连续稳定后点击；首击无效时，只允许重新核验同一图片身份和同一唯一安全关闭控件后再试一次。公告变化、多个弹层或多个关闭控件立即停止，不能连续关闭未知内容。

不要把 click 调用成功当成页面状态成功。京东页面存在 SKU 重绘、日历打开动画和日期点击被吞的竞态。

单日区间也必须执行两次端点点击：第一次建立 `start`，第二次点击同一天闭合 `end`。日历仍打开且页面只显示“当前：YYYY-MM-DD HH:mm:ss”时，区间尚未生效；不得点击页面的品类/渠道“查询”作为兜底。若“下载数据”打开的是仅含“下载设置/最多 1000 行”的弹窗，它是实时汇总下载，必须在写 manifest 和点击“确定”前停止。

上海日期刚跨日时，京东可能尚未开放“昨天”。目标结束日在日历中为 disabled 时必须报告 source-not-ready 并停止，不得降级到前一天或重复点击。每日 10:00 调度会继续使用严格的昨天范围；待京东开放后从失败审计续跑。

## 下载中心硬门禁

只处理文件名匹配店铺、下载类型和目标日期区间的唯一一行；多行同名时停止，不按“最新”猜测。必须同时确认：

- 任务指纹与当前 SKU manifest 完全一致，或任务由本轮明确创建并刚写入 manifest。
- 状态包含 `已生成`，不能只看见“下载”文字。
- 下载按钮唯一、可见且可用。

“生成中”行也可能显示“下载”文字。禁止提前点击，禁止改点较旧同名任务。

如果 manifest 仍为 `submitting`，只可接管创建时间紧邻 `manifest.createdAt` 且标题完全一致的唯一行，并先补写指纹；不能唯一对应时保留 manifest 并停止，禁止重新创建或按“最新”猜测。

## 文件落盘与重试

以 `D:\谷歌浏览器` 的文件系统状态作为最终事实来源：

- 匹配 `.xlsx` 出现且大小连续稳定时成功。
- 匹配 `.crdownload` 存在时继续等待，不得再次点击。
- 初始等待最多 120 秒；有临时文件时额外等待最多 300 秒。
- 只有完整文件和临时文件都不存在时，最多重试一次下载点击。
- 不得因浏览器事件、控制桥或页面响应超时直接重复点击。
- 复用或下载完成后必须读取工作簿表头，第二列严格为 `SKU`；不得只依赖文件名。

## 浏览器选择

当前程序使用持久化专用 Chrome 配置，以复用京东登录状态和固定 D 盘下载目录。不要改用内置浏览器执行日常任务，除非用户明确要求并接受独立登录状态与下载路径差异。

日常任务默认使用 `headless=new`，并在原标签页进入固定下载中心。登录失效、验证码、安全验证或店铺登录身份异常时才关闭无头实例并打开当前店铺可见 Chrome；任务仍失败关闭、保留 manifest，禁止自动重放确认或下载。首次登录或主动排障显式增加 `--interactive-login`。运行前已有同端口人工 Chrome 时会复用现有窗口，因此定时任务前应关闭人工登录窗口。

## 成功报告

报告以下信息：

- 数据起止日期。
- 文件绝对路径和大小。
- 是否复用了本地文件或下载中心任务。
- 实际下载点击次数。
- 工作表数量、行列数和日期覆盖。
- 总耗时，并区分页面操作、京东后台生成和文件下载时间。

没有验证工作簿时，不要声称日期完整；没有实际导入时，不要声称数据已经写入运营管理系统。

脚本默认将 SKU 日数据通过正式 `/api/netshop/import` 自动入库，提交 `source=jd_sku_daily`、`expectedDataset=sku_daily` 和同一目标日期区间；可用 `--no-auto-import` 只下载，用 `--base-url` 覆盖系统地址。仅在 HTTP、`imported|duplicate`、批次 dataset/status/零 warning/date 范围全部通过时才报告导入。工作簿超过本地默认 1 MiB 请求体限制而得到纯文本 413 时，先确认 `next.config.ts` 的 `experimental.serverActions.bodySizeLimit="25mb"` 已生效并重启服务，再仅重传该已验证文件一次。
多店铺运行时使用 `--store-key` 选择注册表中的店铺，例如 `npm run jdsz:product-detail-export -- --store-key jd-chudian-weizhang`。各店铺使用独立 Chrome profile、端口和下载目录；首次登录必须人工完成，密码不写入脚本。
