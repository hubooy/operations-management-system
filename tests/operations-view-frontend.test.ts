import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  beginTemplateRequest,
  calendarDateWithOffset,
  createTemplateRequestLifecycle,
  isCurrentTemplateRequest,
  shanghaiDateWithOffset,
  validateOperationRecordDraft,
  validateTaskDraft,
  validateTaskTemplateDraft,
} from "@/app/operations-view";
import {
  validateNewProductDraft,
  validateStageDraft,
} from "@/app/new-product-launch-view";

const source = (path: string) => readFile(new URL(path, import.meta.url), "utf8");

test("task template dates use the Asia/Shanghai calendar across the UTC boundary", () => {
  const beforeShanghaiMidnight = new Date("2026-08-19T15:59:59.000Z");
  const afterShanghaiMidnight = new Date("2026-08-19T16:00:00.000Z");

  assert.equal(shanghaiDateWithOffset(0, beforeShanghaiMidnight), "2026-08-19");
  assert.equal(shanghaiDateWithOffset(0, afterShanghaiMidnight), "2026-08-20");
  assert.equal(shanghaiDateWithOffset(3, afterShanghaiMidnight), "2026-08-23");
  assert.equal(calendarDateWithOffset("2026-08-31", 1), "2026-09-01");
  assert.equal(calendarDateWithOffset("2028-02-28", 1), "2028-02-29");
});

test("operation record editor reports actionable validation instead of silently disabling creation", async () => {
  const validDraft = {
    title: "大通量净水器新品上架",
    status: "待开始",
    platform: "京东",
    channel: "线上",
    shopName: "待确认",
    owner: "商品组",
    occurredAt: "2026-08-26T09:00",
    dueAt: "2026-08-30T18:00",
    content: "准备上架资料",
    referenceCode: "NEW-001",
    priority: "normal" as const,
  };
  assert.equal(validateOperationRecordDraft(validDraft), "");
  assert.match(validateOperationRecordDraft({ ...validDraft, title: "" }), /事项名称/);
  assert.match(validateOperationRecordDraft({ ...validDraft, shopName: "" }), /待确认/);
  assert.match(validateOperationRecordDraft({ ...validDraft, occurredAt: "" }), /发生时间/);
  assert.match(validateOperationRecordDraft({ ...validDraft, dueAt: "2026-08-25T18:00" }), /不能早于/);

  const operations = await source("../app/operations-view.tsx");
  assert.match(operations, /setEditorError\(messageOf\(reason, "记录保存失败，请稍后重试。"\)\)/);
  assert.match(operations, /className="workflow-edit-validation" role="alert"/);
  assert.match(operations, /type="submit" className="primary-button" disabled=\{saving\}/);
  assert.doesNotMatch(operations, /disabled=\{saving \|\| !draft\.title\.trim\(\) \|\| !draft\.shopName\.trim\(\) \|\| !draft\.occurredAt\}/);
});

test("all work-item and template creation entry points expose actionable validation", async () => {
  const taskDraft = {
    title: "检查新品资料",
    workContent: "核对主图与详情页",
    category: "新品上架",
    owner: "商品组",
    shopName: "待确认",
    startDate: "2026-08-26",
    due: "2026-08-28",
    priority: "normal" as const,
  };
  assert.equal(validateTaskDraft(taskDraft), "");
  assert.match(validateTaskDraft({ ...taskDraft, title: "" }), /工作事项/);
  assert.match(validateTaskDraft({ ...taskDraft, due: "2026-08-25" }), /不能早于/);

  const templateDraft = {
    name: "新品上架模板",
    description: "",
    title: "新品资料检查",
    workContent: "",
    category: "新品上架",
    owner: "",
    shopName: "",
    startOffsetDays: 0,
    dueOffsetDays: 3,
    priority: "normal" as const,
    active: true,
  };
  assert.equal(validateTaskTemplateDraft(templateDraft), "");
  assert.match(validateTaskTemplateDraft({ ...templateDraft, name: "" }), /模板名称/);
  assert.match(validateTaskTemplateDraft({ ...templateDraft, startOffsetDays: 366 }), /-365 至 365/);
  assert.match(validateTaskTemplateDraft({ ...templateDraft, dueOffsetDays: -1 }), /不能早于/);

  const [operations, newProductLaunch] = await Promise.all([
    source("../app/operations-view.tsx"),
    source("../app/new-product-launch-view.tsx"),
  ]);
  assert.match(operations, /setTaskEditorError\(messageOf\(reason, "工作事项保存失败，请稍后重试。"\)\)/);
  assert.match(operations, /setEditorError\(messageOf\(reason, "模板保存失败，请稍后重试。"\)\)/);
  assert.match(operations, />填入工作项<\/button>/);
  assert.match(operations, /请核对后点击“添加工作项”/);
  assert.match(operations, /void submitComment\(\)/);
  assert.match(operations, /void submitReminder\(\)/);
  assert.match(operations, /void submitLink\(\)/);
  assert.match(operations, /"创建巡店记录"/);
  assert.match(operations, /"创建评价记录"/);
  assert.match(operations, /<NewProductLaunchView canWrite=\{canWrite\} \/>/);
  assert.match(newProductLaunch, /新建新品项目/);
  assert.doesNotMatch(operations, /disabled=\{saving \|\| !draft\.title\.trim\(\)\}/);
  assert.doesNotMatch(operations, /disabled=\{saving \|\| !draft\.name\.trim\(\)\}/);
  assert.doesNotMatch(operations, /disabled=\{saving \|\| !comment\.trim\(\)\}/);
  assert.doesNotMatch(operations, /disabled=\{saving \|\| !remindAt\}/);
});

