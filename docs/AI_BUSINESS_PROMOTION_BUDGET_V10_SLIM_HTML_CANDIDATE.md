# renderer 10 离线 HTML 瘦身候选

本切片只给纯多卷 writer 增加显式 `html_slim_v10=True` 参数，默认 `False`。renderer 1–9 明确拒绝该参数；v10 拥有方 `open_volumes`、暂存、ready、路由和生产设置均没有启用它。用同形 1 万行旧样本复验，改动前与新代码默认 v10 HTML 的 SHA-256 完全相同。只有合成工具 `--slim-html-v10` 会使用新格式，不能视为正式交付。

新 HTML 在 `report-data` 中声明 `htmlPayloadVersion: 2`，逐表写入完整规范 NDJSON 的 gzip/base64、原始 NDJSON 字节数、压缩字节 SHA-256 和既有 `rowDigest` 证明；writer 仍单次遍历所有来源，同时生成原 XLSX，不丢行或改数值。每表解压 NDJSON 上限 256 MiB，HTML/XLSX 继续受原每文件 256 MiB、整组 1 GiB 门禁约束，超限整次拒绝，不截断。浏览器先验压缩 SHA，解压后**直接对原 NDJSON 字节核对完整行摘要**，再解析当前表并使用原离线搜索、排序、分页、图表与 CSV 代码；表切换释放旧表的解压行缓存。未加载的大表不会在首开时被解析为行数组。所有单元格继续走 `textContent`，原 CSP 禁外部连接；gzip/base64 不含可执行 HTML。摘要/解压/行宽失败会清空旧表并显示错误。

浏览器兼容门槛是本地 `file://` 下可用 **`DecompressionStream('gzip')` 和 `crypto.subtle` SHA-256**。缺任一能力即拒绝显示压缩表，不请求网络、不静默切换旧格式。已在本机 Chrome `154.0.8037.57` 离线合成文件验证，其他浏览器及 Microsoft Excel 不在本次验收内。

同一组合成 100,000 行数据、9 张原表及 3 张固定预算工作表对比：

| 项目 | 默认 v10 | 显式瘦身候选 |
| --- | ---: | ---: |
| HTML 文件 | 19,452,459 字节 | 5,002,843 字节，减少约 74.3% |
| 首张小表加载后的 Chrome JS 堆 | 39,106,872 字节 | 7,651,244 字节，减少约 80.4% |
| 首次打开 100,000 行大表后的 JS 堆 | 42,234,748 字节 | 43,771,376 字节 |
| 首次打开大表耗时 | 72 毫秒 | 547 毫秒 |
| 单次全表搜索耗时 | 85 毫秒 | 91 毫秒 |

这些时间和堆数据仅为本机单轮合成测量，不是服务水平承诺。首次选择大表时仍要一次解压/解析完整该表，内存优势主要发生在**首开及未访问大表**阶段；对于实际 57.5 万行单表仍需另做浏览器峰值与响应性验收，不能从 10 万行线性推断。

浏览器验收还覆盖第 2 页、排序、图表、末尾唯一 SKU 的全表搜索及 CSV、快速切表、篡改 gzip SHA 后拒绝、**只篡改完整行证明**后拒绝、模拟无解压 API 后拒绝、含 `</script>` 的恶意文本仅作为单元格文字显示；外部请求和脚本错误均为零。独立静态工具把压缩数据的全部 100,013 行逐表解压并与完整 v10 manifest 的行数、列宽、行摘要及 HTML 文件 SHA 核对。证据为 `E:\codex-artifacts\ai-business-v10-html-browser-ndjson-100000-20260925\browser-evidence.json` 和同目录 `static-evidence.json`；默认与候选 HTML 分别在 `E:\codex-artifacts\ai-business-v10-html-baseline-100000-20260925`、`E:\codex-artifacts\ai-business-v10-html-slim-ndjson-100000-20260925`。

可复用命令（每次使用新目录）：

```powershell
python tools/business-budget-v10-static-scale.py E:\codex-artifacts\<new-slim-dir> --rows 100000 --slim-html-v10
python tools/business-v10-slim-html-static-verify.py <slim.html> <complete-manifest.json> <new-static-evidence.json>
node tools/business-v10-slim-html-browser-rehearsal.mjs <default.html> <slim.html> <new-browser-output-dir> <hostile.html>
```

此候选未触发真实店铺来源、五 Agent、数据库发布或 Office 原生重算。启用真实 v10 文件仍需版本化 HTML 格式验收、目标浏览器兼容覆盖、57.5 万行浏览器容量及现有审批/预算/来源/下载门禁复核；不得把合成静态成功标为业务终验。

本独立切片相关纯回归 43 项通过（含旧 writer/多卷/预算证明、空表、跨卷、XSS 与超限拒绝），Python 编译、Node 浏览器脚本语法和差异检查通过。真实角色 PostgreSQL 未运行；默认 v10 的历史 1 万行合成 HTML SHA `9e7fd7da32b19f128e97d942a57ea658f9ffcd93b46842e9b5fa9ddb234f5242` 在本改动前后逐字节相同。
