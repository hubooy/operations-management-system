# ERP来源目录显式重建入口

状态：候选CLI实现；没有运行生产重建、修改业务事实或接入前端按钮。入口为[管理命令](../backend/sales/management/commands/rebuild_analysis_options.py)，复用[既有prepare/publish](../backend/sales/analysis_options_projection.py)。

## 使用边界

`sales.0010_analysis_options`只创建空目录并标记`not_ready`，不自动扫描历史销售。首次使用目录前需显式重建；成功销售数据变更后，已发布目录的销售revision落后，GET也会拒绝并要求重建。两种情况都走同一个一次性命令，GET不触发扫描、没有定时循环或失败自动重试。ERP参考数据revision仍绑定分页版本，但不单独使销售身份目录陈旧。

只有已正确配置的`sales_writer`进程且非只读连接允许进入；必须显式传`--actor-email`，从真实AppUser读取当前启用、无scope的admin身份。不会创建账号、将调用者自动提升为admin或使用本地回退身份。读取账号仅使用现有五列权限，不新增grant。

准备阶段在最外层事务外调用原`prepare_rebuild`，保留真实账号、销售/ERP版本、销售authority、切换回执及runtime guard核验。发布调用原`publish_rebuild`，短事务内重新校验并原子替换目录。没有关闭触发器、绕过运行时校验、改变迁移或重写销售事实；只写目录及其状态。

在受控环境已配置正确的writer身份、完成必要迁移，并有当前操作授权后，入口形式如下（不是本次已执行记录）：

```text
python backend/manage.py rebuild_analysis_options --actor-email <实际管理员邮箱>
```

这里不提供生产数据库连接、凭据或通过修改环境冒充writer的步骤。该命令可查询完整受限销售投影，可能占用数据库资源，正式执行应纳入已有维护流程；当前开发授权不等于生产初始化授权。

成功只输出`generation`、`identityCount`、`coverageVerified:false`，不输出邮箱、店铺/渠道明细、销售行或金额。空事实也可以形成零身份的有效目录；这不证明所选日期或经营数据完整。失败不输出成功回执、不自动重试；若数据库连接结果未知，先核验实际目录状态，不能把错误当成必定零写入。

## 验证

[入口测试](../backend/sales/tests/test_analysis_options_command.py)使用mock和SimpleTestCase验证：缺失/无效邮箱、非writer/只读、外层事务拒绝；未知/停用/有scope/非admin拒绝；同一真实身份按prepare→publish顺序调用；两阶段失败不重试；只输出限定字段；数据库错误不泄露原始明细。测试标签为`sales.tests.test_analysis_options_command`。

这组测试不证明真实数据库发布成功。核心发布与reader的实际PG、迁移/独立恢复证据继续使用[ERP目录设计](AI_BUSINESS_ERP_OPTIONS_DESIGN.md)及对应候选证据，由主线程统一执行。前端选择器与受控重建界面仍应分别验收，不能把CLI存在称为工作台入口已接。

主线程入口单元测试8项通过，日志 `.runtime/sales-options-command-unit.log`。真实core准备/发布及最小角色另见ERP目录候选证据；没有运行正式重建。
