# 市场来源选择候选

2026-09-18。工作台新增按已发布目录选择市场条件，精确保留京东、类目、范围、SKU/SPU及原价格筛选，不从展示名称猜测身份。历史日期仅供参考，不改分析日期，也不证明完整事实或全行业覆盖。

`GET /api/ai/business-plan/market-options` 固定当前账号封套，通过市场 reader 读取。未知和重复查询参数原样交 owning 服务判定；成功页重验完整摘要、身份、目录代次、修订、日期声明及38000 UTF-8字节上限。服务端只验证当前页，前端使用上一完整页末项与目录绑定核验连续分页；不信任客户端提供的前页信息作为权限证明。

组件每次添加前重新读取当前页并固定目录版本，切换账号、筛选、禁用、卸载及取消会隔离迟到响应。每页20项，最多浏览100页后要求收窄条件；不自动裁剪或后台读完全部目录。父工作台按五字段精确去重，最多7项，保留店铺、渠道、日期和问题；改变范围会清除旧预览。提交结果未知时冻结原请求，恢复仍使用同一UUID与原始body。

已执行合成验证：

- 市场纯合同/封套/选择和既有scope相关48项Node通过：`.runtime/business-market-envelope-selection-node.log`。
- 独立组件16项Chrome通过，含分页、100页上限、撤权、未初始化及迟到隔离：`.runtime/business-market-picker-ui/evidence.json`。
- 实际父工作台7项Chrome通过，含精确范围保留、7项限额、账号切换和未知提交恢复：`.runtime/business-market-workbench-ui/evidence.json`。
- 旧网店选择父工作台9项Chrome通过：`.runtime/business-market-old-scope-regression/evidence.json`。
- 根任务检查390px父页面截图，无横向溢出。完整类型检查仍有223行既有错误，与第52批基线逐字相同，无新增错误；不能称全库类型检查通过。

仅独立候选工作区，未部署、未初始化正式市场目录、未触发模型或采集。正式目录规模与业务日期覆盖须另行验收。首次纯路由使用不存在的PublicApiError码`principal_mismatch`已在提交前改为明确403响应，保持错误码供组件识别。
