# ERP 精确来源选择候选

2026-09-18，经营分析工作台新增 ERP 来源选择。用户可筛选精确平台、店铺和渠道，选择前重新读取当前页并核对账号、目录版本和内容摘要，再合并到原分析范围。保留问题、日期、市场条件、已选网店数据集和原有渠道；添加来源会使旧预览失效。

新 ERP 店铺只添加明确销售渠道，不推测推广、SKU、SPU 或 B 端网店数据集。来源名称超过当前计划 100 个 Unicode 码点时明确拒绝，不截断或改名。目录每页 20 项，浏览到 100 页仍有数据时要求缩小精确筛选，不宣称已展示全部。

只读接口 `/api/ai/business-plan/sales-options` 包装当前账号标识，继续调用固定销售 reader GET。后端按实际用户五列和销售/ERP修订号核验；前端验证整个页面的 UTF-8 容量、字段、规范摘要、C 排序及跨页边界。组件取消、权限变化、父级禁用和迟到响应不会添加旧选择。结果未知的创建请求仍使用原持久提交内容，不因选择器重建而改写。

最早/最晚业务日期仅用于描述当前事实日期包络，不代表区间连续覆盖，也不修改用户所选分析日期。目录未初始化或销售事实变更后失效，返回明确错误；显式重建使用 [受控重建入口](AI_BUSINESS_ERP_OPTIONS_REBUILD.md)，GET 不自动扫描销售事实。该命令尚未在生产执行。

## 验证

- ERP、销售gateway及市场合同相关 Node 62 项通过：`.runtime/erp-options-related-node.log`。
- 独立选择器 Chrome 16 项通过：`.runtime/business-sales-picker-ui/evidence.json`。
- 实际父工作台 Chrome 8 项通过：`.runtime/business-sales-workbench-ui-final/evidence.json`。包括手填范围保持、选择前复核、预览失效、重复选择、账号切换、未知提交保持同一完整请求和 390px 布局；页面异常与外联均为零。
- 首次父界面演练夹具未填市场必填平台/榜单维度，被正常表单验证阻止；仅补齐合成夹具后通过，保留 `.runtime/business-sales-workbench-ui/failure.json`。
- 生产构建通过：`.runtime/erp-finance-integration-build.log`。类型检查仍有原基线 223 行错误，与 `.runtime/batch52-types.log` 逐字节相同，不宣称全库类型检查通过。相关 ESLint 通过。

浏览器使用真实组件与合成 loopback API，不代表生产数据、付费模型诊断或正式发布验收。后端实际角色、升级和恢复另见 [ERP候选证据](evidence/erp-source-options-candidate-20260918.json)。
