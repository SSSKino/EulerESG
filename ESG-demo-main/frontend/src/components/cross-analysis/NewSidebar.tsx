"use client";

import { ChevronRight } from "lucide-react";
import { useState } from "react";

interface Issue {
  id: string;
  name: string;
}

interface Dimension {
  id: string;
  name: string;
  issues: Issue[];
}

interface NewSidebarProps {
  selectedDimension: string;
  selectedIssue: string;
  viewMode?: "issue" | "disclosure";
  onSelectIssue: (dimensionId: string, issueId: string) => void;
  onSelectDisclosure?: () => void;
}

export function NewSidebar({ selectedDimension, selectedIssue, viewMode = "issue", onSelectIssue, onSelectDisclosure }: NewSidebarProps) {
  const [expandedDimensions, setExpandedDimensions] = useState<Set<string>>(new Set(["environment"]));

  const dimensions: Dimension[] = [
    {
      id: "environment",
      name: "Environment",
      issues: [
        { id: "emissions", name: "GHG Emissions" },
        { id: "energy", name: "Energy Management" },
        { id: "water", name: "Water & Wastewater" },
        { id: "waste", name: "Waste Management" }
      ]
    },
    {
      id: "social",
      name: "Social",
      issues: [
        { id: "diversity", name: "Diversity & Inclusion" }
      ]
    },
    {
      id: "governance",
      name: "Governance",
      issues: [
        { id: "ethics", name: "Business Ethics" }
      ]
    }
  ];

  const toggleDimension = (dimensionId: string) => {
    const newExpanded = new Set(expandedDimensions);
    if (newExpanded.has(dimensionId)) {
      newExpanded.delete(dimensionId);
    } else {
      newExpanded.add(dimensionId);
    }
    setExpandedDimensions(newExpanded);
  };

  return (
    <div className="w-[320px] bg-white rounded-2xl shadow-sm p-4 h-fit">
      <h3 className="text-xs font-semibold text-[#64748B] uppercase tracking-wide mb-4">
        Navigation
      </h3>

      <div className="space-y-1">
        {dimensions.map((dimension) => {
          const isExpanded = expandedDimensions.has(dimension.id);
          const isActiveDimension = selectedDimension === dimension.id;

          return (
            <div key={dimension.id}>
              <button
                onClick={() => toggleDimension(dimension.id)}
                className={`w-full flex items-center justify-between px-3 py-2 rounded-xl transition-all ${
                  isActiveDimension
                    ? "bg-white border border-slate-300 shadow-sm"
                    : "bg-transparent hover:bg-slate-50"
                }`}
              >
                <span className="text-sm font-medium text-[#0F172A]">{dimension.name}</span>
                <ChevronRight
                  className={`w-4 h-4 text-[#64748B] transition-transform ${
                    isExpanded ? "rotate-90" : ""
                  }`}
                />
              </button>

              {isExpanded && (
                <div className="mt-1 ml-3 space-y-1">
                  {dimension.issues.map((issue) => {
                    const isSelected = selectedDimension === dimension.id && selectedIssue === issue.id;

                    return (
                      <button
                        key={issue.id}
                        onClick={() => onSelectIssue(dimension.id, issue.id)}
                        className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-all ${
                          isSelected
                            ? "bg-[#EFF6FF] border border-[#BFDBFE] text-[#0F172A] font-medium"
                            : "text-[#64748B] hover:bg-slate-50"
                        }`}
                      >
                        {issue.name}
                      </button>
                    );
                  })}
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
