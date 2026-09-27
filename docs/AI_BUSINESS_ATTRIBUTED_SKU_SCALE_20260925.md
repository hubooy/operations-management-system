# 跟单 SKU 关系候选：合成容量与边界

2026-09-25 在独立工作树用 `tools/business-promotion-attributed-sku-scale.py` 测试现有纯 `keyword_searchterm_plan_unit_match_attributed_sku`。输入由实际京东推广规范投影夹具生成后逐页重建，先独立形成 `PageReconciler` 结果，再完整送入候选；没有客户数据、生产连接、模型或报告发布。费用固定每原始行 100 分，完整扫描后的行数与费用必须等于来源核对记录，否则工具失败。

| 合成单期 | 页数 | 唯一关系组 | 逐行/费用回卷 | 主 SQLite 文件 | 关系构建及扫描 | 进程工作集峰值 |
|---|---:|---:|---|---:|---:|---:|
| 149,836 行 | 1,499 | 149,836 | 149,836 行 / 14,983,600 分 | 102,305,792 B | 63.342 秒 | 42,098,688 B |
| 50,000 行 | 500 | 10,000 | 50,000 行 / 5,000,000 分 | 6,844,416 B | 18.901 秒 | 42,188,800 B |

前一项独立核对页耗时 6.607 秒、总约 69.950 秒；后一项分别为 1.854 秒、20.756 秒。峰值工作集是探针进程内每 250 毫秒采样的关系阶段读数，非整个 Django/Agent/HTML/XLSX 内存承诺。私有临时目录同频采样峰值分别为 102,305,792 B 和 6,931,112 B；SQLite 排序可能在系统临时目录产生额外文件，`PRAGMA max_page_count` 只限制主数据库页数，**不能据此声称所有临时盘都有 256 MiB 硬上限**。机器、数据分布和一日合成日期与真实三期不同，不能线性外推耗时。

更重要的准入结果是**参考数据目前不可运行**。关系模块复用 v2 `promotion_views._source/_ingest`：每来源至多 200,000 行、两侧合计至多 2,000 页，每页至多 100 行和 128 KiB，分组并集至多 250,000，主 SQLite 256 MiB。留存参考本期 281,759 行、前期 293,336 行，分别已超过单来源上限；两期共 575,095 行至少需要 5,751 页，亦超过合计页上限。纯测试新增 281,759、293,336、575,095 行声明在读取任何页前拒绝的门禁；不能将成功的 149,836 组合成试验解释成参考数据通过。先完成版本化 v4 封存 Reader/来源页合同与分组容量、全临时盘/耗时验收，再考虑正式 Agent 和文件注册；单纯上调 v2 常数不建立权威。

安全和语义复核：原代码会逐页验证来源/店铺/日期/规范行、页摘要、末页、控制总额和各指标存在性，分组后再次核行数/金额，错误时不暴露部分表；合作式取消在入页与 SQLite 进度中执行。新增宽页负例证明完整页超过 128 KiB 会整体拒绝，不裁剪；现有取消负例证明第二页取消不返回部分结果。纯页的 `request_cursor=verifier.expected_cursor` 只自证页链，并非上游实际请求游标审计。原始多别名的同角色字段冲突仍留在上游投影，不在本关系层消解。`attributedSkuId` 不能推成推广 SKU、商品主数据 SKU 或增量销量；来源级日期覆盖不证明词货逐日完整、归因窗口成熟或跨 ERP/B 端金额可相加。拥有方候选只给选中封存 v2 报告的只读绑定，不等于 Agent 已读或正式 renderer 权威。

可复现命令：

```powershell
python tools/business-promotion-attributed-sku-scale.py --rows 149836 --groups 149836 --output E:\codex-artifacts\ai-business-attributed-sku-scale-20260925\scale-149836.json
python tools/business-promotion-attributed-sku-scale.py --rows 50000 --groups 10000 --output E:\codex-artifacts\ai-business-attributed-sku-scale-20260925\scale-50000.json
```

完整数值证据位于上述两个 JSON。`business_analysis.test_promotion_views`、`business_analysis.test_promotion_keyword_sku`、`business_analysis.test_promotion_attributed_sku_relation` 合计 41 项纯测试通过；未运行 PostgreSQL 或生产。旧推广 SKU 视图源码字节未改。
