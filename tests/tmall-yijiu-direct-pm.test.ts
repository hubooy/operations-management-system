import assert from "node:assert/strict";
import test from "node:test";

import {
  TMALL_DIRECT_PROMOTION_BIZ_CODES,
  TMALL_DIRECT_PROMOTION_FIELDS,
  assertTmallSignedDownloadUrl,
  buildTmallDirectPromotionRequestBody,
  directPromotionLegacyAuditBlocks,
  discoverTmallAlimamaIdentifiers,
  findTmallDirectPromotionTask,
  parseTmallAlimamaIdentifiers,
} from "../tools/tmall-direct-promotion-export";
import type { Page, Request } from "playwright-core";
import type { TmallStore } from "../lib/netshop/tmall-store-registry";
import {
  TMALL_MTOP_API,
  TMALL_MTOP_EXPORT_PATH,
  TMALL_MTOP_LIST_PATH,
  assertTmallProductDownloadUrl,
  buildTmallMtopListBody,
  buildTmallMtopSign,
  extractTmallExportRecords,
  makeTmallDirectProductBatches,
  parseTmallExportSubmitResult,
  parseTmallMtopListRequest,
  resolveTmallDirectProductMasterSnapshotDate,
  selectNewTmallExportRecord,
} from "../tools/tmall-direct-product-master-export";
import {
  TMALL_YIJIU_DIRECT_PM_PROTOCOL,
  tmallDirectPmProtocolError,
  tmallDirectProductMasterRoute,
  tmallDirectPromotionRoute,
} from "../tools/tmall-yijiu-direct-pm-contract";

test("亿玖 P 直连请求固定为单日、四场景、商品+计划维度和现行归因口径", () => {
  const body = buildTmallDirectPromotionRequestBody({
    date: "2026-09-01",
    reportName: "商品报表_20260901_test",
    csrfId: "csrf-safe",
    loginPointId: "123456",
  });
  assert.equal(body.startTime, "2026-09-01");
  assert.equal(body.endTime, "2026-09-01");
  assert.equal(body.splitType, "day");
  assert.equal(body.fieldType, "all");
  assert.equal(body.unifyType, "last_click_by_effect_time");
  assert.equal(body.effectEqual, 15);
  assert.deepEqual(body.bizCodeIn, [...TMALL_DIRECT_PROMOTION_BIZ_CODES]);
  assert.deepEqual(body.queryDomains, ["promotion", "campaign"]);
  assert.deepEqual(body.queryFieldIn, [...TMALL_DIRECT_PROMOTION_FIELDS]);
  assert.equal("subPromotionTypes" in body, false);
  assert.throws(() => buildTmallDirectPromotionRequestBody({
    date: "2026-09-01..02",
    reportName: "bad",
    csrfId: "csrf",
    loginPointId: "point",
  }), /YYYY-MM-DD/);
});

test("P 只从 bpcommon 下载列表真实请求取得临时标识并按唯一 taskId 认领", () => {
  assert.deepEqual(parseTmallAlimamaIdentifiers(
    "https://bpcommon.alimama.com/commonapi/report/async/findPage.json?csrfId=csrf-1&loginPointId=point-2",
  ), { csrfId: "csrf-1", loginPointId: "point-2" });
  assert.equal(parseTmallAlimamaIdentifiers(
    "https://one.alimama.com/commonapi/report/async/findPage.json?csrfId=csrf-1&loginPointId=point-2",
  ), null);
  assert.equal(parseTmallAlimamaIdentifiers(
    "https://bpcommon.alimama.com/commonapi/report/async/findPage.json?csrfId=csrf-1",
  ), null);
  assert.deepEqual(findTmallDirectPromotionTask({
    data: { list: [{ id: 41, status: "RUNNING" }, { id: 42, status: "SUCCESS" }] },
  }, "42"), { id: 42, status: "SUCCESS" });
  assert.throws(() => findTmallDirectPromotionTask({
    data: { list: [{ id: 42 }, { id: "42" }] },
  }, "42"), /重复 taskId/);
});

