"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { requestJson } from "@/lib/http/api-client";
import type { ImportSourceKey, ModuleViewKey } from "./shell/navigation-catalog";
import ImportChainRulesView from "./import-chain-rules-view";
import ImportRunRecordsView from "./import-run-records-view";
import "./import-monitor.css";
import CustomerServiceImportCard from "./customer-service-import-card";
import {
  type CurrentUser,
  type SalesImportBatch,
  type ImportHistoryResponse,
  type InventoryImportHistoryItem,
  type ErpReferenceImportBatch,
  type ProductShippingRateImportBatch,
  type UnifiedImportResponse,
  type UnifiedHistoryItem,
  type CustomerServiceImportHistoryItem,
  type NetshopImportHistoryItem,
  type ImportFeedback,
  DIRECT_IMPORT_FILE_SIZE,
  MAX_IMPORT_FILE_SIZE,
  SALES_UPLOAD_CHUNK_SIZE,
  MAX_INVENTORY_FILE_SIZE,
  DIRECT_INVENTORY_FILE_SIZE,
  INVENTORY_UPLOAD_CHUNK_SIZE,
  MAX_FINANCE_FILE_SIZE,
  MAX_JD_SKU_FILE_SIZE,
  MAX_TMALL_PRODUCT_ASSET_FILE_SIZE,
  TMALL_PRODUCT_ASSET_CHUNK_SIZE,
  formatCount,
  formatFileSize,
  addIsoDays,
  shanghaiIsoToday,
  issueText,
} from "./module-view-shared";

type ImportTab = ModuleViewKey<"import">;
function resolveImportHistoryDomain<T>(
  label: string,
  result: PromiseSettledResult<{ items?: T[] } | null>,
): { items: T[]; error: string | null } {
  if (result.status === "rejected") {
    return {
      items: [],
      error: `${label}：${result.reason instanceof Error ? result.reason.message : "请求失败"}`,
    };
  }
  if (!Array.isArray(result.value?.items)) return { items: [], error: `${label}：响应格式不完整` };
  return { items: result.value.items, error: null };
}

const tmallProductAssetShopOptions = [
  { value: "天猫-志高亿玖专卖店", label: "志高亿玖专卖店" },
  { value: "天猫-志高马思图专卖店", label: "志高马思图专卖店" },
  { value: "天猫-志高丽力专卖店", label: "志高丽力专卖店" },
  { value: "天猫-志高炊之王专卖店", label: "志高炊之王专卖店" },
  { value: "天猫-志高亿用专卖店", label: "志高亿用专卖店" },
  { value: "天猫-志高拓丰专卖店", label: "志高拓丰专卖店" },
] as const;

