"use client";

import React, { useEffect, useMemo, useState } from "react";
import { Alert, Modal, Popover, Progress, Select, Spin, Table } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useRouter } from "next/navigation";
import { useT } from "@/i18n/useT";

import { apiService } from "@/lib/api";
import { CHART_PALETTE } from "@/features/crossAnalysis/tokens";

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

const EMPTY_VALUE_TOKENS = new Set(["", "-", "—", "n/a", "na", "null", "none", "not specified", "not available"]);

function isEmptyValue(value: unknown) {
  if (value === null || value === undefined) return true;
  if (typeof value === "number") return !Number.isFinite(value);
  const s = String(value).trim().toLowerCase();
  return EMPTY_VALUE_TOKENS.has(s);
}

function normalizeCategoryLabel(raw: unknown): string {
  const s = String(raw ?? "").trim();
  if (!s) return "";
  const lower = s.toLowerCase();
  if (lower === "quantitative") return "Quantitative";
  if (lower === "qualitative") return "Discussion and Analysis";
  if (lower === "discussion and analysis" || lower === "discussion") return "Discussion and Analysis";
  return s;
}

function formatDisplayValue(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") {
    return Number.isFinite(value) ? toDisplayValue(value) : null;
  }
  const s = String(value).trim();
  return isEmptyValue(s) ? null : s;
}

