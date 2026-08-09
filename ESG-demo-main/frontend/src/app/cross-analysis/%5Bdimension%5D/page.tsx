"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Button, Card, Empty, Modal, Space, Table, Tag, Typography, Skeleton, Grid } from "antd";
import type { ColumnsType } from "antd/es/table";

import { getStoredAuth } from "@/lib/auth";
import type { CrossExtractedRecord, CrossReportSummary } from "@/features/crossAnalysis/types";
import { normalizeCrossRecords } from "@/features/crossAnalysis/recordAdapter";
import { NewSidebar } from "@/components/cross-analysis/NewSidebar";
import { NewHeader } from "@/components/cross-analysis/NewHeader";
import { MetricChartsGrid, type MetricChartSpec } from "@/components/cross-analysis/MetricChartsGrid";
import { NewDataTable } from "@/components/cross-analysis/NewDataTable";
import DisclosureCompletenessComparison from "@/components/cross-analysis/DisclosureCompletenessComparison";
import FloatingChatAssistant from "@/components/cross-analysis/FloatingChatAssistant";
import { useT } from "@/i18n/useT";

const { Title } = Typography;
const { useBreakpoint } = Grid;

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


function isUuid(v: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(String(v || "").trim());
}

function normalizeReportKey(v: any): string {
  if (!v) return "";
  const s = String(v).trim().toLowerCase();
  const noExt = s.replace(/\.(pdf|json|txt)$/i, "");
  return noExt.replace(/[^a-z0-9]+/g, "");
}


function uniqPreserveOrder(arr: string[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const v0 of arr) {
    const v = safeTrim(v0);
    if (!v) continue;
    if (seen.has(v)) continue;
    seen.add(v);
    out.push(v);
  }
  return out;
}

function slugify(v: string): string {
  const s = safeTrim(v)
    .toLowerCase()
    .replace(/\s+/g, "_")
    .replace(/[^a-z0-9_\-]/g, "")
    .slice(0, 64);
  return s || "nav";
}

function parseIds(raw: string | null): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean)
    .filter((x, idx, arr) => arr.indexOf(x) === idx);
}

function arraysEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const auth = getStoredAuth();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (auth?.token) headers["Authorization"] = `Bearer ${auth.token}`;
  const res = await fetch(url, { ...init, headers });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// NOTE: Cross Analysis no longer reads cross_analysis/output/all_records.json nor triggers
// any re-extraction. It builds its dataset directly from per-report assessment outputs.

type TableRow = CrossExtractedRecord;

