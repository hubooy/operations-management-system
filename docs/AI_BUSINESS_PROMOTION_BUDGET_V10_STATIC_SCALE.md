# renderer 10 合成文件静态规模验收

`tools/business-budget-v10-static-scale.py` 使用现有 `volume_plan`、`volume_files.render`、固定预算纯计算和 `volume_delivery`，从测试夹具与一次性生成器创建**非业务** HTML/XLSX 多卷候选。它不访问数据库、平台、Agent、生产文件或 Microsoft Excel。目录必须事先不存在；只有全部校验通过才写 `complete-manifest.json` 和 `scale-evidence.json`。失败保留 `failure.json` 与未发布的部分文件，不能当完整交付。

2026-09-25 在独立工作树对 `E:\codex-artifacts\ai-business-v10-static-scale-575095-20260925` 运行 `--rows 575095`，使用默认每片 1,000,000 行及 256 MiB 单文件门禁，结果如下：

| 项目 | 实测 |
| --- | ---: |
| 合成推广数据行 | 575,095；全报告表行 575,108 |
| 卷数 | 1 |
| XLSX 压缩文件 | 42,700,860 字节，42.70 MB / 40.72 MiB |
| 主工作表 XML | 453,726,577 字节，453.73 MB / 432.71 MiB |
| 主工作表 XML 行元素 | 575,098，含 3 行表头 |
| HTML 文件 | 112,701,360 字节，107.48 MiB |
| 原生预算页静态公式元素 | 268；没有执行重算 |
| 渲染耗时 / 含全量复验耗时 | 446.744 秒 / 507.697 秒 |
| Python tracemalloc 峰值 / Windows 进程峰值工作集 | 7,740,563 / 41,783,296 字节 |
| 临时成对输出峰值 | 155,402,220 字节，148.20 MiB |

完整清单的 `manifestDigest`、行 SHA、全部 HTML/XLSX 文件 SHA/字节、嵌入工作簿的表摘要均通过重建；所有 ZIP 成员完整读出并检查 CRC，XML 由流式 Expat 解析，JSON OPC ContentType 与 `fullCalcOnLoad` 声明存在。补充复验把三个原生预算页的 24/41/203 条**公式文本及单元格地址**逐条与固定计算器生成结果对比，摘要均一致；这仍不是计算结果。XLSX 的 SHA-256 为 `aa266a0ecb078c58ff39cdcc915cf14f370cb1a32e09d48b99c2172841d3209d`；HTML 为 `5f37d54e2a15b6e99066f73334b28a46dff103f2f72d8ad9a986206589296591`。完整 JSON 清单落盘 SHA 已另读回并与 compact 根一致。证据路径：`E:\codex-artifacts\ai-business-v10-static-scale-575095-20260925\scale-evidence.json` 及同目录 `formula-text-postcheck.json`。

当前硬上限为每片 1,000,000 行、单文件 268,435,456 字节（256 MiB）、整组文件加清单 1 GiB；规划按行数，不会按实际字节动态拆分。本合成样本没有触发门禁，不需要调高上限或增加自动截断。已有小规模负例用 1 KiB 文件上限确认失败即拒绝且不消费完整来源。若真实来源的 HTML 或压缩 XLSX 超过单文件上限，后续应先提出基于可信行宽与实际字节的安全分卷方案，并在全文件证明重建后再试；不能复用此次样本的成功结论或绕过门禁。

合成表只有 9 列，内容刻意调节为参考压缩量和 XML 量级；它**不是**实际推广字段、五 Agent 决策、来源授权或同店三窗口覆盖。`promotionFileProof` 和固定预算来自纯测试夹具，`owningSourceAuthorityVerified=false`。此运行没有打开 Excel、没有执行公式复算，也没有验证真实 30 天来源、市场/B端/销售关联、Office 性能或生产导出。507 秒只覆盖本地生成与静态校验；不可推断包含数据库取数、模型和发布的完整链路可在 600 秒内完成。

重跑示例（使用新的隔离目录）：

```powershell
python tools/business-budget-v10-static-scale.py E:\codex-artifacts\<new-synthetic-dir> --rows 575095
```

纯门禁回归：`PYTHONPATH=backend python -m unittest business_analysis.test_budget_v10_static_scale_tool`。
