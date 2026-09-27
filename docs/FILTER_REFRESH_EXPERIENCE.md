# 筛选刷新与静态缓存交互规范

本文定义运营管理系统中筛选、分页、搜索、标签切换和模块切换的统一交互契约。它既是当前实现说明，也是新增板块和子版块时可直接复用的开发、评审与测试清单。

## 1. 适用范围

适用于所有会触发数据请求的前端交互，包括：

- 单选、复选、级联和搜索型下拉框
- 日期、店铺、平台、类目、仓库、状态等筛选条件
- 表格分页、排序、刷新和详情切换
- 主板块与子版块切换
- 全局搜索、弹窗列表和后台配置列表

## 2. 核心交互契约

1. 筛选变化不得通过页面跳转、表单原生提交或 `location.reload()` 刷新整个页面。
2. 已有成功数据在新请求期间继续显示，页面骨架、筛选栏、表格宽度和滚动位置保持稳定。
3. 只有目标数据区域进入刷新态，并通过轻量渐变提示正在更新。
4. 首次加载且没有任何可展示数据时，才显示完整加载占位。
5. 同一业务身份内的筛选允许保留旧数据；切换店铺、平台、商品或其他强身份时，不得展示不属于新身份的旧数据。
6. 请求失败时保留最后一次成功数据，并在数据区域内显示错误；不得把整个板块替换成错误页。
7. 用户连续切换筛选时应取消旧请求，并使用 generation/request key 防止迟到响应覆盖新结果。
8. 复选下拉框每次勾选后保持展开，方便连续选择；仅用户点击外部、按 Escape、点击明确的完成动作或切换业务上下文时收起。单选下拉选择完成后可以收起。

## 3. 推荐状态模型

每个独立数据区域至少区分以下状态：

```ts
type RefreshableState<T> = {
  data: T | null;
  loaded: boolean;
  refreshing: boolean;
  error: string | null;
  identityKey: string;
};
```

- `data`：最后一次成功并且身份匹配的数据。
- `loaded`：至少完成过一次成功请求；成功的空集合也属于已加载。
- `refreshing`：当前是否存在新请求，不决定是否卸载旧数据。
- `error`：当前请求错误，只作为区域内提示。
- `identityKey`：店铺、平台、商品、类目等强业务身份组成的稳定键。

不要使用 `items.length > 0` 代替 `loaded`。空列表可能是合法结果，如果把它当成未加载，会导致刷新时列表反复卸载。

## 4. React 请求模板

```tsx
const generationRef = useRef(0);

useEffect(() => {
  const controller = new AbortController();
  const generation = ++generationRef.current;

  setRefreshing(true);
  setError(null);

  void fetch(buildUrl(filters), { signal: controller.signal })
    .then(assertOk)
    .then((nextData) => {
      if (generation !== generationRef.current) return;
      setData(nextData);
      setLoaded(true);
    })
    .catch((error) => {
      if (controller.signal.aborted) return;
      if (generation !== generationRef.current) return;
      setError(toUserMessage(error));
    })
    .finally(() => {
      if (generation === generationRef.current) {
        setRefreshing(false);
      }
    });

  return () => controller.abort();
}, [filters]);
```

渲染时保留稳定容器：

```tsx
<section
  className="data-refresh-region"
  data-refreshing={refreshing ? "true" : "false"}
  aria-busy={refreshing}
>
  {!loaded && refreshing ? <InitialSkeleton /> : <DataView data={data} />}
  {error && loaded ? <InlineRefreshError message={error} /> : null}
</section>
```

如果一个视图需要多个接口才能形成一致快照，应只在全部接口返回同一快照标识后原子替换展示数据。发现快照不一致时重新请求，但继续保留上一组完整快照，禁止展示新旧接口结果的混合状态。

## 5. 下拉框规范

### 单选

- 选择后更新值并收起。
- 键盘 Enter 选择后行为一致。
- 筛选刷新不得卸载触发按钮或整个筛选栏。

### 复选

- 勾选或取消单个选项时只更新选中集合，不调用关闭逻辑。
- 面板内按钮使用 `type="button"`，避免触发表单提交。
- 需要批量确认时提供明确的“完成”按钮；没有完成按钮时点击外部关闭。
- 已选项、搜索词和滚动位置在数据刷新期间保持不变。

参考实现：`app/ui/searchable-select.tsx`。

## 6. 动画和布局稳定性

统一使用 `.data-refresh-region` 标记局部刷新区域，模块切换使用 `.module-stage`。动画应满足：

