"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { normalizeTableCellValue, scoreTableFilterControl, tableRowMatchesColumnFilters, tableSummaryShowsPartialDataset } from "@/lib/ui/table-column-filter";

type ExternalFilterControl = {
  element: HTMLElement;
  label: string;
  score: number;
};

type ColumnFilterTarget = {
  mode: "values" | "controls";
  table: HTMLTableElement;
  header: HTMLTableCellElement;
  columnIndex: number;
  label: string;
  options: string[];
  selected: string[];
  left: number;
  top: number;
  width: number;
  visibleRows: number;
  totalRows: number;
  controls: ExternalFilterControl[];
};

const interactiveSelector = "button,input,select,textarea,a,label";
const filterValueSeparator = "\u001f";
const externalFilterControlSelector = [
  "button.searchable-select-trigger[aria-label]",
  "button.multi-filter-trigger[aria-label]",
  "input[type='search'][aria-label]",
  "input[type='search'][placeholder]",
  "input[aria-label*='搜索']",
  "input[placeholder*='搜索']",
  "select",
  "[role='combobox'][aria-label]",
  "[role='combobox'][aria-labelledby]",
  "summary[aria-label]",
  "details > summary",
  "[data-column-filter-control='true']",
].join(",");

function tableRows(table: HTMLTableElement) {
  return Array.from(table.tBodies).flatMap((body) => Array.from(body.rows));
}

function tableCellValue(cell: HTMLTableCellElement | undefined) {
  if (!cell) return "";
  const selectedValues = Array.from(cell.querySelectorAll("select")).map((select) => (
    normalizeTableCellValue(select.selectedOptions[0]?.textContent ?? select.value)
  )).filter(Boolean);
  if (selectedValues.length) return normalizeTableCellValue(selectedValues.join(" / "));
  const enteredValues = Array.from(cell.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("input:not([type='checkbox']):not([type='radio']),textarea"))
    .map((control) => normalizeTableCellValue(control.value)).filter(Boolean);
  if (enteredValues.length) return normalizeTableCellValue(enteredValues.join(" / "));
  const checks = Array.from(cell.querySelectorAll<HTMLInputElement>("input[type='checkbox'],input[type='radio']"));
  if (checks.length && !normalizeTableCellValue(cell.innerText)) return checks.some((control) => control.checked) ? "已选择" : "未选择";
  return normalizeTableCellValue(cell.innerText);
}

function tableCellFilterValues(cell: HTMLTableCellElement | undefined) {
  if (!cell) return [""];
  const explicitValues = cell.dataset.columnFilterValues;
  if (explicitValues === undefined) return [tableCellValue(cell)];
  return [...new Set(explicitValues.split(filterValueSeparator).map(normalizeTableCellValue).filter(Boolean))];
}

function headerLabel(header: HTMLTableCellElement) {
  return normalizeTableCellValue(
    header.dataset.columnFilterLabel
    ?? header.dataset.columnFilterSourceAriaLabel
    ?? header.innerText,
  ) || `第 ${header.cellIndex + 1} 列`;
}

function columnOptions(table: HTMLTableElement, columnIndex: number) {
  return [...new Set(tableRows(table)
    .filter((row) => !Array.from(row.cells).some((cell) => cell.colSpan > 1))
    .flatMap((row) => tableCellFilterValues(row.cells[columnIndex])))]
    .sort((left, right) => left.localeCompare(right, "zh-CN", { numeric: true }));
}

function tableBoundary(table: HTMLTableElement) {
  // A tabpanel can be only the scroll wrapper while its pagination and filters
  // are siblings in the enclosing business panel (for example market review).
  return table.closest<HTMLElement>(".table-panel, article, section")
    ?? table.closest<HTMLElement>("[role='tabpanel']")
    ?? table.parentElement;
}

function numericText(value: string) {
  const parsed = Number(value.replace(/,/g, ""));
  return Number.isFinite(parsed) ? parsed : null;
}

function declaredDatasetTotal(table: HTMLTableElement) {
  const declared = table.dataset.columnFilterTotal;
  return declared === undefined ? null : numericText(declared);
}

