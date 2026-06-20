"use client";

import { CHART_PALETTE } from "@/features/crossAnalysis/tokens";
import { useMemo } from "react";
import { useT } from "@/i18n/useT";

interface CompanyLegendItem {
  label: string;
  color: string;
}

interface NewHeaderProps {
  title?: string;
  dimension?: string;
  reports?: string[];
  frameworkLabel?: string | null;
  semiIndustryLabel?: string | null;
  companyLegend?: CompanyLegendItem[];
}

export function NewHeader({
  title,
  dimension,
  reports = [],
  frameworkLabel,
  semiIndustryLabel,
  companyLegend = [],
}: NewHeaderProps) {
  const { t } = useT();

  const contextLabel = useMemo(() => {
    if (frameworkLabel && semiIndustryLabel) return `${frameworkLabel} · ${semiIndustryLabel}`;
    if (frameworkLabel) return String(frameworkLabel);
    if (semiIndustryLabel) return String(semiIndustryLabel);
    return null;
  }, [frameworkLabel, semiIndustryLabel]);

  const legendItems = companyLegend.length
    ? companyLegend
    : reports.map((label, index) => ({
        label,
        color: CHART_PALETTE[index % CHART_PALETTE.length],
      }));

  return (
    <div className="app-card w-full px-6 py-4">
      <div className="flex flex-col gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
          <h1 className="text-2xl font-bold leading-tight text-[var(--brand-text)] md:text-[26px]">
            {title || t("crossAnalysis.title")}
          </h1>
          {contextLabel ? (
            <span className="inline-flex items-center rounded-full border border-black/8 bg-[var(--brand-primary-soft)] px-3 py-0.5 text-xs font-medium text-[var(--brand-muted)]">
              {contextLabel}
            </span>
          ) : null}
        </div>

        {dimension ? <p className="text-sm text-[var(--brand-subtle)]">{dimension}</p> : null}

        {legendItems.length ? (
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            {legendItems.map((item) => (
              <div key={item.label} className="inline-flex min-w-0 items-center gap-2">
                <span
                  className="inline-block h-2 w-2 shrink-0 rounded-full"
                  style={{ backgroundColor: item.color }}
                  aria-hidden
                />
                <span className="truncate text-sm font-medium text-[var(--brand-muted)]">{item.label}</span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