export default function ImportView({ importSource, currentUser, moduleView, onModuleViewChange }: { importSource?: ImportSourceKey; currentUser: CurrentUser | null; moduleView: ImportTab; onModuleViewChange: (view: ImportTab) => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const activeSection = moduleView;
  const canImport = currentUser?.role === "admin";
  const [selectedSource, setSelectedSource] = useState<ImportSourceKey>(() => importSource ?? "sales");
  const [snapshotDate, setSnapshotDate] = useState(shanghaiIsoToday);
  const [dailyStartDate, setDailyStartDate] = useState(() => addIsoDays(shanghaiIsoToday(), -1));
  const [dailyEndDate, setDailyEndDate] = useState(() => addIsoDays(shanghaiIsoToday(), -1));
  const [tmallProductAssetShop, setTmallProductAssetShop] = useState<string>(tmallProductAssetShopOptions[0].value);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadStage, setUploadStage] = useState("");
  const [feedback, setFeedback] = useState<ImportFeedback | null>(null);
  const [history, setHistory] = useState<UnifiedHistoryItem[]>([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyDomainErrors, setHistoryDomainErrors] = useState<string[]>([]);
  const historyVisible = activeSection === "history";
  const historyRequestGenerationRef = useRef(0);
  const historyRequestControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!importSource) return;
    setSelectedSource(importSource);
    setSelectedFile(null);
    setFeedback(null);
    if (importSource === "tmall_product_master") setSnapshotDate(addIsoDays(shanghaiIsoToday(), -1));
    if (importSource === "tmall_product_assets") setSnapshotDate(shanghaiIsoToday());
  }, [importSource]);

  useEffect(() => {
    if (canImport) return;
    setSelectedFile(null);
    setDragging(false);
  }, [canImport]);

  const loadHistory = useCallback(async () => {
    if (!historyVisible) return;
    const generation = historyRequestGenerationRef.current + 1;
    historyRequestGenerationRef.current = generation;
    historyRequestControllerRef.current?.abort();
    const controller = new AbortController();
    historyRequestControllerRef.current = controller;
    setHistoryLoading(true);
    setHistoryDomainErrors([]);
    try {
      const results = await Promise.allSettled([
        requestJson<ImportHistoryResponse>("/api/imports/sales", { signal: controller.signal }),
        requestJson<{ items?: InventoryImportHistoryItem[] }>("/api/imports/inventory", { signal: controller.signal }),
        requestJson<{ items?: ErpReferenceImportBatch[] }>("/api/imports/erp", { signal: controller.signal }),
        requestJson<{ items?: SalesImportBatch[] }>("/api/imports/finance", { signal: controller.signal }),
        requestJson<{ items?: NetshopImportHistoryItem[] }>("/api/netshop/import?limit=50", { signal: controller.signal }),
        requestJson<{ items?: CustomerServiceImportHistoryItem[] }>("/api/customer-service/import-history?limit=50", { signal: controller.signal }),
        requestJson<{ items?: ProductShippingRateImportBatch[] }>("/api/imports/product-shipping-rates?limit=50", { signal: controller.signal }),
      ] as const);
      if (controller.signal.aborted || generation !== historyRequestGenerationRef.current) return;
      const salesDomain = resolveImportHistoryDomain<SalesImportBatch>("销售导入历史", results[0]);
      const inventoryDomain = resolveImportHistoryDomain<InventoryImportHistoryItem>("库存导入历史", results[1]);
      const erpDomain = resolveImportHistoryDomain<ErpReferenceImportBatch>("ERP 主数据导入历史", results[2]);
      const financeDomain = resolveImportHistoryDomain<SalesImportBatch>("财报导入历史", results[3]);
      const netshopDomain = resolveImportHistoryDomain<NetshopImportHistoryItem>("网店导入历史", results[4]);
      const customerServiceDomain = resolveImportHistoryDomain<CustomerServiceImportHistoryItem>("客服会话导入历史", results[5]);
      const shippingRateDomain = resolveImportHistoryDomain<ProductShippingRateImportBatch>("SKU 快递费率导入历史", results[6]);
      const domainErrors = [salesDomain.error, inventoryDomain.error, erpDomain.error, financeDomain.error, netshopDomain.error, customerServiceDomain.error, shippingRateDomain.error]
        .filter((message): message is string => Boolean(message));
      const combined: UnifiedHistoryItem[] = [
        ...salesDomain.items.map((item) => ({ ...item, sourceKey: "sales" as const, sourceLabel: "吉客云 ERP · 销售明细" })),
        ...inventoryDomain.items.map((item) => ({ ...item, sourceKey: "inventory" as const, sourceLabel: "吉客云 ERP · 分仓库存" })),
        ...erpDomain.items.map((item) => ({ ...item, sourceKey: item.sourceKey, sourceLabel: item.sourceLabel })),
        ...financeDomain.items.map((item) => ({ ...item, sourceKey: "finance" as const, sourceLabel: "月度财报 · 志高事业部" })),
        ...netshopDomain.items
          .filter((item) => item.source === "jd_promotion" || item.source === "jd_product_master" || item.source === "jd_yimei_sku" || item.source.startsWith("tmall_") || item.dataset === "spu_daily" || item.dataset === "sku_daily")
          .map((item) => item.source === "jd_promotion"
            ? { ...item, sourceKey: "jd_promotion" as const, sourceLabel: "京准通 · 推广商品日数据" }
            : item.source === "tmall_product_master"
            ? { ...item, sourceKey: "tmall_product_master" as const, sourceLabel: "天猫店铺 · 店铺货品" }
            : item.source === "tmall_product_assets"
              ? { ...item, sourceKey: "tmall_product_assets" as const, sourceLabel: `${item.shopName.replace(/^天猫-/, "") || "天猫店铺"} · SPU 商品图` }
            : item.source === "tmall_product_daily"
              ? { ...item, sourceKey: "tmall_product_daily" as const, sourceLabel: "天猫店铺 · 生意参谋商品日数据" }
              : item.source === "tmall_promotion"
                ? { ...item, sourceKey: "tmall_promotion" as const, sourceLabel: "天猫店铺 · 推广商品日数据" }
            : item.dataset === "spu_daily"
            ? { ...item, sourceKey: "jd_spu_daily" as const, sourceLabel: "京东店铺 · 商品 SPU 日数据" }
            : item.dataset === "sku_daily"
              ? { ...item, sourceKey: "jd_sku_daily" as const, sourceLabel: "京东店铺 · 商品 SKU 日数据" }
            : item.source === "jd_yimei_sku"
              ? { ...item, sourceKey: "jd_sku_images" as const, sourceLabel: "京东店铺 · SKU 主图" }
              : { ...item, sourceKey: "jd_sku" as const, sourceLabel: "京东店铺 · 商品 SKU" }),
        ...customerServiceDomain.items.map((item) => ({ id: item.id, sourceKey: "customer_service" as const, sourceLabel: `客服会话 · ${item.shopName || "志高商用设备"}`, fileName: `${item.sessionFileName} + ${item.chatFileName}`, status: item.status, rowCount: item.conversationCount, insertedCount: item.matchedCount, warningCount: item.warnings.length, shopName: item.shopName, warnings: item.warnings.map((message) => ({ message })), createdAt: item.createdAt, completedAt: item.completedAt })),
        ...shippingRateDomain.items.map((item) => ({ ...item, sourceKey: "sku_shipping_rates" as const, sourceLabel: "年度利润表 · SKU 快递费率" })),
      ].sort((left, right) => Date.parse(right.completedAt || right.createdAt) - Date.parse(left.completedAt || left.createdAt));
      setHistory((current) => combined.length > 0 || domainErrors.length === 0 ? combined : current);
      setHistoryDomainErrors(domainErrors);
      if (domainErrors.length < results.length) setHistoryLoaded(true);
    } finally {
      if (!controller.signal.aborted && generation === historyRequestGenerationRef.current) {
        setHistoryLoading(false);
        if (historyRequestControllerRef.current === controller) historyRequestControllerRef.current = null;
      }
    }
  }, [historyVisible]);

  useEffect(() => {
    if (!historyVisible) {
      historyRequestGenerationRef.current += 1;
      historyRequestControllerRef.current?.abort();
      historyRequestControllerRef.current = null;
      setHistoryLoading(false);
      return;
    }
    void loadHistory();
    return () => historyRequestControllerRef.current?.abort();
  }, [historyVisible, loadHistory]);

  const sourceOptions: Array<{
    key: ImportSourceKey;
    icon: string;
    label: string;
    report: string;
    directEndpoint: string;
    chunkEndpoint: string;
    directFileSize: number;
    maxFileSize: number;
    chunkSize: number;
    needsSnapshotDate: boolean;
    extensions: string[];
    accept: string;
    systemLabel: string;
    formSource?: string;
    platform?: string;
    shopName?: string;
    includeSnapshotDate?: boolean;
    expectedDataset?: "sku_daily" | "spu_daily";
    needsDailyRange?: boolean;
    isCustomerService?: boolean;
  }> = [
    { key: "sales", icon: "销", label: "销售明细", report: "销售单明细账", directEndpoint: "/api/imports/sales", chunkEndpoint: "/api/imports/sales/chunks", directFileSize: DIRECT_IMPORT_FILE_SIZE, maxFileSize: MAX_IMPORT_FILE_SIZE, chunkSize: SALES_UPLOAD_CHUNK_SIZE, needsSnapshotDate: false, extensions: [".xlsx"], accept: ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", systemLabel: "吉客云 ERP", needsDailyRange: true },
    { key: "inventory", icon: "库", label: "分仓库存", report: "分仓库存快照", directEndpoint: "/api/imports/inventory", chunkEndpoint: "/api/imports/inventory/chunks", directFileSize: DIRECT_INVENTORY_FILE_SIZE, maxFileSize: MAX_INVENTORY_FILE_SIZE, chunkSize: INVENTORY_UPLOAD_CHUNK_SIZE, needsSnapshotDate: true, extensions: [".xlsx"], accept: ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", systemLabel: "吉客云 ERP" },
    { key: "products", icon: "品", label: "货品主数据", report: "货品资料", directEndpoint: "/api/imports/erp", chunkEndpoint: "/api/imports/erp/chunks", directFileSize: DIRECT_IMPORT_FILE_SIZE, maxFileSize: MAX_INVENTORY_FILE_SIZE, chunkSize: INVENTORY_UPLOAD_CHUNK_SIZE, needsSnapshotDate: false, extensions: [".xlsx"], accept: ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", systemLabel: "吉客云 ERP" },
    { key: "sku_shipping_rates", icon: "费", label: "SKU 快递费率", report: "年度利润表 · SKU累计", directEndpoint: "/api/imports/product-shipping-rates", chunkEndpoint: "/api/imports/product-shipping-rates/chunks", directFileSize: DIRECT_IMPORT_FILE_SIZE, maxFileSize: MAX_INVENTORY_FILE_SIZE, chunkSize: INVENTORY_UPLOAD_CHUNK_SIZE, needsSnapshotDate: false, extensions: [".xlsx"], accept: ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", systemLabel: "商品经营" },
    { key: "inventory_age", icon: "龄", label: "库龄", report: "库龄分析表", directEndpoint: "/api/imports/erp", chunkEndpoint: "/api/imports/erp/chunks", directFileSize: DIRECT_IMPORT_FILE_SIZE, maxFileSize: MAX_INVENTORY_FILE_SIZE, chunkSize: INVENTORY_UPLOAD_CHUNK_SIZE, needsSnapshotDate: true, extensions: [".xlsx"], accept: ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", systemLabel: "吉客云 ERP" },
    { key: "combos", icon: "组", label: "组合装", report: "组合装及子件", directEndpoint: "/api/imports/erp", chunkEndpoint: "/api/imports/erp/chunks", directFileSize: DIRECT_IMPORT_FILE_SIZE, maxFileSize: MAX_INVENTORY_FILE_SIZE, chunkSize: INVENTORY_UPLOAD_CHUNK_SIZE, needsSnapshotDate: false, extensions: [".xlsx"], accept: ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", systemLabel: "吉客云 ERP" },
    { key: "finance", icon: "财", label: "月度财报", report: "志高事业部销售财报", directEndpoint: "/api/imports/finance", chunkEndpoint: "", directFileSize: MAX_FINANCE_FILE_SIZE, maxFileSize: MAX_FINANCE_FILE_SIZE, chunkSize: MAX_FINANCE_FILE_SIZE, needsSnapshotDate: false, extensions: [".xls", ".xlsx"], accept: ".xls,.xlsx,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", systemLabel: "月度财报" },
    { key: "customer_service", icon: "服", label: "客服会话", report: "会话记录与聊天记录关联", directEndpoint: "/api/customer-service/import", chunkEndpoint: "", directFileSize: MAX_JD_SKU_FILE_SIZE, maxFileSize: MAX_JD_SKU_FILE_SIZE, chunkSize: MAX_JD_SKU_FILE_SIZE, needsSnapshotDate: false, extensions: [".xlsx", ".log"], accept: ".xlsx,.log,.txt", systemLabel: "客服系统", isCustomerService: true },
    { key: "jd_sku", icon: "京", label: "京东商品 SKU", report: "店铺后台 SKU 导出", directEndpoint: "/api/netshop/import", chunkEndpoint: "", directFileSize: MAX_JD_SKU_FILE_SIZE, maxFileSize: MAX_JD_SKU_FILE_SIZE, chunkSize: MAX_JD_SKU_FILE_SIZE, needsSnapshotDate: false, extensions: [".xlsx"], accept: ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", systemLabel: "京东店铺", formSource: "jd_product_master", platform: "京东", shopName: "志高商用设备旗舰店", includeSnapshotDate: true },
    { key: "jd_sku_daily", icon: "日", label: "京东商品 SKU 日数据", report: "商品明细 SKU 分天下载", directEndpoint: "/api/netshop/import", chunkEndpoint: "", directFileSize: MAX_JD_SKU_FILE_SIZE, maxFileSize: MAX_JD_SKU_FILE_SIZE, chunkSize: MAX_JD_SKU_FILE_SIZE, needsSnapshotDate: false, extensions: [".xlsx"], accept: ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", systemLabel: "京东商智", formSource: "jd_sku_daily", platform: "京东", shopName: "志高商用设备旗舰店", expectedDataset: "sku_daily", needsDailyRange: true },
    { key: "jd_spu_daily", icon: "日", label: "京东商品 SPU 日数据", report: "商品明细 SPU 分天下载", directEndpoint: "/api/netshop/import", chunkEndpoint: "", directFileSize: MAX_JD_SKU_FILE_SIZE, maxFileSize: MAX_JD_SKU_FILE_SIZE, chunkSize: MAX_JD_SKU_FILE_SIZE, needsSnapshotDate: false, extensions: [".xlsx"], accept: ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", systemLabel: "京东商智", formSource: "jd_sku_daily", platform: "京东", shopName: "志高商用设备旗舰店", expectedDataset: "spu_daily", needsDailyRange: true },
    { key: "jd_sku_images", icon: "图", label: "京东 SKU 主图", report: "亿美/商品主图导出", directEndpoint: "/api/netshop/import", chunkEndpoint: "", directFileSize: MAX_JD_SKU_FILE_SIZE, maxFileSize: MAX_JD_SKU_FILE_SIZE, chunkSize: MAX_JD_SKU_FILE_SIZE, needsSnapshotDate: false, extensions: [".xlsx", ".csv"], accept: ".xlsx,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv", systemLabel: "京东 SKU 主图", formSource: "jd_yimei_sku", platform: "京东", shopName: "志高商用设备旗舰店" },
    { key: "tmall_product_master", icon: "猫", label: "天猫亿玖店铺货品", report: "店铺商品发布模板（SKU）", directEndpoint: "/api/netshop/import", chunkEndpoint: "", directFileSize: MAX_JD_SKU_FILE_SIZE, maxFileSize: MAX_JD_SKU_FILE_SIZE, chunkSize: MAX_JD_SKU_FILE_SIZE, needsSnapshotDate: true, extensions: [".xlsx"], accept: ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", systemLabel: "天猫亿玖", formSource: "tmall_product_master", platform: "天猫", shopName: "天猫-志高亿玖专卖店" },
    { key: "tmall_product_assets", icon: "图", label: "天猫 SPU 商品图", report: "店透视商品下载（内嵌主图与 SPU 链接）", directEndpoint: "/api/netshop/import", chunkEndpoint: "/api/netshop/import/chunks", directFileSize: MAX_JD_SKU_FILE_SIZE, maxFileSize: MAX_TMALL_PRODUCT_ASSET_FILE_SIZE, chunkSize: TMALL_PRODUCT_ASSET_CHUNK_SIZE, needsSnapshotDate: true, extensions: [".xlsx"], accept: ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", systemLabel: "天猫店透视", formSource: "tmall_product_assets", platform: "天猫" },
    { key: "tmall_product_daily", icon: "参", label: "天猫商品日数据", report: "生意参谋商品全部明细", directEndpoint: "/api/netshop/import", chunkEndpoint: "", directFileSize: MAX_JD_SKU_FILE_SIZE, maxFileSize: MAX_JD_SKU_FILE_SIZE, chunkSize: MAX_JD_SKU_FILE_SIZE, needsSnapshotDate: false, extensions: [".xls"], accept: ".xls,application/vnd.ms-excel", systemLabel: "生意参谋", formSource: "tmall_product_daily", platform: "天猫", shopName: "天猫-志高亿玖专卖店", needsDailyRange: true },
    { key: "tmall_promotion", icon: "推", label: "天猫推广日数据", report: "推广商品报表 ZIP", directEndpoint: "/api/netshop/import", chunkEndpoint: "", directFileSize: MAX_JD_SKU_FILE_SIZE, maxFileSize: MAX_JD_SKU_FILE_SIZE, chunkSize: MAX_JD_SKU_FILE_SIZE, needsSnapshotDate: false, extensions: [".zip"], accept: ".zip,application/zip", systemLabel: "天猫推广", formSource: "tmall_promotion", platform: "天猫", shopName: "天猫-志高亿玖专卖店", needsDailyRange: true },
  ];
  const activeSource = sourceOptions.find((item) => item.key === selectedSource)!;
  const activeShopName = selectedSource === "tmall_product_assets"
    ? tmallProductAssetShop
    : activeSource.shopName;

  const acceptFile = useCallback((candidate?: File) => {
    setDragging(false);
    if (!candidate || !canImport) return;
    if (!activeSource.extensions.some((extension) => candidate.name.toLowerCase().endsWith(extension))) {
      setSelectedFile(null);
      setFeedback({
        tone: "error",
        title: "文件格式不支持",
        message: `请选择${activeSource.systemLabel}的 ${activeSource.extensions.join(" / ")} ${activeSource.report}。`,
        details: [],
      });
      return;
    }
    if (candidate.size > activeSource.maxFileSize) {
      setSelectedFile(null);
      setFeedback({
        tone: "error",
        title: `文件超过 ${formatFileSize(activeSource.maxFileSize)}`,
        message: `当前文件为 ${formatFileSize(candidate.size)}，超过${activeSource.label}单文件限制。`,
        details: [],
      });
      return;
    }
    setSelectedFile(candidate);
    setFeedback(null);
  }, [activeSource.extensions, activeSource.label, activeSource.maxFileSize, activeSource.report, activeSource.systemLabel, canImport]);

  const showImportResult = (payload: UnifiedImportResponse | null, responseStatus: number) => {
    const warnings = payload?.warnings ?? payload?.batch?.warnings ?? [];
    const errors = payload?.errors ?? [];
    if (!payload?.ok || payload.status === "rejected" || (activeSource.expectedDataset && (payload.batch as { dataset?: string } | undefined)?.dataset !== activeSource.expectedDataset)) {
      setFeedback({
        tone: "error",
        title: "导入未完成",
        message: payload?.message || `文件校验或导入失败（${responseStatus}）`,
        details: errors.slice(0, 8).map(issueText),
      });
      return false;
    }
    if (payload.status === "duplicate") {
      setFeedback({
        tone: "duplicate",
        title: "业务内容完全一致",
        message: payload.message || `全部标准化资料与当前${activeSource.label}数据一致，系统没有重复写入。`,
        details: warnings.slice(0, 8).map(issueText),
      });
    } else if (warnings.length || (payload.batch?.warningCount ?? 0) > 0) {
      setFeedback({
        tone: "warning",
        title: `导入完成，含 ${payload.batch?.warningCount ?? warnings.length} 条提示`,
        message: payload.message || `成功写入 ${formatCount(payload.batch?.insertedCount)} 行${activeSource.label}数据。`,
        details: warnings.slice(0, 8).map(issueText),
      });
    } else {
      setFeedback({
        tone: "success",
        title: `${activeSource.label}导入成功`,
        message: payload.message || `成功写入 ${formatCount(payload.batch?.insertedCount)} 行，相关分析已更新。`,
        details: [
          ...((payload.batch?.excludedCount ?? 0) > 0 ? [`已剔除刷刷仓 ${formatCount(payload.batch?.excludedCount)} 行`] : []),
          ...(payload.verification?.verified ? [`落库回查 ${formatCount(payload.verification.readbackRowCount)} 行 · ${payload.verification.dateMin ?? payload.batch?.snapshotDate ?? "快照"}${payload.verification.dateMax && payload.verification.dateMax !== payload.verification.dateMin ? ` 至 ${payload.verification.dateMax}` : ""}`] : []),
        ],
      });
    }
    return true;
  };

  const importChunkedFile = async (file: File): Promise<{ payload: UnifiedImportResponse | null; status: number }> => {
    const chunkCount = Math.ceil(file.size / activeSource.chunkSize);
    const fingerprint = `${selectedSource}-v1:${file.name}:${file.size}:${file.lastModified}:${activeSource.chunkSize}`;
    setUploadStage("正在检查可续传的上传进度…");
    const initResponse = await fetch(activeSource.chunkEndpoint, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        action: "init",
        source: activeSource.formSource ?? selectedSource,
        ...(activeSource.platform ? { platform: activeSource.platform } : {}),
        ...(activeShopName ? { shopName: activeShopName } : {}),
        fileName: file.name,
        fileSizeBytes: file.size,
        chunkCount,
        fingerprint,
        ...((activeSource.needsSnapshotDate || activeSource.includeSnapshotDate)
          ? { snapshotDate: activeSource.includeSnapshotDate ? shanghaiIsoToday() : snapshotDate }
          : {}),
        ...(activeSource.needsDailyRange ? { expectedStartDate: dailyStartDate, expectedEndDate: dailyEndDate } : {}),
      }),
    });
    const initPayload = await initResponse.json().catch(() => null) as UnifiedImportResponse | null;
    if (!initResponse.ok || !initPayload?.ok || !initPayload.upload) {
      throw new Error(initPayload?.message || "无法创建分片上传任务");
    }
    const uploaded = new Set(initPayload.upload.receivedChunkIndexes);
    let uploadedBytes = 0;
    for (const index of uploaded) {
      const start = index * activeSource.chunkSize;
      uploadedBytes += Math.min(activeSource.chunkSize, file.size - start);
    }
    setUploadProgress(Math.round((uploadedBytes / file.size) * 100));

    for (let index = 0; index < chunkCount; index += 1) {
      if (uploaded.has(index)) continue;
      const start = index * activeSource.chunkSize;
      const part = file.slice(start, Math.min(start + activeSource.chunkSize, file.size));
      setUploadStage(`正在上传第 ${index + 1}/${chunkCount} 个分片…`);
      const partResponse = await fetch(activeSource.chunkEndpoint, {
        method: "PUT",
        headers: { "x-upload-id": initPayload.upload.id, "x-chunk-index": String(index), "content-type": "application/octet-stream" },
        body: part,
      });
      const partPayload = await partResponse.json().catch(() => null) as UnifiedImportResponse | null;
      if (!partResponse.ok || !partPayload?.ok) throw new Error(partPayload?.message || `第 ${index + 1} 个分片上传失败`);
      uploadedBytes += part.size;
      setUploadProgress(Math.min(99, Math.round((uploadedBytes / file.size) * 100)));
    }

    setUploadProgress(100);
    setUploadStage(`分片已上传，正在合并并校验${activeSource.label}…`);
    const completeResponse = await fetch(activeSource.chunkEndpoint, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ action: "complete", source: activeSource.formSource ?? selectedSource, uploadId: initPayload.upload.id, ...(activeSource.platform ? { platform: activeSource.platform } : {}), ...(activeShopName ? { shopName: activeShopName } : {}), ...((activeSource.needsSnapshotDate || activeSource.includeSnapshotDate) ? { snapshotDate: activeSource.includeSnapshotDate ? shanghaiIsoToday() : snapshotDate } : {}), ...(activeSource.needsDailyRange ? { expectedStartDate: dailyStartDate, expectedEndDate: dailyEndDate } : {}) }),
    });
    return {
      payload: await completeResponse.json().catch(() => null) as UnifiedImportResponse | null,
      status: completeResponse.status,
    };
  };

  const importFile = async () => {
    if (!canImport) {
      setFeedback({ tone: "error", title: "当前账号为只读模式", message: "仅管理员可以上传和导入业务数据。", details: [] });
      return;
    }
    if (!selectedFile || uploading) return;
    if (activeSource.needsSnapshotDate && !/^\d{4}-\d{2}-\d{2}$/.test(snapshotDate)) {
      setFeedback({ tone: "error", title: "请选择快照日期", message: `${activeSource.label}必须指定有效的数据快照日期。`, details: [] });
      return;
    }
    if (activeSource.needsDailyRange && (!/^\d{4}-\d{2}-\d{2}$/.test(dailyStartDate) || !/^\d{4}-\d{2}-\d{2}$/.test(dailyEndDate) || dailyStartDate > dailyEndDate)) {
      setFeedback({ tone: "error", title: "请选择有效业务日期区间", message: "该范围用于精确替换本次报表覆盖的数据，不要求每个自然日都有资料。", details: [] });
      return;
    }
    setUploading(true);
    setFeedback(null);
    setUploadProgress(0);
    try {
      let outcome: { payload: UnifiedImportResponse | null; status: number };
      if (selectedFile.size > activeSource.directFileSize) {
        outcome = await importChunkedFile(selectedFile);
      } else {
        setUploadStage(`正在上传并校验${activeSource.label}…`);
        let response: Response;
        if (selectedSource === "finance") {
          response = await fetch(activeSource.directEndpoint, {
            method: "POST",
            headers: { "content-type": "application/octet-stream", "x-file-name": encodeURIComponent(selectedFile.name) },
            body: selectedFile,
          });
        } else {
          const formData = new FormData();
          formData.append("file", selectedFile);
          formData.append("source", activeSource.formSource ?? (selectedSource === "sales" ? "jky" : selectedSource));
          if (activeSource.platform) formData.append("platform", activeSource.platform);
          if (activeShopName) formData.append("shopName", activeShopName);
          if (activeSource.expectedDataset) formData.append("expectedDataset", activeSource.expectedDataset);
          if (activeSource.needsDailyRange) {
            formData.append("expectedStartDate", dailyStartDate);
            formData.append("expectedEndDate", dailyEndDate);
          }
          if (activeSource.needsSnapshotDate || activeSource.includeSnapshotDate) formData.append("snapshotDate", activeSource.includeSnapshotDate ? shanghaiIsoToday() : snapshotDate);
          response = await fetch(activeSource.directEndpoint, { method: "POST", body: formData });
        }
        outcome = { payload: await response.json().catch(() => null) as UnifiedImportResponse | null, status: response.status };
      }
      if (showImportResult(outcome.payload, outcome.status)) await loadHistory();
    } catch (requestError) {
      setFeedback({
        tone: "error",
        title: "导入请求失败",
        message: requestError instanceof Error ? `${requestError.message}；重新选择同一文件后会自动续传已完成的分片。` : "网络异常，请稍后重试。",
        details: [],
      });
    } finally {
      setUploading(false);
      setUploadStage("");
    }
  };

  return (
    <>
      <div className="subnav" role="tablist" aria-label="数据导入工作区">
        <button type="button" role="tab" aria-selected={activeSection === "files"} className={activeSection === "files" ? "active" : ""} onClick={() => onModuleViewChange("files")}>文件导入</button>
        <button type="button" role="tab" aria-selected={activeSection === "history"} className={activeSection === "history" ? "active" : ""} onClick={() => onModuleViewChange("history")}>运行记录</button>
        <button type="button" role="tab" aria-selected={activeSection === "chains"} className={activeSection === "chains" ? "active" : ""} onClick={() => onModuleViewChange("chains")}>链路规则</button>
      </div>
      {activeSection === "files" && <>
      <section className="import-grid">
        <article className="panel import-panel">
          <span className="eyebrow">第 1 步</span><h2>选择数据类型</h2><p>销售、库存、主数据、京东与天猫网店数据使用同一套批次校验和导入历史。</p>
          <div className="source-grid">{sourceOptions.map((item) => <button type="button" className={item.key === selectedSource ? "selected" : ""} aria-pressed={item.key === selectedSource} key={item.key} onClick={() => { setSelectedSource(item.key); if (item.key === "tmall_product_master") setSnapshotDate(addIsoDays(shanghaiIsoToday(), -1)); if (item.key === "tmall_product_assets") setSnapshotDate(shanghaiIsoToday()); setSelectedFile(null); setFeedback(null); setUploadProgress(0); }}><span>{item.icon}</span><strong>{item.label}</strong><small>{item.report}</small></button>)}</div>
        </article>
        <article className="panel import-panel">
          {activeSource.isCustomerService ? <CustomerServiceImportCard canImport={canImport} onCompleted={loadHistory} /> : <>
          <span className="eyebrow">第 2 步</span><h2>上传{activeSource.label}报表</h2><p>支持 {activeSource.extensions.join(" / ")}，单文件最大 {formatFileSize(activeSource.maxFileSize)}；{selectedSource === "sku_shipping_rates" ? "固定读取“SKU累计”，按规格代码发布完整快递费率集合。" : "月度财报按月份自动去重并合并同名科目。"}</p>
          {!canImport && <div className="inventory-feedback" role="note"><span aria-hidden="true">只读</span><div><strong>当前账号仅可查看导入历史</strong><p>文件选择、拖放和正式导入仅向管理员开放。</p></div></div>}
          {selectedSource === "tmall_product_assets" && <label className="import-snapshot-field"><span>目标天猫店铺</span><select value={tmallProductAssetShop} disabled={!canImport || uploading} onChange={(event) => { setTmallProductAssetShop(event.target.value); setSelectedFile(null); setFeedback(null); }}>{tmallProductAssetShopOptions.map((shop) => <option value={shop.value} key={shop.value}>{shop.label}</option>)}</select></label>}
          {activeSource.needsSnapshotDate && <label className="import-snapshot-field"><span>数据快照日期</span><input type="date" value={snapshotDate} max={shanghaiIsoToday()} disabled={!canImport} onChange={(event) => setSnapshotDate(event.target.value)} /></label>}
          {activeSource.needsDailyRange && <div className="import-snapshot-field"><label><span>业务范围起始日期</span><input type="date" value={dailyStartDate} max={dailyEndDate} disabled={!canImport} onChange={(event) => setDailyStartDate(event.target.value)} /></label><label><span>业务范围结束日期</span><input type="date" value={dailyEndDate} min={dailyStartDate} max={addIsoDays(shanghaiIsoToday(), -1)} disabled={!canImport} onChange={(event) => setDailyEndDate(event.target.value)} /></label></div>}
          <input
            ref={inputRef}
            className="file-input-hidden"
            type="file"
            accept={activeSource.accept}
            disabled={!canImport}
            onChange={(event) => {
              acceptFile(event.currentTarget.files?.[0]);
              event.currentTarget.value = "";
            }}
          />
          <button
            type="button"
            className={`dropzone ${selectedFile ? "uploaded" : ""} ${dragging ? "dragging" : ""}`}
            disabled={!canImport}
            onClick={() => { if (canImport) inputRef.current?.click(); }}
            onDragEnter={(event) => { event.preventDefault(); if (canImport) setDragging(true); }}
            onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = canImport ? "copy" : "none"; if (canImport) setDragging(true); }}
            onDragLeave={(event) => { event.preventDefault(); if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false); }}
            onDrop={(event) => { event.preventDefault(); if (canImport) acceptFile(event.dataTransfer.files?.[0]); }}
          >
            <span>{selectedFile ? "✓" : "↑"}</span>
            <strong>{selectedFile ? selectedFile.name : `将 ${activeSource.extensions.join(" / ")} 文件拖到此处，或点击选择`}</strong>
            <small>{selectedFile ? `${formatFileSize(selectedFile.size)} · ${selectedFile.size > activeSource.directFileSize ? "将启用分片上传与断点续传" : "将直接上传并校验"}` : `上传后将写入${activeSource.label}正式数据`}</small>
          </button>
          <div className="import-actions">
            <span>{uploading ? uploadStage : selectedFile ? `准备导入${activeSource.systemLabel} ${activeSource.label}` : "请选择待导入文件"}</span>
            <button type="button" className="primary-button" disabled={!canImport || !selectedFile || uploading} onClick={() => void importFile()}>{uploading ? `${uploadProgress}%` : canImport ? "开始导入" : "仅管理员可导入"}</button>
          </div>
          {uploading && selectedFile && <div className="import-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={uploadProgress} aria-label={`${activeSource.label}上传进度`}><span style={{ width: `${uploadProgress}%` }} /></div>}
          </>}
        </article>
      </section>
      {feedback && <section className={`import-feedback import-feedback-${feedback.tone}`} role={feedback.tone === "error" ? "alert" : "status"} aria-live="polite"><span className="feedback-symbol">{feedback.tone === "success" ? "✓" : feedback.tone === "duplicate" ? "≡" : feedback.tone === "warning" ? "!" : "×"}</span><div><strong>{feedback.title}</strong><p>{feedback.message}</p>{feedback.details.length > 0 && <ul>{feedback.details.map((detail, index) => <li key={`${detail}-${index}`}>{detail}</li>)}</ul>}</div></section>}
      </>}
      {activeSection === "chains" && <ImportChainRulesView currentUser={currentUser} />}
      {activeSection === "history" && <ImportRunRecordsView history={history} loading={historyLoading} loaded={historyLoaded} errors={historyDomainErrors} onRefresh={() => void loadHistory()} onShowChains={() => onModuleViewChange("chains")} />}
    </>
  );
}