export default function CrossAnalysisDimensionPage() {
  const { t } = useT();
  const router = useRouter();
  const params = useParams();
  const searchParams = useSearchParams();

  const screens = useBreakpoint();
  const isMobile = !screens.md;

  const dimensionSlug = safeTrim((params as any)?.dimension || "");

  // IMPORTANT:
  // Next.js `useSearchParams()` may return a new object identity across renders.
  // If we depend on that object in useMemo/useCallback, we can accidentally retrigger
  // data loading (runExtract) on every render, which causes chart flicker.
  // Therefore we only depend on the *string values* we actually use.
  const idsParam = searchParams.get("ids") || "";

  // Cache commonly used query params as strings so hooks don't depend on the
  // `useSearchParams()` object identity.
  const searchParamsStr = searchParams.toString();
  const primaryQ = safeTrim(searchParams.get("primary"));
  const secondaryQ = safeTrim(searchParams.get("secondary"));
  const metricQ = safeTrim(searchParams.get("metric") || "");
  const ids = useMemo(() => parseIds(idsParam), [idsParam]);

  const [reports, setReports] = useState<CrossReportSummary[]>([]);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  const currentFramework = useMemo(() => {
    if (!reports.length) return "";
    const frameworks = (reports as any[]).map((r) => safeTrim(r?.framework ?? "")).filter(Boolean);
    if (frameworks.length !== reports.length) return "";
    return frameworks.every((x) => x === frameworks[0]) ? frameworks[0] : "";
  }, [reports]);

  const isSasbFramework = currentFramework === "SASB";

  const [allRecords, setAllRecords] = useState<CrossExtractedRecord[]>([]);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [recordsError, setRecordsError] = useState<string | null>(null);

  const ACTIVITY_METRICS_PRIMARY = "Activity Metrics";
  const isActivityMetricsPrimary = (p: string) =>
    p === ACTIVITY_METRICS_PRIMARY || safeTrim(p).toLowerCase() === "activity metricss";
  const canonicalPrimary = (p: string) => (isActivityMetricsPrimary(p) ? ACTIVITY_METRICS_PRIMARY : p);
  const ACTIVITY_METRICS_SKIP_SECONDARY = new Set([
    "Quantitative",
    "Qualitative",
    "Discussion and Analysis",
    "General",
  ]);

  // Data-driven navigation: Primary Navigation -> Secondary Navigation (canonical: "Activity Metricss" -> "Activity Metrics")
  const primaryOptions = useMemo(() => {
    return uniqPreserveOrder(
      (allRecords || []).map((r) => canonicalPrimary(safeTrim((r as any).primary_navigation))).filter(Boolean)
    );
  }, [allRecords]);

  const secondaryByPrimary = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const r of allRecords || []) {
      const pRaw = safeTrim((r as any).primary_navigation);
      if (!pRaw) continue;
      const p = canonicalPrimary(pRaw);
      if (isActivityMetricsPrimary(pRaw)) {
        const metricName = safeTrim((r as any).topic) || safeTrim((r as any).secondary_navigation);
        if (!metricName || ACTIVITY_METRICS_SKIP_SECONDARY.has(metricName)) continue;
        const a = m.get(p) || [];
        if (!a.includes(metricName)) a.push(metricName);
        m.set(p, a);
      } else {
        const s = isSasbFramework
          ? safeTrim((r as any).topic) || safeTrim((r as any).secondary_navigation)
          : safeTrim((r as any).secondary_navigation);
        if (!s) continue;
        const a = m.get(p) || [];
        if (!a.includes(s)) a.push(s);
        m.set(p, a);
      }
    }
    m.forEach((arr) => {
      arr.sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    });
    return m;
  }, [allRecords, isSasbFramework]);

  // Tertiary = metric_name: Level 3 under each (primary, secondary). For "Activity Metrics" there is no tertiary (secondary = metric_name).
  const tertiaryByPrimaryAndSecondary = useMemo(() => {
    const outer = new Map<string, Map<string, string[]>>();
    if (isSasbFramework) return outer;
    for (const r of allRecords || []) {
      const pRaw = safeTrim((r as any).primary_navigation);
      if (!pRaw) continue;
      const p = canonicalPrimary(pRaw);
      if (isActivityMetricsPrimary(pRaw)) continue;
      const s = safeTrim((r as any).secondary_navigation);
      const metricName = safeTrim((r as any).topic);
      if (!p || !s || !metricName) continue;
      if (!outer.has(p)) outer.set(p, new Map());
      const inner = outer.get(p)!;
      if (!inner.has(s)) inner.set(s, []);
      const arr = inner.get(s)!;
      if (!arr.includes(metricName)) arr.push(metricName);
    }
    outer.forEach((inner) => {
      inner.forEach((arr) => arr.sort((a, b) => a.localeCompare(b, undefined, { numeric: true })));
    });
    return outer;
  }, [allRecords, isSasbFramework]);

  const [selectedPrimary, setSelectedPrimary] = useState<string>("");
  const [selectedSecondaries, setSelectedSecondaries] = useState<string[]>([]);
  const [selectedTertiary, setSelectedTertiary] = useState<string | null>(null);
  const [expandedPrimaries, setExpandedPrimaries] = useState<Record<string, boolean>>({});

  // Table filters (header filter dropdowns)
  const [filterTopics, setFilterTopics] = useState<string[]>([]);
  const [filterYears, setFilterYears] = useState<string[]>([]);
  const [filterCompanies, setFilterCompanies] = useState<string[]>([]);
  const [filterSubTopics, setFilterSubTopics] = useState<string[]>([]);

  const clearTableFilters = useCallback(() => {
    setFilterTopics([]);
    setFilterSubTopics([]);
    setFilterYears([]);
    setFilterCompanies([]);
  }, []);

  // Resolve selectedPrimary / Secondaries / Tertiary (metric) from URL after data arrives.
  useEffect(() => {
    if (!primaryOptions.length) return;

    const primaryFromQuery = primaryQ;
    let desiredPrimary = "";
    if (primaryFromQuery && primaryOptions.includes(primaryFromQuery)) {
      desiredPrimary = primaryFromQuery;
    } else if (dimensionSlug) {
      desiredPrimary = primaryOptions.find((p) => slugify(p) === dimensionSlug) || "";
    }
    if (!desiredPrimary) desiredPrimary = primaryOptions[0];

    const secondaryOptions = secondaryByPrimary.get(desiredPrimary) || [];
    const secondaryRaw = secondaryQ;
    let desiredSecondaries = secondaryRaw
      ? secondaryRaw
          .split(",")
          .map((x) => safeTrim(x))
          .filter(Boolean)
      : [];
    desiredSecondaries = desiredSecondaries.filter((s) => secondaryOptions.includes(s));

    const inner = tertiaryByPrimaryAndSecondary.get(desiredPrimary);
    const tertiaryOptions =
      desiredPrimary === ACTIVITY_METRICS_PRIMARY
        ? (secondaryByPrimary.get(desiredPrimary) || [])
        : isSasbFramework
          ? []
          : (inner && desiredSecondaries.length ? inner.get(desiredSecondaries[0]) : undefined) || [];
    const desiredTertiary = !isSasbFramework && metricQ && tertiaryOptions.includes(metricQ) ? metricQ : null;

    setExpandedPrimaries((prev) => (prev?.[desiredPrimary] ? prev : { ...prev, [desiredPrimary]: true }));

    setSelectedPrimary((prev) => (prev === desiredPrimary ? prev : desiredPrimary));
    setSelectedSecondaries((prev) => (arraysEqual(prev, desiredSecondaries) ? prev : desiredSecondaries));
    setSelectedTertiary((prev) => (prev === desiredTertiary ? prev : desiredTertiary));
  }, [primaryOptions.join("|"), secondaryByPrimary, tertiaryByPrimaryAndSecondary, dimensionSlug, primaryQ, secondaryQ, metricQ, isSasbFramework]);

  const records = useMemo(() => {
    const p = selectedPrimary;
    const secondarySet = selectedSecondaries.length ? new Set(selectedSecondaries) : null;
    const metricName = selectedTertiary;
    const isActivityMetrics = p === ACTIVITY_METRICS_PRIMARY;
    return (allRecords || []).filter((r) => {
      if (p && canonicalPrimary(safeTrim((r as any).primary_navigation)) !== p) return false;
      if (isActivityMetrics) {
        if (metricName && safeTrim((r as any).topic) !== metricName) return false;
      } else {
        const secondaryValue = isSasbFramework
          ? safeTrim((r as any).topic) || safeTrim((r as any).secondary_navigation)
          : safeTrim((r as any).secondary_navigation);
        if (secondarySet && !secondarySet.has(secondaryValue)) return false;
        if (!isSasbFramework && metricName && safeTrim((r as any).topic) !== metricName) return false;
      }
      return true;
    });
  }, [allRecords, selectedPrimary, selectedSecondaries, selectedTertiary, isSasbFramework]);

  const topicOptions = useMemo(() => {
    return uniqPreserveOrder(records.map((r) => safeTrim((r as any).topic)).filter(Boolean)).sort((a, b) => a.localeCompare(b));
  }, [records]);

  const yearOptions = useMemo(() => {
    const xs = uniqPreserveOrder(records.map((r) => safeTrim((r as any).year)).filter(Boolean));
    return xs.sort((a, b) => b.localeCompare(a));
  }, [records]);

  const companyOptions = useMemo(() => {
    return uniqPreserveOrder(records.map((r) => safeTrim((r as any).name)).filter(Boolean)).sort((a, b) => a.localeCompare(b));
  }, [records]);

  const subTopicOptions = useMemo(() => {
    return uniqPreserveOrder(records.map((r) => safeTrim((r as any).sub_topic)).filter(Boolean)).sort((a, b) => a.localeCompare(b));
  }, [records]);

  // When new data arrives, prune invalid selections so filters don't get "stuck".
  useEffect(() => {
    const topicSet = new Set(topicOptions);
    const yearSet = new Set(yearOptions);
    const compSet = new Set(companyOptions);
    const subTopicSet = new Set(subTopicOptions);
    setFilterTopics((prev) => prev.filter((v) => topicSet.has(v)));
    setFilterYears((prev) => prev.filter((v) => yearSet.has(v)));
    setFilterCompanies((prev) => prev.filter((v) => compSet.has(v)));
    setFilterSubTopics((prev) => prev.filter((v) => subTopicSet.has(v)));
  }, [topicOptions, yearOptions, companyOptions, subTopicOptions]);

  // Note: we intentionally do NOT pre-filter the table dataset with these filter states.
  // Ant Design Table will apply the filters internally, preventing double-filter issues.

  // Load report display names
  useEffect(() => {
    if (ids.length < 2) return;
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetchJson<{ reports: CrossReportSummary[] }>(
          `/api/cross-analysis/reports?ids=${encodeURIComponent(ids.join(","))}`
        );
        if (!cancelled) setReports(resp.reports || []);
      } catch {
        if (!cancelled) {
          setReports(
            ids.map((id, idx) => ({
              file_id: id,
              display_name: `Report ${idx + 1}`,
              short_name: `R${idx + 1}`,
              confidence: 0,
              filename: id,
              has_assessment: false,
            }))
          );
        }
      } finally {
        // no-op
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ids.join(",")]);

  // Only allow comparison when all selected reports share the same framework and scope:
  // - SASB/TCFD: same industry and sub-industry (semi_industry).
  // - GRI: same Sector and Topic (gri_sector, gri_topic); GRI has no sub-industry.
  const canCompare = useMemo(() => {
    if (!reports || reports.length < 2) return false;
    const arr = reports as any[];
    const fw = arr.map((r) => safeTrim(r?.framework ?? ""));
    if (fw.some((x) => !x)) return false;
    if (!fw.every((x) => x === fw[0])) return false;
    const isGRI = fw[0] === "GRI";
    if (isGRI) {
      const sectors = arr.map((r) => safeTrim(r?.gri_sector ?? ""));
      const topics = arr.map((r) => safeTrim(r?.gri_topic ?? ""));
      if (sectors.some((x) => !x) || topics.some((x) => !x)) return false;
      return sectors.every((x) => x === sectors[0]) && topics.every((x) => x === topics[0]);
    }
    const ind = arr.map((r) => safeTrim(r?.industry ?? ""));
    const semi = arr.map((r) => safeTrim(r?.semi_industry ?? ""));
    if (ind.some((x) => !x) || semi.some((x) => !x)) return false;
    return ind.every((x) => x === ind[0]) && semi.every((x) => x === semi[0]);
  }, [reports]);

  const scopeMismatch = ids.length >= 2 && reports.length >= 2 && !canCompare;

  // Prefer using the uploaded report's filename as the display label across Cross Analysis.
  const fileIdToReportLabel = useMemo(() => {
    const m = new Map<string, string>();
    (reports || []).forEach((r) => {
      const fid = safeTrim((r as any)?.file_id);
      if (!fid) return;
      const fileNameRaw = safeTrim((r as any)?.filename);
      const label =
        (fileNameRaw ? stripFileExt(fileNameRaw) : "") ||
        safeTrim((r as any)?.display_name) ||
        safeTrim((r as any)?.short_name) ||
        fid;
      m.set(fid, label);
    });
    return m;
  }, [reports]);

  // Map: (filename stem / display name / short name) -> true uuid file_id
  const reportKeyToFileId = useMemo(() => {
    const m = new Map<string, string>();
    (reports || []).forEach((r) => {
      const fid = safeTrim((r as any)?.file_id);
      if (!fid) return;
      const fname = stripFileExt(safeTrim((r as any)?.filename));
      const dname = stripFileExt(safeTrim((r as any)?.display_name));
      const sname = stripFileExt(safeTrim((r as any)?.short_name));
      [fid, fname, dname, sname].forEach((k) => {
        const nk = normalizeReportKey(k);
        if (nk) m.set(nk, fid);
      });
    });
    return m;
  }, [reports]);


  const runExtract = useCallback(async () => {
    setRecordsLoading(true);
    setRecordsError(null);
    try {
      if (!ids || ids.length < 2) {
        setAllRecords([]);
        return;
      }

      // Prefer the backend cached JSON built from per-report assessment outputs.
      // Cache location:
      //   uploads/outputs/cross_analysis/output/json/{cache_key}.json
      const resp = await fetchJson<any>(
        `/api/cross-analysis/disclosed-cache?ids=${encodeURIComponent(ids.join(","))}`
      );

      const recordsFlat: any[] = Array.isArray(resp?.records) ? resp.records : [];
      const normalized = normalizeCrossRecords(recordsFlat);
      setAllRecords(normalized);

      if (!normalized.length) setRecordsError(t("crossAnalysis.noRecordsFound"));
    } catch (e: any) {
      setRecordsError(e?.message || t("crossAnalysis.failedToLoadRecords"));
      setAllRecords([]);
    } finally {
      setRecordsLoading(false);
    }
  }, [ids.join("|"), t]);

  // Load comparative data only when all reports share the same framework and scope (SASB: sub-industry; GRI: Sector+Topic).
  useEffect(() => {
    if (!canCompare || ids.length < 2) return;
    runExtract();
  }, [ids.join("|"), runExtract, canCompare]);

  // Grouping by Secondary Navigation (this is the "active panel" selector)
  const recordsBySecondaryAll = useMemo(() => {
    const g: Record<string, CrossExtractedRecord[]> = {};
    for (const r of records) {
      const k = safeTrim((r as any).secondary_navigation) || "(unknown)";
      (g[k] = g[k] || []).push(r);
    }
    for (const k of Object.keys(g)) {
      g[k].sort((a, b) => {
        const na = safeTrim((a as any).name);
        const nb = safeTrim((b as any).name);
        if (na !== nb) return na.localeCompare(nb);
        const ta = safeTrim((a as any).topic);
        const tb = safeTrim((b as any).topic);
        if (ta !== tb) return ta.localeCompare(tb);
        const sa = safeTrim((a as any).sub_topic);
        const sb = safeTrim((b as any).sub_topic);
        if (sa !== sb) return sa.localeCompare(sb);
        const ya = safeTrim((a as any).year);
        const yb = safeTrim((b as any).year);
        return yb.localeCompare(ya);
      });
    }
    return g;
  }, [records]);

  // Table always renders all records under the current Primary + selected Secondary(ies).
  // (If multiple secondaries are selected, the table shows their union.)

  const secondaryLabels = useMemo(() => Object.keys(recordsBySecondaryAll), [recordsBySecondaryAll]);
  const [activeSecondary, setActiveSecondary] = useState<string | null>(null);

  useEffect(() => {
    if (!secondaryLabels.length) {
      setActiveSecondary(null);
      return;
    }
    // Prefer first selected secondary.
    const preferred = selectedSecondaries.find((s) => secondaryLabels.includes(s));
    const next = preferred || secondaryLabels[0];
    setActiveSecondary((prev) => (prev === next ? prev : next));
  }, [secondaryLabels.join("|"), selectedSecondaries.join("|")]);

  const activeRowsChart = useMemo(() => {
    if (!activeSecondary) return [] as CrossExtractedRecord[];
    return recordsBySecondaryAll[activeSecondary] || ([] as CrossExtractedRecord[]);
  }, [activeSecondary, recordsBySecondaryAll]);

  const tableRows = records;

  const fullyDisclosedRecords = useMemo(() => {
    return records.filter((record) => safeTrim((record as any).disclosure_status) === "fully_disclosed");
  }, [records]);

  const columns: ColumnsType<TableRow> = useMemo(() => {
    const wrapCell = () => ({ style: { whiteSpace: "normal" as const, wordBreak: "break-word" as const } });

    return [
      {
        title: t("crossAnalysis.table.report"),
        dataIndex: "name",
        key: "name",
        width: isMobile ? 140 : 180,
        onCell: wrapCell,
        filters: companyOptions.map((v) => ({ text: v, value: v })),
        filterSearch: true,
        filterMultiple: true,
        filteredValue: filterCompanies.length ? filterCompanies : null,
        onFilter: (value, record) => safeTrim((record as any).name) === String(value),
        render: (v: string) => <span className="text-slate-900">{v || "—"}</span>,
      },
      {
        title: t("crossAnalysis.table.metric"),
        dataIndex: "topic",
        key: "topic",
        width: isMobile ? 220 : 260,
        onCell: wrapCell,
        filters: topicOptions.map((v) => ({ text: v, value: v })),
        filterSearch: true,
        filterMultiple: true,
        filteredValue: filterTopics.length ? filterTopics : null,
        onFilter: (value, record) => safeTrim((record as any).topic) === String(value),
        render: (_: string, record: any) => {
          const t = safeTrim(record?.topic);
          const st = safeTrim(record?.sub_topic);
          // On mobile, show Sub-topic as a secondary line to avoid horizontal scrolling.
          return isMobile ? (
            <div className="space-y-1">
              <div className="font-medium text-slate-900">{t || "—"}</div>
              {st ? <div className="text-xs text-slate-600">{st}</div> : null}
            </div>
          ) : (
            <span className="text-slate-900">{t || "—"}</span>
          );
        },
      },
      {
        title: t("crossAnalysis.table.subTopic"),
        dataIndex: "sub_topic",
        key: "sub_topic",
        width: 220,
        onCell: wrapCell,
        responsive: ["md"],
        filters: subTopicOptions.map((v) => ({ text: v, value: v })),
        filterSearch: true,
        filterMultiple: true,
        filteredValue: filterSubTopics.length ? filterSubTopics : null,
        onFilter: (value, record) => safeTrim((record as any).sub_topic) === String(value),
        render: (v: string) => <span className="text-slate-700">{v || "—"}</span>,
      },
      {
        title: t("crossAnalysis.table.value"),
        dataIndex: "data",
        key: "data",
        width: 110,
        onCell: wrapCell,
        render: (v: string) => <span className="font-medium text-slate-900">{v ?? "—"}</span>,
      },
      {
        title: t("crossAnalysis.table.unit"),
        dataIndex: "unit",
        key: "unit",
        width: 90,
        onCell: wrapCell,
        render: (v: string | null) => <span className="text-slate-600">{v || "—"}</span>,
      },
      {
        title: t("crossAnalysis.table.year"),
        dataIndex: "year",
        key: "year",
        width: 90,
        onCell: wrapCell,
        filters: yearOptions.map((v) => ({ text: v, value: v })),
        filterSearch: true,
        filterMultiple: true,
        filteredValue: filterYears.length ? filterYears : null,
        onFilter: (value, record) => safeTrim((record as any).year) === String(value),
        render: (v: string) => <span className="text-slate-700">{v || "—"}</span>,
      },
      {
        title: t("crossAnalysis.table.detail"),
        dataIndex: "detail",
        key: "detail",
        onCell: wrapCell,
        responsive: ["md"],
        render: (v: string) => <span className="text-slate-700">{v || "—"}</span>,
      },
      {
        title: "",
        key: "evidence",
        width: 110,
        render: (_: any, record: any) => {
          const rawId = safeTrim(record?.id) || safeTrim((record as any)?.file_id) || safeTrim(record?.name);
          const fileId = rawId
            ? (isUuid(rawId)
                ? rawId
                : reportKeyToFileId.get(normalizeReportKey(rawId)) || reportKeyToFileId.get(normalizeReportKey(record?.name)) || rawId)
            : "";
          const page = record?.page ? String(record.page) : "1";
          const title = `${safeTrim(record?.name) || t("crossAnalysis.table.report")} · ${safeTrim(record?.topic) || t("crossAnalysis.evidence.defaultName")}`;
          const qs = new URLSearchParams({
            file_id: fileId,
            page,
            name: title,
          }).toString();
          const href = `/cross-analysis/evidence?${qs}`;
          return fileId ? (
            <Link href={href} target="_blank" rel="noopener noreferrer">
              <span className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50">
                {t("crossAnalysis.table.evidence")} <span aria-hidden className="text-slate-400">↗</span>
              </span>
            </Link>
          ) : (
            <span className="text-slate-300">—</span>
          );
        },
      },
    ];
  }, [companyOptions, topicOptions, subTopicOptions, yearOptions, filterCompanies, filterTopics, filterSubTopics, filterYears, isMobile, reportKeyToFileId, t]);

  const handleTableChange = useCallback((_: any, filters: any) => {
    setFilterCompanies((filters?.name as string[]) || []);
    setFilterTopics((filters?.topic as string[]) || []);
    setFilterSubTopics((filters?.sub_topic as string[]) || []);
    setFilterYears((filters?.year as string[]) || []);
  }, []);

  const tableExpandable = useMemo(() => {
    if (!isMobile) return undefined;
    return {
      expandedRowRender: (record: any) => {
        const detail = safeTrim(record?.detail);
        const sub = safeTrim(record?.sub_topic);
        return (
          <div className="space-y-1 text-xs text-slate-700">
            {sub ? (
              <div>
                <span className="font-semibold text-slate-900">{t("crossAnalysis.table.subTopic")}: </span>
                {sub}
              </div>
            ) : null}
            {detail ? (
              <div>
                <span className="font-semibold text-slate-900">{t("crossAnalysis.table.detail")}: </span>
                {detail}
              </div>
            ) : (
              <div className="text-slate-400">{t("crossAnalysis.noAdditionalDetail")}</div>
            )}
          </div>
        );
      },
      rowExpandable: () => true,
    } as const;
  }, [isMobile, t]);

  const onSelectPrimary = useCallback(
    (primary: string, secondaryOverride?: string) => {
      const sp = new URLSearchParams(searchParamsStr);
      // Preserve framework/industry/semiIndustry when switching navigation
      sp.set("primary", primary);
      sp.delete("metric");

      const secs = secondaryByPrimary.get(primary) || [];
      const nextSecs = secondaryOverride && secs.includes(secondaryOverride)
        ? [secondaryOverride]
        : secs.length
          ? [secs[0]]
          : [];
      if (nextSecs.length) sp.set("secondary", nextSecs.join(","));
      else sp.delete("secondary");

      clearTableFilters();
      setSelectedTertiary(null);

      const qs = sp.toString();
      router.push(`/cross-analysis/${slugify(primary)}${qs ? `?${qs}` : ""}`);
      setSelectedPrimary(primary);
      setSelectedSecondaries(nextSecs);
      if (nextSecs.length) setActiveSecondary(nextSecs[0]);
    },
    [searchParamsStr, router, secondaryByPrimary, clearTableFilters]
  );

  const onToggleSecondary = useCallback(
    (secondary: string, toggleMode: boolean) => {
      if (!selectedPrimary) return;
      const sp = new URLSearchParams(searchParamsStr);
      // Preserve framework/industry/semiIndustry when switching navigation
      sp.delete("metric");

      const secOptions = secondaryByPrimary.get(selectedPrimary) || [];
      let next: string[] = [];

      // Default click: single-select. Ctrl/Cmd click: toggle multi-select.
      if (!toggleMode) {
        next = [secondary];
        // Changing the active secondary should not inherit stale header filters.
        clearTableFilters();
      } else {
        const has = selectedSecondaries.includes(secondary);
        next = has ? selectedSecondaries.filter((x) => x !== secondary) : [...selectedSecondaries, secondary];
        if (!next.length && secOptions.length) next = [secOptions[0]];
        // Stable ordering based on the primary's secondary order.
        const order = new Map(secOptions.map((v, idx) => [v, idx] as const));
        next.sort((a, b) => (order.get(a) ?? 1e9) - (order.get(b) ?? 1e9));
      }

      if (next.length) sp.set("secondary", next.join(","));
      else sp.delete("secondary");

      setSelectedTertiary(null);

      const qs = sp.toString();
      router.replace(`/cross-analysis/${slugify(selectedPrimary)}${qs ? `?${qs}` : ""}`);
      setSelectedSecondaries(next);
      setActiveSecondary(secondary);
    },
    [selectedPrimary, selectedSecondaries, searchParamsStr, router, secondaryByPrimary, clearTableFilters]
  );

  // 允许在没有 ids 的情况下也显示数据（从直接 JSON 文件加载）
  const isReady = true;

