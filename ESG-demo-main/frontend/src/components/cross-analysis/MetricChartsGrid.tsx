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
  value: number | null;
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
  notDisclosedLabel,
  legendNotDisclosedLabel,
}: {
  charts: MetricChartSpec[];
  companyColors: Record<string, string>;
  notDisclosedLabel?: string;
  legendNotDisclosedLabel?: string;
}) {
  const { t } = useT();
  const notDisclosed = notDisclosedLabel ?? t("crossAnalysis.notDisclosed");
  const legendLabel = legendNotDisclosedLabel ?? t("crossAnalysis.legendNotDisclosed");

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
      {/* Legend: Not Disclosed = text only, no bar */}
      <div className="flex flex-wrap items-center gap-4 text-xs text-slate-600">
        <span className="font-medium text-slate-700">{t("crossAnalysis.table.value")}:</span>
        <span>{t("crossAnalysis.compare.valuesParsedHint")}</span>
        <span>{legendLabel}</span>
      </div>

      {rows.map((row, idx) => (
        <div
          key={`row-${idx}`}
          className="metric-grid-row"
          style={{
            ["--cols" as any]: row.length,
          }}
        >
          {row.map((c) => {
            const numericPoints = c.points.filter((p) => p.value !== null && p.value !== undefined && Number.isFinite(Number(p.value)));
            const maxVal = numericPoints.length ? Math.max(...numericPoints.map((p) => Number(p.value))) : 1;

            // Not Disclosed: value 0 so no bar height; only show text "Not Disclosed", no column
            const data = c.points.map((p) => {
              const isNotDisclosed = p.value === null || p.value === undefined || !Number.isFinite(Number(p.value));
              return {
                company: p.company,
                value: isNotDisclosed ? 0 : Number(p.value),
                year: p.year ?? null,
                isNotDisclosed,
              };
            });

            const config: any = {
              data,
              xField: "company",
              yField: "value",
              colorField: "company",
              color: (datum: any) =>
                datum.isNotDisclosed ? "transparent" : (companyColors[datum.company] || "#1677ff"),
              columnWidthRatio: 0.6,
              animation: false,
              interactions: [{ type: "element-active" }],
              state: {
                active: {
                  style: (datum: any) =>
                    datum.isNotDisclosed
                      ? {}
                      : { lineWidth: 2, stroke: "#111827", shadowBlur: 14, shadowColor: "rgba(0,0,0,0.25)" },
                },
                inactive: { style: { opacity: 0.35 } },
              },
              columnStyle: (datum: any) => {
                if (datum.isNotDisclosed) {
                  return {
                    radius: [0, 0, 0, 0],
                    fill: "transparent",
                    stroke: "none",
                    lineWidth: 0,
                  };
                }
                return { radius: [6, 6, 0, 0] };
              },
              label: false,
              xAxis: {
                label: { autoHide: true, autoRotate: true },
              },
              yAxis: { min: 0 },
              tooltip: {
                shared: false,
                showMarkers: false,
                formatter: (datum: any) => {
                  if (datum.isNotDisclosed) {
                    return { name: datum.company, value: notDisclosed };
                  }
                  // Use value from our data; chart lib may pass transformed datum where value is null
                  const raw = datum.value;
                  const num = typeof raw === "number" && Number.isFinite(raw) ? raw : Number(raw);
                  const fromPoint = c.points.find((pt) => pt.company === datum.company);
                  const actualVal = Number.isFinite(num) ? num : (fromPoint && typeof fromPoint.value === "number" && Number.isFinite(fromPoint.value) ? fromPoint.value : null);
                  const valText = actualVal != null && Number.isFinite(actualVal) ? Number(actualVal).toLocaleString() : "—";
                  const unit = c.unit ? ` ${c.unit}` : "";
                  const year = (datum.year ?? fromPoint?.year) ? ` (${datum.year ?? fromPoint?.year})` : "";
                  return { name: datum.company, value: `${valText}${unit}${year}` };
                },
              },
              meta: {
                value: {
                  alias: c.unit ? `${c.unit}` : "Value",
                  formatter: (v: any) => {
                    if (v == null || v === "" || (typeof v === "number" && !Number.isFinite(v))) return "—";
                    return typeof v === "number" ? v.toLocaleString() : String(v);
                  },
                },
              },
            };

            const hasAnyNotDisclosed = data.some((d: any) => d.isNotDisclosed);

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
                <div className="w-full relative min-h-[200px]">
                  <Column {...config} />
                  {hasAnyNotDisclosed && (
                    <div
                      className="absolute inset-0 grid pointer-events-none place-items-stretch"
                      style={{ gridTemplateColumns: `repeat(${data.length}, 1fr)` }}
                    >
                      {data.map((d: any, i: number) => (
                        <div key={i} className="flex items-center justify-center">
                          {d.isNotDisclosed ? (
                            <span className="text-base font-semibold text-slate-500">
                              {notDisclosed}
                            </span>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  )}
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
