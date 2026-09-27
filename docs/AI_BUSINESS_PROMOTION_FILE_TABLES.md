# 词货材料转工程表：流式纯候选

2026-09-24。`promotion_report_tables.tables(manifest, pages_by_view)` 提供未来HTML/XLSX可共同消费的两张 `report_files.Table`，当前不注册renderer、文件数据库或下载接口，不修改旧1—6版本。

读取来源必须是已完成的 `business_promotion_export.prepare` 材料：调用者传其 manifest 副本和两个 ndjson_pages(view) 迭代器。本模块为纯校验/投影，普通DTO及摘要不授予授权；summary固定 `authorityVerified=false`。未来 owning 文件编排须在生成与交付时复验真实报告、账号和封存版本。

## 表头和无损保留

两表标题“关键词与推广SKU”“关键词商品及投放上下文”，均有66个明确中文列：平台、店铺、关键词、明确推广SKU、计划ID、单元ID、匹配方式、身份可定位、缺失身份字段、分析行位置、完整行ID；本期/基期各9项源指标的值/有值行数/缺值行数；最后为完整原始分析行JSON，保留所有比率、比较状态和其他原始字段。

9项指标为推广费用、曝光、点击、平台归因订单、平台归因成交、加购、直接归因成交、间接归因成交、新客归因成交。金额仍是整数分，null不变成零，负值不取绝对值。汇总视图的计划/单元/匹配方式为空表示已跨这些上下文汇总，并非补造一个未知计划。缺SKU桶和其费用始终保留。

所有列 `total=false`，两表是同源事实的不同分组、不能再相加；平台归因成交不是ERP销售、利润或增量因果。对源已有文字保留原值，XML敏感字符交现成 writer 正确转义，形似Excel公式的文本不作为公式执行。

直接用通用 `TableSpool.flatten` 会让深层比较字段动态扩列；此片复用底层 `Column/Table` 接口，显式定义66列，避免依赖动态英文路径当表头。并未修改原 TableSpool。

## 先完整核验，再提供流

输入规范manifest摘要、算法/报告/source/baseline/tableBindingDigest互相绑定；每表NDJSON实际SHA256、字节数、行数、页数、行位置/身份、缺身份统计和费用汇总重新核对。两表费用和缺失事实行数守恒检查不等于授权或业务来源真实性证明。

每块最多38,000字节，原始材料含manifest最多64MiB；两表合计最多25万行，投影数据另限64MiB。输入只读一遍，完整校验并把投影写入私有临时SQLite后才yield结果，不在最后摘要校验前发出部分表。表的rows是按顺序读取SQLite的一次性流，只能在context内消费；退出清理临时数据库。

完整JSON单元格不得超过Excel的32767 UTF-16单元限制，非法XML控制字符、unsafe整数、未知指标结构或超容量全部拒绝，不截文字/原始JSON。128MiB SQLite页空间限制用于临时磁盘，不是峰值进程内存承诺。未来需要更宽行时应单独设计分表协议，不在旧renderer里隐式裁剪。

## 验证

6项纯测试通过（0.830秒）：105行/多块、空值/负值/缺SKU、真实基期值及比较JSON、manifest/页hash/行数/表绑定篡改、预算/Excel宽度/XML控制/整数上限；并实际调用现有 `write_pair` 在内存生成HTML/XLSX，回读worksheet XML验证中文可读表头、敏感字符转义且无公式节点。它是合成格式验证，不是原生Office或生产业务验收。
