"use client";

import { RefreshCw } from "lucide-react";
import { useT } from "@/i18n/useT";

interface NewHeaderProps {
  dimension: string;
  reports: string[];
  onRefresh: () => void;
}

export function NewHeader({ dimension, reports, onRefresh }: NewHeaderProps) {
  const { t } = useT();
  return (
    <div className="bg-white rounded-2xl shadow-sm p-6 w-full">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[#0F172A] mb-1">{t("crossAnalysis.title")}</h1>
          <p className="text-sm text-[#64748B] mb-3 capitalize">{dimension}</p>
          <div className="flex gap-2">
            {reports.map((report, index) => (
              <span
                key={index}
                className="px-3 py-1 bg-[#F1F5F9] text-[#0F172A] text-xs rounded-full font-medium"
              >
                {report}
              </span>
            ))}
          </div>
        </div>

        <button
          onClick={onRefresh}
          className="flex items-center gap-2 px-4 py-2 bg-[#3B82F6] text-white rounded-xl text-sm font-medium hover:bg-[#2563EB] transition-colors shadow-sm"
        >
          <RefreshCw className="w-4 h-4" />
          {t("common.refresh")}
        </button>
      </div>
    </div>
  );
}
