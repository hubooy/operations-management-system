# 京东店铺 SKU 下载和导出智能体

> 本文描述 `wares-jdm.jd.com` 的商品主数据导出与导入。京东商智商品明细 SKU 日数据请改用[《京东商智商品明细 SKU 日数据下载》](./京东商智商品明细SKU日数据下载.md)和 `$jdsz-product-detail-sku-daily` Skill。

## 目标

自动完成以下流程：

1. 打开京东商家后台商品列表。
2. 先执行“查询”，确认页面结果数大于 0，再进入“批量操作 → 导出查询商品”。
3. 选择“SKU 导出”。
4. 创建并等待导出任务完成。
5. 下载导出的 Excel 文件。
6. 将文件自动导入运营管理系统的网店分析数据。

京东页面：

<https://wares-jdm.jd.com/ware/wareList?activeTab=OnsaleWare&businessModel=0>

## 运行前准备

- Node.js 版本不低于 22.13.0。
- 已安装项目依赖。
- 专用 Chrome 浏览器已完成京东商家后台登录。
- 运营管理系统已启动，默认地址为 `http://localhost:3000`。

## 一键运行

在项目根目录 `D:\运营管理系统` 执行：

```powershell
npm run jackyun:ware-export
```

默认使用不带 `HeadlessChrome` 特征的普通有头 Chrome，在后台隐藏并以最小化方式运行。自动运行先从 `about:blank` 启动，在唯一受控页面导航前监听首屏 `queryValidProductList`；首屏响应为业务成功、正数总行数且页面精确回显后才进入导出，不再提前打开未接管的商品页，也不在首屏查询后补点“查询”。发现同一调试端口残留旧 `HeadlessChrome` 时，先关闭旧实例再启动后台有头实例；切换期间端口被其他进程接管则失败关闭。登录失效、验证码、安全验证、业务码 `601` 或店铺登录身份异常时，后台任务失败关闭并保留恢复审计，随后只打开当前店铺的独立可见 Chrome 供人工处理；程序不会在弹窗后自动重放“查询”“确定导出”或“下载”。`601` 只说明京东服务端拒绝了本次请求，不能只按无头、有头、CDP 或点击方式中的某一项归因，也不得用反检测或系统注入点击绕过。首次登录或主动排障可执行：

```powershell
npm run jackyun:ware-export -- --store-key <店铺键> --interactive-login
```

脚本默认等待京东任务最多 300 秒。下载成功后会自动调用：

```text
POST /api/netshop/import
```

导入参数固定为：

- 数据源：`jd_product_master`
- 平台：京东
- 店铺：所选注册表项的 `shopName`
- 快照日期：中国标准时间当天

## 常用参数

### 延长等待时间

```powershell
npm run jackyun:ware-export -- --task-timeout-seconds 600
```

### 继续已有任务或复用最近一次已完成任务

```powershell
npm run jackyun:ware-export -- --reuse-latest
```

### 跳过自动导入

仅下载文件，不导入运营管理系统：

```powershell
npm run jackyun:ware-export -- --no-auto-import
```

### 指定运营管理系统地址

```powershell
npm run jackyun:ware-export -- --base-url http://localhost:3000
```

也可以设置环境变量：

```powershell
$env:OPERATIONS_SYSTEM_URL = "http://localhost:3000"
npm run jackyun:ware-export
```

### 保存调试截图

```powershell
npm run jackyun:ware-export -- --debug
```

## 文件和审计结果

下载文件保存到：

```text
outputs/jd-ware-export/downloads/<任务ID>-<原始文件名>.xlsx
```

每次运行的审计结果保存到：

```text
outputs/jd-ware-export/run-<时间戳>.json
```

审计结果会记录京东任务 ID、成功条数、下载路径、导入状态、导入批次号和导入行数。

## 成功判定

同时满足以下条件即表示完成：

- 京东任务状态为“已完成”。
- 执行结果显示“成功：N”。
- 本地生成 `.xlsx` 文件。
- 运营管理系统返回导入成功或已识别为重复文件。

## 故障处理

### 新版页面入口或查询结果异常

