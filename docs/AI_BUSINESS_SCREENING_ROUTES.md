# 固定筛查签名只读入口

第四十三批注册三个独立工具：角色阅读包、原生/映射分析表、固定预算。仅 `business_agent_screening_v1` 入口、无范围限制管理员可用；原47个工具定义和原96种入口/角色/范围目录摘要保持不变。

桥接通过签名 GET 访问 `/api/ai/reports/{reportId}/screening/package|analysis|budget`，后端只开放 reader，不允许 writer 或 POST。参数精确绑定报告、证据运行及固定筛查ID；尚未发布、旧报告协议、错误权限、重复或未知参数均明确拒绝，不回退旧接口。角色包保持原协议，其他响应与预检包裹协议一致；完整 UTF-8 响应不能超过38000字节，单工具最多8次。

53项Node回归、9文件语义及lint检查、3组真实PostgreSQL签名路由测试通过，生产构建通过，491模块生产边界检查零违规。成功路由首次静态复审发现 dict 未包装为 HTTP response，已在数据库测试前修正。见 [候选证据](evidence/ai-business-screening-routes-candidate.json)。

这里只注册读取能力，报告创建及调度尚未开放新协议；不代表真实模型已调用、各Agent已读、诊断或文件已完成。
