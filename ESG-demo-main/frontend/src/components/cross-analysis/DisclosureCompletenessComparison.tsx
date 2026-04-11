"use client";

import React, { useEffect, useMemo, useState } from "react";
import { Alert, Modal, Popover, Progress, Select, Spin, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useRouter } from "next/navigation";
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

type SortMode = "default" | "report_asc" | "report_desc" | "disclosed_desc" | "not_disclosed_desc";

function safeTrim(v: any): string {
  if (v === null || v === undefined) return "";
  return String(v).trim();
}

function stripFileExt(name: string): string {
  const s = safeTrim(name);
  if (!s) return "";
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
  if (status === "partially_disclosed") return <Tag color="gold">{t("analysis.status.partial")}</Tag>;
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

function StatusDonut({ red, yellow, green, total }: { red: number; yellow: number; green: number; total: number }) {
  const redPct = total ? (red / total) * 100 : 0;
  const yellowPct = total ? (yellow / total) * 100 : 0;
  const donutStyle: React.CSSProperties = {
    background: `conic-gradient(#ef4444 0% ${redPct}%, #f59e0b ${redPct}% ${redPct + yellowPct}%, #22c55e ${redPct + yellowPct}% 100%)`,
  };

  return (
    <div className="relative h-14 w-14 md:h-[62px] md:w-[62px] shrink-0 rounded-full" style={donutStyle} aria-hidden>
      <div className="absolute inset-[9px] md:inset-[10px] rounded-full bg-white" />
    </div>
  );
}

export default function DisclosureCompletenessComparison(props: {
  fileIds: string[];
  reports?: CrossReportSummary[];
}) {
  const { t } = useT();
  const router = useRouter();
  const PARTIAL_VALUE_TEXT = t("analysis.partialValueText");
  const { fileIds, reports } = props;

  const [resultPage, setResultPage] = useState(1);
  const [resultPageSize, setResultPageSize] = useState(20);
  const [sortMode, setSortMode] = useState<SortMode>("default");
  const [openingFile, setOpeningFile] = useState<{ fileId: string; label: string } | null>(null);
  const [openingProgress, setOpeningProgress] = useState(0);

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

  useEffect(() => {
    setPer((prev) => prev.map((p) => ({ ...p, label: reportLabelFromSummaries(p.fileId, reports) })));
  }, [
    (reports || [])
      .map((r) => `${r.file_id}:${safeTrim(r.short_name)}:${safeTrim(r.display_name)}:${safeTrim(r.filename)}`)
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
            const assessment = await apiService.getAssessmentByFile(id, undefined, true);

            const actualFramework = safeTrim(
              (assessment as any)?.framework ??
                (assessment as any)?.assessment?.framework ??
                (assessment as any)?.current_framework
            );

            const converted: AnalysisDataItem[] = (assessment?.metric_analyses || [])
              .filter((item: any) => {
                const mid = item?.metric_id ?? item?.metric_code ?? item?.metricId ?? item?.Code ?? item?.code;
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
                    item?.["Disclosure Status"] ??
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
                  value,
                  page,
                  unit,
                  category,
                  topic,
                  type,
                  context: context == null ? null : String(context),
                };
              });

            return {
              fileId: id,
              label: reportLabelFromSummaries(id, reports),
              framework: actualFramework || null,
              loading: false,
              error: null,
              metrics: converted,
            } as PerReport;
          } catch (e: any) {
            return {
              fileId: id,
              label: reportLabelFromSummaries(id, reports),
              framework: null,
              loading: false,
              error: e?.message || t("common.error"),
              metrics: [],
            } as PerReport;
          }
        })
      );

      if (!cancelled) setPer(results);
    })();

    return () => {
      cancelled = true;
    };
  }, [fileIds.join("|"), reports?.map((r) => r.file_id).join("|")]);

  useEffect(() => {
    if (!openingFile) return;
    setOpeningProgress(0);
    void apiService.prefetchAssessmentByFile(openingFile.fileId, undefined, true);
    const timer = window.setInterval(() => {
      setOpeningProgress((prev) => {
        if (prev >= 100) {
          window.clearInterval(timer);
          router.push(`/dashboard/chat?file_id=${encodeURIComponent(openingFile.fileId)}`);
          return 100;
        }
        return Math.min(100, prev + 16);
      });
    }, 180);
    return () => window.clearInterval(timer);
  }, [openingFile, router]);

  const anyLoading = per.some((p) => p.loading);

  const summaryCards = useMemo(() => {
    return per.map((p) => ({ ...p, summary: computeSummary(p.metrics) }));
  }, [per]);

  const orderedSummaryCards = useMemo(() => {
    const xs = [...summaryCards];
    if (sortMode === "report_asc") xs.sort((a, b) => a.label.localeCompare(b.label));
    if (sortMode === "report_desc") xs.sort((a, b) => b.label.localeCompare(a.label));
    if (sortMode === "disclosed_desc") {
      xs.sort((a, b) => {
        const ar = a.summary.total ? a.summary.green / a.summary.total : -1;
        const br = b.summary.total ? b.summary.green / b.summary.total : -1;
        return br - ar || a.label.localeCompare(b.label);
      });
    }
    if (sortMode === "not_disclosed_desc") {
      xs.sort((a, b) => {
        const ar = a.summary.total ? a.summary.red / a.summary.total : -1;
        const br = b.summary.total ? b.summary.red / b.summary.total : -1;
        return br - ar || a.label.localeCompare(b.label);
      });
    }
    return xs;
  }, [summaryCards, sortMode]);

  const orderedPer = useMemo(() => {
    const order = new Map(orderedSummaryCards.map((item, index) => [item.fileId, index] as const));
    return [...per].sort((a, b) => (order.get(a.fileId) ?? 1e9) - (order.get(b.fileId) ?? 1e9));
  }, [per, orderedSummaryCards]);

  const metricUnion = useMemo(() => {
    const map = new Map<string, { metric_id: string; metric_name: string }>();
    for (const p of per) {
      for (const m of p.metrics) {
        if (!map.has(m.metric_id)) map.set(m.metric_id, { metric_id: m.metric_id, metric_name: m.metric_name });
      }
    }
    return Array.from(map.values());
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

  useEffect(() => {
    const totalPages = Math.max(1, Math.ceil(tableData.length / resultPageSize));
    setResultPage((prev) => Math.min(prev, totalPages));
  }, [tableData.length, resultPageSize]);

  const columns: ColumnsType<Row> = useMemo(() => {
    const base: ColumnsType<Row> = [
      {
        title: t("crossAnalysis.table.metric"),
        dataIndex: "metric_name",
        key: "metric_name",
        width: 300,
        fixed: "left",
        render: (_: any, row: Row) => <div className="font-medium text-slate-900">{row.metric_name}</div>,
      },
    ];

    const perCols: ColumnsType<Row> = orderedPer.map((p) => ({
      title: (
        <button
          type="button"
          className="font-semibold text-slate-800 hover:text-blue-600 text-left"
          onClick={() => setOpeningFile({ fileId: p.fileId, label: p.label })}
        >
          {p.label}
        </button>
      ),
      key: p.fileId,
      width: 280,
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
            getPopupContainer={(trigger) => trigger.parentElement || document.body}
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

        const rawValueText = isPartiallyDisclosed ? null : toDisplayValue(item.value);
        const valueNode = rawValueText ? (
          ctx ? (
            <Popover
              content={ctx || t("analysis.noEvidenceExcerpt")}
              title={null}
              trigger="hover"
              mouseEnterDelay={0.2}
              getPopupContainer={(trigger) => trigger.parentElement || document.body}
            >
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
  }, [orderedPer, PARTIAL_VALUE_TEXT, t]);

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
      <Modal open={!!openingFile} footer={null} closable={false} maskClosable={false} centered>
        <div className="py-3">
          <div className="text-base font-semibold text-slate-900 mb-4">{openingFile?.label}</div>
          <Progress percent={openingProgress} status="active" />
        </div>
      </Modal>

      {anyError ? (
        <Alert
          message={t("crossAnalysis.disclosure.someReportsFailedTitle")}
          description={t("crossAnalysis.disclosure.someReportsFailedDesc")}
          type="warning"
          showIcon
        />
      ) : null}

      <div className="bg-white rounded-2xl shadow-sm px-2.5 py-4 md:px-4 md:py-4">
        <div className="space-y-8">
          <div className="hidden md:grid grid-cols-[minmax(260px,0.61fr)_minmax(260px,0.76fr)_minmax(400px,0.84fr)_minmax(200px,0.76fr)_150px] gap-8 items-center border-slate-200 px-2.5 mb-2">
            <div className="text-xl font-semibold text-slate-800">{t("crossAnalysis.table.report")}</div>
            <div className="text-xl font-semibold text-slate-800 text-center">{t("analysis.summary.not")}</div>
            <div className="text-xl font-semibold text-slate-800 text-center">{t("analysis.status.partial")}</div>
            <div className="text-xl font-semibold text-slate-800 text-center">{t("analysis.summary.disclosed")}</div>
            <div className="text-xl flex justify-end">
              <Select
                value={sortMode}
                onChange={(value) => setSortMode(value)}
                size="middle"
                bordered={false}
                suffixIcon={null}
                className="w-full text-center [&_.ant-select-selection-item]:text-center"
                style={{ width: '100%', fontSize: '40px' }}
                options={[
                  { value: "default", label: "Default" },
                  { value: "report_asc", label: "Report A-Z" },
                  { value: "report_desc", label: "Report Z-A" },
                  { value: "disclosed_desc", label: "Disclosed first" },
                  { value: "not_disclosed_desc", label: "Not disclosed first" },
                ]}
              />
            </div>
          </div>

          {orderedSummaryCards.map((p) => {
            const s = p.summary;
            const total = s.total || 0;
            const redPct = total ? `${((s.red / total) * 100).toFixed(1)}%` : "0.0%";
            const yellowPct = total ? `${((s.yellow / total) * 100).toFixed(1)}%` : "0.0%";
            const greenPct = total ? `${((s.green / total) * 100).toFixed(1)}%` : "0.0%";

            return (
              <div
                key={p.fileId}
                className="grid grid-cols-1 md:grid-cols-[minmax(260px,0.61fr)_minmax(260px,0.76fr)_minmax(400px,0.84fr)_minmax(200px,0.76fr)_150px] gap-8 items-center rounded-xl border border-slate-200 px-2.5 mb-4 py-4.5"
              >
                <button
                  type="button"
                  onClick={() => setOpeningFile({ fileId: p.fileId, label: p.label })}
                  className="min-w-0 text-left text-xl font-semibold text-slate-900 truncate pr-1 hover:text-blue-600"
                  title={p.label}
                >
                  {p.label}
                </button>

                {p.loading ? (
                  <div className="md:col-span-4 text-slate-500 text-sm">{t("common.loading")}</div>
                ) : p.error ? (
                  <div className="md:col-span-4 text-red-500 text-sm">{p.error}</div>
                ) : total === 0 ? (
                  <div className="md:col-span-4 text-slate-500 text-sm">{t("crossAnalysis.disclosure.noMetricsFound")}</div>
                ) : (
                  <>
                    <div className="text-center">
                      <div className="text-[40px] font-semibold text-red-500 leading-none">{redPct}</div>
                      <div className="mt-1 text-xl text-slate-500">{s.red}/{total}</div>
                    </div>
                    <div className="text-center">
                      <div className="text-[40px] font-semibold text-amber-500 leading-none">{yellowPct}</div>
                      <div className="mt-1 text-xl text-slate-500">{s.yellow}/{total}</div>
                    </div>
                    <div className="text-center">
                      <div className="text-[40px] font-semibold text-green-500 leading-none">{greenPct}</div>
                      <div className="mt-1 text-xl text-slate-500">{s.green}/{total}</div>
                    </div>
                    <div className="flex items-center justify-center">
                      <StatusDonut red={s.red} yellow={s.yellow} green={s.green} total={total} />
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className="bg-white rounded-2xl shadow-sm p-5">
        <div className="flex items-center justify-between gap-3 mb-4">
          <h3 className="text-lg font-semibold text-gray-800">{t("analysis.resultsTitle")}</h3>
        </div>
        <div className="overflow-x-auto">
          <Table
            className="ca-table-wrap"
            columns={columns}
            dataSource={tableData}
            rowKey="key"
            pagination={{
              current: resultPage,
              pageSize: resultPageSize,
              showSizeChanger: true,
              pageSizeOptions: ["10", "20"],
              onChange: (page, pageSize) => {
                setResultPage(page);
                if (pageSize && pageSize !== resultPageSize) setResultPageSize(pageSize);
              },
            }}
            scroll={{ x: 420 + orderedPer.length * 280 }}
            tableLayout="fixed"
          />
        </div>
      </div>
    </div>
  );
}