function tableUsesPartialDataset(table: HTMLTableElement) {
  const declaredScope = table.dataset.columnFilterScope;
  if (declaredScope === "full") return false;
  if (declaredScope === "server" || declaredScope === "none") return true;

  const boundary = tableBoundary(table);
  if (!boundary) return false;
  const rowCount = tableRows(table).filter((row) => !Array.from(row.cells).some((cell) => cell.colSpan > 1)).length;
  const total = declaredDatasetTotal(table);
  if (total !== null && total > rowCount) return true;

  const paginationAction = Array.from(boundary.querySelectorAll<HTMLElement>("button,select"))
    .some((control) => {
      if (control.closest("table") === table) return false;
      const label = normalizeTableCellValue(`${control.getAttribute("aria-label") ?? ""} ${control.textContent ?? ""}`);
      return /上一页|下一页|继续加载|加载更多|页码|每页(?:加载|条数)/.test(label);
    });
  if (paginationAction) return true;

  // Pagination summaries often sit below the rows. The helper intentionally
  // inspects the untruncated boundary text so a long table cannot hide its
  // footer from scope detection.
  return tableSummaryShowsPartialDataset(boundary.textContent ?? "", rowCount);
}

function controlLabel(control: HTMLElement) {
  const ariaLabel = control.getAttribute("aria-label");
  if (ariaLabel) return normalizeTableCellValue(ariaLabel);
  const labelled = control.closest("label");
  const fieldLabel = labelled?.querySelector(":scope > span")?.textContent;
  if (fieldLabel) return normalizeTableCellValue(fieldLabel);
  const leadingLabel = control.querySelector(":scope > span")?.textContent;
  if (leadingLabel) return normalizeTableCellValue(leadingLabel);
  if (control instanceof HTMLSelectElement) {
    const selectedLabel = control.selectedOptions[0]?.textContent ?? "";
    const firstLabel = control.options[0]?.textContent ?? "";
    return normalizeTableCellValue(`${selectedLabel} ${firstLabel}`);
  }
  const placeholder = control instanceof HTMLInputElement || control instanceof HTMLTextAreaElement
    ? control.placeholder
    : "";
  return normalizeTableCellValue(placeholder || control.textContent || "");
}

function controlIsVisible(control: HTMLElement) {
  if (!control.isConnected || control.closest("table") || control.closest(".column-filter-popover")) return false;
  if (control instanceof HTMLButtonElement || control instanceof HTMLInputElement || control instanceof HTMLSelectElement || control instanceof HTMLTextAreaElement) {
    if (control.disabled) return false;
  }
  return control.getClientRects().length > 0;
}

function externalFilterControls(table: HTMLTableElement, header: HTMLTableCellElement) {
  const boundary = tableBoundary(table);
  const moduleRoot = table.closest<HTMLElement>("main")
    ?? table.closest<HTMLElement>(".module-stage")
    ?? document.body;
  const find = (root: ParentNode | null) => {
    if (!root) return [];
    const candidates = Array.from(root.querySelectorAll<HTMLElement>(externalFilterControlSelector));
    const scored = candidates
      .filter(controlIsVisible)
      .map((element) => {
        const label = controlLabel(element);
        return { element, label, score: scoreTableFilterControl(headerLabel(header), label) };
      })
      .filter((item) => item.label && item.score >= 30)
      .sort((left, right) => right.score - left.score || left.label.localeCompare(right.label, "zh-CN"));
    if (!scored.length) return scored;
    const bestScore = scored[0]!.score;
    const unique = new Map<string, ExternalFilterControl>();
    for (const item of scored) {
      if (item.score < bestScore - 18) continue;
      const key = item.label.toLocaleLowerCase("zh-CN");
      if (!unique.has(key)) unique.set(key, item);
    }
    return [...unique.values()].slice(0, 4);
  };
  const nearby = find(boundary);
  return nearby.length ? nearby : find(moduleRoot);
}

function activateExternalControl(control: ExternalFilterControl) {
  const element = control.element;
  element.scrollIntoView({ block: "nearest", inline: "nearest" });
  element.focus({ preventScroll: true });
  if (element instanceof HTMLSelectElement) {
    try {
      element.showPicker();
    } catch {
      element.click();
    }
    return;
  }
  if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) return;
  element.click();
}

