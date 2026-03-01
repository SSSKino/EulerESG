"use client";

import React from "react";
import dynamic from "next/dynamic";
import { Empty } from "antd";
import { useT } from "@/i18n/useT";

const Column = dynamic(() => import("@ant-design/plots").then((m) => m.Column), {
  ssr: false,
});

export type MetricChartPoint = {
  company: string;
  value: number;
  year?: string | null;
};

export type MetricChartSpec = {
  key: string;
  topic: string;
  unit?: string | null;
  yearInfo?: string;
  points: MetricChartPoint[];
};

function chunk<T>(arr: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

function MetricChartsGridInner({
  charts,
  companyColors,
}: {
  charts: MetricChartSpec[];
  companyColors: Record<string, string>;
}) {
  const { t } = useT();
  if (!charts || charts.length === 0) {
    return (
      <div className="bg-white rounded-2xl shadow-sm p-6">
        <Empty description={t("crossAnalysis.noComparableMetrics")} />
      </div>
    );
  }

  const rows = chunk(charts, 4);

  return (
    <div className="space-y-6">
      {rows.map((row, idx) => (
        <div
          key={`row-${idx}`}
          className="metric-grid-row"
          style={{
            // CSS var is consumed by .metric-grid-row in globals.css
            ["--cols" as any]: row.length,
          }}
        >
          {row.map((c) => {
            const data = c.points.map((p) => ({
              company: p.company,
              value: p.value,
              year: p.year ?? null,
            }));

            const config: any = {
              data,
              xField: "company",
              yField: "value",
              colorField: "company",
              color: (datum: any) => companyColors[datum.company] || "#1677ff",
              columnWidthRatio: 0.6,
              animation: false,
              interactions: [{ type: "element-active" }],
              state: {
                active: {
                  style: {
                    lineWidth: 2,
                    stroke: "#111827",
                    shadowBlur: 14,
                    shadowColor: "rgba(0,0,0,0.25)",
                  },
                },
                inactive: { style: { opacity: 0.35 } },
              },
              columnStyle: { radius: [6, 6, 0, 0] },
              xAxis: {
                label: {
                  autoHide: true,
                  autoRotate: true,
                },
              },
              tooltip: {
                shared: false,
                showMarkers: false,
                formatter: (datum: any) => {
                  const rawVal = typeof datum.value === "number" ? datum.value : Number(datum.value);
                  const valText = Number.isFinite(rawVal) ? rawVal.toLocaleString() : String(datum.value ?? "—");
                  const unit = c.unit ? ` ${c.unit}` : "";
                  const year = datum.year ? ` (${datum.year})` : "";
                  return {
                    name: datum.company,
                    value: `${valText}${unit}${year}`,
                  };
                },
              },
              meta: {
                value: {
                  alias: c.unit ? `${c.unit}` : "Value",
                },
              },
            };

            return (
              <div key={c.key} className="bg-white rounded-2xl shadow-sm p-4 min-w-0">
                <div className="mb-3 min-w-0">
                  <div className="font-semibold text-slate-900 leading-snug break-words">
                    {c.topic}
                  </div>
                  <div className="text-xs text-slate-500 mt-1">
                    {c.unit ? `Unit: ${c.unit}` : "Unit: —"}
                    {c.yearInfo ? ` · ${c.yearInfo}` : ""}
                  </div>
                </div>
                <div className="w-full">
                  <Column {...config} />
                </div>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

// Prevent re-rendering (and therefore plot re-initialization) when the page updates
// for unrelated state changes (table filters, sidebar toggles, etc.).
// We only re-render when the chart specs or color map actually change.
export const MetricChartsGrid = React.memo(
  MetricChartsGridInner,
  (prev, next) => prev.charts === next.charts && prev.companyColors === next.companyColors
);