test("P 在首次导航前监听会话标识，登录身份通过后直接使用首次请求", async () => {
  let requestListener: ((request: Request) => void) | undefined;
  let navigations = 0;
  let fallbackWaits = 0;
  let detached = false;
  const page = {
    on: (event: string, listener: (request: Request) => void) => {
      assert.equal(event, "request");
      requestListener = listener;
    },
    off: (event: string, listener: (request: Request) => void) => {
      assert.equal(event, "request");
      assert.equal(listener, requestListener);
      detached = true;
    },
    goto: async () => {
      navigations += 1;
      requestListener?.({
        url: () => "https://bpcommon.alimama.com/commonapi/report/async/findPage.json?csrfId=first-load&loginPointId=store-session",
      } as Request);
    },
    waitForRequest: async () => {
      fallbackWaits += 1;
      throw new Error("首次导航已捕获时不应再次等待");
    },
  } as unknown as Page;
  const store = { storeKey: "tmall-yijiu" } as TmallStore;

  const identifiers = await discoverTmallAlimamaIdentifiers(page, store, {
    waitForIdentity: async () => {},
    captureTimeoutMs: 1,
  });

  assert.deepEqual(identifiers, { csrfId: "first-load", loginPointId: "store-session" });
  assert.equal(navigations, 1);
  assert.equal(fallbackWaits, 0);
  assert.equal(detached, true);
});

test("P/M 临时下载链接限制在 HTTPS 阿里云 OSS，且旧业务动作清单阻止协议切换", () => {
  assert.match(assertTmallSignedDownloadUrl("https://example.oss-cn-hangzhou.aliyuncs.com/signed.zip"), /^https:/);
  assert.throws(() => assertTmallSignedDownloadUrl("https://evil.example/signed.zip"), /受控 HTTPS/);
  assert.match(assertTmallProductDownloadUrl(
    "https://excel-tmall-item.oss-cn-qingdao.aliyuncs.com/path/file.xlsx?signature=redacted",
  ), /^https:/);
  assert.throws(() => assertTmallProductDownloadUrl(
    "https://other-bucket.oss-cn-qingdao.aliyuncs.com/path/file.xlsx",
  ), /受控 HTTPS/);
  assert.equal(directPromotionLegacyAuditBlocks({ stage: "completed" }), false);
  assert.equal(directPromotionLegacyAuditBlocks({ stage: "report_submitting" }), true);
  assert.equal(directPromotionLegacyAuditBlocks({ stage: "failed", resumeStage: "importing" }), true);
});

test("M 只接受千牛首屏的固定 MTOP 只读列表模板且服务端分页固定为 20", () => {
  const data = {
    url: TMALL_MTOP_LIST_PATH,
    jsonBody: JSON.stringify({
      tab: "on_sale",
      pagination: { current: 1, pageSize: 20 },
      filtertab: "",
      filter: { status: "onsale" },
      table: {},
    }),
  };
  const postData = new URLSearchParams({ data: JSON.stringify(data) }).toString();
  const template = parseTmallMtopListRequest({
    url: `https://h5api.m.taobao.com/h5/mtop.tmall.sell.pc.manage.async/1.0/?api=${TMALL_MTOP_API}`,
    postData,
  });
  assert.deepEqual(template, {
    tab: "on_sale",
    filtertab: "",
    filter: { status: "onsale" },
    table: {},
  });
  assert.deepEqual(buildTmallMtopListBody(template!, 2).pagination, { current: 2, pageSize: 20 });
  assert.equal(parseTmallMtopListRequest({
    url: `https://h5api.m.taobao.com/h5/mtop.tmall.sell.pc.manage.async/1.0/?api=${TMALL_MTOP_API}`,
    postData: new URLSearchParams({ data: JSON.stringify({ ...data, url: TMALL_MTOP_EXPORT_PATH }) }).toString(),
  }), null);
});

test("M 的 MTOP 签名与唯一写类导出路径被逐字固定，商品按 itemId 确定性分批", () => {
  assert.equal(TMALL_MTOP_EXPORT_PATH,
    "/tmall/manager/batchFastEdit.htm?optType=batchExportItem&action=submit");
  assert.equal(buildTmallMtopSign("abc", "123", "{\"x\":1}"), "ff46cbf9a6f9180534756d48e4d58b91");
  const items = Array.from({ length: 43 }, (_, index) => ({
    itemId: String(43 - index).padStart(3, "0"),
    catId: "500",
  }));
  const batches = makeTmallDirectProductBatches(items);
  assert.deepEqual(batches.map((batch) => batch.items.length), [20, 20, 3]);
  assert.equal(batches[0]?.items[0]?.itemId, "001");
  assert.equal(batches[2]?.items.at(-1)?.itemId, "043");
  assert.throws(() => makeTmallDirectProductBatches([
    { itemId: "1", catId: "500" },
    { itemId: "1", catId: "500" },
  ]), /重复 itemId/);
  assert.deepEqual(parseTmallExportSubmitResult({ success: true, traceId: "trace:123" }), { traceId: "trace:123" });
  assert.throws(() => parseTmallExportSubmitResult({ success: false, traceId: "trace:123" }), /success=true/);
});

