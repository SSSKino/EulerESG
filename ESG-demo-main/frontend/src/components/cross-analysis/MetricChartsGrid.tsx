"use client";

import React, { useMemo } from "react";
import { DEFAULT_CHART_COLOR } from "@/features/crossAnalysis/tokens";
import dynamic from "next/dynamic";
import { Empty } from "antd";
import { useT } from "@/i18n/useT";

const Column = dynamic(() => import("@ant-design/plots").then((m) => m.Column), {
  ssr: false,
});

export type MetricChartPoint = {
  company: string;
  colorKey?: string;
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

function normalizeUnit(unit?: string | null): string {
  return String(unit ?? "").trim().toLowerCase();
}

function isPercentUnit(unit?: string | null): boolean {
  const u = normalizeUnit(unit);
  return (
    u === "%" ||
    u === "percent" ||
    u === "percentage" ||
    u.includes("%") ||
    u.includes("percent") ||
    u.includes("percentage")
  );
}

function clampPercentValue(value: number): number {
  if (!Number.isFinite(value)) return value;
  if (value < 0) return 0;
  if (value > 100) return 100;
  return value;
}

function getYAxisMax(values: number[], unit?: string | null): number {
  if (!values.length) return 1;
  const maxValue = Math.max(...values);
  if (isPercentUnit(unit)) return 100;
  if (!(maxValue > 0)) return 1;
  return maxValue * 1.01;
}

function formatAxisLabel(text: any, compact: boolean): string {
  const s = String(text ?? "");
  if (!compact) return s;
  if (s.length <= 18) return s;
  return `${s.slice(0, 18)}…`;
}

function formatCompactNumber(value: any): string {
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value ?? "");
  const abs = Math.abs(num);
  if (abs >= 1_000_000_000) return `${(num / 1_000_000_000).toFixed(0)}B`;
  if (abs >= 1_000_000) return `${(num / 1_000_000).toFixed(0)}M`;
  if (abs >= 1_000) return `${(num / 1_000).toFixed(0)}K`;
  return num.toLocaleString();
}

function chunk<T>(arr: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

function getRowSpan(rowLength: number): string {
  if (rowLength <= 1) return "md:col-span-12";
  return "md:col-span-6";
}

function MetricChartsGridInner({
  charts,
  companyColors,
}: {
  charts: MetricChartSpec[];
  companyColors: Record<string, string>;
}) {
  const { t } = useT();

  const normalizedCharts = useMemo(() => {
    return (charts || [])
      .map((chart) => {
        const numericPoints = (chart.points || []).filter(
          (p) => p.value !== null && p.value !== undefined && Number.isFinite(Number(p.value))
        );

        return {
          ...chart,
          points: numericPoints.map((p) => ({
            ...p,
            colorKey: p.colorKey || p.company,
            value: Number(p.value),
          })),
        };
      })
      .filter((chart) => chart.points.length > 0);
  }, [charts]);

  if (!normalizedCharts.length) {
    return (
      <div className="app-card p-6">
        <Empty description={t("crossAnalysis.noComparableMetrics")} />
      </div>
    );
  }

  const colorDomain = Object.keys(companyColors);
  const colorRange = colorDomain.map((key) => companyColors[key]);
  const rows = chunk(normalizedCharts, 2);

  return (
    <div className="space-y-4">
      {rows.map((row, rowIndex) => (
        <div key={`chart-row-${rowIndex}`} className="grid grid-cols-1 gap-4 md:grid-cols-12">
          {row.map((chart) => {
            const percentChart = isPercentUnit(chart.unit);

            const data = chart.points.map((point) => {
              const rawValue = Number(point.value);
              const finalValue =
                percentChart && Number.isFinite(rawValue)
                  ? clampPercentValue(rawValue)
                  : rawValue;

              return {
                company: point.company,
                colorKey: point.colorKey || point.company,
                value: finalValue,
                year: point.year ?? null,
                unit: chart.unit ? String(chart.unit) : "",
              };
            });

            const rowSpanClass = getRowSpan(row.length);
            const chartHeight = 268;
            const yValues = data.map((d) => Number(d.value)).filter((n) => Number.isFinite(n));

            const isPercent = isPercentUnit(chart.unit);
            const config: any = {
              data,
              xField: "company",
              yField: "value",
              colorField: "colorKey",
              scale: {
                color: {
                  domain: colorDomain,
                  range: colorRange,
                },
                y: {
                  domainMin: 0,
                  domainMax: getYAxisMax(yValues, chart.unit),
                  nice: !isPercent,
                },
              },
              color: ({ colorKey }: any) => companyColors[String(colorKey)] || DEFAULT_CHART_COLOR,
              legend: false,
              animation: false,
              autoFit: true,
              padding: [0, 0, 0, 0],
              appendPadding: 0,
              margin: 0,
              inset: 5,
              columnWidthRatio: data.length >= 4 ? 0.52 : data.length === 1 ? 0.28 : 0.42,
              columnStyle: { radius: [8, 8, 0, 0] },
              axis: {
                x: {
                  title: false,
                  labelAutoHide: false,
                  labelAutoRotate: false,
                  labelAutoWrap: false,
                  labelFill: "#64748B",
                  labelFontSize: 12,
                  labelFormatter: (value: any) => formatAxisLabel(value, false),
                },
                y: {
                  title: false,
                  labelAutoHide: false,
                  labelFill: "#64748B",
                  labelFontSize: 13,
                  labelFormatter: (value: any) => formatCompactNumber(value),
                  line: false,
                  grid: true,
                  gridStroke: "#E2E8F0",
                  gridLineDash: [3, 3],
                  tick: false,
                },
              },
              tooltip: {
                title: false,
                marker: false,
                shared: false,
                offset: 200,
                showDelay: 400,
                hideDelay: 0,
                follow: false,
                enterable: false,
                items: [
                  (datum: any) => ({ name: "Name", value: datum.company }),
                  (datum: any) => ({ name: "Year", value: datum.year || "—" }),
                  (datum: any) => ({
                    name: "Value",
                    value: Number.isFinite(Number(datum.value)) ? Number(datum.value).toLocaleString() : "—",
                  }),
                  (datum: any) => ({ name: "Unit", value: datum.unit || "—" }),
                ],
              },
              interaction: { elementHighlight: true },
              interactions: [{ type: "element-active" }, { type: "tooltip" }],
              height: chartHeight,
            };

            return (
              <div key={chart.key} className={`${rowSpanClass} app-card min-w-0 px-4 py-3`}>
                <div className="mb-2 min-w-0">
                  <div className="line-clamp-2 text-sm font-semibold leading-snug text-[var(--brand-text)]">{chart.topic}</div>
                  <div className="mt-1 text-xs text-[var(--brand-subtle)]">
                    {chart.unit ? `Unit: ${chart.unit}` : "Unit: —"}
                    {chart.yearInfo ? ` · ${chart.yearInfo}` : ""}
                  </div>
                </div>

                <div className="w-full min-h-[220px]">
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

export const MetricChartsGrid = React.memo(
  MetricChartsGridInner,
  (prev, next) => prev.charts === next.charts && prev.companyColors === next.companyColors
);
