# v4 跟单 SKU 关系纯候选

`backend/business_analysis/promotion_attributed_sku_relation_v4.py` 新增未注册的单窗口纯读取器，旧 v2 关系与推广 SKU 视图的字节和限额未改。输入是 v4 京东推广完整重放页与同一来源的成功审计/游标链候选证明；纯层再次核固定店铺、窗口、修订、规范行、页摘要、连续游标、日期覆盖、页数/字节、来源控制金额及缺失数，SQLite 分组后再逐指标回卷。证明由调用方传入，本层不验京东上游签名、数据库封存、当前账号或 Agent 读取，因此所有权威标志保持 false。

精确关系键为平台、店铺、计划 ID、单元 ID、匹配类型、关键词、搜索词、`attributedSkuId`；短视图另按关键词和跟单 SKU 分组。缺字段的桶保留费用、行数并标为不可唯一行动。单个来源只处理一个 current/previous/yearAgo 窗口，跨窗口比较须另行绑定三份来源与日期后计算；不能将两视图或推广 SKU 视图金额相加，也不能把跟单 SKU 当作商品主数据或 ERP/B 端增量。

容量门禁：沿用 v4 单来源最多 16,384 页、每页 100 行/128 KiB、2 GiB；本视图最多 250,000 组，主 SQLite 页文件最多 256 MiB，完整行输出预检最多 512 MiB，单窗口合作式时间上限 600 秒，调用方只能收紧。临时表按主键顺序读取，查询计划若需要临时排序 B 树即拒绝；SQLite journal 关闭、临时表放内存，来源页及分组始终按页/小批处理。失败、超额、取消和宽页均不返回部分结果。**同步页提供者若阻塞在 `next()`，进程内截止时间不能强制中断**；生产拥有方仍须提供独立有界的读页/进程控制。SQLite 主文件限额不等于整个宿主磁盘或进程内存的通用硬限，须在目标环境测量。

2026-09-25 独立工作树合成测试，100 行/页、同一合成日期、每行花费 100 分；每期先独立计算完整 `PageReconciler`，再将同样规范页流送入新候选。无客户数据、PG、生产或付费模型。结果：

| 窗口 | 行 / 页 | 六键关系组 | 费用回卷 | 主 SQLite | 输出预检 | 关系阶段 / 含独立核对总耗时 | 工作集峰值 |
|---|---:|---:|---:|---:|---:|---:|---:|
| current | 281,759 / 2,818 | 149,836 | 28,175,900 分 | 124,575,744 B | 159,745,989 B | 108.880 / 122.245 秒 | 37,552,128 B |
| previous | 293,336 / 2,934 | 149,836 | 29,333,600 分 | 125,571,072 B | 159,757,566 B | 113.976 / 128.583 秒 | 37,871,616 B |

两次各自完整处理，行数合计 575,095，不是同一临时库同时载入两期。私有临时目录 250 毫秒采样峰值等于上述主文件大小；这些数值不代表真实词货分布、三期同比、外部页阻塞、正式角色或报告输出表现。证据 JSON 分别为 `E:\codex-artifacts\ai-business-attributed-sku-v4-scale-20260925\current-281759.json` 和 `previous-293336.json`。工具命令为：

```powershell
python tools/business-promotion-attributed-sku-v4-scale.py --rows 281759 --groups 149836 --window current --output E:\codex-artifacts\ai-business-attributed-sku-v4-scale-20260925\current-281759.json
python tools/business-promotion-attributed-sku-v4-scale.py --rows 293336 --groups 149836 --window previous --output E:\codex-artifacts\ai-business-attributed-sku-v4-scale-20260925\previous-293336.json
```

纯回归覆盖三种窗口、精确六键、缺身份、行/费用/归因金额守恒、错证明、缺尾页、改金额、取消、低临时盘和宽页拒绝；原 v4 关系及旧 v2 视图回归一并运行。正式接线还需拥有方把同一持久封存来源、真实请求游标审计和当前账号绑定到本纯结果，并另做三期/真实行宽、页供应商硬超时、目标临时盘和 Agent/renderer 读取证明。本候选不注册这些能力。
