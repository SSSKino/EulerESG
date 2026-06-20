"use client";

import { ChevronRight } from "lucide-react";
import { useMemo, useState, useCallback } from "react";
import { useT } from "@/i18n/useT";

function secondaryKey(primary: string, secondary: string): string {
  return `${primary}|${secondary}`;
}

const CATEGORY_LABELS_NO_SECONDARY = new Set([
  "Quantitative",
  "Qualitative",
  "Discussion and Analysis",
  "General",
]);

export type TertiaryMap = Map<string, Map<string, string[]>>;

export interface NewSidebarProps {
  primaryOptions: string[];
  secondaryByPrimary: Map<string, string[]>;
  tertiaryByPrimaryAndSecondary: TertiaryMap;
  selectedPrimary: string;
  selectedSecondaries: string[];
  selectedTertiary: string | null;
  expandedPrimaries: Record<string, boolean>;
  primaryIsActivityMetrics?: boolean;
  forceSecondaryLeafMode?: boolean;
  viewMode?: "issue" | "disclosure";
  onTogglePrimary: (primary: string) => void;
  onSelectSecondary: (primary: string, secondary: string) => void;
  onSelectTertiary: (primary: string, secondary: string, metricName: string) => void;
  onSelectDisclosure?: () => void;
}

function safeTrim(v: any): string {
  if (v === null || v === undefined) return "";
  return String(v).trim();
}

export function NewSidebar({
  primaryOptions,
  secondaryByPrimary,
  tertiaryByPrimaryAndSecondary,
  selectedPrimary,
  selectedSecondaries,
  selectedTertiary,
  expandedPrimaries,
  primaryIsActivityMetrics = false,
  forceSecondaryLeafMode = false,
  viewMode = "issue",
  onTogglePrimary,
  onSelectSecondary,
  onSelectTertiary,
  onSelectDisclosure,
}: NewSidebarProps) {
  const { t } = useT();
  const selectedSecondarySet = useMemo(() => new Set(selectedSecondaries || []), [selectedSecondaries]);
  const [expandedSecondaries, setExpandedSecondaries] = useState<Record<string, boolean>>({});
  const isActivityMetrics = primaryIsActivityMetrics && safeTrim(selectedPrimary) === "Activity Metrics";
  const secondaryActsAsLeaf = isActivityMetrics || forceSecondaryLeafMode;

  const toggleSecondaryExpand = useCallback((primary: string, secondary: string) => {
    const key = secondaryKey(primary, secondary);
    setExpandedSecondaries((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

  return (
    <div className="app-sidebar-pod h-fit w-[320px] p-4">
      <h3 className="mb-4 pl-8 text-xs font-semibold uppercase tracking-wide text-[var(--brand-subtle)]">
        {t("crossAnalysis.navigation")}
      </h3>

      <div className="space-y-1">
        {primaryOptions.map((primary) => {
          const isExpanded = !!expandedPrimaries?.[primary];
          const isActivePrimary = safeTrim(selectedPrimary) === primary;
          const rawSecondaries = secondaryByPrimary.get(primary) || [];
          const isActivityMetricsPrimary = primary === "Activity Metrics";
          const secondaries =
            isActivityMetricsPrimary
              ? rawSecondaries.filter((s) => !CATEGORY_LABELS_NO_SECONDARY.has(s))
              : rawSecondaries;
          const innerTertiary = tertiaryByPrimaryAndSecondary.get(primary);

          return (
            <div key={primary}>
              <button
                onClick={() => onTogglePrimary(primary)}
                className={`w-full flex items-center justify-between px-3 py-2 rounded-xl transition-all ${
                  isActivePrimary
                    ? "app-nav-active"
                    : "border border-transparent bg-transparent text-[var(--brand-text)] hover:bg-[var(--brand-primary-soft)]"
                }`}
              >
                <span className="line-clamp-2 block text-sm font-medium leading-snug break-words">{primary}</span>
                {/* <ChevronRight
                  className={`w-4 h-4 text-[#64748B] transition-transform ${
                    isExpanded ? "rotate-90" : ""
                  }`}
                /> */}
              </button>

              {isExpanded && (
                <div className="mt-1 ml-3 space-y-1">
                  {secondaries.length ? (
                    secondaries.map((secondary) => {
                      const isSelectedSecondary = isActivePrimary && selectedSecondarySet.has(secondary);
                      const tertiaries = secondaryActsAsLeaf ? [] : (innerTertiary?.get(secondary) || []);
                      const isExpandedSec = !!expandedSecondaries[secondaryKey(primary, secondary)];
                      const hasTertiaries = !secondaryActsAsLeaf && tertiaries.length > 0;

                      return (
                        <div key={secondary}>
                          <div className="flex items-center gap-1">
                            <button
                              onClick={() => {
                                if (isActivityMetrics) {
                                  onSelectTertiary(primary, secondary, secondary);
                                  return;
                                }
                                onSelectSecondary(primary, secondary);
                              }}
                              className={`flex-1 min-w-0 flex items-center text-left px-3 py-2 rounded-lg text-sm transition-all ${
                                isSelectedSecondary
                                  ? "app-nav-active font-medium"
                                  : "border border-transparent bg-transparent text-[var(--brand-subtle)] hover:bg-[var(--brand-primary-soft)]"
                              }`}
                              title={secondary}
                            >
                              <span className="line-clamp-2 block w-full break-words leading-snug">{secondary}</span>
                            </button>

                            {hasTertiaries ? (
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  toggleSecondaryExpand(primary, secondary);
                                }}
                                className="shrink-0 rounded-lg p-2 text-[var(--brand-subtle)] hover:bg-[var(--brand-primary-soft)]"
                                aria-label={secondary}
                              >
                                <ChevronRight
                                  className={`w-3.5 h-3.5 transition-transform ${isExpandedSec ? "rotate-90" : ""}`}
                                />
                              </button>
                            ) : null}
                          </div>

                          {hasTertiaries && isExpandedSec && (
                            <div className="ml-3 mt-0.5 space-y-0.5">
                              {tertiaries.map((metricName) => {
                                const isSelectedMetric = selectedTertiary === metricName;
                                return (
                                  <button
                                    key={metricName}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      onSelectTertiary(primary, secondary, metricName);
                                    }}
                                    className={`w-full rounded-md px-3 py-1.5 text-left text-xs transition-all ${
                                      isSelectedMetric
                                        ? "app-nav-active font-medium"
                                        : "border border-transparent bg-transparent text-[var(--brand-subtle)] hover:bg-[var(--brand-primary-soft)]"
                                    }`}
                                    title={metricName}
                                  >
                                    <span className="line-clamp-2 block break-words leading-snug">{metricName}</span>
                                  </button>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      );
                    })
                  ) : (
                    <div className="px-3 py-2 text-xs text-[var(--brand-subtle)]">{t("crossAnalysis.noSecondaryNav")}</div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="mt-6 border-t border-black/8 pt-4">
        <button
          onClick={() => onSelectDisclosure?.()}
          className={`w-full text-left px-3 py-2 rounded-xl text-sm transition-all ${
            viewMode === "disclosure"
              ? "app-nav-active font-medium"
              : "border border-transparent bg-transparent text-[var(--brand-subtle)] hover:bg-[var(--brand-primary-soft)]"
          }`}
        >
          {t("crossAnalysis.disclosureCompleteness")}
        </button>
      </div>
    </div>
  );
}