function clearHeaderFilterUi(header: HTMLTableCellElement) {
  const sourceAriaLabel = header.dataset.columnFilterSourceAriaLabel;
  if (sourceAriaLabel) header.setAttribute("aria-label", sourceAriaLabel);
  else header.removeAttribute("aria-label");
  if (header.dataset.columnFilterOwnedTabIndex === "true") header.removeAttribute("tabindex");
  delete header.dataset.columnFilterHeader;
  delete header.dataset.columnFilterResolvedLabel;
  delete header.dataset.columnFilterActive;
  delete header.dataset.columnFilterMode;
  delete header.dataset.columnFilterOwnedTabIndex;
  header.removeAttribute("aria-haspopup");
}

function targetPosition(header: HTMLTableCellElement) {
  const bounds = header.getBoundingClientRect();
  const width = Math.min(320, Math.max(240, window.innerWidth - 24));
  const left = Math.max(12, Math.min(bounds.right - width, window.innerWidth - width - 12));
  const preferredTop = bounds.bottom + 7;
  return {
    left,
    top: Math.min(preferredTop, Math.max(12, window.innerHeight - 410)),
    width,
  };
}

function tablesInSubtree(node: Node) {
  if (node instanceof HTMLTableElement) return [node];
  if (!(node instanceof Element)) return [];
  return Array.from(node.querySelectorAll<HTMLTableElement>("table"));
}

function nodeContainsExternalFilterControl(node: Node) {
  return node instanceof Element && (
    node.matches(externalFilterControlSelector)
    || Boolean(node.querySelector(externalFilterControlSelector))
  );
}

function attributeMutationTouchesExternalFilterControl(record: MutationRecord, target: Element) {
  if (target.matches(externalFilterControlSelector)) return true;
  if (record.attributeName === "data-column-filter-control" && record.oldValue === "true") return true;
  if (record.attributeName === "role" && record.oldValue === "combobox") return true;
  return target.matches("button.searchable-select-trigger,button.multi-filter-trigger,input,select,summary");
}

function tablesRelatedToExternalFilter(target: Element) {
  const boundarySelector = ".table-panel, article, section, [role='tabpanel'], .module-stage, main";
  let boundary = target.closest<HTMLElement>(boundarySelector);
  while (boundary) {
    const tables = Array.from(boundary.querySelectorAll<HTMLTableElement>("table"));
    if (tables.length > 0) return tables;
    boundary = boundary.parentElement?.closest<HTMLElement>(boundarySelector) ?? null;
  }
  return [];
}

