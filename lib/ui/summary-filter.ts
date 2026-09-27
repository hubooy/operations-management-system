export function toggleSingleFilter<T>(current: T, selected: T, empty: T): T {
  return current === selected ? empty : selected;
}

export function toggleFilterGroup<T>(current: T[], selected: T[]): T[] {
  return current.length === selected.length && selected.every(value => current.includes(value)) ? [] : selected;
}

export type WorkCard = "all" | "open" | "pending" | "active" | "completed" | "overdue" | "today";
export function workCardStatuses(card: WorkCard): Array<"待开始" | "工作中" | "已完成"> {
  if (card === "all") return ["待开始", "工作中", "已完成"];
  if (card === "pending") return ["待开始"];
  if (card === "active") return ["工作中"];
  if (card === "completed") return ["已完成"];
  return ["待开始", "工作中"];
}

export function applyWorkCardDates(params: URLSearchParams, card: WorkCard, today: string, tomorrow: string) {
  if (card !== "overdue" && card !== "today") return;
  const upper = card === "overdue" ? today : tomorrow;
  if (!params.get("dueTo") || params.get("dueTo")! > upper) params.set("dueTo", upper);
  if (card === "today" && (!params.get("dueFrom") || params.get("dueFrom")! < today)) params.set("dueFrom", today);
}
