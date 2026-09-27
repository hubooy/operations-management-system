"use client";

import { createContext, useContext, useEffect, useMemo, type ReactNode } from "react";
import type { AiPageFilters } from "@/lib/ai/page-context";

export type AiPageDetails = { period?: { startDate: string; endDate: string } | null; filters?: AiPageFilters; blockedReason?: string };
export type AiPageRegistration = { token: object; module: string; view: string; details: AiPageDetails };
const Publisher = createContext<((registration: AiPageRegistration) => () => void) | null>(null);

export function AiPageContextProvider({ module, view, publish, children }: {
  module: string; view: string; publish: (registration: AiPageRegistration) => () => void; children: ReactNode;
}) {
  const value = useMemo(() => (registration: AiPageRegistration) => {
    if (registration.module !== module) return () => {};
    return publish({ ...registration, view });
  }, [module, view, publish]);
  return <Publisher.Provider value={value}>{children}</Publisher.Provider>;
}

export function useAiPageDetails(module: string, details: AiPageDetails, enabled = true) {
  const publish = useContext(Publisher);
  const serialized = JSON.stringify(details);
  useEffect(() => {
    if (!publish || !enabled) return;
    return publish({ token: {}, module, view: "", details: JSON.parse(serialized) as AiPageDetails });
  }, [module, publish, serialized, enabled]);
}
