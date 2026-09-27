# 小特 pandas 容器分析工具

## 状态与使用方式

`run_pandas_analysis` 已于 **2026-09-11 在本机生产上线**，保留既有 JSON AST 分析沙箱。网页对话、钉钉对话和 Agent 中央工具目录已接入，分析员、操作员和管理员可调用；viewer 不可调用，通用 MCP 暂不开放。当前 Worker 为 `20260911T081342Z-6b7a6a81082f9a5b`；真实容器、镜像 PostgreSQL、正式工具链与审计验收通过，完整记录见 [生产采用证据](evidence/pandas-sandbox-production-20260911.json)。未调用付费模型或发送真实钉钉消息，不能据此声称所有模型自动问数场景都已验收。

用户可以向小特提出“把两个数据集按货品编码关联，按供应商汇总”“按日透视这些商品的表现”等问题。小特先通过 `describe_system_datasets` 取得实际 ID、字段、单位和 querySchema，再用工具生成一次临时分析。业务写入仍通过现有对应模块完成。

工具参数：

- `inputsJson`：1 至 3 项的 JSON 数组字符串，每项有 `name`、`dataset`、`query`，可选 `collection`。别名为小写英文，不能重复；collection 是实际响应中的记录数组路径，逐行数据固定 `rows`，分析数据默认 `items`，支持如 `trend.items` 的路径。
- `code`：Python 代码，`pd` 是 pandas，`frames['别名']` 是本次导出的 DataFrame。把最终表格赋给 `result`；Series 会转成表格。不通过 print 返回结果。
- 输入不能指定宿主文件、挂载、URL、连接、SQL、凭据、代码运行参数或自行上传的数据行。允许代码在隔离容器内使用 Python；容器内临时文件与内存全部是一次性的。

仅说明代码约定的合成示例（dataset/query 必须由实际目录发现，不复制虚构 ID）：

```python
sales = frames['sales'].copy()
sales['amount_cents'] = pd.to_numeric(sales['amount_cents'], errors='raise')
result = sales.groupby('product_code', as_index=False)['amount_cents'].sum()
result = result.sort_values('amount_cents', ascending=False).head(20)
```

金额默认沿用字段的人民币分口径，不自动乘除 100；净销售额保留负退款。大毛利率、正向销量、库存窗口、刷刷仓排除、精确平台/店铺身份及 TOP 榜单覆盖等继续遵守 `AGENTS.md`，代码生成不能另定义业务事实。

## 数据导出与完整性

应用层复用 `ai_assistant.datasets.query`，每页都通过中央工具目录、当前真实 principal、字段白名单、角色/scope、水位与查询审计，不给 AI 账号添加业务表权限。219 个逐行数据集继续只允许无限制管理员，私有 AI 行仍按 owner 过滤。

逐行数据从第一页开始，在限额内沿签名游标连续读取。重复游标、空页仍有后续、孤立游标、文本字段截断或未读完的单元格均失败关闭。分析数据接口保留各自原契约，只接收显式证明完整的记录集合；不猜分页方式，也不把 TOP/单页结果当全量。输入字段目前须为标量；需要嵌套数据时先选择可导出的标量列。

每个来源返回查询、来源域、查询起止时间、水位、业务截止日期、导出行数、页数和内容 SHA-256。只将清洗后的数据行与代码通过 stdin 送入容器；principal、来源查询、签名密钥及业务连接不进入容器。stdin 不提供宿主文件路径或可写共享输入卷。

`complete=true` 只表示该查询按当前源契约导出完毕，不代表日期覆盖无缺口、跨页/跨域原子快照或完整行业市场。逐行查询是 `live_per_page`，可能跨业务更新；回答必须披露这一限制。分析前后重新核验账号，停用、角色变化或范围收窄时拒绝返回结果。

## 配额与回传

| 项目 | 固定上限 |
| --- | --- |
| 数据集数 / 总输入行数 | 3 / 2000 |
| 每数据集分页 | 20 页 |
| 导出网络预算 | 8 秒 |
| 数据与代码输入 | 2 MiB，代码最多 16000 UTF-8 字节 |
| 容器计算 | 8 秒；创建/检查/计算合计预算 14 秒，清理另有 6 秒 |
| 容器资源 | 1 CPU、512 MiB 内存、无额外 swap、64 个 PID |
| 临时空间 | `/work` 32 MiB、`/tmp` 16 MiB、共享内存 16 MiB |
| 输出 | 100 行、20 列、24000 UTF-8 字节，单文本单元格 1000 字符 |
| 并发 | 每 AI writer 进程 1 个 pandas 请求，独立 broker 全局串行 |
| 单次对话工具预算 | 1 次，整个工具仍为 30 秒 |

超限拒绝整个结果，不静默截断。调用方应缩小筛选、选择所需字段、优先使用业务聚合数据。不同 native 分析工具可能有更小的源上限。

结果以被动表格回到对话，可形成原有 owner/scope 隔离的表格/CSV 产物。该产物沿用最多 50 行、12 列与安全字段过滤，有截断时仍显式标记。计算结果须按源口径复核，容器隔离不保证模型编写的公式正确。

