# 电扇运营管理系统

2026-09-27 **丽力连接恢复，货品仍待来源出数**：原安全重试 4595 创建完整丽力执行 4610，A 登录和店铺身份通过；B 因 9 月 26 日生意参谋商品日 `SOURCE_NOT_READY` 停止，未进入 C/P/M、未新增导入。原 M 线程异常尚未复现，下一次自然调度为 9 月 27 日 11:10。见 [回查证据](docs/evidence/tmall-lili-retry-4610-20260927.json)。

2026-09-26 **丽力线程诊断版本已采用，货品故障尚未恢复**：新增脱敏线程类别、源码位置、阶段和退出码记录。一次原 n8n 完整运行 4594 在 A 阶段浏览器初始化超时，未进入 M、未新增导入；浏览器与 helper 正常收尾，原安全重试 4595 等待至 9 月 27 日 00:29。不能将该超时认作原 M 线程崩溃根因。见 [诊断与采用记录](docs/TMALL_LILI_THREAD_DIAGNOSIS_20260926.md)。

2026-09-23 **吉客云销售改为最近 45 天滚动同步**：新运行按上海时间截至昨天（含）取 45 天，月初与跨月也保持 45 天；成功的新快照更新窗口内明细，同范围同内容不重复累计，范围外历史保留。执行器与销售后端已同步采用，真实五表测试 4134 及独立批次回查通过，销售覆盖 8 月 9 日至 9 月 22 日共 39,614 行；分仓库存与组合装均已同步。6,803 行零成本销售明细仍未匹配系统成本，按原规则保留并提示。见 [规则与验收记录](docs/JACKYUN_SALES_ROLLING_WINDOW.md)。

2026-09-23 **吉客云修复后的真实五表测试已成功**：按用户追加授权，从原 n8n 手动入口运行一次，4121 于 10:36 完成，耗时约 3 分 22 秒。货品 8,517、库存 23,952、库龄 5,747、销售 20,350、组合装 4,450 行，精确批次和系统事实独立回查通过；库存/库龄为 9 月 23 日快照，销售覆盖 9 月 1–22 日。源数据非阻断提示保留，执行器、浏览器和锁收尾通过。见 [真实业务验收](docs/evidence/jackyun-4121-business-verification-20260923.json)。

2026-09-23 **吉客云登录页面检查与无效重试处理已上线**：页面须稳定且唯一后才进入原登录流程；页面异常和旧运行未闭合停止派生重试，并提供纯计数诊断。同类页面失败可复用原 operator 核验关闭，仍保留原计划和历史。4102 已关闭、旧等待 4117 已取消；真实仅登录验证、12 组件及两轮看门狗通过。五表未手动补跑，业务成功仍待原自然计划验收。见 [修复及正式验收](docs/JACKYUN_LOGIN_PAGE_RECOVERY.md)。

2026-09-23 **吉客云 4098 失败阻塞已解除**：已发布本次登录页面识别错误的精确关闭规则，原失败历史保留；系统 12 组件、启动绑定和备份归档验收通过。后续由原 n8n 自动重试接手，本次没有手动下载或导入，登录页面根因仍需另行处理。见 [恢复记录](docs/JACKYUN_4098_RECOVERY.md)。

2026-09-22 **零成本库存保留规则已上线**：明确填写固定成本为 0 的分仓库存正常保存，库存金额按 0 元计入，并参与原有库存健康及补货计算；缺失或无效成本仍不能当作零成本。此前被过滤的货品需下一次库存同步后出现，自动补货仍受原设置及数据质量门禁控制。见 [发布记录](docs/evidence/zero-cost-inventory-production-20260922.json)。

