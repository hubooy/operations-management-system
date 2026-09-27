# 第二批证据与复现

- `performance.json`：最终无新增索引版本的签名 Django API 样本。每页默认主数据 30 条、榜单 20 条；单用户和四用户各 20 次，另有首次、筛选/空结果/翻页成对样本及独立内存采样。每次重新建立只读连接，建立连接在请求计时之外；API 计时覆盖签名验证、市场版本核验、查询及 JSON 响应。
- `old-new-comparison.json`：同一合成库、同一原有索引下的旧/新业务实现，直接调用并逐字段比较整份响应。旧实现取自第一批提交 `b9374841`，不是历史生产日志。这里的业务函数时间与包含鉴权/版本核验的 API 时间分开记录。
- `schema-and-permissions.json`：最终仅原有四条市场迁移，无候选索引；32 万条榜单完整行摘要不变；非只读事务内的 reader 写入探针也以 PostgreSQL `42501` 被拒绝。
- `summary.json`：源码 SHA256、版本、检查结果、原始日志摘要和清理状态。未将任何连接密码、环境文件、真实客户数据或正式数据复制到证据目录。

## 复现条件

复制本目录的三个 Python 脚本到独立 worktree 的忽略目录，在新建的、空的 PostgreSQL 17 数据库中运行。脚本固定核验端口 `55453`，数据库名使用 `health_review`；不得使用正式连接或 `.dev.vars`。Django 环境采用 `teruisi_backend.settings`、`development` 进程角色及测试环境，私有连接和内部测试签名密钥由运行方临时提供。连接配置不属于交付文件。

先执行当前源码的 `backend/manage.py migrate --noinput`，再运行 `synthetic-fixture.py`。造数脚本整理自本次分步执行的造数步骤，包含 6 个月、7 类目、13 品牌和多价格段来源；图片 URL 全部是 `synthetic.invalid`，不执行外部请求。之后运行 `benchmark.py 20`。测试角色只有 SELECT 权限，接口通过 Django Client 执行；销售 consumer 在进程内访问真实 PostgreSQL，未包含跨服务 HTTP、Worker 或浏览器耗时。

旧版对照需要把 `b9374841` 中以下三份源码只读导出到脚本同目录：

| 仓库文件 | 对照文件名 |
|---|---|
| `backend/market/admin.py` | `old_admin.py` |
| `backend/market/query.py` | `old_query.py` |
| `backend/market/ranking_query.py` | `old_ranking_query.py` |

然后执行 `compare.py`。主数据旧实现限定同样 1,000 个身份，以有界方式对照全字段、SQL 数和分配峰值；新 API 的 5 万身份场景单独测量。首次进程读取不等于操作系统/数据库冷缓存。启用 tracemalloc 的时间不能加入普通延迟样本。

本次隔离 PostgreSQL 已停止，临时凭据已删除。原始测试和试验日志保留在未合并 worktree 的 `.runtime/batch2/`；正式交付结论仅使用这里的最终样本，覆盖索引及其他未采用试验不属于交付代码。