新版商品列表会把“导出查询商品”收进“批量操作”下拉菜单；不要只等待页面上的隐藏按钮直接变为可见。按以下顺序操作：

1. 关闭残留的导出抽屉或覆盖层。
2. 点击唯一可见的“查询”，读取结果总数。
3. 仅当结果数大于 0 时，打开“批量操作”，再选择“导出查询商品 → SKU 导出”。

若页面显示商品页签数量但查询结果仍为 `共 0 条`，禁止点击“确定导出”。京东会拒绝空导出；保留审计记录，并复用已经验证且快照日期满足要求的批次，或请人工排查筛选条件与页面数据加载。

### 等待超时

每次进入商品列表后，脚本会先有界等待唯一查询区完成异步渲染，再关闭可能捕获旧查询状态的 SKU 导出抽屉，重新执行一次唯一的商品查询，并要求接口的 `data.totalCount`（兼容旧版 `data.total`）与页面同时精确回显同一个正数总行数，之后才重新打开导出抽屉；两个字段同时存在但不一致时失败关闭。查询按钮允许在唯一定位后发送一次 DOM click，以避开京东固定顶部栏对鼠标坐标的遮挡，但仍必须收到唯一精确查询响应并核对页面总数。即使商品表格后来显示了商品，旧抽屉仍可能保留 `total=0`，因此不能直接复用已打开的抽屉。

慢店铺可能先出现一个页面级“查询”按钮，稍后才渲染可验证的商品筛选容器。此时脚本只等待、不点击；容器与按钮最终一一对应后才继续。等待期出现多个按钮或超时仍失败关闭。

新版商品页的“批量操作”由外层 `role=button` 与内层真实 `<button>` 嵌套组成，按钮文字还带尾随空格。脚本只绑定内层真实按钮，允许两侧空白，并明确排除“更多批量工具”，避免把同一控件误判为两个入口或完全找不到。

“确定导出”点击后，脚本会先等待并校验京东 `createExportJob` 的 HTTP 与业务码均为成功，再开始刷新导出记录。京东页面组件会静默吞掉业务错误，因此不能只凭按钮点击完成或 HTTP 200 判断任务已创建；接口拒绝会立即写入有界错误并停止，只有任务表出现唯一的 post-baseline 任务行才进入下载。

可以直接用同一店铺键重跑：脚本会从 `outputs/jd-ware-export/active-task-<storeKey>.json` 按 taskId 接管原任务；即使它在两次运行之间已从 pending 变为 completed，也不会再次点击“确定导出”。旧版全局 `active-task.json` 存在时会安全停止，要求人工确认归属后迁移或删除，绝不会静默跨店复用。若上次在取得 ID 前中断，则只接受相对持久化 baseline 的唯一新增任务；缺失或歧义时保留清单并安全停止。只有明确需要复用其他已完成记录时才加 `--reuse-latest`：

```powershell
npm run jackyun:ware-export -- --reuse-latest --task-timeout-seconds 600
```

### 提示未登录

在专用 Chrome 配置中手工登录京东商家后台，然后重新运行脚本。脚本会先检查 passport URL 或登录页信号，避免未登录时先等待 30 秒导出按钮。

### 下载成功但没有自动导入

检查运营管理系统是否运行，并确认：

```text
http://localhost:3000/api/netshop/import
```

可先使用 `--no-auto-import` 下载文件，再在系统“数据导入 / 网店分析”页面手工上传。

若下载点击已发送但本地文件未得到验证，运行会以失败状态退出并保留活动任务清单；重跑时接管同一任务，不会新建远端任务。

下载点击后没有本地文件时，先检查专用 Chrome 的下载目录、`.crdownload` 与下载行为配置；不得再次点击同一任务的“下载”。只有确认首次点击没有产生文件、临时文件或浏览器下载事件，且恢复规则明确允许时才可安排一次受控重试。

### 重复导入

运营管理系统按文件哈希去重。重复运行同一个文件时会返回“duplicate”，不会重复写入商品数据。

## 自动化脚本位置

```text
tools/jackyun-ware-export.ts
```

项目命令定义在：

```text
package.json
```