function extractContextText(raw: any): string {
  if (raw === null || raw === undefined) return "";
  if (typeof raw === "string") return raw.trim();

  const pickTextFromObj = (o: any): string => {
    if (!o || typeof o !== "object") return "";
    const cand =
      o.context ??
      o.Context ??
      o.specific_data_found ??
      o.specificDataFound ??
      o.text ??
      o.Text ??
      o.excerpt ??
      o.Excerpt ??
      o.evidence_text ??
      o.evidenceText ??
      o.evidence ??
      o.snippet ??
      o.Snippet;

    if (typeof cand === "string") return cand.trim();
    if (cand && typeof cand === "object") {
      const nested = pickTextFromObj(cand);
      if (nested) return nested;
    }

    try {
      const shallow = {
        context: o.context ?? o.Context,
        text: o.text ?? o.Text,
        excerpt: o.excerpt ?? o.Excerpt,
        page: o.page ?? o.page_number ?? o.pageNumber,
      };
      const s = JSON.stringify(shallow);
      return s === "{}" ? "" : s;
    } catch {
      return "";
    }
  };

  if (Array.isArray(raw)) {
    const parts = raw
      .map((seg) => {
        if (typeof seg === "string") return seg.trim();
        return pickTextFromObj(seg);
      })
      .filter((s) => !!s);
    return parts.join("\n\n");
  }

  if (typeof raw === "object") {
    return pickTextFromObj(raw);
  }

  return String(raw).trim();
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
  if (status === "fully_disclosed") {
    return (
      <span className="inline-flex rounded-full bg-[var(--brand-primary-soft)] px-2.5 py-0.5 text-xs font-medium text-[var(--brand-primary)]">
        {t("analysis.summary.disclosed")}
      </span>
    );
  }
  if (status === "partially_disclosed") {
    return (
      <span className="inline-flex rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-700">
        {t("analysis.status.partial")}
      </span>
    );
  }
  return (
    <span className="inline-flex rounded-full bg-red-50 px-2.5 py-0.5 text-xs font-medium text-red-600">
      {t("analysis.summary.not")}
    </span>
  );
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

function StatusDonut({ red, yellow, total }: { red: number; yellow: number; green: number; total: number }) {
  const redPct = total ? (red / total) * 100 : 0;
  const yellowPct = total ? (yellow / total) * 100 : 0;
  const donutStyle: React.CSSProperties = {
    background: `conic-gradient(#dc2626 0% ${redPct}%, #d97706 ${redPct}% ${redPct + yellowPct}%, var(--brand-primary) ${redPct + yellowPct}% 100%)`,
  };

  return (
    <div className="relative h-12 w-12 shrink-0 rounded-full md:h-14 md:w-14" style={donutStyle} aria-hidden>
      <div className="absolute inset-[8px] rounded-full bg-white md:inset-[9px]" />
    </div>
  );
}

export default function DisclosureCompletenessComparison(props: {
  fileIds: string[];
  reports?: CrossReportSummary[];
}) {
  const { t } = useT();
  const router = useRouter();
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
            const assessment = await apiService.getAssessmentByFile(id, undefined, false);

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
                  item?.Data
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
                const category = normalizeCategoryLabel(pick(item?.category, item?.Category) ?? "");
                const topic = pick(item?.topic, item?.Topic) ?? "";
                const type = pick(item?.type, item?.Type) ?? "";
                const reasoning = String(
                  pick(item?.reasoning, item?.["LLM Analysis"], item?.Reasoning, item?.analysis, item?.Analysis) ?? ""
                ).trim();
                const contextRaw = pick(
                  item?.context,
                  item?.Context,
                  item?.specific_data_found,
                  item?.specificDataFound,
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
                  context: extractContextText(contextRaw) || null,
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
    void apiService.prefetchAssessmentByFile(openingFile.fileId, undefined, false);
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
        width: "22%",
        ellipsis: true,
        render: (_: any, row: Row) => <div className="font-medium text-[var(--brand-text)]">{row.metric_name}</div>,
      },
    ];

    const perCols: ColumnsType<Row> = orderedPer.map((p) => ({
      title: (
        <button
          type="button"
          className="text-left font-semibold text-[var(--brand-text)] hover:text-[var(--brand-primary)]"
          onClick={() => setOpeningFile({ fileId: p.fileId, label: p.label })}
        >
          {p.label}
        </button>
      ),
      key: p.fileId,
      width: `${Math.floor(78 / Math.max(orderedPer.length, 1))}%`,
      render: (_: any, row: Row) => {
        const item = row.byReport[p.fileId];
        if (p.loading) return <span className="text-[var(--brand-subtle)]">{t("common.loading")}</span>;
        if (p.error) return <span className="text-red-600">{p.error}</span>;
        if (!item) return <span className="text-[var(--brand-subtle)]">—</span>;

        const status = item.disclosure_status;
        const category = normalizeCategoryLabel(item.category);
        const page = normalizePage(item.page);
        const unit = safeTrim(item.unit);
        const ctx = safeTrim(item.context);
        const evidenceTitle = `${p.label} · ${row.metric_name}`;
        const formattedValue = formatDisplayValue(item.value);
        const reasoningText = safeTrim(item.reasoning);
        const isDiscussionAndAnalysis = category === "Discussion and Analysis";

        let displayValue = "";
        if (status === "fully_disclosed") {
          displayValue = isDiscussionAndAnalysis
            ? reasoningText || t("analysis.noAnalysisText")
            : formattedValue || t("analysis.summary.notSpecified");
        } else {
          displayValue = reasoningText || t("analysis.noAnalysisText");
        }

        const contextContent = (
          <div className="max-w-[520px] p-2">
            <div className="text-xs font-semibold text-gray-700">{t("analysis.columns.context")}</div>
            {ctx ? (
              <div className="mt-1 whitespace-pre-wrap text-sm">{ctx}</div>
            ) : (
              <div className="mt-1 text-xs text-gray-500">{t("analysis.noEvidenceExcerpt")}</div>
            )}
          </div>
        );

        const pageNode = page ? (
          <button
            className="text-xs text-[var(--brand-primary)] hover:underline"
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
            <div className="flex items-center gap-2">{statusTag(t, status)}</div>
            <div className="flex flex-wrap items-start gap-2 text-sm text-[var(--brand-text)]">
              <span className="min-w-0 whitespace-pre-wrap break-words">{displayValue}</span>
              <Popover
                content={contextContent}
                title={null}
                trigger="hover"
                mouseEnterDelay={0.2}
                getPopupContainer={(trigger) => trigger.parentElement || document.body}
              >
                <span
                  className="inline-flex h-4 w-4 shrink-0 cursor-pointer select-none items-center justify-center rounded-full border border-black/15 text-[11px] font-semibold leading-none text-[var(--brand-muted)]"
                  aria-label={t("analysis.columns.context")}
                  onClick={(e) => e.stopPropagation()}
                >
                  !
                </span>
              </Popover>
              {status === "fully_disclosed" && !isDiscussionAndAnalysis && unit ? (
                <span className="shrink-0 text-xs text-[var(--brand-subtle)]">{unit}</span>
              ) : null}
              {pageNode}
            </div>
          </div>
        );
      },
    }));

    return [...base, ...perCols];
  }, [orderedPer, t]);

  if (!fileIds || fileIds.length < 2) {
    return (
      <div className="app-card p-6">
        <div className="font-semibold text-[var(--brand-text)]">{t("crossAnalysis.disclosureCompleteness")}</div>
        <div className="mt-1 text-[var(--brand-subtle)]">{t("files.selectAtLeastTwoReports")}</div>
      </div>
    );
  }

  if (anyLoading && per.every((p) => p.metrics.length === 0 && !p.error)) {
    return (
      <div className="app-card p-10 text-center">
        <Spin size="large" />
        <div className="mt-4 text-[var(--brand-subtle)]">{t("crossAnalysis.disclosure.loading")}</div>
      </div>
    );
  }

  const anyError = per.some((p) => p.error);

  return (
    <div className="space-y-4">
      <Modal open={!!openingFile} footer={null} closable={false} maskClosable={false} centered>
        <div className="py-3">
          <div className="mb-4 text-base font-semibold text-[var(--brand-text)]">{openingFile?.label}</div>
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

      <div className="app-card overflow-hidden p-4 md:p-6">
        <div className="mb-4 flex flex-col gap-3 border-b border-black/6 pb-4 sm:flex-row sm:items-center sm:justify-between">
          <h3 className="text-lg font-semibold text-[var(--brand-text)]">{t("crossAnalysis.disclosureCompleteness")}</h3>
          <Select
            value={sortMode}
            onChange={(value) => setSortMode(value)}
            size="middle"
            className="min-w-[180px]"
            options={[
              { value: "default", label: "Default" },
              { value: "report_asc", label: "Report A-Z" },
              { value: "report_desc", label: "Report Z-A" },
              { value: "disclosed_desc", label: "Disclosed first" },
              { value: "not_disclosed_desc", label: "Not disclosed first" },
            ]}
          />
        </div>

        <div className="hidden gap-3 border-b border-black/6 px-2 pb-3 text-xs font-semibold uppercase tracking-wide text-[var(--brand-subtle)] md:grid md:grid-cols-[1.2fr_repeat(3,1fr)_72px]">
          <div>{t("crossAnalysis.table.report")}</div>
          <div className="text-center">{t("analysis.summary.not")}</div>
          <div className="text-center">{t("analysis.status.partial")}</div>
          <div className="text-center">{t("analysis.summary.disclosed")}</div>
          <div className="text-center">%</div>
        </div>

        <div className="space-y-3 pt-3">
          {orderedSummaryCards.map((p, index) => {
            const s = p.summary;
            const total = s.total || 0;
            const redPct = total ? `${((s.red / total) * 100).toFixed(1)}%` : "0.0%";
            const yellowPct = total ? `${((s.yellow / total) * 100).toFixed(1)}%` : "0.0%";
            const greenPct = total ? `${((s.green / total) * 100).toFixed(1)}%` : "0.0%";
            const accent = CHART_PALETTE[index % CHART_PALETTE.length];

            return (
              <div
                key={p.fileId}
                className="grid grid-cols-1 items-center gap-3 rounded-2xl border border-black/6 bg-[var(--brand-surface)]/45 px-3 py-4 md:grid-cols-[1.2fr_repeat(3,1fr)_72px] md:gap-4 md:px-4"
              >
                <button
                  type="button"
                  onClick={() => setOpeningFile({ fileId: p.fileId, label: p.label })}
                  className="min-w-0 truncate text-left text-base font-semibold text-[var(--brand-text)] hover:text-[var(--brand-primary)]"
                  title={p.label}
                >
                  <span className="mr-2 inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: accent }} />
                  {p.label}
                </button>

                {p.loading ? (
                  <div className="text-sm text-[var(--brand-subtle)] md:col-span-4">{t("common.loading")}</div>
                ) : p.error ? (
                  <div className="text-sm text-red-600 md:col-span-4">{p.error}</div>
                ) : total === 0 ? (
                  <div className="text-sm text-[var(--brand-subtle)] md:col-span-4">{t("crossAnalysis.disclosure.noMetricsFound")}</div>
                ) : (
                  <>
                    <div className="text-center">
                      <div className="text-2xl font-bold leading-none text-red-600">{redPct}</div>
                      <div className="mt-1 text-sm text-[var(--brand-subtle)]">{s.red}/{total}</div>
                    </div>
                    <div className="text-center">
                      <div className="text-2xl font-bold leading-none text-amber-600">{yellowPct}</div>
                      <div className="mt-1 text-sm text-[var(--brand-subtle)]">{s.yellow}/{total}</div>
                    </div>
                    <div className="text-center">
                      <div className="text-2xl font-bold leading-none text-[var(--brand-primary)]">{greenPct}</div>
                      <div className="mt-1 text-sm text-[var(--brand-subtle)]">{s.green}/{total}</div>
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

      <div className="app-card p-4 md:p-6">
        <div className="mb-4">
          <h3 className="text-lg font-semibold text-[var(--brand-text)]">{t("analysis.resultsTitle")}</h3>
        </div>
        <Table
          className="ca-table-wrap disclosure-results-table"
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
          tableLayout="fixed"
        />
      </div>
    </div>
  );
}