// Cross Analysis navigation is fully data-driven:
// Primary Navigation -> Secondary Navigation are extracted from all_records.json (and therefore reflect the real dataset).
const [viewMode, setViewMode] = useState<"issue" | "disclosure">("issue");

const buildNavUrl = useCallback(
  (primary: string, secondaries: string[], metric?: string | null) => {
    const next = new URLSearchParams(searchParamsStr);
    // Preserve framework/industry/semiIndustry (and ids) when switching navigation
    if (primary) next.set("primary", primary);
    else next.delete("primary");
    if (secondaries && secondaries.length) next.set("secondary", secondaries.join(","));
    else next.delete("secondary");
    if (metric) next.set("metric", metric);
    else next.delete("metric");

    const slug = slugify(primary || "nav");
    const qs = next.toString();
    return qs ? `/cross-analysis/${slug}?${qs}` : `/cross-analysis/${slug}`;
  },
  [searchParamsStr]
);

const handleTogglePrimary = useCallback(
  (primary: string) => {
    setViewMode("issue");
    setExpandedPrimaries((prev) => ({ ...prev, [primary]: safeTrim(selectedPrimary) === primary ? !prev?.[primary] : true }));

    if (safeTrim(selectedPrimary) !== primary) {
      const nextSecondaries: string[] = [];
      setSelectedPrimary(primary);
      setSelectedSecondaries(nextSecondaries);
      setSelectedTertiary(null);
      router.replace(buildNavUrl(primary, nextSecondaries, null));
    }
  },
  [router, buildNavUrl, selectedPrimary]
);

