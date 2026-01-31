"use client";

import { ChevronRight } from "lucide-react";
import { useMemo } from "react";

export interface NewSidebarProps {
  primaryOptions: string[];
  secondaryByPrimary: Map<string, string[]>;
  selectedPrimary: string;
  selectedSecondaries: string[];
  expandedPrimaries: Record<string, boolean>;
  viewMode?: "issue" | "disclosure";
  onTogglePrimary: (primary: string) => void;
  onSelectSecondary: (primary: string, secondary: string) => void;
  onSelectDisclosure?: () => void;
}

function safeTrim(v: any): string {
  if (v === null || v === undefined) return "";
  return String(v).trim();
}

export function NewSidebar({
  primaryOptions,
  secondaryByPrimary,
  selectedPrimary,
  selectedSecondaries,
  expandedPrimaries,
  viewMode = "issue",
  onTogglePrimary,
  onSelectSecondary,
  onSelectDisclosure,
}: NewSidebarProps) {
  const selectedSecondarySet = useMemo(() => new Set(selectedSecondaries || []), [selectedSecondaries]);

  return (
    <div className="w-[320px] bg-white rounded-2xl shadow-sm p-4 h-fit">
      <h3 className="text-xs font-semibold text-[#64748B] uppercase tracking-wide mb-4">
        Navigation
      </h3>

      <div className="space-y-1">
        {primaryOptions.map((primary) => {
          const isExpanded = !!expandedPrimaries?.[primary];
          const isActivePrimary = safeTrim(selectedPrimary) === primary;
          const secondaries = secondaryByPrimary.get(primary) || [];

          return (
            <div key={primary}>
              <button
                onClick={() => onTogglePrimary(primary)}
                className={`w-full flex items-center justify-between px-3 py-2 rounded-xl transition-all ${
                  isActivePrimary
                    ? "bg-white border border-slate-300 shadow-sm"
                    : "bg-transparent hover:bg-slate-50"
                }`}
              >
                <span className="text-sm font-medium text-[#0F172A] truncate">{primary}</span>
                <ChevronRight
                  className={`w-4 h-4 text-[#64748B] transition-transform ${
                    isExpanded ? "rotate-90" : ""
                  }`}
                />
              </button>

              {isExpanded && (
                <div className="mt-1 ml-3 space-y-1">
                  {secondaries.length ? (
                    secondaries.map((secondary) => {
                      const isSelected = isActivePrimary && selectedSecondarySet.has(secondary);
                      return (
                        <button
                          key={secondary}
                          onClick={() => onSelectSecondary(primary, secondary)}
                          className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-all ${
                            isSelected
                              ? "bg-[#EFF6FF] border border-[#BFDBFE] text-[#0F172A] font-medium"
                              : "text-[#64748B] hover:bg-slate-50"
                          }`}
                          title={secondary}
                        >
                          <span className="block truncate">{secondary}</span>
                        </button>
                      );
                    })
                  ) : (
                    <div className="px-3 py-2 text-xs text-slate-400">No secondary navigation</div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Disclosure completeness navigation */}
      <div className="mt-6 pt-4 border-t border-slate-200">
        <button
          onClick={() => onSelectDisclosure?.()}
          className={`w-full text-left px-3 py-2 rounded-xl text-sm transition-all ${
            viewMode === "disclosure"
              ? "bg-[#EFF6FF] border border-[#BFDBFE] text-[#0F172A] font-medium"
              : "text-[#64748B] hover:bg-slate-50"
          }`}
        >
          Disclosure completeness
        </button>
      </div>
    </div>
  );
}
