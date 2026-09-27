---
name: jd-shop-sku-export
description: 自动执行京东商家后台商品 SKU 导出、下载和运营管理系统导入。用户要求下载京东店铺 SKU、运行京东店铺 SKU 导出智能体、把导出的 Excel 同步到运营管理系统，或排查导出任务等待、下载和导入问题时使用。
---

# 京东店铺 SKU 导出

## 核心流程

1. 确认店铺独立 profile 已登录京东商家后台，运营管理系统已启动；商品主数据日常使用普通有头 Chrome 后台隐藏并最小化运行，不使用 `HeadlessChrome`。
   - 自动运行从 `about:blank` 启动，在唯一受控页导航前监听首屏 `queryValidProductList`；首屏成功并与页面总数一致才继续，禁止重复商品页和首屏后的额外“查询”。
2. 在项目根目录 `D:\运营管理系统` 执行 `npm run jackyun:ware-export`。
3. 让脚本先执行商品查询，确认结果数大于 0；新版页面经“批量操作 → 导出查询商品 → SKU 导出”进入导出抽屉。
4. 等待导出任务状态变为“已完成”，核对“成功：N”条。
5. 只下载当前任务对应的文件，保存到 `outputs/jd-ware-export/downloads/`。
6. 下载验证成功后，将文件通过 `/api/netshop/import` 自动导入运营管理系统。
7. 在运行审计 JSON 中记录任务 ID、下载路径、导入批次、行数和状态。

## 运行命令

```powershell
# 创建新导出任务并自动下载、导入
npm run jackyun:ware-export

# 复用最近一条已完成任务
npm run jackyun:ware-export -- --reuse-latest

# 京东任务较慢时延长等待时间（秒）
npm run jackyun:ware-export -- --task-timeout-seconds 600

# 只下载，不自动导入
npm run jackyun:ware-export -- --no-auto-import

# 首次登录或人工排障时显示店铺 Chrome
npm run jackyun:ware-export -- --store-key <店铺键> --interactive-login
```

默认等待时间为 300 秒。只有在页面明确显示任务已完成且下载记录可定位时才继续下载；不能因超时或多个未知任务而随意点击下载。

## 导入约定

调用本地运营管理系统：

```text
POST http://localhost:3000/api/netshop/import
```

表单字段：`source=jd_product_master`、`platform=京东`、`shop_name=所选注册表项的 shopName`、当天上海时区 `snapshot_date`，以及字段名为 `file` 的 `.xlsx` 文件。

导入返回 `imported` 表示新文件已写入；返回 `duplicate` 表示系统按文件哈希去重，不应重复处理。

## 安全与故障处理

- 商品页签数量不等于当前查询有结果。若页面为 `共 0 条`，关闭残留抽屉后执行唯一“查询”；仍为 0 时禁止点击“确定导出”，不要创建空任务。
- “导出查询商品”在新版后台默认隐藏在“批量操作”菜单中。打开菜单后再按唯一可见元素定位；不要等待隐藏按钮自行可见。
- 未登录时停止并要求用户在专用 Chrome 中登录，不要尝试绕过登录。
- 登录失效、验证码、安全验证、业务码 `601` 或店铺登录身份异常时打开当前店铺可见 Chrome；仍以失败状态退出并保留恢复清单，不得自动重放查询、确认或下载点击。`601` 只表示京东服务端风控拒绝，不把无头/CDP/点击方式中的任何单项写成已确认根因，也不实现反检测或系统注入点击绕过。若调试端口残留旧 `HeadlessChrome`，先关闭旧实例再启动后台有头实例；切换期间端口被并发进程接管时失败关闭。
- 导出任务超时后直接用同一 `--store-key` 重新运行：脚本会从 `active-task-<storeKey>.json` 按 taskId 接管原任务；即使它已从 pending 变为 completed，也不会再次点击“确定导出”。旧版全局 `active-task.json` 存在时安全停止，必须人工确认归属后处理；若上次在取得 ID 前中断，则只接受相对持久化 baseline 的唯一新增任务；缺失或歧义时安全停止。
- 下载事件未验证时不自动导入未知文件，也不要重复点击下载按钮。
- 点击下载后未落盘时，先检查配置的下载目录、`.crdownload` 与下载行为；首次点击没有可验证文件时保留审计并停止。
- 运营管理系统不可用时保留本地 `.xlsx`；失败审计必须记录任务 ID、文件路径、API 地址、`stage=auto_import` 和错误，并保持非零退出，不声称导入成功。
- 若存在多个新任务，停止自动下载，要求人工确认目标任务 ID。

## 相关文件

- 自动化脚本：`tools/jackyun-ware-export.ts`
- 可复制操作手册：`docs/京东店铺SKU下载和导出智能体.md`
- 下载目录：`outputs/jd-ware-export/downloads/`
- 审计目录：`outputs/jd-ware-export/`
## 多店铺顺序执行

店铺注册信息保存在 `config/jd-store-accounts.json`，只包含账号标签、店铺名称、shopId 和独立浏览器 profile/端口，不保存密码。首次使用每个 profile 时必须人工登录并完成验证；脚本不会输入或读取密码。

单店运行：`npm run jackyun:ware-export -- --store-key jd-yiyong-director`。全部启用店铺按顺序运行：`npm run jd:all-stores`。执行器一次只运行一个店铺，单店失败会停止并保留现场，避免把任务或下载文件串到下一店铺。
