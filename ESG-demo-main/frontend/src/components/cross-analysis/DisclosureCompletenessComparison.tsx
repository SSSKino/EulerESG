"use client";

import React, { useEffect, useMemo, useState } from "react";
import { Alert, Popover, Spin, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useT } from "@/i18n/useT";

import { apiService } from "@/lib/api";
import type { CrossReportSummary } from "@/features/crossAnalysis/types";

type DisclosureStatus = "fully_disclosed" | "partially_disclosed" | "not_disclosed";

export type AnalysisDataItem = {
  metric_id: string;
  metric_name: string;
  disclosure_status: DisclosureStatus;
  reasoning: string;
  unit?: string;
  category?: string;
  topic?: string;
  type?: string;
  value?: string | number | null;
  page?: string | number | null;
  context?: string | null;
};

type PerReport = {
  fileId: string;
  label: string;
  framework?: string | null;
  loading: boolean;
  error: string | null;
  metrics: AnalysisDataItem[];
};

const PARTIAL_VALUE_TEXT = "";

function safeTrim(v: any): string {
  if (v === null || v === undefined) return "";
  return String(v).trim();
}

function stripFileExt(name: string): string {
  const s = safeTrim(name);
  if (!s) return "";
  // Remove the last extension only (e.g., ".pdf"), keep internal dots.
  return s.replace(/\.[^/.]+$/, "");
}


function normalizeStatus(raw: any): DisclosureStatus {
  const s = String(raw ?? "").trim().toLowerCase();
  if (!s) return "not_disclosed";
  if (s.includes("fully")) return "fully_disclosed";
  if (s.includes("partial")) return "partially_disclosed";
  if (s.includes("not")) return "not_disclosed";
  if (s === "fully_disclosed") return "fully_disclosed";
  if (s === "partially_disclosed") return "partially_disclosed";
  if (s === "not_disclosed") return "not_disclosed";
  return "not_disclosed";
}

function pick(...vals: any[]) {
  for (const v of vals) {
    if (v === null || v === undefined) continue;
    if (typeof v === "string" && v.trim() === "") continue;
    return v;
  }
  return null;
}

function normalizePage(page: string | number | null | undefined): number | null {
  if (page === null || page === undefined) return null;
  if (typeof page === "number") return Number.isFinite(page) ? page : null;
  const s = String(page).trim();
  if (!s) return null;
  const firstToken = s.split(",")[0].trim();
  const rangeFirst = firstToken.split("-")[0].trim();
  const m = rangeFirst.match(/\d+/);
  if (!m) return null;
  const n = parseInt(m[0], 10);
  return Number.isFinite(n) ? n : null;
}

function statusTag(t: (key: string, vars?: Record<string, any>) => string, status: DisclosureStatus) {
  if (status === "fully_disclosed") return <Tag color="green">{t("analysis.summary.disclosed")}</Tag>;
  if (status === "partially_disclosed") return <Tag color="gold">{t("analysis.summary.partial")}</Tag>;
  return <Tag color="red">{t("analysis.summary.not")}</Tag>;
}

function toDisplayValue(v: any): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") {
    try {
      return new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 }).format(v);
    } catch {
      return String(v);
    }
  }
  return String(v);
}

function computeSummary(items: AnalysisDataItem[]) {
  const red = items.filter((x) => x.disclosure_status === "not_disclosed").length;
  const yellow = items.filter((x) => x.disclosure_status === "partially_disclosed").length;
  const green = items.filter((x) => x.disclosure_status === "fully_disclosed").length;
  return { red, yellow, green, total: red + yellow + green };
}

function reportLabelFromSummaries(fileId: string, reports?: CrossReportSummary[]) {
  const r = (reports || []).find((x) => x.file_id === fileId);
  // Cross Analysis naming: prefer the original uploaded filename.
  const fn = safeTrim(r?.filename);
  return (fn ? stripFileExt(fn) : "") || safeTrim(r?.display_name) || safeTrim(r?.short_name) || fileId;
}

function openEvidence(fileId: string, page: number | null, title: string) {
  const qs = new URLSearchParams({
    file_id: fileId,
    name: title,
  });
  if (page && page > 0) qs.set("page", String(page));
  window.open(`/cross-analysis/evidence?${qs.toString()}`, "_blank", "noopener,noreferrer");
}