const handleSelectSecondary = useCallback(
  (primary: string, secondary: string) => {
    setViewMode("issue");
    setExpandedPrimaries((prev) => ({ ...prev, [primary]: true }));

    const currentOrder = secondaryByPrimary.get(primary) || [];
    const existing = safeTrim(selectedPrimary) === primary ? [...selectedSecondaries] : [];
    const hasSecondary = existing.includes(secondary);
    const nextSecondaries = hasSecondary ? existing.filter((x) => x !== secondary) : [...existing, secondary];
    const order = new Map(currentOrder.map((v, idx) => [v, idx] as const));
    nextSecondaries.sort((a, b) => (order.get(a) ?? 1e9) - (order.get(b) ?? 1e9));

    setSelectedPrimary(primary);
    setSelectedSecondaries(nextSecondaries);
    setSelectedTertiary(null);
    router.replace(buildNavUrl(primary, nextSecondaries, null));
  },
  [router, buildNavUrl, selectedPrimary, selectedSecondaries, secondaryByPrimary]
);

const handleSelectTertiary = useCallback(
  (primary: string, secondary: string, metricName: string) => {
    setViewMode("issue");
    setExpandedPrimaries((prev) => ({ ...prev, [primary]: true }));

    const isSameMetric =
      safeTrim(selectedPrimary) === primary &&
      selectedSecondaries.length === 1 &&
      selectedSecondaries[0] === secondary &&
      selectedTertiary === metricName;

    const nextSecondaries = isSameMetric ? [] : [secondary];
    const nextMetric = isSameMetric ? null : metricName;

    setSelectedPrimary(primary);
    setSelectedSecondaries(nextSecondaries);
    setSelectedTertiary(nextMetric);
    router.replace(buildNavUrl(primary, nextSecondaries, nextMetric));
  },
  [router, buildNavUrl, selectedPrimary, selectedSecondaries, selectedTertiary]
);

