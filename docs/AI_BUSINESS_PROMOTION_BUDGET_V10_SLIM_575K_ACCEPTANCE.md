# renderer 10 瘦身 HTML：57.5 万行合成容量验收

2026-09-25，基于整合 `ebfe11eb` 在独立工作树运行。全部数据来自固定测试夹具和确定性行生成器；没有访问客户工作簿、当前店铺、数据库、Agent、Office 或生产服务写接口。瘦身只由本次脚本 `--slim-html-v10` 显式启用，现有 v10 及 renderer 1–9 默认输出、下载和发布行为不变。

执行前只读核验：正式系统 `Running`、后端 `Ready`、Worker `exact_release`、12 个组件在线；CPU 三次采样约 12%–14%，物理空闲内存 4.12 GiB，E 盘可用 379.03 GiB，原有 Chrome 进程 18 个、Excel 0 个。合成 Python 进程精确设为 `BelowNormal`，生成过程中没有并发启动私有浏览器。静态生成后正式系统仍是同一三项就绪状态；浏览器结束后再查仍为 `Running/Ready/exact_release`、12 组件，Chrome 回到 18 个、Excel 仍为 0，合成进程已经退出。CPU 偶有一次高采样，后续采样回落；没有持续资源告警。

| 静态文件与容量 | 合成实测 |
| --- | ---: |
| 推广大表数据行 / 全报告数据行 | 575,095 / 575,108 |
| 卷数 / 原表 | 1 卷 / 9 表，另有 3 张原生预算计算页 |
| 瘦身 HTML | 28,657,240 字节，27.33 MiB |
| 同形默认 v10 HTML 历史基准 | 112,701,360 字节，瘦身减少约 74.6% |
| 大表规范 NDJSON 原文 / gzip 流 | 112,653,676 / 21,455,206 字节 |
| XLSX 压缩文件 | 42,700,870 字节，42.70 MB |
| 大表工作表 XML | 453,726,577 字节，432.71 MiB |
| 大表 XML 行元素 | 575,098，含 3 行表头 |
| 固定预算公式 | 268 个单元格的地址与公式文本逐一匹配 |
| 渲染 / 含 ZIP、XML 与清单复验 | 373.030 / 472.308 秒 |
| 生成进程峰值工作集 / 成对输出峰值 | 42,745,856 / 71,358,110 字节 |

原 writer 的完整 manifest 重建、每个文件大小/SHA、每个表片与全表行 SHA、ZIP 成员 CRC、OPC JSON ContentType、流式 XML、预算公式文本均通过；没有截断。另以逐行流式静态验收器重新检查独立落盘 HTML 的 575,108 行、列宽、规范 NDJSON、gzip SHA、完整行摘要及 manifest 文件 SHA；解压输出单块最多 2 MiB，不构造全表行数组。最终版本耗时 27.497 秒、Python `tracemalloc` 峰值 114,575,786 字节。证据：`E:\codex-artifacts\ai-business-v10-slim-575095-20260925\scale-evidence.json`、`slim-html-static-final.json` 和同目录完整清单。

浏览器仅使用新开的私有无头 Chrome `154.0.8037.57` 读取上述本地 `file://` HTML，阻断所有 HTTP(S) 请求。资源门禁：启动需空闲内存至少 2.5 GiB，尝试大表需至少 3 GiB，过程中低于 2 GiB 立即关闭；V8 old-space 512 MiB、测量 JS 堆上限 512 MiB；小表 20 秒、大表 45 秒。结果：

| 离线浏览器阶段 | 实测 |
| --- | ---: |
| 首张小表加载 | 310 毫秒；CDP JS 堆 30,480,128 字节 |
| 大表完整载入 | 2,705 毫秒；CDP JS 堆 235,751,856 字节 |
| 搜索末尾第 575,095 行的唯一合成 SKU | 400 毫秒，精确命中 |
| 外部请求 / 页面脚本错误 | 0 / 0 |
| 大表完成后系统空闲内存 / 私有浏览器退出后 | 3,728,957,440 / 4,311,257,088 字节 |

浏览器结果为 `passed`，未触发任一资源或超时门禁；证据：`E:\codex-artifacts\ai-business-v10-slim-575095-browser-20260925\browser-evidence.json`。512 MiB 是 V8 old-space 与事后 CDP JS 堆门禁，**不是整个 Chrome 进程树的硬内存上限**；另以系统空闲内存轮询设置 2 GiB 停止地板。该测量只说明本机当时的合成文件能在上述限制下打开与搜索，不保证真实多卷、其他浏览器、较窄内存环境或相同时间表现。大表一经选中仍完整解压，JS 堆约 236 MB；可编辑预算的 Microsoft Excel 原生打开/公式重算仍因许可门槛另行验收。真实同店三窗口及市场、B 端、销售源的授权/覆盖与容量也没有在此执行。任何生产开关、发布及业务结论仍保持关闭。

可复用命令须使用**新的 E 盘合成目录**：

```powershell
python tools/business-budget-v10-static-scale.py E:\codex-artifacts\<new-static-dir> --rows 575095 --slim-html-v10
python tools/business-v10-slim-html-static-verify.py <volume-001.html> <complete-manifest.json> <new-static-proof.json>
node tools/business-v10-slim-large-browser-rehearsal.mjs <volume-001.html> E:\codex-artifacts\<new-browser-dir>
```