export default function DisclosureCompletenessComparison(props: {
  fileIds: string[];
  reports?: CrossReportSummary[];
}) {
  const { t } = useT();
  const PARTIAL_VALUE_TEXT = t("analysis.partialValueText");
  const { fileIds, reports } = props;

  const [per, setPer] = useState<PerReport[]>(() =>
    (fileIds || []).map((id) => ({
      fileId: id,
      label: reportLabelFromSummaries(id, reports),
      framework: null,
      loading: true,
      error: null,
      metrics: [],
    }))
  );

  // Refresh labels when reports arrive
  useEffect(() => {
    setPer((prev) =>
      prev.map((p) => ({ ...p, label: reportLabelFromSummaries(p.fileId, reports) }))
    );
  }, [
    (reports || [])
      .map((r) => `${r.file_id}:${safeTrim(r.short_name)}:${safeTrim(r.display_name)}`)
      .join("|"),
  ]);

  useEffect(() => {
    let cancelled = false;
    if (!fileIds || fileIds.length < 2) return;

    setPer(
      fileIds.map((id) => ({
        fileId: id,
        label: reportLabelFromSummaries(id, reports),
        framework: null,
        loading: true,
        error: null,
        metrics: [],
      }))
    );

    (async () => {
      const results = await Promise.all(
        fileIds.map(async (id) => {
          try {
            const assessment = await apiService.getAssessmentByFile(id);

            const actualFramework = safeTrim(
              (assessment as any)?.framework ??
                (assessment as any)?.assessment?.framework ??
                (assessment as any)?.current_framework
            );

            const converted: AnalysisDataItem[] = (assessment?.metric_analyses || [])
              .filter((item: any) => {
                const mid =
                  item?.metric_id ?? item?.metric_code ?? item?.metricId ?? item?.Code ?? item?.code;
                const name = item?.metric_name ?? item?.Metric ?? item?.metric;
                return Boolean(mid && name);
              })
              .map((item: any) => {
                const metric_id = String(
                  item?.metric_id ?? item?.metric_code ?? item?.metricId ?? item?.Code ?? item?.code
                );
                const metric_name = String(item?.metric_name ?? item?.Metric ?? item?.metric);

                const disclosure_status = normalizeStatus(
                  item?.disclosure_status ??
                    item?.disclosureStatus ??
                    item?.status ??
                    item?.["Model Disclosure Status"]
                );

                const value = pick(
                  item?.value,
                  item?.Value,
                  item?.data,
                  item?.Data,
                  item?.specific_data_found,
                  item?.specificDataFound
                );

                const page = pick(
                  item?.page,
                  item?.Page,
                  item?.page_number,
                  item?.pageNumber,
                  item?.evidence?.page,
                  item?.evidence_segments?.[0]?.page_number,
                  item?.evidence_segments?.[0]?.page
                );

                const unit = pick(item?.unit, item?.Unit) ?? "";
                const category = pick(item?.category, item?.Category) ?? "";
                const topic = pick(item?.topic, item?.Topic) ?? "";
                const type = pick(item?.type, item?.Type) ?? "";
                const reasoning = String(
                  pick(item?.reasoning, item?.Reasoning, item?.analysis, item?.Analysis) ?? ""
                );
                const context = pick(
                  item?.context,
                  item?.Context,
                  item?.evidence,
                  item?.evidence_text,
                  item?.evidenceText
                );

                return {
                  metric_id,
                  metric_name,
                  disclosure_status,
                  reasoning,
                  unit,
                  category,
                  topic,
                  type,
                  value: value ?? null,
                  page: page ?? null,
                  context: (context ?? null) as any,
                };
              });

            return { ok: true as const, fileId: id, framework: actualFramework || null, metrics: converted };
          } catch (e: any) {
            return { ok: false as const, fileId: id, error: e?.message || t("crossAnalysis.disclosure.failedToLoadAssessment"), metrics: [] };
          }
        })
      );

      if (cancelled) return;

      setPer((prev) =>
        prev.map((p) => {
          const r = results.find((x) => x.fileId === p.fileId);
          if (!r) return { ...p, loading: false, error: t("crossAnalysis.disclosure.noResult"), metrics: [] };
          if (!r.ok) return { ...p, loading: false, error: r.error, metrics: [] };
          return { ...p, loading: false, error: null, framework: (r as any).framework ?? null, metrics: r.metrics };
        })
      );
    })();

    return () => {
      cancelled = true;
    };
  }, [fileIds.join("|"), reports?.map((r) => r.file_id).join("|")]);

  const anyLoading = per.some((p) => p.loading);

  const summaryCards = useMemo(() => {
    return per.map((p) => ({ ...p, summary: computeSummary(p.metrics) }));
  }, [per]);

  const metricUnion = useMemo(() => {
    const map = new Map<string, { metric_id: string; metric_name: string }>();
    for (const p of per) {
      for (const m of p.metrics) {
        if (!map.has(m.metric_id)) map.set(m.metric_id, { metric_id: m.metric_id, metric_name: m.metric_name });
      }
    }
    return Array.from(map.values()).sort((a, b) => a.metric_name.localeCompare(b.metric_name));
  }, [per]);

  type Row = {
    key: string;
    metric_id: string;
    metric_name: string;
    byReport: Record<string, AnalysisDataItem | null>;
  };

  const tableData: Row[] = useMemo(() => {
    return metricUnion.map((m) => {
      const byReport: Record<string, AnalysisDataItem | null> = {};
      for (const p of per) {
        const hit = p.metrics.find((x) => x.metric_id === m.metric_id) || null;
        byReport[p.fileId] = hit;
      }
      return {
        key: m.metric_id,
        metric_id: m.metric_id,
        metric_name: m.metric_name,
        byReport,
      };
    });
  }, [metricUnion, per]);

  const columns: ColumnsType<Row> = useMemo(() => {
    const base: ColumnsType<Row> = [
      {
        title: t("crossAnalysis.table.metric"),
        dataIndex: "metric_name",
        key: "metric_name",
        width: 360,
        render: (_: any, row: Row) => (
          <div className="space-y-1">
            <div className="font-medium text-slate-900">{row.metric_name}</div>
            {/* Hide metric ID in the UI (keep it only for internal keys / matching). */}
          </div>
        ),
      },
    ];

    const perCols: ColumnsType<Row> = per.map((p) => ({
      title: (
        <div className="flex items-center gap-2">
          <span>{p.label}</span>
          {p.framework ? <Tag>{p.framework}</Tag> : null}
        </div>
      ),
      key: p.fileId,
      width: 320,
      render: (_: any, row: Row) => {
        const item = row.byReport[p.fileId];
        if (p.loading) return <span className="text-slate-400">{t("common.loading")}</span>;
        if (p.error) return <span className="text-red-500">{p.error}</span>;
        if (!item) return <span className="text-slate-400">—</span>;

        const status = item.disclosure_status;
        const isNotDisclosed = status === "not_disclosed";
        const isPartiallyDisclosed = status === "partially_disclosed";

        const page = normalizePage(item.page);
        const unit = safeTrim(item.unit);
        const ctx = safeTrim(item.context);

        const evidenceTitle = `${p.label} · ${row.metric_name}`;

        const reasoningNode = item.reasoning ? (
          <Popover
            content={<div className="max-w-[520px] whitespace-pre-wrap text-sm">{item.reasoning}</div>}
            title={null}
            trigger="hover"
            mouseEnterDelay={0.2}
          >
            <span
              className="inline-flex items-center justify-center w-4 h-4 rounded-full border border-gray-300 text-gray-700 text-[11px] font-semibold leading-none cursor-pointer select-none"
              aria-label={t("analysis.analysisLabel")}
              onClick={(e) => e.stopPropagation()}
            >
              !
            </span>
          </Popover>
        ) : null;

        // Rule: not_disclosed -> show no value and no page
        if (isNotDisclosed) {
          return (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                {statusTag(t, status)}
                {reasoningNode}
              </div>
            </div>
          );
        }

        const rawValueText = isPartiallyDisclosed ? PARTIAL_VALUE_TEXT : toDisplayValue(item.value);

        const valueNode = rawValueText ? (
          ctx ? (
            <Popover content={ctx || t("analysis.noEvidenceExcerpt")} title={null} trigger="hover" mouseEnterDelay={0.2}>
              <span className="cursor-pointer underline decoration-dotted">{rawValueText}</span>
            </Popover>
          ) : (
            <span>{rawValueText}</span>
          )
        ) : (
          <span className="text-slate-400">—</span>
        );

        const pageNode = page ? (
          <button
            className="text-blue-500 hover:underline text-xs"
            onClick={(e) => {
              e.stopPropagation();
              openEvidence(p.fileId, page, evidenceTitle);
            }}
            title={t("crossAnalysis.disclosure.openEvidence")}
          >
            {t("crossAnalysis.disclosure.evidencePage", { page })}
          </button>
        ) : null;

        return (
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              {statusTag(t, status)}
              {reasoningNode}
            </div>
            <div className="text-sm text-slate-900 flex items-baseline gap-2 flex-wrap">
              <span className="font-medium">{valueNode}</span>
              {!isPartiallyDisclosed && unit ? <span className="text-xs text-slate-500">{unit}</span> : null}
              {pageNode}
            </div>
          </div>
        );
      },
    }));

    return [...base, ...perCols];
  }, [per]);

  if (!fileIds || fileIds.length < 2) {
    return (
      <div className="bg-white rounded-2xl shadow-sm p-6">
        <div className="text-slate-900 font-semibold">{t("crossAnalysis.disclosureCompleteness")}</div>
        <div className="text-slate-600 mt-1">{t("files.selectAtLeastTwoReports")}</div>
      </div>
    );
  }

  if (anyLoading && per.every((p) => p.metrics.length === 0 && !p.error)) {
    return (
      <div className="bg-white rounded-2xl shadow-sm p-10 text-center">
        <Spin size="large" />
        <div className="mt-4 text-slate-600">{t("crossAnalysis.disclosure.loading")}</div>
      </div>
    );
  }

  const anyError = per.some((p) => p.error);

  return (
    <div className="space-y-4">
      {anyError ? (
        <Alert
          message={t("crossAnalysis.disclosure.someReportsFailedTitle")}
          description={t("crossAnalysis.disclosure.someReportsFailedDesc")}
          type="warning"
          showIcon
        />
      ) : null}

      {/* Summary cards (mirrors single-report Analysis Summary) */}
      <div className="bg-white rounded-2xl shadow-sm p-6">
        <h3 className="text-xl font-semibold mb-4 text-gray-800">{t("analysis.summaryTitle")}</h3>

        <div className="space-y-4">
          {/* Column headers (desktop) */}
          <div className="hidden md:grid md:grid-cols-4 gap-4">
            <div className="text-xs font-semibold text-slate-700 px-1">{t("crossAnalysis.table.report")}</div>
            <div className="text-xs font-semibold text-slate-700 text-center px-1">{t("analysis.summary.not")}</div>
            <div className="text-xs font-semibold text-slate-700 text-center px-1">{t("analysis.summary.partial")}</div>
            <div className="text-xs font-semibold text-slate-700 text-center px-1">{t("analysis.summary.disclosed")}</div>
          </div>

          {/* Rows */}
          {summaryCards.map((p) => {
            const s = p.summary;
            const total = s.total || 0;
            const redPct = total ? ((s.red / total) * 100).toFixed(1) : "0.0";
            const yellowPct = total ? ((s.yellow / total) * 100).toFixed(1) : "0.0";
            const greenPct = total ? ((s.green / total) * 100).toFixed(1) : "0.0";

            return (
              <div key={p.fileId} className="grid grid-cols-1 md:grid-cols-4 gap-4 items-stretch">
                <div className="border border-slate-200 rounded-xl p-4 font-semibold text-slate-900 flex items-center gap-2">
                  <span>{p.label}</span>
                  {p.framework ? <Tag>{p.framework}</Tag> : null}
                </div>

                {p.loading ? (
                  <div className="border border-slate-200 rounded-xl p-4 text-slate-500 md:col-span-3 flex items-center justify-center">
                    {t("common.loading")}
                  </div>
                ) : p.error ? (
                  <div className="border border-slate-200 rounded-xl p-4 text-red-500 text-sm md:col-span-3 flex items-center">
                    {p.error}
                  </div>
                ) : total === 0 ? (
                  <div className="border border-slate-200 rounded-xl p-4 text-slate-500 text-sm md:col-span-3 flex items-center justify-center">
                    {t("crossAnalysis.disclosure.noMetricsFound")}
                  </div>
                ) : (
                  <>
                    <div className="border border-slate-200 rounded-xl p-3 flex flex-col items-center justify-center gap-1">
                      <div className="text-3xl font-bold text-red-500">{redPct}%</div>
                      <div className="text-sm text-gray-600">({s.red}/{total})</div>
                    </div>

                    <div className="border border-slate-200 rounded-xl p-3 flex flex-col items-center justify-center gap-1">
                      <div className="text-3xl font-bold text-yellow-500">{yellowPct}%</div>
                      <div className="text-sm text-gray-600">({s.yellow}/{total})</div>
                    </div>

                    <div className="border border-slate-200 rounded-xl p-3 flex flex-col items-center justify-center gap-1">
                      <div className="text-3xl font-bold text-green-500">{greenPct}%</div>
                      <div className="text-sm text-gray-600">({s.green}/{total})</div>
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Results table (mirrors single-report Analysis Results, but compared across reports) */}
      <div className="bg-white rounded-2xl shadow-sm p-6">
        <h3 className="text-xl font-semibold mb-4 text-gray-800">{t("analysis.resultsTitle")}</h3>
        <Table
          className="ca-table-wrap"
          columns={columns}
          dataSource={tableData}
          rowKey="key"
          pagination={{ pageSize: 20, showSizeChanger: true }}
          scroll={{ y: 560 }}
          tableLayout="fixed"
        />
      </div>
    </div>
  );
}