2026-09-22 **首次打开的并发启动等待修复已上线**：后台已在启动时，桌面入口会等待并核验就绪，相关等待最长 15 分钟；停止、维护和真实故障仍会拒绝。此次受控恢复中只启动一次桌面入口，约 2 分 46 秒后自动打开，12 组件与前后备份通过；整机重启后的验收仍待下一次实际开机。见 [并发启动说明](docs/COLD_START_COORDINATION.md#2026-09-22-正式采用)。

2026-09-21 **启动与发布流程优化已上线**：支持不停服准备 Worker/Django 候选、保留数据库的应用维护，以及 11 个业务域读写服务重叠启动。首次采用使用完整维护；本轮 Django 启动约 87.64 秒，完整版本/组件、备份恢复与归档通过。此数字不代表整机可用时间，原市场查询超时告警仍保留。见 [上线说明](docs/STARTUP_RELEASE_OPTIMIZATION.md#2026-09-21-正式采用)。

2026-09-20 **市场查询与看门狗优化已上线**：筛选与榜单分离、版本缓存、四个窄索引及明确超时提示已采用。正式榜单首次约 3.17 秒、重复约 1.86 秒，筛选首次约 6.51 秒、缓存约 62 毫秒；12 组件、钉钉连接及看门狗两轮独立检查通过，前备份独立恢复与前后 E 盘归档完成。适用范围和发布过程见 [上线说明](docs/MARKET_QUERY_WATCHDOG_RECOVERY.md)。

**系统独立看门狗**：Windows 在当前用户登录后、每分钟检查网页、后端和守护进程。连续两次异常后按原安全启动流程尝试恢复，维护或人工完整停止期间不自动拉起，未知进程不强行接管；恢复需两轮健康检查通过。异常通过“志高助手”发本人单聊，通知身份或投递失败会保留本机记录，正常时不打扰。使用与限制见 [看门狗说明](docs/OPERATIONS_WATCHDOG.md)。

2026-09-19 **字号调整已在本机正式采用**：统一常用字号变量，销售概览指标数字改为 24px，渠道明细与说明采用 12–13px 并提高文字对比度。正式页面资源及 12 组件就绪回查通过；浏览器自动视觉验收暂不可用。维护期间天猫亿用 execution 3320 失败，交既有安全重试 3321；周报 3318/3319 失败后 3322 自然成功，未人工补跑。详见 [发布验证记录](docs/evidence/typography-production-20260919.json)。

2026-09-19 **吉客云 3134 失败阻塞已解除**：已发布本次 DPAPI `binding` 错误的精确零业务效果关闭规则，并保留原计划与失败历史；后续可从原工作流重新创建完整执行。系统已恢复 Running / Ready，未触发业务补跑，也未更改原每小时安全重试和 00:10 调度；登录根因及通用自动恢复仍待处理。每日多次运行已有内容幂等和范围替换基础，源采集时间防倒灌等改进见 [多次运行评估](docs/JACKYUN_REPEAT_RUN_ASSESSMENT.md)，发布证据见 [3134 采用记录](docs/evidence/jackyun-3134-production-20260919.json)。

2026-09-17 **吉客云后台凭据读取修复已上线并补跑完成**：无控制台 PowerShell 改用显式 UTF-8 标准流，保留 DPAPI、绑定及权限门禁。Worker/helper `20260916T194114Z-df8da4ea6ac81cf6` 已采用源码 `2db37150`；原 n8n 工作流 `2667` 于 03:55 成功，货品 8,506、库存 22,792、库龄 5,684、销售 14,461 行，组合装 4,431 行内容未变而幂等复用。库存/库龄快照为 9 月 17 日，销售覆盖 9 月 1–16 日，五个精确批次独立回查通过，源数据告警保留。全栈恢复、发布前备份独立恢复和前后 E 盘归档通过，无迁移、n8n 未重启。详见 [采用与补跑记录](docs/evidence/jackyun-headless-dpapi-production-20260917.json)。

2026-09-16 **系统体检第一、二批修复已合并上线**：客服保存反馈、市场统计及通用数据目录修正一同采用；主数据改为数据库分页，正式 4.82 万身份默认页首次约 1.37 秒、重读约 0.56 秒。榜单正式 32.11 万条首次约 8 秒、重读约 5.45 秒，翻页及未确认价格正常，尚未达到 3 秒目标。发布时补充修正正式库价格关联的执行计划退化；无迁移，12 组件、版本绑定及前后备份通过。维护影响的京东 10:00 任务仍由原安全重试处理，不代表当天业务已完成。详见 [发布记录](docs/SYSTEM_HEALTH_RELEASE.md)。

2026-09-16 **数据工作流已统一调整为早班并启用每 10 分钟监控**：吉客云五表继续每天 00:10；京东四店 10:00、京东市场 10:30、京准通商用设备 10:40、切肉机 10:50；天猫亿玖、丽力、拓丰、炊之王、马思图、亿用依次为 11:00–11:50，每店间隔 10 分钟。11 条正式 n8n 工作流均已启用且草稿版本等于生效版本，显示名称、cron 和发布版本一致；未重启 n8n、Django，未迁移数据库，也未因改计划触发下载或导入。Codex heartbeat `automation-5` 改为每小时 05/15/25/35/45/55 分检查，只核验已超过计划时间 5 分钟的流程，跟踪现有 execution 与共享安全重试到终态，无状态变化时保持安静。52 项相关测试通过。最终端到端验收由下一次自然早班执行完成，监控会持续到各流程当天成功关闭。详见 [生产证据](docs/evidence/data-workflow-morning-schedules-production-20260916.json)。

2026-09-16 **吉客云五表时钟偏差故障已修复并完成正式导入**：平台服务器时间比本机快 901 毫秒，旧实现混用平台下界与本机上界，误报“导出任务绑定条件无效”。源码 `be2c4dff` 统一任务观察及下载交接时钟，并以一次性许可复用原库存任务，未重复提交库存导出。n8n `2437` 成功完成五表：货品 8,504 行、库存 22,787 行、库龄 5,683 行、销售 13,628 行、组合装 4,431 行；库存/库龄快照为 9 月 16 日，销售覆盖 9 月 1–15 日，五个精确批次独立回查通过。Worker/helper 为 `20260916T004429Z-914353db1e3250b3`；Running/Ready/exact_release、12 组件、启动绑定、发布前独立恢复及前后 E 盘归档通过。过期等待重试 `2431/2433` 已取消，未迁移数据库、未重启 n8n。详见 [生产证据](docs/evidence/jackyun-clock-recovery-production-20260916.json)。

2026-09-16 **京东商智新版行业榜单下载与导入已恢复**：按新版市场 → 行业榜单 → 商品榜 → 类目 → SKU/查询 → 单日自定义 → 下载数据执行，严格核验原生 XLSX 和实际下载请求身份。9 月 15 日失败任务经 n8n `2289` 于次日 00:13 完成：七类目各补齐 9 月 14 日 200 行，共 1,400 行，独立覆盖回查零缺口；不代表已补 9 月 15 日数据。最终源码 `9a3ba905`、Worker/helper `20260915T162551Z-78d9ad08f15648ec`，额外处理唯一普通公告，安全验证仍交由人工。79 项相关测试、不可变构建、正式 Running/Ready/exact_release、启动绑定及发布前独立恢复/发布后备份/E 盘归档通过；未迁移数据库、未重启 n8n。详见 [工作流说明](docs/京东市场商品榜单SKU日数据n8n工作流.md) 和 [生产证据](docs/evidence/jd-market-new-ui-production-20260916.json)。

2026-09-15 **广东仓规则与 Worker 存活自愈已合并上线**：库存减少或不变时保留人工健康，只有精确广东仓实物库存较上一当前快照增加时恢复系统判定；型号明细新增“备货数量 / 下单剩余库存”。不可变 Worker supervisor 同时使用独立存活端点识别“外层进程存活但 workerd 不可用”，连续 14 次失败后受控终止所属进程树并恢复，保留 3 GiB 堆适配、就绪降级不重启和 10 分钟最多 5 次的重启风暴门禁。源码 `5e3cdeb7`，Worker/helper `20260915T143857Z-c71f254c9e28b6f2`；正式 42 条广东仓 API 新字段、Running/Ready/exact_release、12 组件、启动绑定及钉钉接收器连接通过，发布前后备份均完成独立恢复并归档 E 盘，n8n 未重启。详见 [广东入仓监控规则](docs/INVENTORY_MANAGEMENT.md)、[Worker 存活自愈](docs/WORKER_LIVENESS_RECOVERY.md) 和 [生产采用证据](docs/evidence/guangdong-worker-liveness-production-20260915.json)。

2026-09-15 **店铺年度目标 Excel 导入及四项进度已上线**：在“销售分析 → 目标设置”选择年份后可导入 `店铺年度目标.xlsx`，销售/利润金额按万元转换，大毛利率/推广费率按百分比保存；正式导入 21 家，6 行空白目标跳过，未列目标保持不变。页面按成功月度财报累计销售、利润、大毛利率和推广费率，当前显示财报截至 2026-08。源码 `71ce54cb`，Worker/helper `20260915T113701Z-5c7c6173998295bb`，Django manifest SHA `788cd95480c09af4fcedff2c246998b92e4d0e06353d51051c55b29bd6cdc969`；前备份独立恢复、发布后备份及 E 盘归档、Running/Ready/exact_release、钉钉 connected 均通过，n8n 未重启。详见 [年度目标说明](docs/WORKFLOW_ANNUAL_TARGETS.md) 和 [生产采用证据](docs/evidence/store-annual-target-import-production-20260915.json)。

2026-09-15 **三项竞态修复已上线**：完整停机/重启连续持锁，多步部署使用持久维护标记；上新销售周报隔离旧响应，市场 Prompt 按类目原子分配版本。Worker/helper `20260915T081612Z-730408eeaa9cbc1c` 与 Django 已一同采用，无新增迁移。正式状态、维护启动拦截、周报快速切周、接口和资源摘要通过，守护健康、钉钉已连接；前备份独立恢复及前后 E 盘归档完成。操作见 [竞态修复说明](docs/RACE_CONDITION_FIXES.md)，证据见 [生产核验](docs/evidence/race-fixes-production-20260915.json)。

2026-09-15 **数据工作流监控已合并为单一 Agent**：Codex heartbeat `automation-5` 在一个任务中统一核验吉客云五表、京东四店、京东市场、京准通两店和天猫六店共 11 条现行正式 n8n 工作流，日常不创建或委派子 Agent。原京东四店、京东市场、京准通、天猫六店四个分散日监控及每小时“三连败 AI 诊断”均已暂停后续调度；n8n 的共享错误工作流、60 分钟安全重试、业务隔离和失败关闭规则保持不变。统一 Agent 正常时只读取紧凑批量状态，异常时才加载对应专项手册；只负责只读监控、诊断、终态核验和既有通知，不在监控任务内修改代码、部署或重启服务。当前每 10 分钟监控安排见 2026-09-16 记录。

2026-09-15 **市场榜单分页与错误恢复已合并上线**：正式 31.8 万条全范围榜单、第二页和“未确认价格”筛选均返回成功，实测约 12–13 秒；每页固定 20 条，失败后仍可调整日期、筛选或切换子页面。补充后端通过 66 项隔离 PostgreSQL 回归，正式浏览器翻页和运行状态通过。行业汇报继续保留原计算范围上限。见 [查询分页说明](docs/MARKET_QUERY_PAGINATION.md) 和 [生产采用记录](docs/evidence/market-query-recovery-production-20260915.json)。

2026-09-15 **标题与完整截图合成一条钉钉图文消息已上线**：源码 `4d0757cd`，Worker/helper `20260915T031124Z-50915712d427032f`。机器人使用一条 Markdown 消息展示上方固定文案、下方完整图片；原新品周报 v3 的标题“新品周销量趋势数据”、每天 09:00 和接收目标保持不变。Node 2167 通过、隔离 PostgreSQL 230 通过、页面 20 项通过，正式编辑页和接收器连接已核验。本次没有触发真实发送，新版钉钉客户端效果待实际投递验收。见 [生产采用记录](docs/evidence/scheduled-single-message-production-20260915.json)。

2026-09-15 **定时截图支持可编辑固定文案**：在 AI 助理 → AI定时任务 → 编辑中填写“附带文案”，留空只发图。原新品周报任务已设置标题“新品周销量趋势数据”，继续每天 09:00 发给原目标。首版分开发送原文和图片，现已由上方单条图文版本替代；历史采用见 [发布核验](docs/evidence/scheduled-caption-production-20260915.json)。

2026-09-15 **上新跟进的钉钉周报支持完整截图**：自动选取上海时间最近完整周，保留所有产品行、产品图、趋势和累计周列；不再限于屏幕可见范围。正式截图已验证，原“新品上新周销量趋势”已改为截图任务，仍每天 09:00 发给原本人目标。本次没有触发钉钉图片投递。见 [采用记录](docs/evidence/weekly-table-capture-production-20260915.json)。
2026-09-15，分类统计卡片“点击筛选、再次点击取消”、编辑产品线学习代码、新品店铺规划与可见备注、店铺销售/利润年度目标已合入 main 并在本机采用。实际完成额自动累计月度财报，纯金额/比率卡片保留展示。正式页面、只读学习、权限和数据摘要验证通过，备份已独立恢复并归档 E 盘；详见 `docs/WORKFLOW_ANNUAL_TARGETS.md`、`docs/SUMMARY_CARD_FILTERS.md` 和 `docs/evidence/workflow-annual-cards-production-20260915.json`。

2026-09-14 **AI 定时任务的指定页面截图与已复核 Excel 报告已在本机生产采用**：按企业应用机器人身份发送，沿用创建人身份、群审批和发送前持久预留。AI `0013` 已迁移，专用浏览器目录及正式 BI 页面截图验证通过，接收器已连接；尚未创建媒体任务或执行真实钉钉图片/文件投递。发布前独立恢复、发布后备份校验及 E 盘归档通过。见 [功能与限制](docs/AI_DINGTALK_SCHEDULES.md#指定页面截图与已复核报告文件) 和 [采用证据](docs/evidence/dingtalk-scheduled-media-production-20260914.json)。

2026-09-14 **报告模板、Skill 管理和 AI 流水线已上线**：AI 助理新增三类模板与方法的版本管理、持久取数分析、人工复核及 HTML/Excel 交付。AI 0012 与最小权限已采用，原 52 张 AI 表摘要不变；2165 项 Node 回归、20 项页面测试及隔离 PostgreSQL 验证通过。保留同期财务修复和 3 GB 内存配置。正式页面已验证，真实付费模型与通知未做生产实跑。详见 [AI 报告库](docs/AI_REPORT_LIBRARY.md) 和 [发布核验](docs/evidence/ai-report-library-production-20260914.json)。

2026-09-14 **本机 workerd 老生代内存上限已调整为 3072 MiB（3 GiB）**：通过精确摘要绑定的 Miniflare 配置适配传入内部运行时，未修改系统级 Node 内存设置。隔离配置实测、2163 项单元测试、20 项页面测试和备份独立恢复通过；网页已受控重启，后端保持运行。此次缓解约 1.4 GB 堆耗尽，不代表已修复内存增长或自动恢复缺口。详见 [内存配置](docs/WORKER_HEAP_LIMIT.md) 与 [采用记录](docs/evidence/workerd-heap-3gb-production-20260914.json)。

2026-09-14 **8 月财务数据已导入，财报兼容性问题已修复上线**：Worker 解析结果不再携带内部列号，继续保留 Django 严格字段校验。仅导入 2026 年 8 月 2294 条记录，0 警告；30 店铺、81 科目，9 项主要金额回查一致，原有 19 个月份记录未变。构建、2160 项 Node 回归与 20 项 PostgreSQL 财务测试通过；发布前备份独立恢复和导入后备份复验通过，备份已归档 E 盘。详见 [财报导入修复](docs/FINANCE_IMPORT_COLUMN_CONTRACT.md)。

2026-09-14 **市场 AI 旧图候选阻塞批量入库已修复并上线**：复核、跨页选择和入库统一校验当前图片及月份；184 条原勾选失效候选单独提示并保留，16,554 条有效勾选可继续入库。镜像 PostgreSQL 61 项、全量 Node 2147 项通过（20 跳过），正式页面与资源字节回查通过。4 条原计划先暂停排空再发布，恢复后自然新增完成 71 条，74 条隔离记录未变；未用生产入库或人工模型调用做验收。详见 [发布核验](docs/evidence/market-stale-candidates-production-20260914.json)。

2026-09-13 **市场 AI 批量入库的后端异常已修复并上线**：覆盖已有标注或同图跨月入库时，旧值审计中的日期现按 ISO 格式保存，不再因日期无法写入 JSON 而返回“市场服务暂时不可用”。镜像入库、重复提交、整批回滚和正式角色权限验证通过。切换期间结果未知的候选已隔离；用户确认后，23:40 已恢复 4 条原计划的其余图片，均已自然推进，全部 74 条隔离记录保持原样。详见 [发布与运行记录](docs/evidence/market-annotation-commit-json-production-20260913.json) 和 [恢复核验](docs/evidence/market-annotation-remaining-resume-20260913.json)。

2026-09-13 **市场标注刷新恢复与 AI 回复黑色字体已上线**：标注进度/复核读取恢复后清除各自错误，超时与切换任务取消旧请求，并保留保存/入库错误；AI 回复的正文、标题、表格和底部文字统一为黑色。本次只切换 Worker/helper，无数据库迁移或 Django/n8n 重启。原始上游 503 原因尚未确认，主数据列表超时仍单独保留。验证与备份恢复见 [发布记录](docs/evidence/market-annotation-refresh-production-20260913.json)。

2026-09-13 **AI 对话与配置设置已在本机上线**：AI 对话独立放在运营事务左侧、充分利用页面高度；管理员可在 AI 助理 → 配置设置维护全局提示词、业务口径及历史版本。前后端、新表迁移和权限已一同采用，默认六类口径已保存为 v1 并经正式接口及页面回读；从下一次提问生效。持久会话工作能力仍列第二阶段，真实付费模型对新配置的完整对话效果未验收。使用说明见 [AI 对话与配置设置](docs/AI_PROMPT_SETTINGS.md)，采用证据见 [发布记录](docs/evidence/ai-chat-settings-production-20260913.json)。

2026-09-13 **钉钉接收器随系统自动启动已上线并启用**：Windows 登录启动或手动启动运营系统，会在数据库、业务服务和网页就绪后自动开启接收器；无需保留 PowerShell 窗口。实际系统启动已自动拉起并连接钉钉，重复启动复用同一接收器。手动停止接收器会同时关闭自动启动，正常停止整套系统保留配置。说明见 [AI 定时任务](docs/AI_DINGTALK_SCHEDULES.md#接收器随系统自动启动)，采用证据见 [发布记录](docs/evidence/dingtalk-receiver-autostart-production-20260913.json)。

2026-09-13已受控清理137份旧Worker依赖，D盘可用空间从26.78 GiB恢复至126.68 GiB；当前和最近两个前驱的依赖、151个发布目录及完整版本链仍保留。完整版本复验及Running / Ready / exact_release检查通过，业务数据库和E盘备份未改动。旧版本归档依赖缺失为正常状态，不应批量补装。详见 [清理证据](docs/evidence/storage-dependency-cleanup-20260913.json)。

用户于2026-09-13指定业务数据库备份副本保存到 `E:\运营管理系统业务数据`；首次新备份已复制校验，每日22:30由“清理无用文件释放D盘空间”任务中的自动化（ID `e`）生成并归档，依赖电脑、桌面应用及数据库正常运行。受保护程序仍先在D盘生成备份，D盘旧备份未删除，业务数据库未上传GitHub。详见 `docs/STORAGE_RETENTION.md`。

本机存储归档使用独立 GitHub 私有仓库，按内容去重保存旧程序依赖；受控工具保留当前版本及最近两个前驱、完整版本链和审计。2026-09-13已核验账号全部4个仓库均为私有、协作者仅所有者，个人资料已设为私有；后续新建仓库默认私有。业务数据的加密云备份与离线恢复密钥尚需完成恢复验收，现有数据库本地保留策略继续生效；源码推送不代表数据库已上传。运行与清理门槛见 [存储保留说明](docs/STORAGE_RETENTION.md)。

2026-09-12 **绿色主题与跨模块沙箱修复已上线**：导航、Tab、公共筛选、视图切换、导入卡片和横幅参考 AI 对话统一配色，缩小自动化横幅并改善商品经营按钮可读性。移除沙箱配置页面，保留 AI 对话计算，修复分页数据集导出与中文单位结果列。Worker/helper 为 `20260912T143827Z-79e971ff2b5bce03`；系统 Running/Ready，正式资源、沙箱合成计算、发布前独立恢复及发布后备份复验通过。跨领域真实容器测试通过，尚未进行付费模型自然语言端到端验收。详见 [说明](docs/UNIFIED_THEME_CROSS_APP_SANDBOX.md) 和 [发布证据](docs/evidence/unified-theme-cross-app-sandbox-production-20260912.json)。

2026-09-12 **顶部导航与冻结 Tab 已合并并在本机上线**。桌面菜单保持单行，AI 助理同排，账号收为“章”按钮；“问当前页面”和统计周期移入顶部，移除工作台品牌、可见搜索框及重复标题行。新增近30天（含上海当天）和去年同期（选中区间回退一年，重复选择不继续回退，闰日夹至二月末）。Worker/helper 为 `20260912T095027Z-43c06ff99569edfa`；全量测试 2121 通过、0 失败、20 跳过，正式页面资源回读、发布前独立恢复及发布后备份复验通过。本次仅切换 Worker/helper，详见 [采用证据](docs/evidence/top-navigation-production-20260912.json)。

2026-09-12 **AI 助理 → AI定时任务**已在本机上线，作为“AI 管理”右侧的独立子模块。管理员可设置每日、每周或每月任务，把 AI 只读查询结果发送到已绑定的本人私聊或已批准的钉钉群；新任务默认停用，当前支持纯文本结果。Worker/helper 为 `20260912T085820Z-cde69efbfa6cb274`，AI `0010` 与执行器已采用；正式页面、备份恢复及连接状态通过核验，尚未做真实消息投递测试。说明见 [AI定时任务](docs/AI_DINGTALK_SCHEDULES.md)，采用记录见 [发布证据](docs/evidence/ai-scheduled-production-20260912.json)。

2026-09-12 **京准通 AI 推广报表迁移提示修复已在本机受控上线**。两店仍使用原 n8n 日调度与每小时安全重试；导出脚本仅在确认当前店铺身份、唯一且文案匹配的提示弹窗后点击“知道了”，延迟遮挡时只重试一次可逆的报表入口，不重放生成或下载。Worker/helper effective release 为 `20260912T074346Z-e49e9dd1dc958ecc`。本次发布未创建京东任务或导入数据；隔离恢复、整栈就绪和发布后备份复验结果见 [采用证据](docs/evidence/jd-promotion-migration-guide-production-20260912.json)。

2026-09-12 **广东仓实物库存变化后恢复自动风险判断**已合入 `main` 并在本机受控上线。手工标为“库存健康”的型号，在后续成功发布的当前库存快照中，精确“广东仓”的实物库存数量发生变化时，会清空手工健康覆盖并结束既有备货健康跟进，恢复系统自动判定；缺失记录、历史补传、其他仓和仅可用/在途变化均不触发。此次无数据库迁移、无权限扩展，Worker 继续使用 `20260911T165424Z-941a792a6c05e33e`，Django deployment manifest SHA 为 `68c16a99ef2a973c3f1ddc8369bf716c7020714bbc5619fc7e64755a5b795b6b`。发布前备份已完成独立恢复，发布后备份复验和只读接口回查通过；验收未触发生产导入、未修改手工风险记录，新逻辑将在下一次满足条件的库存导入中生效。详见 [生产采用证据](docs/evidence/guangdong-manual-health-reset-production-20260912.json)。

2026-09-12 **丽力每日 MTOP 分批 + 天猫店铺独立执行**已受控发布为 `20260911T165424Z-941a792a6c05e33e`。指定 9 月 11 日 22:44:54 的旧任务已保留证据归档作废，丽力 cadence 已从三日迁为每日。获额外授权后在空闲窗口重启一次 n8n，数据库和 Django 未重启；丽力 1058 与亿玖 1059 已取得真实并行运行证据，业务终态须按 [独立执行方案](docs/TMALL_STORE_ISOLATION.md) 的实际验收记录判定，不能用发布成功代替商品导入成功。

2026-09-11 晚丽力商品管家恢复修复已受控发布，当前 Worker/helper 为 `20260911T151219Z-9d2563d0433704b8`。完整验证 **1038 已通过原 M 响应卡点，但未找到与原提交时间匹配的导出记录，商品文件仍未下载或导入，不能视为丽力导出已修好**。原清单保留在 `export_confirmed`，禁止重发或替用旧任务；需人工核对“前往下载”打开后的最新记录及创建时间。n8n 定义与数据库服务未改动，详情见 [恢复采用及运行证据](docs/evidence/tmall-lili-resume-completed-card-20260911.json)；此前 1029 失败的历史保留在 [首轮记录](docs/evidence/tmall-lili-export-repair-production-20260911.json)。

小特 **pandas 容器分析工具已于 2026-09-11 本机上线**：按当前账号权限导出系统数据集，在独立 Linux/rootless Docker 容器中做临时关联、分组和透视，并返回对话表格。保留原 JSON 分析沙箱；单次最多 3 个数据集、2000 行，计算最多 8 秒。真实容器、镜像 PostgreSQL 联调、受控启动停止及正式工具调用与审计验收已通过。使用方式与实际采用证据见 [pandas 容器分析说明](docs/AI_PANDAS_SANDBOX.md)。

库存健康指标、广东入仓型号编辑保存和各板块 AI 对话框布局已于 **2026-09-11 在本机生产统一上线**。库存健康分布只统计京东仓、天猫履约仓、精确广东仓和自营仓，统一六类状态并以库存周转严格大于 180 天判定低周转；型号编辑继续通过独立库存 writer 执行版本 fencing 与写后回查。板块 AI 对话工作台现填满抽屉可用高度，输入区下移到底部并缩小占用，更多空间用于显示历史对话。当前 Worker/helper effective release 为 `20260911T035534Z-683ed76137b93190`，Django deployment manifest SHA 为 `da378980df03116093a14e03b4f4c1a599a5ea0365223d9d2d53437d63afba5d`；整栈 Running / Ready / exact_release，发布前备份独立恢复及发布后备份复验通过。详见 [统一发布证据](docs/evidence/inventory-health-ai-panel-production-20260911.json)。

AI 管理的生成参数已于 2026-09-10 完成本机生产采用，贯通 Django 配置、provider 和 Web 对话：支持更高输出额度、上下文预算、按端点选择推理格式、默认温度、任务时限、附加业务提示词，以及消息用量与停止原因。新建模型默认输出 65536、对话总时限 1000000 秒，移除单轮超时配置，自定义温度合并到温度设置。AI `0009` 已应用，旧配置不自动增额；具体范围见 [模型生成能力配置](docs/AI_MODEL_GENERATION_CAPABILITIES.md)，运行版本、数据回查与备份恢复见 [生产采用证据](docs/evidence/ai-workbench-production-20260910.json)。

AI 对话工作台已随本次版本上线，支持个人会话侧栏、页面上下文、格式化 Markdown 和 SSE 增量正文；用户消息与输入框为 13px，AI 正文为 14px。实现与验收边界见 [AI 对话工作台说明](docs/AI_CHAT_WORKBENCH.md)。当前 effective release 为 `20260910T140701Z-0194554bc4bcead5`；正式 SSE 验收使用派发前拒绝的无效输入，不产生付费模型调用。

钉钉 AI 群对话增强与库存备货计划批量导入已于 **2026-09-10 在本机生产统一采用**：系统设置新增独立 AI 对话群设置，群内 @ 在原群回复、私聊在原私聊回复，并按现有账号权限接入 12 个系统领域的只读查询；备货计划支持标准 Excel 模板原子导入和多选已确认计划一键顺序提交钉钉。同日后续版本已上线草稿计划多选和“一键确认并提交钉钉”，混合选择会保留未处理状态及失败项。AI 0008、reader/writer 最小权限、Django runtime 与 Worker 已完成受控采用，接收器已连接。首轮发布、备份恢复和只读验收见 [统一发布证据](docs/evidence/inventory-dingtalk-unified-release-20260910.json)，草稿批量确认发布见 [增量发布证据](docs/evidence/inventory-draft-bulk-confirm-production-20260910.json)，业务说明见 [钉钉 AI 对话说明](docs/DINGTALK_READONLY_ASK.md) 与 [库存管理说明](docs/INVENTORY_MANAGEMENT.md)。未执行真实群消息或付费模型端到端调用，不能据此宣称所有自然语言分析场景已验收。

钉钉“志高助手”只读问数于 **2026-09-10 在本机生产启用**。支持本人单聊和“测试群聊”内 @ 提问，结果私聊回复，查询范围为销售、库存和网店，包含 ERP 当前品牌精确销售筛选。用户已确认收到私聊回复；经营分析答案仍受现有模型稳定性和数据覆盖限制。配置、发布证据及重启后需显式启动接收器的限制见 [钉钉问数说明](docs/DINGTALK_READONLY_ASK.md)。

**2026-09-10 AI 故障复盘更新：整体回复能力尚未通过稳定性验收。** 已上线撤销收尾阶段向 Ark GLM 注入不支持参数的回归，并补齐调用阶段及 HTTP 状态审计；原问题实测仍遇到 GLM 生成超时，豆包对照也因请求不存在的工具而中断。不能把此前少数成功对话或本次参数修复等同为 AI 全面可用。历次问题、两次真实失败结果及建议改造方式见 [可靠性复盘](docs/AI_CHAT_RELIABILITY_REVIEW_20260910.md) 与 [本次生产记录](docs/evidence/ai-provider-contract-production-20260910.json)。

AI 板块独立会话首批优化已于 **2026-09-09 在本机正式上线**：包含 12 个板块的聊天入口、个人历史会话恢复、实际页面筛选上下文及网络中断后的只读回执恢复；原有 16 个本人会话保留。数据库迁移及 AI 读写权限已采用，跨板块分析增强、周期监控和主动提醒仍属后续工作。上线验收、备份恢复及本机代理的已知限制见 [首批优化说明](docs/AI_MODULE_CONVERSATIONS.md)。

同日已修复板块对话中“工具未获授权或调用超限”导致回答中断的问题：已授权工具用尽本次查询次数后，AI 会依据已取得的数据生成回答并说明缺口；未授权工具仍拒绝。库存总览和广东入仓清单的工具用途、固定仓库参数已校正，聊天侧栏正文与输入框缩为 14px。库存页面的真实分析问题已得到回复，并通过刷新恢复和服务器消息回查；复杂问题仍受模型响应时间、查询范围及额度约束。见 [修复与上线验证](docs/AI_MODULE_CONVERSATIONS.md)。

系统数据集 API 已于 2026-09-08 在本机正式上线：243 个数据集（219 个权威记录数据集 + 24 个分析数据集），覆盖销售、ERP、财务、网店、市场、商品、库存、运营事务、客服、权限、AI 和 BI 全部 12 个领域，支持目录、字段 schema、筛选和连续分页，AI 对话已接入中央数据集工具。记录数据集仅向无数据范围限制的管理员开放，私有 AI 内容按本人过滤，凭据、原始客户聊天和文件字节不进入通用查询。配套 Worker、Django 和各域 reader 列级授权已采用，无需复制业务表或新增迁移。见 [API 调用说明](docs/SYSTEM_DATASETS_API.md)、[完整覆盖清单](docs/SYSTEM_DATASETS_COVERAGE.md) 与 [本机采用证据](docs/evidence/system-datasets-production-20260908.json)。

库存管理“广东入仓监控”已于 2026-09-08 在本机生产上线：仅监控人工清单内启用型号及精确“广东仓”，支持 Excel 清单导入导出、粘贴多行、供应商到仓周期和风险健康分布。库存与销量继续来自系统权威数据。库存迁移 `0007_guangdong_monitor` 已应用，首次使用需添加监控型号并设置供应商周期；操作口径及发布证据见 [库存管理说明](docs/INVENTORY_MANAGEMENT.md)。

广东备货健康跟进规则已于 **2026-09-11 在本机生产采用**：新增或增加备货数量后显示健康，实物库存首次增加后恢复检测；库存总览的广东仓同步同一规则。库存迁移 `0010_replenishment_health` 已应用，53 条原备货计划的业务字段摘要不变；详见 [备货健康口径](docs/INVENTORY_MANAGEMENT.md#广东入仓监控) 和 [统一发布证据](docs/evidence/guangdong-replenishment-health-production-20260911.json)。

库存总览的“库存健康分布”统一只统计京东仓、天猫履约仓、精确广东仓和自营仓，不再把工厂代发、售后、样品及海外等仓别计入分布指标；状态名称统一为无库存可用、紧急补货、补货预警、积压风险、低周转和库存健康，其中库存周转严格大于 180 天才属于低周转。广东入仓型号设置通过独立写侧 `PATCH` 保存并执行版本冲突与写后回查。

吉客云五表“会话接口下载”已于 2026-09-08 在本机替换原“网页校验 + HTTP 导出”工作流。浏览器负责 DPAPI 登录与会话核验，五类报表使用同一登录会话直接查询、提交导出、轮询和下载。原 n8n ID 保持不变，已设为每天本机时间 00:10 自动运行（Asia/Shanghai，UTC+08:00），同时保留手动入口，现行定义为 `automation/n8n/jackyun-five-dataset-api.workflow.json`；销售仍先匹配成本、校验通过后导入。此次发布没有新增正式导入；此前执行 896 的正式导入和精确批次回查记录保留，销售覆盖截至 2026-09-07。当前版本与采用证据见 [`docs/JACKYUN_SESSION_API_EXPORT.md`](docs/JACKYUN_SESSION_API_EXPORT.md)，历史网页校验版见 [`docs/JACKYUN_HTTP_EXPORT.md`](docs/JACKYUN_HTTP_EXPORT.md)。

本机用户、固定角色、数据范围与权限变更审计已于 2026-09-05 正式切换至 Django/PostgreSQL（reader/writer：8101/8102），入口保持“系统设置 → 权限”。旧 D1 权限表已终态退役，不存在 D1 权限回退；D1 历史审计证据及其他域仍使用的 R2 对象保留。迁移、系统测试、备份与恢复证据见 [`docs/DJANGO_ACCESS_CONTROL_MIGRATION.md`](docs/DJANGO_ACCESS_CONTROL_MIGRATION.md)。

AI 助理完整数据域已于 2026-09-05 在本机正式切换至 Django/PostgreSQL（reader/writer：8111/8112），39 张历史表、536 条记录迁移复验通过，旧 AI D1 已终态退役。现有 React 六个工作区和中央只读工具注册表保留；图片字节亦已于 2026-09-06 切换到 PostgreSQL，AI R2 命名空间已退役，其他业务域 R2 保留，详见 [`docs/DJANGO_AI_R2_RETIREMENT.md`](docs/DJANGO_AI_R2_RETIREMENT.md)。系统测试、激活前后备份恢复和正式采用证据见 [`docs/DJANGO_AI_ASSISTANT_MIGRATION.md`](docs/DJANGO_AI_ASSISTANT_MIGRATION.md)。

AI 对话已于 2026-09-09 在本机修复代理虚拟 DNS 地址拦截与完整 HTTP 响应读取后误报连接失败的问题；默认火山方舟 `glm-5.2` 的思考模式已修正为自动，避免供应商拒绝关闭思考参数。市场工具已修复筛选参数映射，并返回有界分析摘要，避免完整页面内容超过工具上限。普通对话与指定类目、日期、SKU 维度的市场分析均已取得真实模型回复并完成消息保存回查。市场分析仍受现有查询量上限约束，提问时应指定类目、日期和 SKU/SPU 维度。故障处理见 [AI 助理配置说明](docs/AI_ASSISTANT_SETUP.md)，发布与验收记录见 [本机采用证据](docs/evidence/ai-chat-production-20260909.json)。

同日后续修复了自然提问的生成超时：默认 `glm-5.2` 单轮等待从 60 秒调整为 120 秒，保留整个对话 260 秒上限及未知结果不重试的约束；市场问题优先给完整概览，模型调用记录成功/失败耗时，输出截断明确提示。18:00 浏览器原样发送“切肉机市场分析”已收到完整回复并完成服务端同步，耗时约 141 秒；首次过宽查询超时后，缩小日期的查询成功。此验收不代表所有问题均会快速响应，详见 [自然提问修复证据](docs/evidence/ai-chat-natural-question-production-20260909.json)。旧失败请求保留审计；再次提问可刷新后新建对话，不重放原请求号。

2026-09-13 **市场 AI 标注的多计划并发与跨页复核已上线**，源码 `2a1867c244fc1c8d3b4cb55675b8dd71d11044a7`：后台复验任务创建人权限、公平派发多计划，同图跨月复用，已完成结果可先复核，并按 500 条分批入库。5 条原有计划均已自然推进，“全选筛选结果”已恢复可用；历史失败记录保留。诊断、验证和采用边界见 [市场标注可靠性修复](docs/MARKET_ANNOTATION_RELIABILITY.md)。

## 页面导航

应用主菜单、“章”字账号按钮、“问当前页面”和统计周期统一放在顶部深色菜单栏，移除“我的工作台”、可见全局搜索框及重复模块标题行；全局搜索仍可通过键盘快捷键打开。桌面模块菜单固定一排，空间不足时横向滚动，当前模块自动保持可见；支持紧凑图标显示，窄屏从顶部展开。账号按钮点击展开登录/退出操作。各模块主 Tab 随页面滚动固定在菜单栏下方，并按实际高度调整，避免窗口缩放或工具换行时遮挡。

统计周期支持“近30天”（含上海今天）及“去年同期”。去年同期按当前选定区间的起止日期分别回退一年，2 月 29 日在非闰年对应 2 月 28 日；重复选择不再次回退，切换模块或刷新保留该区间。新增快捷筛选继续使用既有起止日期查询接口。

## 后端与聚合入口

本机已于 2026-09-06 完成聚合层受控发布，结构化业务事实与状态统一由 Django/PostgreSQL 负责。全局搜索、AI 财务工具、财务公开 API、市场标注和后台调度已清除 D1 访问；当次采用的 Worker `20260905T180043Z-7364a22437c52ae1` 已取消 D1 binding 和 Drizzle 迁移，后续控制链采用保持此边界，当前版本见下节。23 个 Django 服务及 14 分组搜索已回查，网店搜索的重复批次查询也已修复。现有 React 前端与薄 Worker 保留，市场/网店图片及运营事务附件继续使用原 R2。检查命令为 `npm run check:backend-boundary`，聚合层历史采用清单、验证与发布证据见 [`docs/DJANGO_AGGREGATE_CUTOVER.md`](docs/DJANGO_AGGREGATE_CUTOVER.md)。

## 启动方式

钉钉接收器已配置随系统自动启动，要求登录原 Windows 用户；当前不支持无人登录运行或接收器异常退出后的独立自动恢复。配置方式与启停语义见 [AI 定时任务的接收器启动说明](docs/AI_DINGTALK_SCHEDULES.md#接收器随系统自动启动)。

n8n 后台计划任务采用独立的无控制台启动程序；Execute Command 的 Windows 子进程也采用无窗口选项，解决新品周报每 5 分钟检查时弹出 PowerShell 的问题。原有登录身份、失败重试、日志、命令返回值和子进程收尾继续保留。启动时校验命令节点补丁，n8n 升级后需重新审查与采用。安装、隔离验证及受控切换见 [n8n 无控制台启动](docs/N8N_NO_CONSOLE_STARTUP.md)。

BI 看板复用库存总览的广东仓健康规则，BI 只读账号必须具备广东仓监控清单及供应商周期配置的读取权限。2026-09-11 已补齐本机权限和就绪检查，默认、近 7 天、自定义看板及浏览器页面均通过验收；无新增数据库迁移，网页版本保留。排障验收除总控状态和首页外，还须实际验证 `/api/bi/overview`；首页可打开不代表看板聚合查询成功。权限契约见 [BI 只读聚合契约](docs/DJANGO_BI_MIGRATION.md)，部署、权限和备份证据见 [本机修复记录](docs/evidence/bi-guangdong-permissions-production-20260911.json)。

2026-09-06 本机已正式完成 D1 控制链脱钩：业务统一使用 Django/PostgreSQL，Worker/Django 日常启动、自动子进程恢复和后续发布不再读取历史 D1 文件。当前采用 release 为 `20260906T035823Z-fceee410b71f79b0`，23 个 Django 服务及网页已回查正常。不可变发布链继续绑定全局退役证明；历史 D1、永久 guard 和审计证据保留，R2 图片/附件边界不变。正式采用与验证证据见 [`docs/GLOBAL_D1_CONTROL_RETIREMENT.md`](docs/GLOBAL_D1_CONTROL_RETIREMENT.md)，原始评估见 [`docs/GLOBAL_D1_RETIREMENT_ASSESSMENT.md`](docs/GLOBAL_D1_RETIREMENT_ASSESSMENT.md)。本结论只覆盖当前 Windows 本机，不代表远程部署或 D1 物理销毁。

当前本机正式环境的人工启动统一使用唯一总控。总控通过内核级互斥避免重复启动，先检查并启动 PostgreSQL、各业务域（含 ERP 主数据 reader/writer）和已启用的 BI 只读聚合服务，再处理已经验证的不可变 Worker effective head，最后回查全部域、Worker、辅助服务和主页：

```powershell
& "D:\运营管理系统\tools\operations-system-control.ps1" -Action Start -Open
& "D:\运营管理系统\tools\operations-system-control.ps1" -Action Status -Json
```

`-Open` 及桌面控制面板的“打开页面”会显式使用 Google Chrome，不依赖 Windows 默认浏览器。

桌面应用可用 `pwsh -NoProfile -File tools/install-desktop-launcher.ps1` 安装或重建。安装后双击桌面/开始菜单中的“运营管理系统”，或按 `Ctrl+Alt+O`：已就绪时直接用 Chrome 打开；未就绪时显示等待窗口，仅委托既有总控 `Start -Open`，成功后自动打开页面。应用安装在当前用户的 `%LOCALAPPDATA%\TERUISI\Launcher`，失败时可在窗口查看本次日志；关闭窗口不会停止服务器。安装只创建本机桌面入口，不改变服务部署、开机启动项或业务数据。开发时可用 `-InstallDirectory <临时目录> -NoShortcuts` 隔离验证。

唯一启动引擎位于 `tools/worker-local-service.ps1 -Action Start`：它先验证并按需启动完整 Django/PostgreSQL 栈，再处理 Worker。`运行项目.bat`、`npm start`、`npm run dev` 和登录启动项直接汇聚到该引擎；桌面控制面板与上面的 `operations-system-control.ps1 -Action Start` 是带组合状态、日志和最终 HTTP 回查的界面层，启动时仍只调用这一个引擎，不再复制启动逻辑。重复点击控制面板时返回 `start_in_progress`；系统已经完整运行时返回 `already_running`，不会重启现有进程。当前机器的完整冷启动预算为 1–2 分钟；控制面板会持续显示当前阶段和日志摘要。登录快捷方式已于 2026-09-06 经受控激活重绑至 `20260905T180043Z-7364a22437c52ae1` 并通过回读；后续版本仍必须走 successor 激活和启动绑定门禁。

顶层 `Start`/`Stop` 把销售/财务与网店、市场、商品经营、库存和运营事务新品视为同一次受控生命周期操作：完整运行目录 ACL 审计只执行一次，后续子域只能在同一 PowerShell 进程、同一 runtime/部署清单且 15 分钟内复用该结果，并仍回读根 ACL 与应用清单。直接操作某个子域、上下文过期或任一绑定不一致时，仍会执行完整 ACL 审计。Worker release 的 source、dist、`node_modules` 和 helper 仍逐文件校验，但元数据读取与文件预取使用有界并发，最终 SHA-256 顺序和旧 manifest 协议保持不变。

唯一引擎不会自行判断或删除任意残留进程。只有内部 Worker 状态明确为 `stale_or_invalid_receipt`，且受保护回执可重新验证、supervisor 已不存在时，它才通过既有精确回执删除门禁清理该回执，并在状态稳定为 `stopped` 后继续；端口占用、身份不明、回执损坏或健康探针异常仍失败关闭。控制面板的“暂停网页服务”只通过底层身份门禁停止 Worker，Django/PostgreSQL 后端继续运行。

正常登录启动仍由随 Django runtime 和不可变 Worker release 部署的各自受控 supervisor 负责；它们是总控复用的底层生命周期机制，不是第二套人工入口。Windows 重启后，Django operator 只会清理能够由三项时间证据证明属于上一次开机的旧进程 receipt，不会接管或终止当前复用相同 PID 的进程；同一次开机内或证据不完整的身份冲突仍失败关闭。supervisor 状态和启用/回退步骤见 [`docs/DJANGO_RUNTIME_SUPERVISION.md`](docs/DJANGO_RUNTIME_SUPERVISION.md)。

正式本机环境禁止直接运行 Wrangler、`dist`、旧 release 或 `tools/start-local-worker.mjs`；Worker 升级只能在停服时通过 append-only successor 协议前向发布。日常启动只能通过上述受控入口进入 `worker-local-service.ps1 -Action Start`，不得单独调用 Django 控制器后再自行拉起 Worker。`npm run build` 仅用于 Worker 已停止的源码验证，不会自动部署正式运行目录。

隔离开发环境中的旧 `start:local-worker` 流程会把被 Git 忽略的根目录 `.dev.vars` 以硬链接提供给构建产物；它不属于当前本机正式启动或发布路径。正式不可变 Worker 内部仍以回环存活检查、有界重启和熔断门禁守护自己持有的 Worker/helper 子进程；网页 liveness 与后端 readiness 分离；Django 就绪异常只披露受影响服务，不触发 Worker 自动重启，也不存在 D1 回退。

本机 `.dev.vars` 同时显式设置 `TERUISI_LOCAL_DIRECT_ACCESS=true` 与 `TERUISI_RUNTIME_ENV=development` 时，AI 助理可直接使用本地管理员身份，无需登录；该能力还要求真实开发/受控本机构建，并由 Worker 与身份层双重限制在 `127.0.0.1`、`localhost` 或 IPv6 回环地址。生产构建、LAN 地址、任意域名、Host 伪装和 DNS rebinding 都不会获得匿名管理员权限。具体配置与验收方法见 [`docs/AI_ASSISTANT_SETUP.md`](docs/AI_ASSISTANT_SETUP.md)。

### Windows 启动 / 停止 / 重启脚本

`运营系统.bat`（根目录）和 `tools/operations-system-service.ps1` 是同一套生命周期入口，不复制任何启动逻辑：`Start` 仍调用 `operations-system-control.ps1 -Action Start` 进入唯一启动引擎；`Stop` 先经 `worker-local-service.ps1 -Action Stop` 的身份门禁停止 Worker，再调用运行目录中的 `django-local-service.ps1 -Action Stop` 停止各域 Django 与 PostgreSQL；日常 `restart` 是目标 30 秒内的网页 Worker 热重启，Django/PostgreSQL 全程保持运行；`restart-full` 才执行完整 Stop + Start；`Status` 复用总控的组合状态。Stop/Restart 与桌面控制面板共用同一个系统互斥，拿不到锁时直接拒绝而不是交错执行。

```powershell
运营系统.bat                     # 菜单：启动 / 停止 / 30 秒热重启 / 完整重启 / 日志 / 状态
运营系统.bat start-bg            # 后台启动并立即返回；进度用 运营系统.bat logs，结果用 运营系统.bat status
运营系统.bat restart             # 热重启 Worker，保留 Django/PostgreSQL
运营系统.bat restart-full        # 完整重启全部服务
npm run system:start             # 等价于 tools\operations-system-service.ps1 -Action Start
npm run system:stop              # 完整停止；只停网页 Worker 用 -Action Stop -KeepBackend
npm run system:restart           # 热重启 Worker
npm run system:restart:full      # 完整重启
npm run system:logs
```

`-Background` 只是把同一条命令放到隐藏的独立 PowerShell 进程里执行，输出写到 `tmp\system-service\*.log`，并把 PID 与进程创建时间绑定到 `tmp\system-service\background.json`；已有后台任务在执行时会拒绝重复提交，`Status` 会同时显示后台任务是否仍在运行。所有子脚本都通过文件重定向启动并只等待直接子进程，不会因为 Worker/PostgreSQL 等长期子进程继承句柄而挂住。

#### 启动耗时治理（2026-09-03）

取证发现完整 `Start` 曾在同一次启动内重复生成昂贵证据。当前实现保留所有失败关闭边界，同时消除可证明等价的重复工作：核心 Django Start 完成应用树与全 runtime ACL 校验后，绑定当前 PID、runtime 路径和部署清单 SHA-256 的 15 分钟上下文由同进程各业务域复用，域独立启动仍完整校验；Django Start 只有在全部已启用 reader/writer 和 BI reader 的有界 readiness 通过后才成功退出；外层和 Worker 现在各只调用一次 `AggregateStatus`，在同一 pwsh 进程内核验 PostgreSQL、18 个业务 reader/writer 与 1 个 BI reader 的精确进程回执及 readiness，不再串行冷启动多个 Status 控制器。ERP 主数据 reader/writer 独立重算货品与组合装摘要并核对 PostgreSQL authority、revision 和只读事务，旧 ERP watch/bridge 已退出运行路径。

全 runtime ACL 契约仍逐对象核验重解析点、继承保护、主体集合、Allow/FullControl、继承标志及显式 ACE，但扫描改为单个受控 .NET verifier，避免 9 万余次 PowerShell provider/ETS 对象构造。当前生产 runtime 的只读复测为：94,057 个文件系统对象完整 ACL 核验约 9.2 秒，全部服务聚合状态约 8.9 秒；旧基线分别约 128 秒和多轮合计约 80–100 秒。根据 300.8 秒历史完整冷启动时间线，消除的重复成本把同硬件冷启动预算压到约 100 秒；正式发布时间仍须在隔离镜像先做完整冷启动验收，再按受控发布流程回读生产证据。

热重启不会停止 PostgreSQL、任一 Django 服务、immutable supervisor 或 5791 helper。Worker 服务先确认当前进程是 effective head 对应的 `exact_release`，再按 PID、CreationDate、命令行、父子树和 manifest 身份精确锁定 3000 Worker 子树，只终止这棵子树；已经在冷启动时消费完整 release 校验证据的同一 supervisor 随后复用其既有崩溃恢复门禁，重新核验 process receipt、authority guard、固定参数、持久化和 Miniflare cache 边界后创建新 Worker。控制器要求 supervisor 身份保持不变、3000 端口进程 PID 已替换、主页为 HTTP 200 且 helper 健康，任一歧义都失败关闭。该路径没有放宽 supervisor 首次启动必须持有两分钟有效一次性完整校验回执的契约；完整重启行为仍可通过 `restart-full` 使用。

Worker release 的 source snapshot、`dist`、`node_modules`、helper、bundled npm、guard、activation fence 和硬链接仍由启动引擎逐文件完整校验一次。校验成功后，引擎在受保护的 runtime state 中 create-only 发布一个绑定 manifest SHA-256、release 路径 SHA-256、source/build fingerprint 且仅两分钟有效的一次性回执；supervisor 先核验自身 canonical process receipt，再消费该回执，避免第二遍约 4 万文件哈希。无效回执失败关闭；仅身份完全一致且已过期的中断回执可在下一次完整校验后清理。Worker 就绪等待在端口尚未出现时只做轻量监听检查，桌面并发启动等待最多每 10 秒生成一次完整状态。生产登录快捷方式仍固定于当前不可变 release，只有按既有 `plan`/精确 SHA `apply` 门禁激活并回读重绑后才会使用这些变更。

仍有意保留的成本包括每次 Start 的一次 Worker 完整逐文件校验、一次 Django 应用树校验、一次 runtime 全树 ACL 精确审计，以及当前的 migrate/最小权限重置。不得用 mtime、目录排除、旧回执或跳过规则来替代这些完整性证据。Windows Defender 排除属于主机安全策略变更，不由启动脚本自动执行。

启动与发布优化已于 2026-09-21 采用：支持在线准备 Worker、提前暂存 Django、保留 PostgreSQL 的应用维护和同域读写服务重叠启动。实测范围、报告建议取舍、采用顺序和限制见 [启动与发布优化](docs/STARTUP_RELEASE_OPTIMIZATION.md)。

### 前端一键演示预览

在独立 `codex/*` worktree 中，双击 `预览系统.bat` 或运行 `npm run preview:isolated`。页面使用独立的 `127.0.0.1:3100`，代码保存自动更新；首次自动安装缺少的前端依赖、准备独立 Django/SQLite 与合成销售、货品、库存数据。`preview:prepare` 重新准备数据，`preview:snapshot` / `preview:restore` 保存和恢复预览数据，`preview:verify` 检查数据和隔离行为，`preview:stop` 停止预览。生产继续运行。当前仅开放查询和页面展示，其余领域以空态为主，完整写流程和 PostgreSQL 契约仍需领域镜像验收。使用和隔离边界见 [一键预览说明](docs/ISOLATED_PREVIEW.md)。

### macOS / Linux 开发机本地启动

开发机没有 PostgreSQL 与受控 runtime，直接运行 `npx vinext dev` 时所有 Django 域都会提示"Django xx 服务配置不完整"。`tools/django-dev-backend.mjs` 用 SQLite 与 `development` 进程角色在本机拉起一套仅供开发的 Django 后端，并把 Worker 需要的 `TERUISI_DJANGO_*` 变量写入 `.dev.vars` 的受管块：

```bash
npm run backend:dev          # 创建 .runtime/django-dev/venv、生成 backend.env、migrate SQLite、启动读(8001)/写(8002)两个进程并同步 .dev.vars
npx vinext dev               # .dev.vars 只在启动时读取，首次同步后需要重启 dev server
npm run backend:dev:status   # 进程、端口、/health/live 与 .dev.vars 同步状态
npm run backend:dev:stop
```

`development` 角色同时挂载每个域的 reader/writer 路由与 BI 只读路由，两个进程即可覆盖销售、财务、ERP 主数据、网店、市场、商品经营、库存、运营事务、客服、权限控制以及 BI；它不启用生产 authority 门禁，仅在 `sales_writer` 等生产角色开放的写路径（如销售导入）在开发模式下不可用。BI 的生产拓扑、组合 revision 和迁移门禁见 [`docs/DJANGO_BI_MIGRATION.md`](docs/DJANGO_BI_MIGRATION.md)。`.runtime/`、`backend.env` 与其中的随机密钥都被 Git 忽略，不得用于 Windows 生产主机。Vite dev server 的依赖预打包缓存固定放在 `node_modules/.vite-sites-cache`（构建仍用根目录 `.vite-sites-cache`）：vinext 内置的 CommonJS 插件只跳过路径含 `node_modules/.vite` 的预打包产物，放在别处会在启动时报 "A module cannot have multiple default exports"。

## 工作流运行通知

钉钉运行消息只在整条工作流完成、失败需要人工协助、或持续卡住无法恢复时发送，由“志高助手”发送到“测试群聊”。中间节点和数据集完成不再逐条通知，同一卡点不重复提醒；恢复后以整条流程完成消息收尾。吉客云五表和京东四店整条流程各汇总一条，天猫及京东 AI 推广按每店独立完整工作流各一条。完整单位、阻塞判定与防重规则见 [工作流钉钉通知规则](docs/WORKFLOW_DINGTALK_NOTIFICATIONS.md)。

## 吉客云自动化

五表另提供浏览器仅登录、报表走会话接口的候选工作流 `automation/n8n/jackyun-five-dataset-api.workflow.json`。它保留原有销售成本校验、五表屏障和导入回查，并为平台与本机时间差增加任务匹配证据；尚未替换上文已采用版本。参数校准、运行边界和验收见 [`docs/JACKYUN_SESSION_API_EXPORT.md`](docs/JACKYUN_SESSION_API_EXPORT.md)。

五表 n8n 模板采用一次专用登录会话顺序导出：通过当前账号的网站接口核验权限、查询条件和总数，再提交本轮异步任务、轮询并直链下载。登录沿用 DPAPI，会话不写入 n8n；下载阶段无需加载五个报表页面。五表齐全后统一校验、导入、回查，旧单表计划和只读诊断不能升级成新批量运行。部署和实际采用状态见下方五表文档。

- `npm run jackyun:login`：打开专属浏览器，手工登录吉客云
- `npm run jackyun:credential:setup`：打开本机 DPAPI 凭据录入窗口，填写手机号或工号和密码；`npm run jackyun:credential:status` 只检查当前 Windows 用户能否解密。`npm run jackyun:authenticate` 只验证专用登录与企业号，不执行导出或导入。配置见 `config/jackyun-login.json`，维护步骤见 [`docs/吉客云DPAPI登录配置.md`](docs/吉客云DPAPI登录配置.md)。
- 五表任务若只在首次库存登录检查或指定查询验证失败，可按五表文档使用受控 `jackyun-preflight-recovery.ts` 闭合导出前停止的旧执行，再从 n8n 手动入口启动完整新执行。查询失败必须额外核验唯一 controller 状态及摘要、无导出意图，并保留全部旧证据；有任何导出、下载或导入证据时拒绝该恢复方式。
- `npm run jackyun:daily`：运行每日五类数据导入
- `automation/n8n/jackyun-five-dataset-api.workflow.json`：本机已采用、每天本机时间 00:10 自动运行并保留手动入口的吉客云五表工作流，按分仓库存 → 组合装及子件 → 发货时间销售明细 → 库龄 → SKU 货品依次导出；五表全部校验后，再按主数据和成本依赖统一导入并独立核验精确批次。库存和库龄使用实际采集日，销售使用本月至昨天。新旧传输的计划和恢复状态隔离，工作流不保存账号、密码或会话。原 `jackyun-five-dataset-http.workflow.json` 和 `jackyun-five-dataset-daily.workflow.json` 保留用于历史协议，不是当前已采用定义。详见 [`docs/JACKYUN_SESSION_API_EXPORT.md`](docs/JACKYUN_SESSION_API_EXPORT.md)。
- 网页会话版已在本机完成真实五表下载、Django 导入和独立批次回查。货品、组合装内容未变化时按幂等规则复用原批次；库存/库龄精确归属由同 revision 的只读事实核验，销售同时绑定发货日期和本轮库存成本源。组合装确认会等待按钮事件就绪后只点击一次。日调度仍停用；各次 n8n 执行、原始文件、采用回执及受控恢复记录保留在五表文档中，失败执行不会改写为成功。
- 2026-08-05 历史快照的多 AI、多方法验证结论和真实来源限制见 [`docs/吉客云导入系统多AI多方法跑通测试-2026-08-06.md`](docs/吉客云导入系统多AI多方法跑通测试-2026-08-06.md)。

## 网店数据

天猫六店“每日查缺、逐日补齐”的新版 n8n 模板已完成开发，尚待本机受控发布。每店按最早缺失日循环商品日/推广日下载、导入和覆盖核验；每轮最多补 14 天，30 分钟后不再开启新日，最后按原到期规则执行一次货品主数据并收尾。预算耗尽仍有缺口会明确报未完成。原店铺、定时与隔离保持不变，正式生效需同时发布 helper 和六个原 n8n 工作流。规则与发布步骤见 [天猫逐日补缺](docs/天猫商品与推广缺失日规划.md)。

推广导入的 Django 回执统一返回实际落库行数 `readbackRowCount` 与日期、平台、店铺，首次导入和内容幂等重复导入使用相同字段契约。天猫页面版推广恢复会优先续接同店、同协议的原日期任务，回查原日商品数据后再处理本轮计划；提交前失败的清单和已完成清单在换日时保留历史归档。商品报表按商品 ID 汇总计划行，标题差异保留为可追溯的标题列表，不影响金额汇总。DPAPI 登录或阿里妈妈单页应用懒加载造成目标日期路由漂移、页面骨架未完整加载时，P 仅允许执行一次无业务副作用的精确路由恢复；恢复后重新核验店铺身份、目标日期路由和三个页面语义锚点，全部通过前不得点击营销场景、维度或下载报表，第二次仍未就绪即失败关闭。上述源码更新须经受控发布后才进入本机 n8n 执行环境，恢复规则见 [`docs/天猫n8n每日导入监控与安全恢复手册.md`](docs/天猫n8n每日导入监控与安全恢复手册.md)。
- 天猫商品日与推广日“下载前查缺失、逐日补缺”已于 2026-09-06 随本机 effective release 受控采用；亿玖 execution 847 已补齐 8 月 28 日商品日/推广日，848 已补齐 8 月 29 日商品日。2026-09-08 又采用页面版 P 的单次安全路由恢复：拓丰 execution 909 补齐 8 月 28 日推广日 67 行，马思图 execution 910 补齐 8 月 29 日推广日 36 行，两轮均为 0 告警、批次/覆盖回查通过、M `not_due` 且浏览器与 helper 完成收尾。该证据确认原“登录回跳/页面骨架未就绪”卡点已闭合，但不据此宣称六店长期无故障；六店调度与 M 节奏未改变。采用证据、单轮预算和旧活动清单门禁见 [缺失日规划](docs/天猫商品与推广缺失日规划.md)。

- 京东四店商品数据提供默认未激活的 n8n 副本：`automation/n8n/jd-multi-store-daily.workflow.json`。它每日上海时间 10:00 先通过 `127.0.0.1:5791/coordination/claim` 原子领取共享 helper；未获授权时每 5 分钟等待，累计 72 次、约 6 小时后失败关闭，手动入口也不能绕过该门禁。领取后执行 A → B → C：A 固化“昨天所在月 1 日至昨天”的扫描范围，逐店读取 SKU/SPU 权威落库覆盖，验证实际日期与缺失日期完整互斥且响应未截断，并只把相邻缺失日合并为下载区间；B 按店铺串行执行，每店商品 SKU 主数据仍每日更新一次，SKU/SPU 仅下载、导入缺口区间，无缺口维度不创建任务；C 独立重读每个审计和本机精确导入批次，随后再次回查四店两维度的完整扫描范围必须无缺口。四店固定映射为“志高商用设备旗舰店 → Default、志高商用厨电旗舰店 → Profile 1、志高切肉机旗舰店 → Profile 2、志高商用洗碗机旗舰店 → Profile 3”，启动器使用独立安装的 Chromium，不再回退到 Google Chrome。京东商品主数据日常使用普通有头 Chromium 在后台隐藏并最小化运行；自动流程从空白页启动，在导航前监听并复用页面首屏的唯一商品查询，禁止先打开重复商品页或在首屏结果后再补点“查询”。商智 SKU/SPU 分天独立运行仍默认 `headless=new`，在同店串行链路中则复用已经启动的后台有头实例；恢复清单、建立下载中心基线、确认创建任务和点击下载前，程序都会从页头商城链接精确核验注册表中的店铺名与 shopId，清单也绑定 storeKey/shopName/shopId，防止同一 profile 被人工切店后把数据记到另一店。登录失效、验证码、安全验证、业务码 `601` 或店铺登录身份异常时，程序关闭后台实例并打开当前店铺对应的可见 Chromium profile，任务仍失败关闭、保留审计且不自动重放导出动作；`601` 是京东服务端风控结果，不能仅凭是否无头或点击方式推断单一根因。人工首次登录可显式使用 `--interactive-login`。三个业务节点只调用 `127.0.0.1:5791/jd/*`，绑定同一 n8n execution ID；账号、密码、Cookie、Token、Session 和 profile 路径均不进入工作流或页面。
- 四个京东 Profile 是固定店铺会话，流程不会在浏览器页面内切换账号。四店均显式使用 `loginMode=windows_dpapi_credentials`：已有登录态时直接继续；登录失效时只由当前 Windows 用户解密该店独立 DPAPI 凭据，在唯一京东账号密码表单中填入并最多提交一次。首次配置或改密运行 `npm run jd:credential:setup -- -StoreKey <店铺键>`。账号密码不进入 n8n、仓库、环境变量、命令参数或日志；验证码、短信、滑块、安全验证、凭据拒绝或登录控件歧义仍失败关闭并要求人工处理。
- 京准通 AI 推广明细提供两条仓库默认未激活、由本机 n8n 独立发布的工作流。`automation/n8n/jd-promotion-daily.workflow.json` 固定绑定 `jd-yiyong-director`（志高商用设备旗舰店、Default profile），每天上海时间 10:40 执行；`automation/n8n/jd-promotion-cut-meat-20260813-14.workflow.json` 固定绑定 `jd-maidehao-operator1`（志高切肉机旗舰店、Profile 2、shopId 745866），每天 10:50 执行。两店均从注册表中的推广起始日 `2026-07-01` 扫描到上海昨天：A 先读取运营系统实际推广日期覆盖，只把最早 31 个缺失日固化为逐日计划；无缺口时不启动 Chromium、不创建京东任务。B 按日期升序逐日生成或唯一接管报表、校验 UTF-8 CSV，并按 `jd_promotion/ad` 精确单日导入和回查；C 逐文件复验 SHA-256、行数、账户集合、completed 批次和最终日期覆盖。手动范围也先查缺口，已有覆盖日不会下载。两条流程仍先原子领取共享 helper，店铺 profile、下载目录、恢复清单和任务完全隔离，并与其他京东流程共用全局 Chromium 锁。设备旗舰店说明见 [`docs/京准通AI推广数据n8n工作流.md`](docs/京准通AI推广数据n8n工作流.md)，切肉机店说明见 [`docs/京准通志高切肉机AI推广数据n8n工作流.md`](docs/京准通志高切肉机AI推广数据n8n工作流.md)。
- 京东市场商品榜单由 n8n 工作流 `JdMarketSilentCopy2026`（仓库模板 `automation/n8n/jd-market-ranking-daily.chromium-silent-copy.workflow.json`）托管每日调度。它固定“市场 → 商品榜单 → 交易榜单 → SKU”，按受控顺序串行处理 7 个类目，并绑定 `jd-cuizhiwang-dengweizhang`（志高商用洗碗机旗舰店、Profile 3、调试端口 9227、`silentNoWindow=true`）。每天上海时间 10:30 创建定时 execution，定时分支再等待 1 分钟后进入原子协调门禁；未获授权时每 5 分钟等待，累计 72 次后失败关闭。领取后按 A → B → C 完成缺失日计划、隐藏下载签收导入和全部目标日覆盖回查。每 10 分钟运行的统一 Codex 监控只核验已经到计划时间至少 5 分钟的 n8n execution、业务终态和通知，不直接调用 helper 执行业务节点。详细步骤见 `docs/京东市场商品榜单SKU日数据n8n工作流.md`。
- 京东多店铺流程另提供默认未激活的 Chromium 静默下载副本 `automation/n8n/jd-multi-store-daily.chromium-silent-copy.workflow.json`。市场榜单仍保留未激活的主模板 `automation/n8n/jd-market-ranking-daily.workflow.json`，但当前发布调度只使用上面的 `JdMarketSilentCopy2026`；二者不能同时启用。隐藏流程使用普通有头 Chromium 离屏启动，并由绑定实际 browser PID 的 Windows 守护持续隐藏顶层窗口；所有京东 Chromium 阶段共用活进程校验的全局所有权锁，严格拒绝复用不受本轮守护的实例。登录或安全验证异常只失败关闭并保留审计，不弹窗、不自动重放业务点击。

- 数据导入页支持京东商品主数据与 SKU/SPU 日数据，以及已注册天猫店铺的货品、生意参谋商品日数据、推广商品 ZIP 和炊之王店透视 SPU 商品图 XLSX。天猫来源固定绑定平台“天猫”，店铺必须来自启用的受控注册项；货品与 SPU 商品图要求快照日，日数据要求完整的预期日期范围。SPU 商品图导入会逐行核验内嵌主图、SPUID、店铺和商品链接，图片以内容哈希写入私有 R2，D1 只保存可审计映射。
- 数据导入页还支持年度利润表中的 SKU 快递费率：工作簿必须包含名称精确为 `SKU累计` 的子表，并以 B 列“代码”、M 列“实际金额”、Z 列“合计快递费”和 AA 列“快递费占比”为固定契约。服务端按 `合计快递费 / 实际金额` 重新计算（实际金额为 0 时按 0），同时核验 AA 缓存值；完整权威集合按规格代码原子替换并回查，冲突重复行会失败关闭。负值或超过 100% 的源数据不静默截断，只记告警并在商品经营明细的“退货率”后展示；未匹配规格显示 `—`。超过 2 MiB 的文件通过 1 MiB 分片上传，单文件最大 20 MiB。
- 天猫亿玖店浏览器当前固定映射到专属 Chromium 用户数据目录 `%LOCALAPPDATA%/Chromium-Tmall-Yijiu/User Data` 的 `Default` Profile 和调试端口 `9334`；该目录不与日常 Chromium 或其他店铺 Profile 共享进程锁。货品导出、商品日 Cookie 和阿里妈妈商品推广报表三个阶段统一从该注册项启动，不再回退到共享 `Profile 4` 或旧的 `.runtime/tmall-yijiu-chrome-profile`。
- 天猫工作流的定时和手动入口都先通过 `127.0.0.1:5791/coordination/claim` 原子领取共享 helper；未获授权时每 5 分钟等待，累计 72 次、约 6 小时后失败关闭，只有领取成功的 execution 才能进入 A。协调节点和 A/B/C/P/M 都必须发送相同的 `X-TERUISI-TMALL-STORE-KEY`，helper 将店铺键与 execution ID 一并锁定，拒绝跨店续跑。A 节点随后执行登录守护：启动受控店铺独立 Chromium，已有登录态时直接继续；登录失效且店铺设置 `loginMode=windows_dpapi_credentials` 时，只从当前 Windows 用户绑定的 DPAPI 加密凭据库在内存中解密，向唯一密码表单填入并最多提交一次。账号密码不进入 n8n、仓库、环境变量、命令参数或日志；验证码、短信、安全验证、凭据缺失/损坏、登录按钮歧义或店铺身份不符均失败关闭并要求人工处理。首次配置或修改密码时运行 `npm run tmall:credential:setup -- -StoreKey <店铺键>` 交互录入；需要人工处理平台验证时运行 `npm run tmall:login -- --store-key <店铺键>` 打开可见专属 Chromium。首次登录允许读取尚未启用的已注册店铺，但所有业务节点仍要求 `enabled=true`。
- 天猫 n8n 流程固定按 A→B→C→P→M 串行：A/B/C/P 每日完成商品日与推广下载、导入和回查。亿玖、亿用、丽力 M 每日执行，成功后把下一到期日推进到次日；拓丰、炊之王、马思图继续按独立三日节奏错峰。失败不推进，并在下一次新的完整流程补跑。未到期返回 `not_due`，不创建货品任务，但仍关闭该店 Chromium、释放 helper；n8n 手动完整运行明确强制 M。M 到期失败不会回滚已完成回查的商品日或推广数据，但整个 execution 仍失败并保留原货品活动清单，重跑不得重置已发生的业务点击。日常 Chromium 和其他店铺实例不会被扫描或关闭。
- 天猫生意参谋 SPU 分天数据支持受控多店铺工作流：`config/tmall-store-accounts.json` 为每店配置独立 `executablePath + userDataDir + profileName + profileDir + debugPort + downloadDir`，需要自动登录的店铺显式设置 `loginMode=windows_dpapi_credentials`；各店严格串行且不得共享 Profile、端口、下载目录、签收单、恢复清单或凭据项。五段式 n8n 副本按 A→B→C→P→M 完成商品日、推广日和货品主数据的下载、内容校验、导入与落库回查，业务范围与完整规范化内容一致时才返回 `duplicate`。六店放在京东流程之后，按亿玖 11:00、丽力 11:10、拓丰 11:20、炊之王 11:30、马思图 11:40、亿用 11:50 错峰触发；工作流不保存明文账号、密码、Cookie、Token 或 Session。实现与配置细节见 `docs/天猫生意参谋SPU多店铺工作流.md`；每日监控、故障恢复和扩店复用见 `docs/天猫n8n每日导入监控与安全恢复手册.md`。
- 天猫丽力、拓丰、亿用、炊之王、马思图五店已经完成独立 `userDataDir/profileDir/debugPort/downloadDir`、`windows_dpapi_credentials` 和千牛/生意参谋/阿里妈妈首次身份核验，注册项现为 `enabled=true`；六店实际业务阶段只能串行。仓库模板继续保持 `active=false`，运行 `npm run tmall:n8n:generate` 只重新生成模板，不会自行发布或激活工作流；每 10 分钟运行的统一 Codex 监控从批量状态快照核验已到计划时间至少 5 分钟的六店触发和终态，异常时再按各店计划时间后 5 分钟的门槛读取专项证据。
- 拓丰、炊之王和马思图的现有五段式 n8n 流程仍使用 `/product-master` M 节点和 execution 门禁，货品阶段使用 `on_sale_pagewise_excel`：从“商品 > 我的商品 > 出售中”按 20 条逐页全选，逐页创建 `excel商品批量导出` 任务，最后一页才进入导出记录并下载本轮全部任务。分页文件必须先分别校验，再合并成唯一商品数等于出售中总数的单个权威 XLSX；只允许该合并文件进行一次导入和落库回查，禁止逐页导入互相覆盖。丽力继续使用商品管家“导出全部商品”；亿玖、亿用使用下述 P/M 直连模板，不回退旧 UI 入口。
- 亿玖现行仓库模板为 `automation/n8n/tmall-yijiu-direct-pm-candidate.workflow.json`（文件名因历史兼容保留）。它固定 workflow ID `M4xY8kQ2vR6sT9pC`，P 使用唯一 taskId 的阿里妈妈报表接口，M 每日使用每 20 商品一批的固定 MTOP 导出接口。n8n 只允许最新发布版本承接新的定时和恢复 execution；旧 UI 版和旧直连版本仅保留历史审计，不得重新激活。接口参数、安全栅栏与发布门禁见 `docs/天猫亿玖P-M直连候选工作流.md`。
- 亿用 P/M 直连试点已采用配套 helper 和每日 M 持久节奏，现行模板为 `automation/n8n/tmall-yiyong-direct-pm-candidate.workflow.json`，仍使用原 ID `TmallYiyongDaily2026`、每天 11:50。仓库 `active=false` 不表示本机未发布；live 与已发布历史必须逐项匹配该模板。亿用协议头固定为 `yiyong-direct-pm-v1`，不得复用亿玖协议或回退旧管家模板。完整验收与旧任务保全门禁见 [亿用试点](docs/天猫亿用直连每日M试点.md)。
- 天猫货品导出的活动清单跨日后不会永久阻塞新一轮：已经提交、确认或下载的旧任务必须先按原快照日安全续接，且不得再次发送“导出全部商品”；只有仍停在业务点击前的旧清单可以丢弃。处于 `export_submitting` 的未决任务继续失败关闭，需人工核对商品管家聊天，避免重复创建导出任务。
- 左侧“自动化中心”可切换“吉客云导入系统”“天猫店铺数据导入”“京东多店铺商品数据统一下载与导入”“京东市场商品榜单缺失日下载与导入”“京东设备旗舰店 AI 推广”和“京东切肉机旗舰店 AI 推广”，并为 `operator`、`admin` 嵌入本机 `http://localhost:5678` 的对应 n8n 画布；`viewer`、`analyst` 只能查看概览。页面会读取 `127.0.0.1:5791/health` 的非敏感就绪状态，在服务离线、天猫/吉客云/京东所需的受控 Chromium profile 缺失或已有任务运行时阻止执行；天猫备用 Cookie 原文件仅在专属浏览器不可连接时使用，缺失本身不再阻止页面进入。页面不会显示 profile 路径。“辅助服务已就绪”只代表本地服务与非敏感前置检查通过，业务平台登录态仍由对应执行阶段重新核验。页面展示仓库模板信息，实际发布状态以 n8n 画布为准。本地 Worker 启动器负责拉起一次性辅助进程，并在每轮成功或失败退出后重新待命。六条工作流都保持后端幂等与落库回查，运营系统不读取 n8n 登录态或业务平台凭证，也不会自动发布、激活或执行流程。
- 数据下载导入工作流的每小时安全重试已于 **2026-09-11 在本机生产采用**，覆盖吉客云五表、京东多店铺/市场榜单/两店推广和天猫六店共 11 条正式流程。定时或上一轮自动重试发生可重试失败时等待 60 分钟，并从原工作流“领取共享 helper”创建新的完整 execution；成功即停止。验证码/风控、凭据或登录失效、店铺身份不符、任务歧义、来源未就绪、业务点击或提交结果未决及需要人工确认的内容完整性错误均停止自动重试。共享错误工作流与 11 个专用 Webhook 已发布，n8n 固定只监听 `127.0.0.1:5678`；两个京东兼容模板保持未激活。结构、白名单、采用状态与验证边界见 [`docs/N8N_HOURLY_SAFE_RETRY.md`](docs/N8N_HOURLY_SAFE_RETRY.md)，采用证据见 [`docs/evidence/n8n-hourly-safe-retry-production-20260911.json`](docs/evidence/n8n-hourly-safe-retry-production-20260911.json)。
- Codex heartbeat `automation-5` 在每小时 05、15、25、35、45、55 分以一个 Agent 只读检查上述 11 条现行工作流，只处理已到计划时间至少 5 分钟的流程并追踪到当天闭环；同一 workflow ID 最新三个生产 execution 连续 `error/crashed` 且中间没有成功时，AI 才介入脱敏诊断，同一组三个 execution 只处置一次。统一监控不创建子 Agent，不在监控任务中修改代码、发布生产 n8n、部署或重启服务；验证码、身份/凭据、跨店、来源未开放、任务歧义和业务提交未决只转人工。完整门禁见 [`docs/N8N_HOURLY_SAFE_RETRY.md`](docs/N8N_HOURLY_SAFE_RETRY.md#连续三次失败后的-ai-升级)。
- 网店分析提供跨平台货品目录、京东 SKU 日表现、京东/天猫 SPU 商品表现，以及独立的“京东推广 / 天猫推广”分析页。受控天猫店铺的店透视 SPU 商品图按 `platform + shop_name + SPUID` 精确关联到货品目录和 SPU 商品明细，并通过登录态保护的同源图片接口展示，不跨店复用；页面可选择已支持店铺，超过 25 MiB 的工作簿使用有所有权栅栏的 2 MiB 分片上传，压缩文件和图片解压总量均保持 64 MiB 有界。京东推广按京准通跟单 SKU 汇总花费、展现、点击、总订单行和总订单金额，并只在同日京东商智 SKU 成交金额有覆盖时计算推广费率与成交占比；京准通总订单金额不表达退款后销售净额。金额接口统一使用人民币分；商品访客只按“商品×日”累计展示，不解释为店铺去重 UV。
- 推广概览与商品明细默认只读取按“平台 + 店铺 + 业务日 + 商品”发布的预聚合，并在响应读前、读后核验平台完整性 manifest、精确失效状态和单调数据版本；读取期间发生导入或替换时明确返回可重试错误，不会把失效店铺静默过滤为零。旧库启用该路径前先对项目目录内的 D1 文件执行 `npm run netshop:promotion:backfill -- --database "<绝对数据库路径>" --dry-run`，核验范围后再在受控维护窗口改为 `--apply`；只有不带店铺和日期裁剪的完整平台回填才能发布 manifest，局部补数不会授权全平台读取。
- 推广费率与推广成交占比只使用推广报表和生意参谋支付金额均有覆盖的业务日；ROAS、CTR、CPC 等比例从汇总金额或计数重新计算，不平均源文件行比率。
- 2026-09-01，本机网店分析后端已完成 Django/PostgreSQL 正式单写切换与 D1 终态退役；PostgreSQL 现为网店事实、批次、推广、上传、revision、幂等/尝试审计和全部读写的唯一权威，`127.0.0.1:8021/8022` 已加入受控开机启动链。网店分析前端继续使用原有 React/Next.js `shop-module-view`，没有改写为 Django template；公开 Worker 只保留真实鉴权、签名、解析、allowlist 和有界转发。旧 D1 网店对象已变为 15 个空 tombstone view，并由 9 个共享表 guard 永久拒绝网店域复活；恢复只允许 PostgreSQL 备份/WAL/PITR、兼容代码或审批过的前向修复。正式 cutover、备份恢复和退役证据见 [`docs/DJANGO_NETSHOP_MIGRATION.md`](docs/DJANGO_NETSHOP_MIGRATION.md)。
- 指定历史推广日期时，只能从对应店铺的现有 n8n 工作流启动新的完整 `A→B→C→P→M` execution；受控 CLI 恢复可在启动该次 n8n execution 前设置 `TERUISI_TMALL_PLAN_START_DATE` 与 `TERUISI_TMALL_PLAN_END_DATE`，两者必须是同一个不晚于昨天的业务日。工作流仍逐日串行，C 先补齐并回查同日商品数据，P 再生成同日“商品报表”；内容一致返回 `duplicate`，内容变化则由导入事务精确删除并替换同店同日旧事实，落库回查成功后才进入下一天。禁止直接运行推广脚本或绕过 n8n 单独调用 P。
- 所有导入接口统一按“业务域 + 精确业务范围 + 解析、清洗和业务过滤后的完整规范化内容”判重。文件名、原始行号、行顺序或工作簿元数据变化不会单独触发导入；通过校验的权威业务集合中任一业务字段、业务行或范围变化都会创建新批次并原子替换精确范围。原文件 SHA-256 仍用于签收和审计，但不单独决定 `duplicate`；签名、解析、表头或范围校验失败只记录拒绝尝试，不创建业务指纹、不抢占范围锁。空文件或清洗后零行业务文件保持失败关闭，不会被当成权威空集合清除既有事实。接口在确认成功前回查批次、行数、日期和关键范围。本地验证不等同于生产导入，生产迁移与数据导入仍需单独执行。
- 2026-09-02，本机商品经营板块已完成 Django/PostgreSQL 正式单写切换与 D1 终态退役，cutover ID 为 `products-pg-20260901T164758Z-1c636a3a0f564bc9`。PostgreSQL 是 SKU 快递费率、批次、幂等/尝试审计、原始分片、revision、库存只读投影和商品经营读写的唯一权威；现有 React 页面、公开 API、AI 与全局搜索契约保持不变，薄 Worker 将读写固定转发到 `127.0.0.1:8041/8042`。旧 D1 商品对象已变为 3 个空 tombstone view，18 个永久 guard 拒绝商品域复活；商品在线路径不再读写 R2，但全局 D1/R2 binding 仍供市场图片、运营事务附件及其他尚未退役业务域使用。切换已跨过 PNR，恢复只允许 PostgreSQL 备份/WAL/PITR、兼容代码或经审批的前向修复。正式证据见 [`docs/DJANGO_PRODUCTS_MIGRATION.md`](docs/DJANGO_PRODUCTS_MIGRATION.md)。
- 2026-09-03，本机运营事务“新品上新”子域已完成 Django/PostgreSQL 正式单写切换、D1 终态退役和新品 R2 路径下线，cutover ID 为 `workflow-pg-20260902T110500Z-bdfebd254007`。PostgreSQL 是新品项目、目标店铺、七阶段、活动、revision、幂等回放和新品读写的唯一权威；React 页面、全局搜索和 AI 有界只读工具保持原入口，薄 Worker 将读写固定转发到 `127.0.0.1:8061/8062`。12 条旧新品记录已迁为 12 个项目、12 个目标、84 个阶段和 38 条活动，缺失的供应商、编码、店铺与阶段事实均显式保留为待补信息。旧 D1 `launch` 事实已清除，authority 已变为空 tombstone view，3 个永久 guard 拒绝复活；新品 R2 候选命名空间为空且生产源码不再可达。切换已跨过 PNR，不得恢复 `legacy`、D1/R2 新品路径或双写。正式证据见 [`docs/DJANGO_WORKFLOW_MIGRATION.md`](docs/DJANGO_WORKFLOW_MIGRATION.md)。
- 2026-09-04，本机运营事务全板块已完成 Django/PostgreSQL 正式单写切换与 D1 终态退役，operations cutover ID 为 `workflow-ops-pg-20260904T094000Z-7438caa33f18`，authority epoch 为 `5ab6bed6-df07-4585-84d9-f650e0855ef1`。正式 run `workflow-ops-7438caa33f189924efd6bdbc04192660` 精确迁移并复验 35 个工作任务、1 条评论和 34 条活动；当前提醒、模板、关联、附件、清理队列、巡店/评价记录及其活动为 0。现有 React 六个 tab、公开 API、AI、全局搜索与库存执行事项保持原入口，Worker 只保留真实鉴权、scope、HMAC、R2 附件字节和薄适配，全部业务事实、状态、审计、revision 与附件元数据统一进入 `127.0.0.1:8061/8062` 的 Django/PostgreSQL。旧 D1 operations 对象已变为 14 个空 tombstone view，42 个永久 guard 拒绝复活；切换已跨过 PNR，不得恢复 D1/legacy/shadow、双写或反向迁移。附件字节仍保留在现有 R2 命名空间，当前迁移水位为 0 个附件；全局 D1/R2 binding 不删除。完整证据与恢复边界见 [`docs/DJANGO_WORKFLOW_MIGRATION.md`](docs/DJANGO_WORKFLOW_MIGRATION.md)。
- 2026-09-05，本机客服分析已完成 Django/PostgreSQL 正式单写切换、D1 终态退役和历史 `inventory-upload/` R2 前缀下线，cutover ID 为 `customer-service-pg-20260905T012130Z-5e02b476b398`。正式 run `customer-service-5e02b476b3984cb590f46fd11081c6d9` 精确迁移并复验 29,018 条会话、7 个批次和 1 个 scope head，源/目标摘要一致。现有 React 页面、公开 API、AI 与全局搜索保持原入口，Worker 只保留真实鉴权、scope、Excel/聊天解析、HMAC 和薄适配，客服读写固定进入 `127.0.0.1:8071/8072`。旧 D1 客服对象已变为 5 个空 tombstone view，18 个永久 guard 拒绝复活；R2 历史前缀对象、字节和 multipart 均为 0。切换已跨过 PNR，不得恢复 D1/R2/legacy/shadow、双写或反向迁移；全局 D1 数据库及 R2 bucket/binding 继续供其他现行域使用。完整证据与恢复边界见 [`docs/DJANGO_CUSTOMER_SERVICE_MIGRATION.md`](docs/DJANGO_CUSTOMER_SERVICE_MIGRATION.md)。
- 2026-09-05，用户、固定角色、仓库/渠道/平台数据范围与权限审计域已完成 Django/PostgreSQL 代码重构和当前 D1 权限水位的隔离 PostgreSQL 17 镜像迁移演练。公开 Worker 改为只通过 `127.0.0.1:8101/8102` 的签名 Django 接口解析实时权限，未知用户不再自动补建；管理写入具备原因、CAS、幂等回执、管理员不变量和追加式审计。隔离演练复验 1 个启用无限制管理员，源/目标摘要一致；生产 authority、D1 退役、运行服务与 Worker release 尚未切换，不能把本分支或演练 ID 解释为生产迁移完成。契约、演练证据和受控 cutover 门禁见 [`docs/DJANGO_ACCESS_CONTROL_MIGRATION.md`](docs/DJANGO_ACCESS_CONTROL_MIGRATION.md)。
- 运营事务新增“上新跟进”：由用户维护产品线展示名称、产品图和吉客云名称学习关键词，以吉客云货品代码为唯一归集键，按运行机器本地时区查看完整自然周销售、环比、退款、毛利与上新累计。产品线从监控开始日持续跟踪，可在列表暂停或启动；暂停项不进入周报预览、Excel 和发送截图。吉客云货品主数据导入后会触发代码学习，唯一命中自动补入，多义命中不自动归类。机器人配置集中在“系统设置 → 钉钉机器人”，上新跟进图片预览区提供直达入口；钉钉周报发送器在执行时动态唯一核验“志高助手”与“测试群聊”，只发送“新品销售周报”文案和 PNG 截图预览入口，并使用 PostgreSQL 投递账本阻止同一报告周重复发送。默认未激活的 n8n 调度模板为 `automation/n8n/new-product-weekly-dingtalk.workflow.json`，页面配置和生产调度默认关闭，启用须走受控发布流程。详见 [`docs/NEW_PRODUCT_WEEKLY_FOLLOWUP.md`](docs/NEW_PRODUCT_WEEKLY_FOLLOWUP.md)。
- 2026-09-03，本机库存管理板块已完成 Django/PostgreSQL 正式单写切换、D1 终态退役和库存 R2 路径下线，cutover ID 为 `inventory-pg-20260902T162501Z-c6f5c5af1d254c0d`。PostgreSQL 现为分仓库存、库龄、批次、导入控制、原始分片、备货计划、运营设置、revision、审计和全部库存读写的唯一权威；`127.0.0.1:8051/8052` 已加入受控开机启动链。现有 React 页面与公开 API 不变，商品经营投影、系统成本、AI 和全局搜索统一使用有界 Django consumer。正式迁移核对库存事实 1,085,958 行、库龄事实 275,669 行，最新 2026-09-01 快照分别为 22,586/5,539 行；旧 D1 库存对象已变为 6 个空 tombstone view，24 个永久 guard 拒绝复活，库存 R2 前缀为空。切换已跨过 PNR，不得恢复 D1/R2/legacy/shadow；完整证据见 [`docs/DJANGO_INVENTORY_MIGRATION.md`](docs/DJANGO_INVENTORY_MIGRATION.md)。
- 销售明细导入以表单提交的开始/结束日期作为权威替换边界，不查询运营库判断“缺哪一天”；即使新文件最后一天没有记录，也会清除该边界内已从新版本消失的旧行。默认仍把该日期范围内的销售台账视为完整业务集合；仅导入部分店铺时，调用方必须同时提交非空、去重、已纳入白名单的 `expectedChannels` JSON 字符串数组，文件中的渠道必须与声明范围完全一致，系统只原子替换“日期范围 + 精确渠道集合”，缺店、混入其他渠道或未注册渠道都会失败关闭。库存快照使用稳定的“仓库 + 货品”身份，单个文件出现重复身份时会拒绝导入，单纯调换行序不会新增库存事实。

## 数据导入工作区

数据导入工作区“文件导入、运行记录、链路规则”已于 **2026-09-11 在本机生产采用**，已移除数据连续性入口。运行记录展示各业务接口最近的导入批次；链路规则以 n8n 工作流为列、吉客云和店铺为行，展示配置及只读执行元数据提供的今天状态和完成时间。11 条链已接入本机 n8n 执行元数据，共用工作流显示整链结果。2026-09-12 已受控发布京东多店铺状态修复：该列绑定正式启用的 Chromium 静默下载副本，并区分定时完成与自动重试完成；生产页面回读四店共用执行 #1243 于本机 11:07 重试完成。状态来源不可用时显示无法核实，执行级成功不替代业务导入回查。数据范围与维护方法见 [运行记录和链路规则说明](docs/IMPORT_MONITOR.md)，原始板块采用见 [统一发布证据](docs/evidence/guangdong-replenishment-health-production-20260911.json)，本次修复见 [链路规则发布证据](docs/evidence/import-chain-rules-production-20260912.json)。

## 说明

本项目的主界面运行在 `http://localhost:3000`。

销售分析的“大毛利率”统一按 `(分摊后金额合计 - 货品成本合计) / 分摊后金额合计` 计算，不扣费用分摊；“订单毛利”仍展示导入明细的毛利合计，两个指标不得混用。

### 钉钉自定义机器人加签消息

按[钉钉官方自定义机器人文档](https://open.dingtalk.com/document/orgapp/custom-robot-access)的加签方式发送文本消息。使用环境变量传入 Webhook 和加签密钥，避免凭证出现在源码或命令行历史中：

```powershell
$env:DINGTALK_ROBOT_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=<token>"
$env:DINGTALK_ROBOT_SECRET = "<secret>"
npm run dingtalk:robot:send -- --text "hello" --dry-run
npm run dingtalk:robot:send -- --text "hello"
```

`--dry-run` 仅验证参数与加签流程，输出中会隐藏 Webhook Token；去掉该参数才会真实发送。

2026-09-05，本机 BI 看板已完成 Django 只读聚合生产启用。BI 通过独立 `teruisi_bi_reader` 和 `127.0.0.1:8081` 一次组合销售、ERP 主数据与库存的 PostgreSQL 权威数据，公开页面只调用 `/api/bi/overview`；它不复制业务事实、不设 writer，也不形成第二套业务 revision。生产采用 run 为 `bi-apply-1079734fb42842eeb1cb13b830bbb8a6`，恢复只依赖现有上游 PostgreSQL 权威和共享数据库备份，不存在旧 BI 后端回退。正式证据与运维边界见 [`docs/DJANGO_BI_MIGRATION.md`](docs/DJANGO_BI_MIGRATION.md)。

主界面左侧按“协同执行、经营分析、商品与供应链、系统与智能”分组：协同执行包含运营事务和自动化中心；经营分析包含 BI 看板、网店分析、市场分析、客服分析和销售分析；商品与供应链包含库存管理、商品经营和数据导入；系统与智能包含系统设置和 AI 助理。页面顶部只保留当前模块的全局页头与统一统计周期，分析模块切换后继续使用同一日期范围。模块、导入来源与统计周期同步到 URL 查询参数，可复制链接并使用浏览器前进/后退恢复；非法参数会回退到安全默认值。业务筛选支持多选并由接口按重复参数执行服务端筛选；任意已渲染表格的列标题还可打开列值多选，用于继续筛选当前已加载页。销售分析使用统一公共筛选组件与 URL 状态：平台、店铺、品类和货品条件在销售总览、渠道分析与品类分析之间持续保留并同步应用，财报分析继承其支持的平台和店铺，目标设置作为管理页不受分析筛选影响；平台变更会同步移除不属于所选平台的店铺，店铺身份始终按“平台 + 店铺”隔离，浏览器前进/后退也会恢复筛选。销售总览和品类分析统一按商品主数据品类（缺失时回退销售行品类，仍缺失时归入“未分类”）展示和筛选；品类候选使用 ERP 商品主数据全集与当前期间销售明细兜底品类的并集，因此“长龙洗碗机”等当期零成交品类也保持可选。品类分析另保留渠道和趋势粒度两项专属设置，汇总销售、退款、毛利、趋势、结构和排名；品类明细展示净销量、金额口径退货率、净销售额同比、截止日近 7 天对比此前 7 天的“环比上周”，以及最近 24 个有数据周期的品类趋势；趋势列后的“详情”按同一筛选和账号数据范围下钻到每个平台、店铺的净销售额、贡献率、净销量、退款和毛利数据。当前商品主数据仅提供一个品类层级，因此页面只开放一级品类，并在出现稳定的多级主数据后再逐级开放筛选与下钻。

运营事务提供“工作计划、巡店检查、评价维护、新品上架、上新跟进、变量配置”六个工作区。工作计划使用真实持久化任务数据，展示进行中、已逾期、今日到期、已完成和合计指标，并按状态、紧急程度和跟进人形成可视化概览；支持服务端关键词、状态、紧急程度、工作类型、跟进人、店铺、来源和截止日期筛选，筛选候选由服务端统一返回，CSV 下载会分页获取并导出完整筛选结果。快速录入复用标准工作类型、店铺、责任人与来源候选，同时保留表格/时间轴、版本化编辑、删除确认、评论、活动、提醒、业务关联和附件能力；附件通过受控下载地址读取，删除失败由清理队列续处理。巡店与评价记录继续按原状态流转并保留版本与活动历史。新品上架使用已正式切换的结构化 React 工作区和 Django/PostgreSQL 单写领域，覆盖供应商/货品编码/图片/定价、多平台多店铺目标，以及“建模、分析定价、图片、视频、上架、备货、上新复盘”七阶段的负责人、截止日、阻塞、证据和活动审计，并提供横向阶段矩阵、状态看板、全局搜索与 AI 有界只读投影；生产模式必须保持 `django`，旧 D1 新品记录不再作为 fallback。上新跟进按吉客云货品代码归集销售，产品线持续跟踪且可暂停/启动；图片周报从 2026-08-03 所在周开始持续累积“品牌、产品图、产品名称、趋势、周净销量”列，页面和 Excel 保持相同蓝色调与趋势图，并可直达“系统设置 → 钉钉机器人”配置独立设置。自动任务将同版式 PNG 上传钉盘后由固定机器人仅发送周报文案和截图在线预览入口，凭据不进入业务库。变量配置页提供真实模板的新增、编辑、停用和套用，只有 `operator`、`admin` 可修改。逾期、今日到期和模板日期统一按 `Asia/Shanghai` 判定；新品切换与恢复边界见 [`docs/DJANGO_WORKFLOW_MIGRATION.md`](docs/DJANGO_WORKFLOW_MIGRATION.md)。

顶部全局搜索覆盖主要业务域，结果携带经过服务端校验的模块与子视图目标；点击后写入同一套 URL 导航状态，刷新、前进和后退均可恢复。搜索和列表都在服务端分页并限制响应体积，旧请求会被取消且迟到响应不能覆盖当前关键词。AI 助理与客服分析已经从首屏入口拆为独立按需模块；AI 助理下设可深链接的 AI 对话、Agent 工作流、全局记忆、分析沙箱、AI 空间和 AI 管理六个工作区。每个业务页面都可以把白名单页面上下文带入对话，并通过中央注册表调用复用领域服务的有界只读工具；页面文字本身不作为身份、权限或数据事实。全局记忆仅由 owner 明确确认写入，以低信任、请求内数据召回；分析沙箱只执行白名单数据集上的确定性 JSON AST，不运行任意 Python、JavaScript、SQL 或网络代码。正式 Agent 长任务与正式多 Agent DAG 已开放，并继续保留 dry-run 和人工复核；创建时固定模型版本和工具策略，每轮重验 owner、角色、scope、模型版本与工具摘要。Agent runner 每个微步最多派发一次 provider 或执行一次注册表只读工具，provider/tool 派发均有持久 ledger；外部结果未知时失败关闭且不自动重试。详细架构与当前边界见 [`docs/AI_PLATFORM_ARCHITECTURE.md`](docs/AI_PLATFORM_ARCHITECTURE.md)。AI 会话、消息、导出产物、图片任务与私有图片资产绑定创建时的数据 scope，账号 scope 收紧后旧的越界内容会失败关闭；AI 对话和图片生成请求均携带稳定客户端幂等键，已进入可能付费派发的请求不会因页面重试而再次调用供应商。AI 空间当前是文本生成电商视觉草稿的治理型 MVP，仅开放商品主图、卖点详情和活动视觉，不读取真实 SKU 参考图，也不自动发布；所有产物都标记为 AI 草稿并要求人工复核。图片生成模型与文本/视觉识别模型独立配置，后台用持久任务、派发 receipt、租约 fencing、模型版本栅栏和 PostgreSQL 私有资产/字节存储执行；账号撤权、scope 收窄、模型变化或派发状态不确定时均失败关闭。AI 模型、渠道和图片模板配置只允许无数据范围限制的管理员读取或修改，列表只返回掩码或脱敏地址。AI 管理当前覆盖配置与中央只读工具目录，图片模型/模板具备版本控制和追加式审计；对话模型/渠道的同级 CAS 审计以及供应商币种单价换算、人民币费用仪表盘尚未实现，不能把它解释为完整成本管理平台。

正式 Agent 与非 dry-run 多 Agent DAG 的代码入口现已开放；executor 不运行任意 Python、JavaScript、SQL、浏览器自动化或运营写入，只能调用中央注册表明确允许的有界只读工具。任务、执行 ledger 与审计由 Django/PostgreSQL AI 域管理，后续结构变更使用 Django migrations 并走受控发布。历史 `drizzle/0087_ai_agent_executor.sql` 仅保留为隔离迁移/测试材料，不再是正式部署步骤；迁移与生产重启不会由页面自动执行。

2026-09-01，本机市场分析后端已完成 Django/PostgreSQL 正式单写切换、完整垂直链路验收和 D1 终态退役，cutover ID 为 `market-pg-20260901T112221Z-a687294e320f`。PostgreSQL 现在是市场事实、批次、价格、标注、图片缓存元数据、任务、revision、幂等/尝试审计、网店投影及全部读写的唯一权威；独立最小权限 reader/writer 固定监听 `127.0.0.1:8031/8032` 并已加入受控启动链。现有 React/Next.js 市场页面保留，公开 Worker 只负责真实鉴权、签名、边缘解析/图片能力、体积与超时边界和有界转发。两笔历史 `processing` 批次及 8,000 条未发布 staging 已在 PNR 前受控处置；旧 D1 市场对象现为 49 个空 tombstone view，9 个共享表 guard 永久拒绝市场域复活。恢复只允许 PostgreSQL 备份/WAL/PITR、兼容修复或经审批的前向修复。正式证据与恢复边界见 [`docs/DJANGO_MARKET_MIGRATION.md`](docs/DJANGO_MARKET_MIGRATION.md)。

市场分析的商品榜单首屏只从服务端读取并富化 20 条，页面底部可继续按 20 条一批加载；分页总数、筛选和缓存均在服务端按同一查询身份处理，全局筛选项使用独立的版本化持久缓存，用户停留在其他板块时还会空闲预热当前周期的市场首屏。切换筛选时会取消旧请求，避免迟到的下一批混入当前结果；再次进入市场分析时先显示当前会话缓存，再在后台校验最新结果。行业汇报仅在进入对应工作区时汇总，SKU 数据库和 AI 标注继续使用各自的服务端分页；“系统和 AI 设置”不再呈现或读取完整市场 SKU 目录，避免目录查询影响工作台加载。市场主数据、品牌和价格管理后台已从默认榜单模块拆成独立按需资源，普通榜单浏览不会提前下载管理运行时代码。

市场分析的 SKU 主图标准售价经 AI 识别、人工复核并正式入库后，后续月份若三级类目、榜单 scope、SKU 维度、SKUID 和图片均一致，系统会直接沿用历史分类和标准价，不再调用 AI 或重复批量入库；同一业务身份的 SKUID 更换图片时，只沿用仍在当前字典中的人工确认分类，并使用精简的价格专用视觉识别重新读取新图价格；新 SKUID 才执行分类和价格的完整图片识别。商品榜单识别到正式市场定位价格（主图价）后，该行成交均价按同一主图价展示；未识别到主图价时才按成交数据计算。

人工复核到批量入库期间若榜单身份、月份价格快照或主图内容版本发生变化，系统会拒绝把旧候选写入正式数据，并在报错处提供“重建这条失效候选”。原候选保留为审计证据；同一 SKU 换图时，新候选沿用仍有效的人工细分类目，只重新识别新图价格，完成后再人工复核入库。

市场分析的“行业汇报”提供商用直饮机核心口径预设，按最近 12 个完整自然月锁定商用净饮水设备、整体 SKU 榜单和六个核心细分类目。汇报包含规模与月度趋势、商品进出、价格带、品牌份额及 CR3/CR5、自营与 POP、流量转化象限、标题卖点和机会矩阵；所有结论均明确限定为“当前 TOP 榜单覆盖市场”。评价、问大家、搜索词、真实服务履约、成本利润和合规准入仍作为待补充数据单独披露。

市场图片 AI 识别优先使用系统已验证的 R2 图片缓存，缺失时才回源京东图床。超大模型输入图会在不改变原图哈希和人工复核原图的前提下，自动缩放到最长边 1600 像素并编码为 WebP；只有结果确实更小时才采用，转换不可用、失败或结果无效时自动回退原图，成功的衍生图按原图哈希缓存到 R2；旧缓存缺少衍生图时会首次按需补齐，后续直接复用。SKU AI 标注的模型并发数可在 1–50 之间按“三级类目 + 执行器”独立记忆；云端默认 10、建议 10–20，过高可能触发供应商限流并计入失败，本地 Ollama 默认且建议 1。新任务配置和当前任务控制分区展示，当前任务即使正在运行也可单独保存，后台会在下一批即时应用新的并发数。点击开始只负责快速、幂等地把任务登记到 Django 持有的市场队列，Cloudflare 每分钟的原生定时触发只负责唤醒该队列；已经运行且仍有可重试推理单元的任务可再次安全唤醒，但不会清空协调租约、自适应并发或冷却状态；已经完成推理的任务会明确提示创建下一批，不会把空队列伪装成恢复成功。自适应并发、冷却时间和协调租约全部持久化在 PostgreSQL 市场域；本地 Ollama 仍依赖本机 runner。每个任务最多容纳 10,000 条候选；待处理量超过上限时，上一批进入人工复核后可继续创建下一批，等待复核或入库的旧批次不会占用可运行任务槽。每次模型 HTTP 请求只处理一张图片，服务端按同一类目配置原子限制有效推理租约；同一任务中业务身份与图片哈希相同的跨月记录只领取一次推理，结果批量扩散到全部月份，重复创建同一仍有推理工作的任务会恢复已有任务。视觉模型单次调用最多等待 90 秒，启动页面请求最多等待 110 秒，推理租约为 3 分钟；单次模型超时或网络异常只会温和降低运行并发，并让触发异常的通道从 5 秒短冷却开始，其他通道继续处理图片；同一冷却窗口的并发失败只算一次，重复异常才继续阶梯降级且普通异常冷却最多 30 秒。供应商 429 限流会让所有通道按减半并发和 60 秒至 5 分钟全局退避。连接每连续成功 3 张就增加一路，直至恢复该类目的配置值；并发已经降到 1 后，如果没有任何成功图片又出现 3 个独立失败窗口，后台会自动暂停并显示脱敏原因，不再无限重试。豆包 Seed 系列在该严格 JSON 分类/抽取任务中关闭额外思考，完整分类输出上限为 600 tokens，仅价格识别为 320 tokens。每张图的取图、图片处理、模型调用、总耗时和输入大小都会落库，页面展示最近 100 张平均值，用于区分取图/压图瓶颈与模型供应商推理瓶颈。

“系统和 AI 设置”中的待 AI 标注总量按类目、榜单 scope、SKU/SPU 维度和商品编码去重，并互斥拆分为：同图直接复用、新图仅识别价格、完整分类和价格、暂不可自动识别。一个商品身份跨月份命中多种路径时按“完整识别 > 仅价格识别 > 同图复用”归类；无图、非 SKU、有效 Prompt 缺失或识别失败封顶进入阻塞项，四项合计始终等于待 AI 标注总量。因此该总量不是一次可创建的模型调用数；类目卡片的“可新建”数量与当前任务的“剩余推理”才决定创建或恢复按钮的行为。

## Django 后端渐进迁移方向

2026-08-27 确认 Django 为后端长期目标框架后，本机按业务域逐步完成迁移，并于 2026-09-06 完成聚合入口切换。当前所有结构化业务均以 Django/PostgreSQL 为权威，现有 React/Next.js 前端与薄 Worker 保留；所有新增后端业务能力使用 Django，每个业务范围只保留一个权威写入后端。下文按日期保留各域历史采用记录，不能将其中当时的其他域状态或 D1 用途解释为当前生产依赖。

2026-08-29/30，本机销售域已完成 Django/PostgreSQL 终态单写切换。销售事实、批次、导入幂等与尝试审计、上传/暂存元数据、revision、查询和分析均以 PostgreSQL 为唯一权威来源；公开 Worker 仅负责真实鉴权、principal HMAC、Excel 解析、分片请求边界、请求超时与体积边界和边缘协议适配，销售分片字节与生命周期也由 PostgreSQL 原子管理，不再读写 R2。任何销售读写故障都失败关闭，不存在销售 D1、R2、`legacy` 或 `shadow` 回退路径。D1 中的销售事实、批次、上传、缓存、投影 outbox 和 authority 对象已由受控 `0092_sales_domain_retirement.sql` 退役；只读 tombstone view、retirement receipt 与共享表永久写入 guard 是防止旧代码复活的终态证据，不是仍在运行的销售后端。ERP 主数据现由独立 Django/PostgreSQL 域维护；它唯一允许的跨域写权限仍只是按 ERP 映射更新既有 `sales_order_lines.resolved_category` 派生分类，不能新增或删除销售事实，也不能修改金额、成本、销量、`gross_profit`、其他销售字段或批次。全局 R2 binding 仍供市场图片、运营事务附件和其他现行范围使用，不得因销售、库存或 ERP 下线旧 R2 路径而删除。

本机 cutover ID 为 `sales-pg-20260829T204417Z-d9896e904d8092cb`，`0092` SHA-256 为 `f981a62efd0515a7f64dd9f174151b8cfeb0c4b071d8236c481b5459761a3b8f`。切换快照记录为 572,015 条销售事实、88 个销售批次、8,443 条 ERP 参照与 revision `8:5`；这些只是该次验收水位，不是当前常量。PostgreSQL、Django reader/writer 仍只监听 `127.0.0.1:5432/8001/8002`。Worker bootstrap current/authority 是不可变切换证据，后续 release 只能通过受控的 append-only successor 链前向发布，不得覆盖旧 release、attestation 或 forward-recovery。该结论仅适用于当前 Windows 主机和销售域，不代表远程生产、高可用或其他业务域已经迁移。迁移、运行、验证、备份审计和恢复边界见 [`docs/DJANGO_SALES_MIGRATION.md`](docs/DJANGO_SALES_MIGRATION.md)。

后续业务域复用销售基础时必须遵守 [`docs/DJANGO_DATA_IMPORT_ARCHITECTURE.md`](docs/DJANGO_DATA_IMPORT_ARCHITECTURE.md)。财务分析虽然位于“销售分析”页面内，但仍是独立数据所有权范围；其迁移不得修改销售 authority、销售事实、ERP 主数据 authority 或其他模块运行状态。PostgreSQL 的不停服日常备份、独立端口恢复演练和受控保留规则见 [`docs/DJANGO_POSTGRES_OPERATIONS.md`](docs/DJANGO_POSTGRES_OPERATIONS.md)；Django runtime 的崩溃恢复、desired-state fencing 和主动健康告警见 [`docs/DJANGO_RUNTIME_SUPERVISION.md`](docs/DJANGO_RUNTIME_SUPERVISION.md)。

2026-08-31，本机月度财报已完成 PostgreSQL/Django 正式单写切换，未改动已经完成的“销售分析 → 财务分析”前端模板。独立 `finance_reader`/`finance_writer` 固定监听 `127.0.0.1:8011/8012`，`TERUISI_DJANGO_FINANCE_MODE=django`；财报事实、导入幂等与审计、经营目标、读取和全局搜索中的财务来源均以 PostgreSQL 为唯一权威。正式迁移核对了 3 个完成批次、19 个月和 40,233 条财报行，cutover ID 为 `finance-pg-20260830T194437Z-184fdca41051401f`。切换已跨过 PNR，D1 财务对象只作为永久写保护下的审计材料保留，不得重新承担读写或回滚；恢复仅允许 PostgreSQL 备份恢复、兼容代码或审批过的前向修复。销售总览、渠道、品类、销售导入、ERP 主数据和其他模块的权威边界均未改变，正式证据与恢复边界见 [`docs/DJANGO_FINANCE_MIGRATION.md`](docs/DJANGO_FINANCE_MIGRATION.md)。

2026-09-01，本机网店域已完成 Django/PostgreSQL 正式单写 cutover。独立 `netshop_reader`/`netshop_writer` 固定监听 `127.0.0.1:8021/8022` 并已加入受控启动链；PostgreSQL 是网店事实、批次、推广、上传、revision、审计和全部读写的唯一权威。现有 React/Next.js 网店前端保持不变，公开 Worker 只保留鉴权、签名、解析、allowlist 和有界转发；市场域通过 Django netshop consumer 将有界兼容投影原子写入 PostgreSQL 市场表，不形成网店第二事实源。旧 D1 网店事实路径已经终态退役，不能作为 fallback 或回滚来源；完整证据与恢复边界见 [`docs/DJANGO_NETSHOP_MIGRATION.md`](docs/DJANGO_NETSHOP_MIGRATION.md)。

2026-09-04，本机运营事务已在既有新品项目切换基础上完成全板块 Django/PostgreSQL 正式单写 cutover。独立 `workflow_reader`/`workflow_writer` 固定监听 `127.0.0.1:8061/8062` 并加入受控启动链；PostgreSQL 是工作计划、协作、提醒、模板/变量配置、附件元数据与清理、巡店/评价、结构化新品项目、多店目标、七阶段、产品线、周报配置、投递账本、revision、request receipt 和全部运营事务读写的唯一权威，生产模式固定为 `django`。既有新品 cutover `workflow-pg-20260902T110500Z-bdfebd254007` 与本次 operations cutover `workflow-ops-pg-20260904T094000Z-7438caa33f18` 作为双 authority 共同核验。旧 D1 运营事务事实已清除并由空 tombstone 与永久 guard 保护；附件字节仍由薄 Worker 在 R2 管理，但对象元数据和清理状态只以 PostgreSQL 为准。全局 binding 未删除，完整证据与恢复边界见 [`docs/DJANGO_WORKFLOW_MIGRATION.md`](docs/DJANGO_WORKFLOW_MIGRATION.md)。

2026-09-03，库存管理的 Django/PostgreSQL 垂直链路已在本机正式切换。独立 `inventory_reader`/`inventory_writer` 固定监听 `127.0.0.1:8051/8052` 并加入受控启动链；PostgreSQL 是库存/库龄事实、批次、导入控制、备货计划、设置、revision、审计和分片的唯一权威。Worker 只保留鉴权、解析和薄适配，商品经营投影、系统成本、AI、搜索统一使用库存 consumer。旧 D1 库存事实和共享命名空间已由空 tombstone 与永久 guard 终态退役，库存 R2 前缀和生产访问路径也已下线；恢复只允许 PostgreSQL 备份/WAL/PITR、兼容代码或审批过的前向修复。完整生产证据见 [`docs/DJANGO_INVENTORY_MIGRATION.md`](docs/DJANGO_INVENTORY_MIGRATION.md)。

2026-09-05，本机 ERP 主数据域已完成 Django/PostgreSQL 全链路正式切换。独立 `erp_reference_reader`/`erp_reference_writer` 固定监听 `127.0.0.1:8091/8092`；货品、组合装、批次、scope head、内容指纹、导入尝试、分片、revision、迁移与审计均以 PostgreSQL 为唯一权威。生产 run `erp-reference-eb5fa9fc5cd0467ba58c9dc0a9c11b01` 复验 8,473 条货品、4,392 条组合件、83 个批次、58 个尝试、83 个指纹与 2 个 scope head，源/目标摘要一致。旧 D1 ERP 对象已由 `0110` 变为 7 个空 tombstone view，并安装 18 个永久 guard；旧 ERP bridge 和 `inventory-upload/` R2 路径已退出生产。cutover ID 为 `erp-reference-pg-20260905T102200Z-8a8dbcb59fc8`，切换已跨过 PNR；完整证据与恢复边界见 [`docs/DJANGO_ERP_REFERENCE_MIGRATION.md`](docs/DJANGO_ERP_REFERENCE_MIGRATION.md)。

库存总览固定使用销售最新截止日向前 30 个自然日的正向销量，退款不冲减备货需求；页面保留“库存健康明细（近30天）”这一张货品汇总表，不再重复展示顶部库存 KPI、质量暂停横幅和“销量近30天”表格。备货计划中的“近30天总销量”按货品跨仓汇总，部门预填“志高项目组”但允许留空，预计消耗周期可人工调整。备货计划页支持下载标准 Excel 模板并一次原子导入最多 200 个规格；导入按最新权威库存校验并回填商品数据，不自动调用钉钉。草稿计划可多选后一键顺序确认并提交到配置绑定的钉钉“备货管理”多维表；多选已确认计划仍可批量重试提交，任一阶段失败项保留勾选。群消息仍须先预览按采购和工厂分组的话术，再通过系统设置绑定的机器人发送到精确匹配的钉钉群；同一批消息有发送账本防重。多维表备注的新标记统一为“运营管理系统备货计划ID”，并兼容查询、更新历史 TERUISI 标记。分仓库存导入读取吉客云“规格默认供应商”，并使用 `config/inventory-warehouse-mapping.json` 中的仓库分类和“计入库存”标记。详细口径见 [`docs/INVENTORY_MANAGEMENT.md`](docs/INVENTORY_MANAGEMENT.md)。授权失效时应由管理员在库存服务所属 Windows 用户下为配置绑定的钉钉账号重新授权；核验表格访问后重试原计划，避免重建计划造成重复。DWS profile 显示 active 不能替代表格调用验证。

销售数据运维可见性使用只读 `GET /api/sales/data-health`：仅 `operator/admin` 且无数据范围限制的账号可读取，返回 Django/PostgreSQL 单写来源、动态 sales/ERP revision、上海业务日期、销售覆盖起止日、距当前业务日的机械天数、是否覆盖昨天及最近成功批次。该接口不自行定义“过期”阈值，不读取 runtime 文件或凭据，也没有改动销售/财务页面模板。

上新跟进表格预览支持生成带相同内容、蓝色调、产品图和趋势图的 Excel 工作簿；主数据品牌为空时按当前业务口径展示“志高”。钉钉机器人仍投递同版式 PNG 在线预览。

## 项目文档与长期信息

2026-09-17 **经营分析 v2 多 Agent 执行为候选代码**：首次工作台显式预览 v2 完整范围，支持最多 48 来源的分页采集、暂停恢复与封存，再手动启动分析。报告使用轻量引用，三个专业 Agent 并行后由独立复核和报告整合接续，每个 Agent 有独立目录读取证明。已接通持久多卷 HTML/XLSX 与完整清单校验下载。事实仍限 64 MiB/2000 页，v2 固定预算、自动身份匹配和真实经营验收继续开发。旧请求按原协议恢复；未合入 main、生产迁移或部署。详见 [阶段进度](docs/AI_BUSINESS_ANALYSIS.md) 和 [候选证据](docs/evidence/ai-business-workbench-v2-candidate.json)。

多 Agent 深度经营分析按“数据基础 → 协同闭环 → 跨域诊断 → 工程级文件 → 工作台验收”推进，涵盖店铺、品类、SPU、SKU、关键词、同比环比、市场与 B 端。候选工作台支持输入问题及精确范围、完整来源预览、后台采集、刷新找回和手动启动分析；范围尚需显式选择，超限或不支持的来源不会被自动删除。已支持固定证据预算情景、新版本重新分析、持久 HTML/XLSX 生成及校验下载；HTML 可离线试算，Excel 附参数与公式联动页，超精度结果明确留空，原报告保留。完整来源容量、自动理解并匹配范围和真实业务验收仍待完成。开发进度、数据口径和未完成项见 [实施说明](docs/AI_BUSINESS_ANALYSIS.md)；开发代码不代表已经生产采用。

候选报告页已接入后台 HTML/Excel 双文件生成，支持查看进度、暂停、恢复、取消和下载完整性校验。文件复用同一封存证据及诊断，生成无需保持页面打开；正式文件须先人工复核。首次任务提交按账号保存完整参数和请求编号，未知回执可跨刷新确认同一次提交。该能力尚未部署，完整自然语言规划、来源容量扩展与真实业务验收仍在开发。

容量扩展候选底座新增独立来源目录和逐卷 HTML/Excel 渲染，按完整来源、连续行片段与文件摘要核验交付。第二十一批接通首次 v2 工作台；19 来源合成任务从预览、真实领域读取器采集、五 Agent 流程及复核接口到 156 张表、两卷文件已贯通，模型文案使用夹具。超限仍完整展示并拒绝创建，旧任务及备份版本保持兼容。第二十五批新增大规模商品关联只读接口，已通过 5001 行完整采集封存及每源 30001 行纯计算；报告集成继续开发。合成验收不代表真实数据或模型决策效果通过。详见 [容量实现与后续依赖](docs/AI_BUSINESS_ANALYSIS_CAPACITY.md)。

第二十二至二十四批候选代码接通固定预算参数、专用多 Agent 协议、完整读取证明、调用前容量预检和多卷预算交付。工作台可以从封存数据逐页选择真实目标，明确填写参数并免费试算，再手动启动模拟或正式分析；金额和业绩假设不默认填写。修改参数须创建新版本，旧报告保留；最多 100 个目标不保证模型能够容纳，超限在模型调用前拒绝。未知提交可跨刷新确认原请求，新迁移仅在隔离环境验证，未用于正式数据库。

第二十六至二十七批候选接通固定 ERP 商品关联计划、SKU/SPU 映射及同比环比，集成报告可以同时使用原推广预算。五个 Agent 独立验证读取记录，引用数值从完整封存证据重算；文件包含完整关联、汇总、比较及来源证明。0022 不新增表，隔离升级与独立恢复保留旧 63 表摘要。旧工具、报告和文件保持兼容，工作台关联选择仍在下一批接入。此为候选开发进度，真实经营验收及生产采用尚未完成，见 [集成协议](docs/AI_BUSINESS_ANALYSIS_INTEGRATED.md)。

第二十八批候选已在工作台接通显式商品关联选择，普通报告和固定预算报告均可使用。完整目录核验后才可选择；重读失败或同任务版本变化不会静默丢弃关联意图，未知提交保留原参数和请求编号。组件与新旧工作台浏览器共 52 项通过。自然语言范围建议、真实身份选择、独立店铺总览、长期恢复和真实经营验收按 [剩余工作清单](docs/AI_BUSINESS_REMAINING_ACCEPTANCE.md) 继续。

第二十九批候选在一次完整报告复核内复用已核验的分析页与预算计算，仍独立检查五个 Agent 的读取证明。第三十批新增精确网店来源选项 GET，只读取成功导入元数据，签名分页绑定账号和版本；历史日期不表示事实完整覆盖。工作台接入、ERP/市场选项及全表异常筛查继续开发，详见 [实施进度](docs/AI_BUSINESS_ANALYSIS.md)。

第三十一批候选完成七类异常规则的全表纯计算底座，明确披露扫描、命中、候选遗漏和日期覆盖；18 项新增测试与18项旧回归通过。结果仅为未发布计算，不具有来源授权或 Agent 阅读证明，尚未接入运行时，见 [筛查设计](docs/AI_BUSINESS_DIAGNOSTIC_COVERAGE_DESIGN.md)。

第三十二批候选工作台已可从历史导入中分页选择精确网店来源，添加前核验当前账号与目录版本；同店仅合并明确选中的数据集，不改日期、ERP 渠道或市场条件。52 项 Node 与 68 项新旧浏览器检查通过；自然语言匹配、跨域来源目录及真实业务验收继续开发，见 [来源选择证据](docs/evidence/ai-business-source-picker-candidate.json)。

第三十三批候选已将固定完整筛查计划接到真实封存流，分别披露请求覆盖、扫描完成和日期完整；超容量完整拒绝，分页实时复验账号权限。62项纯测试与18项隔离数据库新旧报告测试通过，尚未接入Agent运行与文件交付。原参考文件及新增诊断质量门禁见 [参考质量对照](docs/AI_BUSINESS_REFERENCE_QUALITY.md) 和 [六张验收卡](docs/AI_BUSINESS_DIAGNOSTIC_ACCEPTANCE.md)。

第三十四批候选补充京东推广计划/单元/匹配方式三种纯派生视图，保留缺身份核查桶及完整两期核对，32项新旧测试通过。天猫合并后的商品日数据不能还原计划明细；新视图尚未接入Agent和报告文件，见 [设计及限制](docs/AI_BUSINESS_PROMOTION_VIEWS_DESIGN.md)。

- `README.md` 维护面向使用者的当前系统说明、启动方式、主要能力和必要限制。
- `AGENTS.md` 维护开发、数据处理、自动化和 AI 协作时必须遵守的业务口径与工程规则。
- 本项目不再使用外部 Obsidian 作为项目记忆。只有长期、稳定、可复用的信息才写入上述两个文件；临时运行结果和敏感数据不写入。