test("M 用提交前 id 基线唯一认领新记录，并同时核对行数、时间窗和完成状态", () => {
  const records = extractTmallExportRecords({
    data: {
      table: {
        dataSource: [
          { id: 100, rowCount: 20, taskStatus: "已完成", gmtCreate: "2026-09-02 12:50:00", reportUrl: "https://excel-tmall-item.oss-cn-qingdao.aliyuncs.com/old" },
          { id: 101, rowCount: 20, taskStatus: "已完成", gmtCreate: "2026-09-02 13:00:05", reportUrl: "https://excel-tmall-item.oss-cn-qingdao.aliyuncs.com/new" },
        ],
      },
    },
  });
  assert.equal(selectNewTmallExportRecord({
    records,
    baselineRecordIds: ["100"],
    expectedRows: 20,
    submittedAt: "2026-09-02T05:00:00.000Z",
    now: new Date("2026-09-02T05:01:00.000Z"),
  })?.id, "101");
  assert.throws(() => selectNewTmallExportRecord({
    records: [...records, {
      id: "102", rowCount: 20, status: "已完成", createdAt: "2026-09-02 13:00:10",
      reportUrl: "https://excel-tmall-item.oss-cn-qingdao.aliyuncs.com/newer",
    }],
    baselineRecordIds: ["100"],
    expectedRows: 20,
    submittedAt: "2026-09-02T05:00:00.000Z",
  }), /多个新天猫导出记录/);
  assert.throws(() => selectNewTmallExportRecord({
    records: [{ ...records[1]!, rowCount: 19 }],
    baselineRecordIds: [],
    expectedRows: 20,
    submittedAt: "2026-09-02T05:00:00.000Z",
  }), /行数 19/);
});

test("M 接受唯一具名的空导出记录列表，并拒绝无关空数组", () => {
  assert.deepEqual(extractTmallExportRecords({
    data: { table: { dataSource: [] } },
    ret: [],
  }), []);
  assert.throws(() => extractTmallExportRecords({ ret: [], warnings: [] }), /没有唯一记录数组/);
  assert.equal(extractTmallExportRecords({
    data: { table: { dataSource: [
      { id: 100, rowCount: 20, taskStatus: "已完成", gmtCreate: "2026-09-02 12:50:00" },
    ] } },
    ret: [],
  })[0]?.id, "100");
});

test("M 默认续跑未完成快照，显式冲突和非法日期失败关闭", () => {
  assert.equal(resolveTmallDirectProductMasterSnapshotDate(undefined, undefined, "2026-09-07"), "2026-09-07");
  assert.equal(resolveTmallDirectProductMasterSnapshotDate(undefined, "2026-09-06", "2026-09-07"), "2026-09-06");
  assert.equal(resolveTmallDirectProductMasterSnapshotDate("2026-09-06", "2026-09-06", "2026-09-07"), "2026-09-06");
  assert.throws(() => resolveTmallDirectProductMasterSnapshotDate("2026-09-07", "2026-09-06", "2026-09-07"), /拒绝覆盖/);
  assert.throws(() => resolveTmallDirectProductMasterSnapshotDate("2026-9-7", undefined, "2026-09-07"), /YYYY-MM-DD/);
  assert.throws(() => resolveTmallDirectProductMasterSnapshotDate(undefined, "bad", "2026-09-07"), /YYYY-MM-DD/);
  assert.throws(() => resolveTmallDirectProductMasterSnapshotDate(undefined, undefined, "bad"), /YYYY-MM-DD/);
});

test("亿玖现行协议保持兼容，丽力不能使用亿玖协议", () => {
  for (const route of [tmallDirectPromotionRoute, tmallDirectProductMasterRoute]) {
    assert.equal(tmallDirectPmProtocolError({
      route,
      storeKey: "tmall-yijiu",
      protocol: TMALL_YIJIU_DIRECT_PM_PROTOCOL,
    }), null);
    assert.deepEqual(tmallDirectPmProtocolError({
      route,
      storeKey: "tmall-yijiu",
      protocol: undefined,
    }), { error: "missing_or_invalid_tmall_direct_pm_protocol" });
    assert.deepEqual(tmallDirectPmProtocolError({
      route,
      storeKey: "tmall-lili",
      protocol: TMALL_YIJIU_DIRECT_PM_PROTOCOL,
    }), { error: route === tmallDirectProductMasterRoute ? "missing_or_invalid_tmall_direct_pm_protocol" : "tmall_direct_pm_store_not_allowed" });
  }
  assert.equal(tmallDirectPmProtocolError({ route: "/promotion", storeKey: "tmall-lili", protocol: undefined }), null);
});