中央审计只存代码/参数摘要、状态、耗时、行数与结果摘要。Django 原有私有 receipt 保存结果供精确请求回读，对话可能保存模型工具调用及结果，继续受原有 owner/scope 规则约束；本工具不承诺对话中绝不存代码。broker 仅保存防重 nonce 和时间，不保存原始数据或代码，也关闭容器日志。

## 隔离边界

执行服务在 `backend/pandas_runner/`，独立于 Django 启动，不加载 Django、数据库环境或凭据。固定监听 `127.0.0.1:8121/v1/pandas`，双向 HMAC 绑定版本、路径、请求时间、nonce 和正文摘要。签名密钥与业务/edge 密钥独立；请求有 60 秒时钟窗口、持久防重账本、10000 条日内限额和单进程文件锁。签名响应必须绑定配置中的精确镜像 ID，并证明清理已完成。

仅支持 Linux 非 root 服务账号和该账号的 rootless Docker Unix socket，要求 cgroup v2 + systemd、内存/交换/PID/CPU 支持及默认 seccomp。镜像必须是本地 `sha256:<64位摘要>`，禁止拉取、可变标签、镜像声明卷和改变入口。容器内启动器在读取数据前再次检查实际 cgroup CPU、内存和 PID 配额。

容器固定 `network=none`、非 root、只读根文件系统、`cap-drop=ALL`、`no-new-privileges`、私有 IPC、无宿主挂载、无 Docker socket、无业务环境变量、无额外设备。临时 tmpfs 使用 noexec/nosuid/nodev；Python 仍可解释临时文件，安全边界是容器而非这些挂载标记。启动器中的 `exec` 只存在于容器镜像内，不在 Worker、Django 或 broker 中执行。

成功、代码失败、超时、输出洪泛等路径都必须精确删除本次容器，并通过 Docker 再查证明不存在。清理不确定时抑制结果并禁止该 broker 继续接任务；重启时发现历史同命名空间容器也拒绝启动，由操作员核验后处理。无自动降级至宿主 Python、无自动重放未知结果、无新业务 writer。

