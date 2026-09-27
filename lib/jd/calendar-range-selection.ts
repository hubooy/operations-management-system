import { jdDateRangeEchoMatches } from "./product-detail-selection";

export function jdDateRangeSelectionPlan(startDate: string, endDate: string) {
  // JD's range picker requires two endpoint clicks even when both endpoints
  // are the same day. Returning both entries intentionally preserves that
  // second click instead of collapsing the range to one interaction.
  return [startDate, endDate] as const;
}

export function isStaticCurrentTimestamp(echoText: string) {
  return /^\s*当前[：:]\s*\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s*$/.test(echoText);
}

export function isVerifiedJdDateRangeEcho(echoText: string, startDate: string, endDate: string) {
  return !isStaticCurrentTimestamp(echoText) && jdDateRangeEchoMatches(echoText, startDate, endDate);
}

export type JdCalendarCellState = { disabled: boolean; now: boolean; start: boolean; end: boolean; selected: boolean };

/** JD hashes calendar class names per build; match the stable cell-state segment within each class token. */
export function jdCalendarCellState(className: string | null | undefined): JdCalendarCellState {
  const tokens = String(className ?? "").split(/\s+/).filter(Boolean);
  const has = (segment: string) => tokens.some((token) => token.includes(`cell-${segment}`));
  return { disabled: has("disabled"), now: has("now"), start: has("start"), end: has("end"), selected: has("selected") };
}

export function isJdCalendarDateDispatchable(state: JdCalendarCellState) {
  return !state.disabled;
}

export type JdCalendarDateDispatchDecision = "dispatch" | "blocked_disabled";

/** Keep the decision pure so disabled days never reach a click/dispatch side effect. */
export function jdCalendarDateDispatchDecision(className: string | null | undefined): JdCalendarDateDispatchDecision {
  return jdCalendarCellState(className).disabled ? "blocked_disabled" : "dispatch";
}

export function isJdCalendarEndSelected(state: JdCalendarCellState) {
  // Today is a visual marker, never proof of a selected range endpoint. Even
  // end+selected is diagnostic only: JD can add it while hovering secondDate.
  return !state.disabled && !state.now && state.end && state.selected;
}

export type JdCalendarEndDecision = "confirmed_echo" | "blocked_disabled" | "end_selected_without_echo" | "unconfirmed";

export function jdCalendarEndSelectionDecision(input: { className: string | null | undefined; echoText: string; startDate: string; endDate: string }): JdCalendarEndDecision {
  if (isVerifiedJdDateRangeEcho(input.echoText, input.startDate, input.endDate)) return "confirmed_echo";
  const state = jdCalendarCellState(input.className);
  if (state.disabled) return "blocked_disabled";
  return isJdCalendarEndSelected(state) ? "end_selected_without_echo" : "unconfirmed";
}