export default function TableColumnFilters() {
  const filtersRef = useRef(new WeakMap<HTMLTableElement, Map<number, Set<string>>>());
  const popoverRef = useRef<HTMLDivElement>(null);
  const [target, setTarget] = useState<ColumnFilterTarget | null>(null);
  const [query, setQuery] = useState("");

  const filtersFor = useCallback((table: HTMLTableElement) => {
    const existing = filtersRef.current.get(table);
    if (existing) return existing;
    const created = new Map<number, Set<string>>();
    filtersRef.current.set(table, created);
    return created;
  }, []);

  const applyFilters = useCallback((table: HTMLTableElement) => {
    if (tableUsesPartialDataset(table)) {
      filtersFor(table).clear();
      const rows = tableRows(table);
      for (const row of rows) row.classList.remove("column-filter-row-hidden");
      const totalRows = rows.filter((row) => !Array.from(row.cells).some((cell) => cell.colSpan > 1)).length;
      table.dataset.columnFilterVisibleRows = String(totalRows);
      table.dataset.columnFilterTotalRows = String(totalRows);
      return { visibleRows: totalRows, totalRows };
    }
    const filters = filtersFor(table);
    const rows = tableRows(table);
    let visibleRows = 0;
    for (const row of rows) {
      if (Array.from(row.cells).some((cell) => cell.colSpan > 1)) {
        row.classList.remove("column-filter-row-hidden");
        continue;
      }
      const values = Array.from(row.cells).map((cell) => tableCellFilterValues(cell));
      const visible = tableRowMatchesColumnFilters(values, filters);
      row.classList.toggle("column-filter-row-hidden", !visible);
      if (visible) visibleRows += 1;
    }
    for (const header of Array.from(table.querySelectorAll<HTMLTableCellElement>("thead th"))) {
      header.dataset.columnFilterActive = filters.get(header.cellIndex)?.size ? "true" : "false";
    }
    table.dataset.columnFilterVisibleRows = String(visibleRows);
    table.dataset.columnFilterTotalRows = String(rows.filter((row) => !Array.from(row.cells).some((cell) => cell.colSpan > 1)).length);
    return { visibleRows, totalRows: Number(table.dataset.columnFilterTotalRows) };
  }, [filtersFor]);

  const prepareTable = useCallback((table: HTMLTableElement) => {
    const partialDataset = tableUsesPartialDataset(table);
    const explicitlyDisabled = table.dataset.columnFilterScope === "none";
    let enabledHeaders = 0;
    for (const header of Array.from(table.querySelectorAll<HTMLTableCellElement>("thead th"))) {
      if (header.dataset.columnFilterHeader !== "true") {
        const sourceAriaLabel = header.getAttribute("aria-label");
        if (sourceAriaLabel) header.dataset.columnFilterSourceAriaLabel = sourceAriaLabel;
      }
      const label = headerLabel(header);
      const controls = partialDataset && !explicitlyDisabled ? externalFilterControls(table, header) : [];
      if (explicitlyDisabled || header.dataset.columnFilterDisabled === "true" || (partialDataset && controls.length === 0)) {
        filtersFor(table).delete(header.cellIndex);
        clearHeaderFilterUi(header);
        continue;
      }
      enabledHeaders += 1;
      header.dataset.columnFilterHeader = "true";
      header.dataset.columnFilterResolvedLabel = label;
      header.dataset.columnFilterMode = partialDataset ? "server" : "local";
      if (!header.hasAttribute("tabindex")) {
        header.tabIndex = 0;
        header.dataset.columnFilterOwnedTabIndex = "true";
      }
      const directControl = partialDataset && controls.length === 1 ? controls[0]!.element : null;
      if (directControl instanceof HTMLInputElement || directControl instanceof HTMLTextAreaElement) {
        header.removeAttribute("aria-haspopup");
      } else {
        const opensListbox = directControl instanceof HTMLSelectElement || directControl?.getAttribute("role") === "combobox";
        header.setAttribute("aria-haspopup", directControl?.getAttribute("aria-haspopup") || (opensListbox ? "listbox" : "dialog"));
      }
      header.setAttribute("aria-label", `${label}，打开${partialDataset ? "全量" : ""}列筛选`);
      header.dataset.columnFilterActive = partialDataset ? "false" : filtersFor(table).get(header.cellIndex)?.size ? "true" : "false";
    }
    table.dataset.columnFilterTable = enabledHeaders > 0 ? "true" : "false";
    table.dataset.columnFilterScopeResolved = explicitlyDisabled ? "none" : partialDataset ? "server" : "full";
    return applyFilters(table);
  }, [applyFilters, filtersFor]);

  const openForHeader = useCallback((header: HTMLTableCellElement) => {
    const table = header.closest("table");
    if (!(table instanceof HTMLTableElement)) return;
    if (header.dataset.columnFilterHeader !== "true" || table.dataset.columnFilterScope === "none") return;
    const columnIndex = header.cellIndex;
    const partialDataset = tableUsesPartialDataset(table);
    const controls = partialDataset ? externalFilterControls(table, header) : [];
    if (partialDataset && controls.length === 1) {
      setTarget(null);
      activateExternalControl(controls[0]!);
      return;
    }
    if (partialDataset && controls.length === 0) return;
    const options = columnOptions(table, columnIndex);
    const selected = [...(filtersFor(table).get(columnIndex) ?? new Set<string>())];
    const counts = prepareTable(table);
    setQuery("");
    setTarget({
      mode: partialDataset ? "controls" : "values",
      table,
      header,
      columnIndex,
      label: header.dataset.columnFilterResolvedLabel || headerLabel(header),
      options: partialDataset ? controls.map((control) => control.label) : options,
      selected,
      ...targetPosition(header),
      ...counts,
      controls,
    });
  }, [filtersFor, prepareTable]);

  useEffect(() => {
    let frame: number | null = null;
    const pendingTables = new Set<HTMLTableElement>();
    const tableObservers = new Map<HTMLTableElement, MutationObserver>();

    const flushTables = () => {
      frame = null;
      const refreshed = new Map<HTMLTableElement, { visibleRows: number; totalRows: number }>();
      for (const table of pendingTables) {
        if (table.isConnected) refreshed.set(table, prepareTable(table));
      }
      pendingTables.clear();
      if (refreshed.size > 0) {
        setTarget((current) => {
          if (!current) return current;
          const counts = refreshed.get(current.table);
          if (!counts) return current;
          if (!current.header.isConnected || current.header.dataset.columnFilterHeader !== "true") return null;
          const controls = current.mode === "controls"
            ? externalFilterControls(current.table, current.header)
            : current.controls;
          if (current.mode === "controls" && controls.length === 0) return null;
          return {
            ...current,
            label: current.header.dataset.columnFilterResolvedLabel || headerLabel(current.header),
            options: current.mode === "controls" ? controls.map((control) => control.label) : columnOptions(current.table, current.columnIndex),
            selected: current.mode === "controls" ? [] : [...(filtersFor(current.table).get(current.columnIndex) ?? new Set<string>())],
            controls,
            ...counts,
          };
        });
      }
    };

    const scheduleTable = (table: HTMLTableElement) => {
      if (!table.isConnected) return;
      pendingTables.add(table);
      if (frame === null) frame = window.requestAnimationFrame(flushTables);
    };

    const observeTable = (table: HTMLTableElement) => {
      if (!table.isConnected || tableObservers.has(table)) return;
      const observer = new MutationObserver(() => scheduleTable(table));
      observer.observe(table, {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
        attributeFilter: ["data-column-filter-values", "data-column-filter-scope", "data-column-filter-total"],
      });
      tableObservers.set(table, observer);
      scheduleTable(table);
    };

    const forgetTable = (table: HTMLTableElement) => {
      if (table.isConnected) return;
      pendingTables.delete(table);
      tableObservers.get(table)?.disconnect();
      tableObservers.delete(table);
    };

    for (const table of document.querySelectorAll<HTMLTableElement>("table")) observeTable(table);

    const documentObserver = new MutationObserver((records) => {
      const addedTables = new Set<HTMLTableElement>();
      const removedTables = new Set<HTMLTableElement>();
      const affectedTables = new Set<HTMLTableElement>();
      for (const record of records) {
        for (const node of record.addedNodes) {
          for (const table of tablesInSubtree(node)) addedTables.add(table);
        }
        for (const node of record.removedNodes) {
          for (const table of tablesInSubtree(node)) removedTables.add(table);
        }
        const target = record.target instanceof Element ? record.target : record.target.parentElement;
        const externalControlChanged = record.type === "attributes"
          ? Boolean(target && attributeMutationTouchesExternalFilterControl(record, target))
          : [...record.addedNodes, ...record.removedNodes].some(nodeContainsExternalFilterControl);
        if (target && !target.closest("table") && externalControlChanged) {
          for (const table of tablesRelatedToExternalFilter(target)) affectedTables.add(table);
        }
      }
      for (const table of removedTables) forgetTable(table);
      for (const table of addedTables) observeTable(table);
      for (const table of affectedTables) {
        observeTable(table);
        scheduleTable(table);
      }
      if (removedTables.size > 0) {
        setTarget((current) => current && !current.table.isConnected ? null : current);
      }
    });
    documentObserver.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeOldValue: true,
      attributeFilter: ["aria-label", "aria-labelledby", "disabled", "hidden", "placeholder", "role", "data-column-filter-control"],
    });

    const scheduleEventTable = (event: Event) => {
      const table = event.target instanceof Element
        ? event.target.closest<HTMLTableElement>("table")
        : null;
      if (!table) return;
      observeTable(table);
      scheduleTable(table);
    };
    const onClick = (event: MouseEvent) => {
      const element = event.target instanceof Element ? event.target : null;
      const header = element?.closest<HTMLTableCellElement>("table thead th");
      if (!header || element?.closest(interactiveSelector)) return;
      const table = header.closest<HTMLTableElement>("table");
      if (!table) return;
      observeTable(table);
      prepareTable(table);
      if (header.dataset.columnFilterHeader !== "true") return;
      event.preventDefault();
      openForHeader(header);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setTarget((current) => {
          if (!current) return current;
          window.requestAnimationFrame(() => current.header.isConnected && current.header.focus());
          return null;
        });
        return;
      }
      const header = event.target instanceof Element
        ? event.target.closest<HTMLTableCellElement>("table thead th")
        : null;
      const interactive = event.target instanceof Element
        ? event.target.closest(interactiveSelector)
        : null;
      if (
        !header
        || interactive
        || (event.key !== "Enter" && event.key !== " ")
      ) return;
      const table = header.closest<HTMLTableElement>("table");
      if (!table) return;
      observeTable(table);
      prepareTable(table);
      if (header.dataset.columnFilterHeader !== "true") return;
      event.preventDefault();
      openForHeader(header);
    };
    const onPointerDown = (event: PointerEvent) => {
      if (!(event.target instanceof Node)) return;
      const node = event.target;
      setTarget((current) => {
        if (!current || popoverRef.current?.contains(node) || current.header.contains(node)) return current;
        return null;
      });
    };
    const close = () => setTarget(null);
    const closeOnExternalScroll = (event: Event) => {
      const source = event.target;
      if (source instanceof Node && popoverRef.current?.contains(source)) return;
      setTarget(null);
    };
    document.addEventListener("click", onClick);
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("input", scheduleEventTable);
    document.addEventListener("change", scheduleEventTable);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", closeOnExternalScroll, true);
    return () => {
      if (frame !== null) window.cancelAnimationFrame(frame);
      documentObserver.disconnect();
      for (const observer of tableObservers.values()) observer.disconnect();
      document.removeEventListener("click", onClick);
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("input", scheduleEventTable);
      document.removeEventListener("change", scheduleEventTable);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", closeOnExternalScroll, true);
    };
  }, [filtersFor, openForHeader, prepareTable]);

  const updateSelection = (nextValues: string[]) => {
    if (!target || target.mode !== "values") return;
    const filters = filtersFor(target.table);
    const normalized = [...new Set(nextValues)];
    if (normalized.length) filters.set(target.columnIndex, new Set(normalized));
    else filters.delete(target.columnIndex);
    const counts = applyFilters(target.table);
    setTarget((current) => current ? { ...current, selected: normalized, ...counts } : current);
  };

  if (!target) return null;
  const normalizedQuery = normalizeTableCellValue(query).toLocaleLowerCase("zh-CN");
  const visibleOptions = target.options.filter((value) => (value || "（空白）").toLocaleLowerCase("zh-CN").includes(normalizedQuery));
  const selectedValues = new Set(target.selected);

  return <div
    ref={popoverRef}
    className="column-filter-popover"
    role="dialog"
    aria-label={`${target.label}列筛选`}
    style={{ left: target.left, top: target.top, width: target.width }}
  >
    <header><div><strong>{target.label}</strong><small>{target.mode === "controls" ? "选择覆盖全部分页数据的业务筛选" : "多选完整表格中的值"}</small></div><button type="button" onClick={() => setTarget(null)} aria-label="关闭列筛选">×</button></header>
    {target.mode === "values" ? <div className="column-filter-actions"><button type="button" onClick={() => updateSelection(target.options)}>全选</button><button type="button" onClick={() => updateSelection([])} disabled={target.selected.length === 0}>清除筛选</button></div> : <div className="column-filter-actions"><span>不会再使用当前页临时选项</span></div>}
    <label className="column-filter-search"><span aria-hidden="true">⌕</span><input autoFocus type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={target.mode === "controls" ? "搜索全量筛选入口" : "搜索本列值"} aria-label={target.mode === "controls" ? `搜索${target.label}全量筛选入口` : `搜索${target.label}列值`} /></label>
    {target.mode === "controls"
      ? <div className="column-filter-options" role="radiogroup" aria-label={`${target.label}全量筛选入口`}>
          {visibleOptions.map((value) => <label key={value}><input type="radio" name="column-filter-control" checked={false} onChange={() => { const control = target.controls.find((item) => item.label === value); if (control) { setTarget(null); activateExternalControl(control); } }} /><span title={value}>{value}</span></label>)}
          {visibleOptions.length === 0 && <p>没有匹配项</p>}
        </div>
      : <div className="column-filter-options" role="listbox" aria-multiselectable="true">
          {visibleOptions.map((value) => <label key={value || "__empty__"} role="option" aria-selected={selectedValues.has(value)}><input type="checkbox" checked={selectedValues.has(value)} onChange={() => updateSelection(selectedValues.has(value) ? target.selected.filter((item) => item !== value) : [...target.selected, value])} /><span title={value || "（空白）"}>{value || "（空白）"}</span></label>)}
          {visibleOptions.length === 0 && <p>没有匹配项</p>}
        </div>}
    <footer aria-live="polite">{target.mode === "controls" ? "所选业务筛选会重新查询全部数据" : `显示 ${target.visibleRows} / ${target.totalRows} 行${target.selected.length ? ` · 已选 ${target.selected.length} 项` : " · 未筛选"}`}</footer>
  </div>;
}