test("operations work plan keeps the paged collaboration and concurrency contract", async () => {
  const operations = await source("../app/operations-view.tsx");

  assert.match(operations, /pageSize", String\(TASK_PAGE_SIZE\)/);
  assert.match(operations, /listParams\.append\("status", value\)/);
  assert.match(operations, /commonParams\.append\("priority", value\)/);
  assert.match(operations, /commonParams\.append\("owner", value\)/);
  assert.match(operations, /calendarDateWithOffset\(taskDueTo, 1\)/);
  assert.match(operations, /expectedVersion/);
  assert.match(operations, /payload\.summary \?\? fallbackSummary/);
  assert.match(operations, /requestGeneration/);
  assert.match(operations, /taskGeneration/);
  assert.match(operations, /beginTemplateRequest\(templateRequestLifecycle\.current, requestKey\)/);
  assert.match(operations, /request\.controller\.signal/);
  assert.match(operations, /isCurrentTemplateRequest\(templateRequestLifecycle\.current, request\)/);
  assert.match(operations, /cancelTemplateRequest\(lifecycle\)/);
  assert.match(operations, /if \(activeTab !== "plan"\) \{\s+taskGeneration\.current \+= 1;\s+return;/);
  assert.match(operations, /if \(activeTab !== "plan" && activeTab !== "variables"\) \{\s+cancelTemplateRequest\(lifecycle\);\s+return;/);
  assert.match(operations, /loadControllerRef\.current\?\.abort\(\)/);
  assert.match(operations, /generation !== loadGenerationRef\.current/);
  assert.match(operations, /collaboration`, \{ signal: controller\.signal \}/);
  assert.match(operations, /downloadCsvFile/);
  assert.doesNotMatch(operations, /URL\.createObjectURL/);
});

test("template loading ignores stale active-only success, error, and loading completion after the role gains write access", async () => {
  const lifecycle = createTemplateRequestLifecycle();
  let items = ["initial"];
  let loading = true;
  let error = "";

  const apply = async (request: ReturnType<typeof beginTemplateRequest>, outcome: Promise<string[]>) => {
    try {
      const nextItems = await outcome;
      if (!isCurrentTemplateRequest(lifecycle, request)) return;
      items = nextItems;
    } catch (reason) {
      if (!isCurrentTemplateRequest(lifecycle, request)) return;
      error = reason instanceof Error ? reason.message : "failed";
    } finally {
      if (isCurrentTemplateRequest(lifecycle, request)) loading = false;
    }
  };

  let rejectActiveOnly!: (reason: Error) => void;
  const activeOnlyFailure = new Promise<string[]>((_, reject) => { rejectActiveOnly = reject; });
  const activeOnlyRequest = beginTemplateRequest(lifecycle, "/api/workflow/templates");
  const activeOnlyCommit = apply(activeOnlyRequest, activeOnlyFailure);

  let resolveIncludeInactive!: (value: string[]) => void;
  const includeInactiveResponse = new Promise<string[]>((resolve) => { resolveIncludeInactive = resolve; });
  const includeInactiveRequest = beginTemplateRequest(lifecycle, "/api/workflow/templates?includeInactive=true");
  const includeInactiveCommit = apply(includeInactiveRequest, includeInactiveResponse);

  assert.equal(activeOnlyRequest.controller.signal.aborted, true);
  rejectActiveOnly(new Error("stale active-only failure"));
  await activeOnlyCommit;
  assert.equal(error, "");
  assert.equal(loading, true);

  resolveIncludeInactive(["active", "inactive"]);
  await includeInactiveCommit;
  assert.deepEqual(items, ["active", "inactive"]);
  assert.equal(loading, false);

  loading = true;
  let resolveLateActiveOnly!: (value: string[]) => void;
  const lateActiveOnlyResponse = new Promise<string[]>((resolve) => { resolveLateActiveOnly = resolve; });
  const lateActiveOnlyRequest = beginTemplateRequest(lifecycle, "/api/workflow/templates");
  const lateActiveOnlyCommit = apply(lateActiveOnlyRequest, lateActiveOnlyResponse);

  const latestIncludeInactiveRequest = beginTemplateRequest(lifecycle, "/api/workflow/templates?includeInactive=true");
  await apply(latestIncludeInactiveRequest, Promise.resolve(["new active", "new inactive"]));
  resolveLateActiveOnly(["stale active only"]);
  await lateActiveOnlyCommit;

  assert.equal(lateActiveOnlyRequest.controller.signal.aborted, true);
  assert.deepEqual(items, ["new active", "new inactive"]);
  assert.equal(loading, false);
  assert.equal(error, "");
});

test("work plan restores filters, analytics, timeline, source metadata, and nine-column mobile labels", async () => {
  const [operations, css] = await Promise.all([
    source("../app/operations-view.tsx"),
    source("../app/globals.css"),
  ]);

  for (const feature of [
    "workflow-summary-grid",
    "workflow-insight-grid",
    "workflow-task-buckets",
    "workflow-view-switch",
    "workflow-timeline",
    "MultiSelectFilter",
    "TaskTransitionActions",
    "formatRecordedAt",
  ]) assert.match(operations, new RegExp(feature));

  assert.match(operations, /<th>店铺 \/ 来源<\/th>/);
  assert.match(operations, /<th>截止 \/ 录入<\/th>/);
  assert.match(operations, /colSpan=\{9\}/);
  assert.match(css, /\.operations-plan-table td:nth-child\(9\)::before \{ content: "操作"; \}/);
  assert.match(css, /\.operations-plan-table td:nth-child\(8\)::before \{ content: "协作"; \}/);
  assert.match(css, /@media \(max-width: 960px\)/);
});

test("work plan exposes server facets, standardized suggestions and complete filtered export", async () => {
  const [operations, tasks, route] = await Promise.all([
    source("../app/operations-view.tsx"),
    source("../lib/workflow/tasks.ts"),
    source("../app/api/workflow/tasks/route.ts"),
  ]);
  for (const parameter of ["shopName", "category", "source"]) {
    assert.match(operations, new RegExp(`append\\(\"${parameter}\"`));
  }
  assert.match(operations, /导出筛选完整清单/);
  assert.match(operations, /while \(exported\.length < total\)/);
  assert.match(operations, /workflow-category-options/);
  assert.match(operations, /workflow-shop-options/);
  assert.match(operations, /workflow-owner-options/);
  assert.match(tasks, /facets: \{/);
  assert.match(tasks, /categories: boundedList\(input\.categories/);
  assert.match(tasks, /sources: boundedList\(input\.sources/);
  assert.match(route, /path: WORKFLOW_TASKS_PATH/);
  assert.match(route, /service: "reader"/);
  assert.match(route, /rawQuery: new URL\(request\.url\)\.searchParams\.toString\(\)/);
  assert.doesNotMatch(route, /getD1Database|listWorkflowTasks/);
});

test("new-product editor accepts blank planning and validates dates, money and optional stage details", async () => {
  const draft = {
    productName: "大通量商用净水器",
    supplierName: "供应商甲",
    brand: "志高",
    category: "商用净水",
    erpProductCode: "ERP-1",
    skuCode: "SKU-1",
    spuCode: "SPU-1",
    productImageUrl: "",
    proposedBy: "商品组",
    proposedDate: "2026-09-02",
    owner: "新品负责人",
    targetLaunchDate: "2026-09-16",
    lifecycleStatus: "active" as const,
    priority: "normal" as const,
    recommendedPriceYuan: "3999.00",
    approvedPriceYuan: "",
    estimatedGrossMarginPercent: "32.50",
    notes: "",
    shopPlan: "",
  };
  assert.equal(validateNewProductDraft(draft), "");
  assert.match(validateNewProductDraft({ ...draft, productName: "" }), /商品名称/);
  assert.match(validateNewProductDraft({ ...draft, targetLaunchDate: "2026-09-01" }), /不能早于/);
  assert.match(validateNewProductDraft({ ...draft, recommendedPriceYuan: "1.999" }), /最多保留 2 位/);
  assert.equal(validateNewProductDraft({ ...draft, shopPlan: "京东测试店\n天猫测试店：下周上架" }), "");
  assert.equal(validateStageDraft({ status: "blocked", owner: "", plannedDueDate: "", blocker: "", notes: "", evidenceUrl: "", evidenceLabel: "" }), "");
  assert.match(validateStageDraft({ status: "completed", owner: "", plannedDueDate: "", blocker: "", notes: "", evidenceUrl: "ftp://invalid", evidenceLabel: "" }), /http/);
});

test("new-product workspace includes editable planning, status-only stages and reachable modal actions", async () => {
  const [launch, operations, css] = await Promise.all([
    source("../app/new-product-launch-view.tsx"),
    source("../app/operations-view.tsx"),
    source("../app/globals.css"),
  ]);
  for (const label of ["建模", "分析定价", "图片", "视频", "上架", "备货", "上新复盘"]) {
    assert.match(launch, new RegExp(label));
  }
  for (const feature of ["阶段矩阵", "看板", "店铺规划", "编辑店铺规划", "负责人", "工作状态备注", "阻塞原因（选填）", "证据链接（选填）", "最近活动"]) {
    assert.match(launch, new RegExp(feature));
  }
  assert.match(launch, /STATUS_ONLY_STAGE_KEYS = new Set<StageKey>\(\["modeling", "pricing", "image", "video", "stocking"\]\)/);
  assert.match(launch, /STATUS_ONLY_STAGE_KEYS\.has\(stage\.stageKey\)/);
  assert.doesNotMatch(launch, /legacyFallback|structured === false/);
  assert.doesNotMatch(operations, /OperationsRecordWorkspace type="launch"/);
  assert.match(css, /\.launch-matrix-table/);
  assert.match(css, /\.modal-backdrop:has\(\.workflow-edit-modal\)/);
  assert.match(css, /\.workflow-edit-actions \{ position: sticky;/);
  assert.match(css, /\.launch-planning-cell/);
  assert.match(css, /\.launch-kanban/);
  assert.match(css, /\.launch-detail-stages/);
});

test("new-product follow-up renders continuous monitoring, product images, matching Excel export, and governed DingTalk settings", async () => {
  const [followup, robotSettings, service, sender, imageRenderer, css] = await Promise.all([
    source("../app/new-product-sales-followup-view.tsx"),
    source("../app/dingtalk-robot-settings.tsx"),
    source("../backend/workflow/followup.py"),
    source("../backend/workflow/management/commands/new_product_weekly_report.py"),
    source("../backend/workflow/weekly_report_image.py"),
    source("../app/globals.css"),
  ]);
  for (const label of ["钉钉周报表格预览", "品牌", "产品图", "产品名称", "趋势", "钉钉机器人设置", "打开 Excel 表格", "暂停监控", "启动监控", "吉客云名称（学习关键词）"]) {
    assert.match(followup, new RegExp(label));
  }
  assert.match(robotSettings, /<h2>钉钉图片周报<\/h2>/);
  assert.match(robotSettings, /PNG 上传钉盘 \+ 机器人在线预览链接/);
  assert.match(followup, /REPORT_TIMELINE_START = "2026-08-03"/);
  assert.match(followup, /weeklyNetQuantities/);
  assert.match(followup, /createNewProductFollowupWorkbookBytes/);
  assert.match(followup, /trendPngImage/);
  assert.match(followup, /\.xlsx/);
  assert.match(followup, /type="file"/);
  assert.match(followup, /打开本地文件夹选择图片/);
  assert.match(followup, /prepareProductImage/);
  assert.match(followup, /productImage: null/);
  assert.doesNotMatch(followup, />产品图链接</);
  assert.doesNotMatch(followup, /trackingWeeks|跟踪周数/);
  assert.match(service, /REPORT_TIMELINE_START = date\(2026, 8, 3\)/);
  assert.match(service, /deleted_at__isnull=True, active=True/);
  assert.match(service, /"weeks": weeks/);
  assert.match(service, /"brand":/);
  assert.match(service, /"productImageUrl": _line_image_source\(line/);
  assert.match(service, /embed_uploaded_images/);
  assert.match(sender, /"drive", "upload"/);
  assert.match(sender, /png_drive_preview_by_bot/);
  assert.match(sender, /查看周报预览图片/);
  assert.doesNotMatch(sender, /本周净销量/);
  assert.match(imageRenderer, /--headless=new/);
  assert.match(imageRenderer, /MAX_IMAGE_BYTES/);
  assert.match(imageRenderer, /object-fit:contain/);
  assert.match(css, /\.launch-followup-matrix-table/);
  assert.match(css, /\.launch-followup-robot/);
  for (const selector of ["launch-followup-product-image", "launch-followup-matrix-image", "new-product-line-image-picker"]) {
    assert.match(css, new RegExp(`${selector}[^}]*object-fit: contain`));
  }
});