const handleSelectDisclosure = useCallback(() => {
  setViewMode("disclosure");
}, []);

// Normalize URL (slug + query) once selection is resolved from data/URL.
useEffect(() => {
  if (!selectedPrimary) return;

  const pQ = primaryQ;
  const sQ = secondaryQ;
  const mQ = searchParams.get("metric") || "";
  const desiredSlug = slugify(selectedPrimary);
  const curSlug = safeTrim((params as any)?.dimension || "");
  const desiredSecondaryStr = (selectedSecondaries || []).join(",");
  const desiredMetric = selectedTertiary || "";

  const needsUpdate =
    pQ !== selectedPrimary ||
    (desiredSecondaryStr ? sQ !== desiredSecondaryStr : !!sQ) ||
    desiredMetric !== mQ ||
    (curSlug && curSlug !== desiredSlug);

  if (needsUpdate) {
    router.replace(buildNavUrl(selectedPrimary, selectedSecondaries || [], selectedTertiary));
  }
}, [selectedPrimary, (selectedSecondaries || []).join("|"), selectedTertiary, primaryQ, secondaryQ, router, buildNavUrl, params, searchParams]);

  // 生成新样式需要的表格数据
  const newTableData = useMemo(() => {
    // 创建报告名称到 file_id 的映射
    const nameToFileIdMap = new Map<string, string>();
    reports.forEach((r) => {
      const fid = safeTrim((r as any).file_id);
      if (!fid) return;
      const fn = safeTrim((r as any).filename);
      const keys = [
        fn,
        fn ? stripFileExt(fn) : "",
        safeTrim((r as any).short_name),
        safeTrim((r as any).display_name),
      ].filter(Boolean);
      keys.forEach((k) => nameToFileIdMap.set(k, fid));
    });

    return fullyDisclosedRecords.map((record, index) => {
      const dataStr = safeTrim((record as any).data);
      const numericValue = parseFloat(dataStr?.replace(/,/g, "") || "0");
      const isNotDisclosed = !dataStr || (!Number.isFinite(numericValue) && (record as any).disclosure_status !== "fully_disclosed");
      const formattedValue = isNotDisclosed
        ? ""
        : isNaN(numericValue)
          ? dataStr || t("common.na")
          : numericValue.toLocaleString();
      // Extract file_id + page. Rows may come from old JSON where `id` is a company alias (e.g., Google2025).
      // Prefer true uuid file_id; otherwise resolve alias/name to uuid using report meta.
      const recordName = safeTrim((record as any).name) || "";
      const rawFileId = safeTrim((record as any).id) || safeTrim((record as any).file_id) || nameToFileIdMap.get(recordName) || "";
      const recordId = rawFileId
        ? (isUuid(rawFileId)
            ? rawFileId
            : reportKeyToFileId.get(normalizeReportKey(rawFileId)) || reportKeyToFileId.get(normalizeReportKey(recordName)) || rawFileId)
        : "";
      const recordPage = (record as any).page;
      const pageNumber = recordPage !== null && recordPage !== undefined 
        ? (typeof recordPage === 'number' ? recordPage : parseInt(String(recordPage))) 
        : null;

      const reportLabel = recordId
        ? fileIdToReportLabel.get(String(recordId)) || recordName || t("common.unknown")
        : recordName || t("common.unknown");

      return {
        id: index + 1,
        report: reportLabel,
        metric: safeTrim((record as any).topic) || t("common.na"),
        detail: [safeTrim((record as any).sub_topic), safeTrim((record as any).detail)]
          .filter(Boolean)
          .join(" — "),
        year: parseInt(safeTrim((record as any).year) || "0") || new Date().getFullYear(),
        value: formattedValue,
        isNotDisclosed,
        unit: safeTrim((record as any).unit) || "",
        fileId: recordId,
        page: pageNumber,
      };
    });
  }, [fullyDisclosedRecords, reports, fileIdToReportLabel, reportKeyToFileId, t]);

  // Report display names (may duplicate when same filename for multiple reports)
  const reportNames = useMemo(() => {
    if (reports.length > 0) {
      return reports
        .map((r) => {
          const fn = safeTrim((r as any)?.filename);
          return (fn ? stripFileExt(fn) : "") || safeTrim((r as any)?.short_name) || safeTrim((r as any)?.display_name) || safeTrim((r as any)?.file_id);
        })
        .filter(Boolean);
    }
    if (ids.length > 0) return ids;
    const uniqueNames = new Set<string>();
    allRecords.forEach((record) => {
      const name = safeTrim((record as any).name);
      if (name) uniqueNames.add(name);
    });
    return Array.from(uniqueNames);
  }, [reports, allRecords, ids]);

  // One slot per report with a unique chart label so the bar chart always shows one bar per report.
  // When two reports share the same name (e.g. "bmw esg 2024"), use "bmw esg 2024", "bmw esg 2024 (2)".
  const reportChartSlots = useMemo(() => {
    if (reports.length > 0) {
      const nameCount = new Map<string, number>();
      return reports.map((r) => {
        const fileId = safeTrim((r as any)?.file_id) || "";
        const fn = safeTrim((r as any)?.filename);
        const base = (fn ? stripFileExt(fn) : "") || safeTrim((r as any)?.short_name) || safeTrim((r as any)?.display_name) || fileId;
        const count = (nameCount.get(base) ?? 0) + 1;
        nameCount.set(base, count);
        const label = count === 1 ? base : `${base} (${count})`;
        return { fileId, label };
      });
    }
    const nameCount = new Map<string, number>();
    return (ids.length ? ids : reportNames.map((_, i) => String(i))).map((fileId, i) => {
      const base = reportNames[i] || fileId || String(i);
      const count = (nameCount.get(base) ?? 0) + 1;
      nameCount.set(base, count);
      const label = count === 1 ? base : `${base} (${count})`;
      return { fileId, label };
    });
  }, [reports, reportNames, ids]);

  // Reuse framework & sub-industry when all selected reports share the same (no re-prompt).
  const reportsFrameworkLabel = useMemo(() => {
    if (!reports.length) return null;
    const fw = (reports as any[]).map((r) => safeTrim(r.framework)).filter(Boolean);
    if (fw.length !== reports.length) return null;
    const first = fw[0];
    return fw.every((x) => x === first) ? first : null;
  }, [reports]);

  const reportsSemiIndustryLabel = useMemo(() => {
    if (!reports.length) return null;
    const semi = (reports as any[]).map((r) => safeTrim(r.semi_industry)).filter(Boolean);
    if (semi.length !== reports.length) return null;
    const first = semi[0];
    return semi.every((x) => x === first) ? first : null;
  }, [reports]);

  // Assign a stable color per report slot (one bar per report; used by all charts)
  const companyColors = useMemo(() => {
    const palette = [
      "#1677ff",
      "#52c41a",
      "#faad14",
      "#f5222d",
      "#722ed1",
      "#13c2c2",
      "#eb2f96",
      "#a0d911",
    ];
    const map: Record<string, string> = {};
    reportChartSlots.forEach((slot, idx) => {
      map[slot.fileId || slot.label] = palette[idx % palette.length];
    });
    return map;
  }, [reportChartSlots]);

  const companyLegend = useMemo(() => {
    return reportChartSlots.map((slot) => ({
      label: slot.label,
      color: companyColors[slot.fileId || slot.label] || "#1677ff",
    }));
  }, [reportChartSlots, companyColors]);

  // Build charts: one point per report (by file_id) so we always get one bar per report; value null = Not Disclosed.
  const metricCharts = useMemo<MetricChartSpec[]>(() => {
    if (!fullyDisclosedRecords.length && !reportChartSlots.length) return [];

    const yearNum = (y: string | null | undefined) => {
      const n = Number(safeTrim(y));
      return Number.isFinite(n) ? n : -Infinity;
    };

    const byKey = new Map<
      string,
      {
        topic: string;
        unit: string | null;
        category: string | null;
        perFileId: Map<string, { value: number | null; year: string | null }>;
      }
    >();

    fullyDisclosedRecords.forEach((record) => {
      const topic = safeTrim((record as any).topic) || t("crossAnalysis.table.metric");
      const unit = safeTrim((record as any).unit) || null;
      const category = safeTrim((record as any).category) || null;

      const fid = safeTrim((record as any).id || (record as any).file_id);

      const raw = safeTrim((record as any).data).replace(/,/g, "");
      const match = raw.match(/-?\d+(?:\.\d+)?/);
      const value = match && Number.isFinite(Number(match[0])) ? Number(match[0]) : null;
      const year = safeTrim((record as any).year) || null;

      const key = `${topic}||${unit || ""}`;
      if (!byKey.has(key)) {
        byKey.set(key, { topic, unit, category, perFileId: new Map() });
      }
      const bucket = byKey.get(key)!;
      if (category === "Quantitative") bucket.category = "Quantitative";
      const prev = bucket.perFileId.get(fid);
      if (!prev || (value != null && (prev.value == null || yearNum(year) > yearNum(prev.year)))) {
        bucket.perFileId.set(fid, { value, year });
      }
    });

    const charts: MetricChartSpec[] = [];
    byKey.forEach((bucket, key) => {
      const cat = bucket.category ? safeTrim(bucket.category) : "";
      if (cat && cat !== "Quantitative") return;

      const perFileId = bucket.perFileId;
      // One point per report slot so the chart always shows one bar per report (disclosed or Not Disclosed).
      const points: { company: string; colorKey: string; value: number | null; year: string | null }[] = reportChartSlots
        .map((slot) => {
          const v = perFileId.get(slot.fileId);
          return {
            company: slot.label,
            colorKey: slot.fileId || slot.label,
            value: v ? v.value : null,
            year: v ? v.year : null,
          };
        })
        .filter((p) => p.value !== null && p.value !== undefined && Number.isFinite(Number(p.value)));

      if (!points.length) return;

      const years = Array.from(new Set(points.map((p) => safeTrim(p.year)))).filter(Boolean) as string[];
      const yearInfo = years.length === 1 ? t("crossAnalysis.yearSingle", { year: years[0] }) : years.length > 1 ? t("crossAnalysis.yearsVary") : undefined;

      charts.push({
        key,
        topic: bucket.topic,
        unit: bucket.unit,
        yearInfo,
        points,
      });
    });

    charts.sort((a, b) => {
      const aWithData = a.points.filter((p) => p.value != null).length;
      const bWithData = b.points.filter((p) => p.value != null).length;
      return bWithData - aWithData || a.topic.localeCompare(b.topic);
    });
    return charts;
  }, [fullyDisclosedRecords, reportChartSlots, t]);

  return (
    <div className="min-h-screen bg-gradient-to-b from-[#F8FAFC] to-[#FFFFFF] p-6 w-full">
      {ids.length < 2 ? (
        <div className="w-full flex flex-col items-center justify-center min-h-[50vh] text-slate-600">
          <p className="text-base mb-2">{t("crossAnalysis.title")}</p>
          <p className="text-sm">{t("files.selectAtLeastTwoReports")}</p>
        </div>
      ) : scopeMismatch ? (
        <>
          <Modal
            open={true}
            title={t("crossAnalysis.title")}
            closable={false}
            maskClosable={false}
            footer={
              <Button type="primary" onClick={() => router.push("/dashboard")}>
                {t("common.back")}
              </Button>
            }
          >
            <p className="text-slate-700">{t("crossAnalysis.sameScopeRequired")}</p>
          </Modal>
        </>
      ) : (
      <div className="w-full flex gap-4 lg:gap-6 relative">
        {sidebarCollapsed ? (
          <button
            type="button"
            onClick={() => setSidebarCollapsed(false)}
            className="fixed left-4 top-24 z-30 w-11 h-11 bg-white rounded-full shadow-md border border-slate-200 flex items-center justify-center text-slate-600 hover:text-slate-900 hover:bg-slate-50 transition-colors"
            aria-label={t("crossAnalysis.navigation")}
            title={t("crossAnalysis.navigation")}
          >
            <PanelLeftOpen className="w-4 h-4" />
          </button>
        ) : null}

        <div className={`${sidebarCollapsed ? "w-0 overflow-visible" : "w-[320px]"} flex-shrink-0 transition-[width] duration-200 ease-[var(--motion-fluid)]`}>
          <div className="sticky top-6">
            {!sidebarCollapsed && (recordsLoading ? (
              <div className="relative w-[320px]">
                <button
                  type="button"
                  onClick={() => setSidebarCollapsed(true)}
                  className="absolute top-3 right-3 z-10 w-8 h-8 rounded-lg border border-slate-200 bg-white text-slate-500 hover:text-slate-900 hover:bg-slate-50 transition-colors"
                  aria-label={t("crossAnalysis.navigation")}
                >
                  <PanelLeftClose className="w-4 h-4 mx-auto" />
                </button>
                <Skeleton active paragraph={{ rows: 6 }} />
              </div>
            ) : (
              <div className="relative w-[320px]">
                <button
                  type="button"
                  onClick={() => setSidebarCollapsed(true)}
                  className="absolute top-3 right-3 z-10 w-8 h-8 rounded-lg border border-slate-200 bg-white text-slate-500 hover:text-slate-900 hover:bg-slate-50 transition-colors"
                  aria-label={t("crossAnalysis.navigation")}
                  title={t("crossAnalysis.navigation")}
                >
                  <PanelLeftClose className="w-4 h-4 mx-auto" />
                </button>
                <NewSidebar
                  primaryOptions={primaryOptions}
                  secondaryByPrimary={secondaryByPrimary}
                  tertiaryByPrimaryAndSecondary={tertiaryByPrimaryAndSecondary}
                  selectedPrimary={selectedPrimary}
                  selectedSecondaries={selectedSecondaries}
                  selectedTertiary={selectedTertiary}
                  expandedPrimaries={expandedPrimaries}
                  primaryIsActivityMetrics={selectedPrimary === ACTIVITY_METRICS_PRIMARY}
                  forceSecondaryLeafMode={isSasbFramework && selectedPrimary !== ACTIVITY_METRICS_PRIMARY}
                  viewMode={viewMode}
                  onTogglePrimary={handleTogglePrimary}
                  onSelectSecondary={handleSelectSecondary}
                  onSelectTertiary={handleSelectTertiary}
                  onSelectDisclosure={handleSelectDisclosure}
                />
              </div>
            ))}
          </div>
        </div>

        <div className="flex-1 min-w-0 space-y-4">
          <NewHeader
            title={viewMode === "disclosure" ? t("crossAnalysis.disclosureCompleteness") : t("crossAnalysis.title")}
            dimension=""
            reports={reportNames}
            frameworkLabel={reportsFrameworkLabel}
            semiIndustryLabel={reportsSemiIndustryLabel}
            companyLegend={companyLegend}
          />

          {viewMode === "disclosure" ? (
              <DisclosureCompletenessComparison
                fileIds={ids}
                reports={reports}
              />
          ) : (
            <>
              {/* Comparison Chart Card */}
              {recordsLoading ? (
                <Skeleton active paragraph={{ rows: 8 }} />
              ) : (
                <MetricChartsGrid charts={metricCharts} companyColors={companyColors} />
              )}

              {/* Data Table Card */}
              {recordsLoading ? (
                <Skeleton active paragraph={{ rows: 10 }} />
              ) : recordsError ? (
                <div className="bg-white rounded-2xl shadow-sm p-6 text-center text-red-500">
                  {recordsError}
                </div>
              ) : newTableData.length > 0 ? (
                <NewDataTable
                  data={newTableData}
                  onViewEvidence={(row) => {
                    // 跳转到证据页面
                    if (row.fileId) {
                      const params = new URLSearchParams({
                        file_id: row.fileId,
                        name: row.report || t("crossAnalysis.evidence.defaultName"),
                      });
                      if (row.page !== null && row.page !== undefined) {
                        params.set("page", String(row.page));
                      }
                      // Open in a new tab (do not replace the current Cross Analysis view)
                      window.open(`/cross-analysis/evidence?${params.toString()}`, "_blank", "noopener,noreferrer");
                    } else {
                      console.warn("No file_id found for row:", row);
                    }
                  }}
                />
              ) : (
                <div className="bg-white rounded-2xl shadow-sm p-6 text-center text-[#64748B]">
                  {t("crossAnalysis.noRecordsFound")}
                </div>
              )}
            </>
          )}
        </div>
      </div>
      )}

      {/* 悬浮 AI 助手：仅在对比分析主内容展示时显示 */}
      {canCompare && <FloatingChatAssistant />}
    </div>
  );
}