- 不改变元素高度、宽度、定位和表格列宽。
- 使用 `opacity`、轻微 `filter` 或覆盖层渐变，避免导致回流的属性。
- 刷新期间旧内容保持可见；必要时暂时禁止重复点击。
- 动画持续时间短，不阻塞数据读取。
- 遵守 `prefers-reduced-motion: reduce`，关闭非必要动画。
- 使用 `aria-busy` 表达刷新状态，不要依赖颜色或动画作为唯一提示。

公共样式位于 `app/globals.css`。新增模块应复用公共类，不要各自实现不同的加载闪烁效果。

## 7. 路由和模块切换

- 主板块与子版块状态使用客户端状态和 History API 同步 URL。
- 不使用普通文档导航重新加载当前应用。
- 懒加载新模块时保留当前模块，直到新模块可渲染，再进行渐变切换。
- URL 可复制、前进和后退，但这些操作仍应由客户端恢复对应模块。
- 模块容器应设置稳定的最小高度，避免懒加载时页面跳动。

参考实现：`app/page.tsx`。

## 8. 缓存边界

| 资源类型 | 推荐策略 | 原因 |
| --- | --- | --- |
| 带内容哈希的 `/assets/*` | `public, max-age=31536000, immutable` | 文件名变化即形成新版本 |
| 图标、固定公共图片 | 短期公共缓存并允许后台再验证 | 降低重复传输且可更新 |
| HTML、RSC/页面入口 | `no-store, must-revalidate` | 防止旧应用外壳和身份内容滞留 |
| 普通业务 API | `no-store` | 数据、权限和 scope 可能变化 |
| 明确以内容哈希寻址的图片 API | 仅在严格校验哈希、状态码和请求方法后保留 immutable | 内容身份稳定 |
| 错误响应、写请求、鉴权响应 | `no-store` | 禁止缓存失败或跨身份结果 |

不得为了减少闪烁而缓存普通业务 API。闪烁应通过保留旧数据、并发复用和稳定渲染解决；API 缓存必须另有版本指纹、权限隔离和精确失效策略。

当前实现参考：`public/_headers`、`worker/cache-policy.ts` 和 `worker/index.ts`。

## 9. 板块巡检清单

每个主板块及其全部子版块逐项检查：

- [ ] 筛选操作没有文档级导航或硬刷新。
- [ ] 筛选栏、标题、分页和表格容器在刷新期间不卸载。
- [ ] 初次加载和后台刷新使用不同表现。
- [ ] 成功空结果不会重新进入首次加载态。
- [ ] 请求失败保留最后一次成功数据。
- [ ] 连续快速切换时旧响应不会覆盖新响应。
- [ ] 强业务身份切换不会展示其他身份的旧数据。
- [ ] 多接口数据保持同一快照。
- [ ] 复选下拉可以连续勾选，单选行为正常。
- [ ] 键盘操作、焦点和 `aria-busy` 正常。
- [ ] 减少动态效果系统设置生效。
- [ ] URL、前进、后退和刷新后的模块定位正确。
- [ ] 静态资源缓存，HTML 和普通 API 不误缓存。

## 10. 测试门禁

至少覆盖以下自动化测试：

1. 复选选项改变后弹层仍保持展开。
2. 刷新开始时旧数据仍在 DOM 中，区域带有 `aria-busy=true`。
3. 首次加载无数据时显示占位，成功空列表后不再显示首次加载占位。
4. 较早请求晚于新请求返回时，不改变当前数据。
5. 请求失败时旧数据和筛选条件保持不变。
6. 强身份切换时不展示错误身份的数据。
7. 多接口快照不一致时不发布混合数据。
8. 模块切换不触发文档级重新加载。
9. CSS 包含局部渐变和 reduced-motion 规则。
10. 静态资源、HTML、普通 API 和内容哈希图片的响应头符合缓存矩阵。

项目回归入口：

```powershell
node --import tsx --test --test-concurrency=1 tests/*.test.ts
node --test tests/rendered-html.test.mjs
npm run build
```

针对本规范的契约测试位于 `tests/filter-refresh-experience.test.ts` 和 `tests/static-cache-policy.test.ts`。

## 11. 评审中的常见反模式

- 筛选变化时先执行 `setData(null)`。
- 用一个 `loading` 布尔值决定卸载整个页面。
- 在刷新期间用加载行替换已有表格数据。
- 复选回调与单选共用无条件 `setOpen(false)`。
- 只做防抖，不取消请求也不防止迟到响应。
- 同一界面直接拼接来自不同快照的多个接口结果。
- 使用 API 长缓存掩盖前端闪烁。
- 为局部数据变化调用 `router.refresh()`、`location.assign()` 或原生表单提交。
- 动画改变高度、边距或定位，造成累计布局偏移。

发现以上模式时，应按本规范修正后再合并。