Docker 参数及 rootless cgroup 条件依据官方文档：[运行容器](https://docs.docker.com/engine/containers/run/)、[Rootless 资源限制](https://docs.docker.com/engine/security/rootless/tips/)、[默认 seccomp](https://docs.docker.com/engine/security/seccomp/)。容器仍共享 Linux 内核，需保持宿主、Docker、内核与已审查镜像更新；这是受控隔离，不是对任意恶意代码的绝对安全证明。

## 独立环境准备及受控采用

2026-09-11 已准备独立 `TERUISI-Pandas` WSL2 发行版：Ubuntu 24.04.5、systemd、cgroup v2、Docker 29.8.0 rootless、独立 UID 1000 服务账号。发行版存放于 `D:\teruisi-runtime\pandas-sandbox-wsl`；Windows 磁盘自动挂载和程序互通关闭，系统级 rootful Docker/容器服务被 mask，Windows→Linux localhost 签名请求已实测。仅启用账号私有 Unix Docker socket，不开放 TCP daemon。

1. 在隔离构建环境中审查并固定 Python 3.12 slim 基础镜像 digest，按仓库根目录作为 build context，使用 `containers/pandas-sandbox/Dockerfile` 构建。`PYTHON_BASE` 必须由操作员传入带 digest 的已审查引用。镜像固定 pandas 2.3.3 和 numpy 2.3.5；构建后记录实际镜像 ID 与构建材料摘要。运行阶段不安装包、不联网。
2. 为 broker 创建权限 0700 的状态目录、0600 的独立随机密钥及配置文件，属于独立 Linux 服务账号。broker 配置必须恰好包含 `docker`（绝对 CLI 路径）、`socket`（该账号 rootless Unix socket）、`image`（精确 sha256 ID）、`keyFile`、`stateDirectory`。不接受业务账号密码或生产目录。
3. 从独立服务安装目录的 `backend` 启动 `python3 -m pandas_runner.server --config <私有配置绝对路径>`。仅 broker 账号拥有 Docker socket 权限，Django 账号不拥有它；不要在生产 Django 进程内启动 broker。可参考下述用户 systemd unit，正式采用须纳入当前部署、状态和回滚流程。
4. 用专用测试配置运行 `TERUISI_PANDAS_TEST_CONFIG=<私有配置路径> python3 -m unittest pandas_runner.test_runner`，仅合成数据。真实容器检查涵盖 pandas 关联/中文/退款、网络和根文件系统限制、无业务密钥、超时、输出洪泛、内存限制及清理后再次正常执行。
5. 同时完成镜像 PostgreSQL 的真实角色、scope、审计与 receipt 集成回查、进程崩溃/清理核验及失败关闭演练。三项真实容器测试因环境缺失被跳过时属于未验收，不能视为通过。
6. 测试及必要复审通过后，才可按项目规则合并。生产采用另行授权：备份与独立恢复验证、部署 Django 新模块及 Worker 工具、为 Django AI writer 配置 `TERUISI_PANDAS_RUNNER_KEY_FILE` 与 `TERUISI_PANDAS_RUNNER_IMAGE`。Windows 密钥文件必须为 CurrentUser DPAPI 密文 JSON，结构为 `version=1` 与 `keyDpapiBase64`（无附加 entropy），仅授予运行身份读取 ACL；不允许明文密钥文件，不复制到 worktree、Git 或容器。同一随机密钥在 Linux 端按独立服务账号 0600 文件保护。该密钥仅用于 broker 通道，不是系统通用访问密钥。
7. `django-ai.ps1` 已接入独立 broker 管理：`ConfigurePandas -PandasImage <精确镜像ID>` 在 AI reader/writer 停止时，从固定 `D:\teruisi-runtime\pandas-sandbox\channel.dpapi.json` 导入独立 DPAPI 密钥，签名探针与合成计算通过后才创建配置。AI Start 建立带 PID/创建时间/命令行/fingerprint 的 WSL 保活进程并启动固定 user unit；AI Stop 先停止 writer，再正常结束 broker、回查零残留容器，最后停止精确保活进程。已配置后的 AI Status 包含 `PandasReadiness`，探针失败会影响 writer 就绪；`PandasCheck` 可单独检查。配置、镜像或密钥指纹不匹配拒绝复用进程；只有 writer 获得通道配置，其余 Django 域主动清空同名环境变量。配置不存在时其他业务保持既有行为。回退采用停止 broker、停用工具的前向兼容操作，不恢复 D1。

Linux 首次安装使用 `python3 -B -m pandas_runner.install_linux --image <精确ID> --approved-source-sha256 <package_digest()>`，独立 32 字节随机密钥仅从 stdin 传入。安装器仅接受固定非特权账号和精确来源摘要，按摘要创建 root 所有、不可写的 `/opt/teruisi-pandas/releases/<摘要>`，创建私有配置和 `/etc/systemd/user/teruisi-pandas.service`，不启动服务。已有安装拒绝覆盖；更新需准备新的已审查 release，并在 AI 停止期间受控切换 unit。`/v1/status` 是独立路径签名探针，返回镜像和源码摘要，不执行模型代码；Windows 必须核对与当前部署的源码一致。SIGTERM 等待本次有界请求及清理完成，超过 unit 的 35 秒停止预算或留有容器均失败关闭。

以下仅展示 user unit 的关键项；正式 unit 由安装器创建于 `/etc/systemd/user/`，路径绑定不可变 release，完整停止和输出策略以安装器为准：

```ini
[Unit]
Description=TERUISI pandas container broker
After=docker.service
Requires=docker.service

[Service]
WorkingDirectory=/opt/teruisi-pandas/releases/<approved-source-sha256>/backend
ExecStart=/usr/bin/python3 -B -m pandas_runner.server --config %h/.config/teruisi-pandas/broker.json
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
Restart=no

KillMode=mixed
TimeoutStopSec=35
```

broker 由 AI 控制器显式启动，不单独启用 user unit 的自启动。AI Status 和总控 AggregateStatus 会进行签名就绪检查；既有持续守护继续监测 Django 进程及 HTTP 就绪，本次不增加 broker 自动恢复或任务重放。沙箱不可用时工具失败关闭；通过 `PandasCheck`、AI Status 或总控状态定位后受控处理。

## 本次验证边界

隔离测试使用合成输入、临时 SQLite、固定 HTTP/模型协议夹具；未在宿主执行模型生成代码。已有数据集/AI 回归、双模型协议工具结果与私有表格、签名 HTTP、防重、权限收窄、字段截断/分页超限、清理失败抑制结果均覆盖。源码构建及后端边界检查通过。全仓 `tsc --noEmit` 存在其他既有文件诊断，需与当前 main 做同一配置差异核对，不能报告全仓类型检查通过。

候选验证计数及未执行范围见 [候选证据](evidence/pandas-sandbox-candidate-20260911.json)：83 项 Python 测试中 79 项通过、4 项跳过；41 项工具链与 20 项构建产物测试通过。类型诊断与 main 基线均为 146 项，无本次新增；Windows CurrentUser DPAPI 使用随机合成密钥完成往返与拒绝明文测试。

后续发布准备已补齐真实验证，见 [发布前证据](evidence/pandas-sandbox-pre-release-20260911.json)：164 项 AI 测试在独立 PostgreSQL 55447 端口通过，新增用例通过真实数据集 SQL reader 导出合成数据、签名调用真实容器，并回查 receipt、防重复执行、scope 拒绝和逐页审计；仅 edge 网络传输以直接调用所属 reader 的夹具替代。Linux 容器测试 13 项通过，2 项 Windows 专属测试在 Windows 单独覆盖；Windows 共 12 项通过，3 项 Linux 容器测试由前述环境覆盖。48 项相关 Node/PowerShell 测试、构建及后端边界通过。受控生命周期在独立 runtime 目录实测首次/重复启动、正常停止、零残留、停止后拒绝与重启。付费模型和真实钉钉消息仍未执行，不据此宣称所有模型自动问数场景已经验收。
