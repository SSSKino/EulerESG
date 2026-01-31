"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Button, Card, Empty, Space, Table, Tag, Typography, Skeleton, Grid } from "antd";
import type { ColumnsType } from "antd/es/table";

import { getStoredAuth } from "@/lib/auth";
import type { CrossExtractedRecord, CrossReportSummary } from "@/features/crossAnalysis/types";
import { normalizeCrossRecords } from "@/features/crossAnalysis/recordAdapter";
import { NewSidebar } from "@/components/cross-analysis/NewSidebar";
import { NewHeader } from "@/components/cross-analysis/NewHeader";
import { MetricChartsGrid, type MetricChartSpec } from "@/components/cross-analysis/MetricChartsGrid";
import { NewDataTable } from "@/components/cross-analysis/NewDataTable";
import DisclosureCompletenessComparison from "@/components/cross-analysis/DisclosureCompletenessComparison";

const { Title } = Typography;
const { useBreakpoint } = Grid;

type ExcelMetricsResponse = {
  records: any[];
  generated_at: string;
};

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

// Public JSON files (static outputs) do NOT require auth headers.
async function tryFetchPublicJson<T>(url: string): Promise<T | null> {
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

async function loadAllRecordsFromStatic(): Promise<any[] | null> {
  // The JSON is stored under uploads/outputs/cross_analysis/output/all_records.json on disk.
  // Browsers cannot read disk paths directly; it must be exposed over HTTP.
  // We therefore try the corresponding HTTP paths (same-origin), which are usually proxied to the backend.
  const ts = Date.now();
  const candidates = [
    `/uploads/outputs/cross_analysis/output/all_records.json?ts=${ts}`,
    `/uploads/outputs/cross_analysis/output/all_records.json`,
    `/uploads/outputs/cross_analysis/excel_output/all_records.json?ts=${ts}`,
    `/uploads/outputs/cross_analysis/excel_output/all_records.json`,
    `/output/all_records.json?ts=${ts}`,
    `/output/all_records.json`,
  ];

  for (const u of candidates) {
    const raw = await tryFetchPublicJson<any>(u);
    if (!raw) continue;
    if (Array.isArray(raw)) return raw;
    if (Array.isArray(raw?.records)) return raw.records;
  }
  return null;
}

type TableRow = CrossExtractedRecord;

export default function CrossAnalysisDimensionPage() {
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
  const frameworkParam = searchParams.get("framework") || "";

  // Cache commonly used query params as strings so hooks don't depend on the
  // `useSearchParams()` object identity.
  const searchParamsStr = searchParams.toString();
  const primaryQ = safeTrim(searchParams.get("primary"));
  const secondaryQ = safeTrim(searchParams.get("secondary"));

  const ids = useMemo(() => parseIds(idsParam), [idsParam]);
  const selectedFramework = useMemo(() => safeTrim(frameworkParam), [frameworkParam]);

  const [reports, setReports] = useState<CrossReportSummary[]>([]);
  const [reportsLoading, setReportsLoading] = useState(false);

  const [allRecords, setAllRecords] = useState<CrossExtractedRecord[]>([]);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [recordsError, setRecordsError] = useState<string | null>(null);

  // Data-driven navigation: Primary Navigation -> Secondary Navigation
  const primaryOptions = useMemo(() => {
    return uniqPreserveOrder(
      (allRecords || []).map((r) => safeTrim((r as any).primary_navigation)).filter(Boolean)
    );
  }, [allRecords]);

  const secondaryByPrimary = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const r of allRecords || []) {
      const p = safeTrim((r as any).primary_navigation);
      const s = safeTrim((r as any).secondary_navigation);
      if (!p || !s) continue;
      const a = m.get(p) || [];
      if (!a.includes(s)) a.push(s);
      m.set(p, a);
    }
    return m;
  }, [allRecords]);

  const [selectedPrimary, setSelectedPrimary] = useState<string>("");
  const [selectedSecondaries, setSelectedSecondaries] = useState<string[]>([]);
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

  // Resolve selectedPrimary/Secondaries from URL (or slug) after data arrives.
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
    if (!desiredSecondaries.length && secondaryOptions.length) desiredSecondaries = [secondaryOptions[0]];

    // Avoid pointless state updates that can cause continuous rerenders.
    setExpandedPrimaries((prev) => (prev?.[desiredPrimary] ? prev : { ...prev, [desiredPrimary]: true }));

    setSelectedPrimary((prev) => (prev === desiredPrimary ? prev : desiredPrimary));
    setSelectedSecondaries((prev) => (arraysEqual(prev, desiredSecondaries) ? prev : desiredSecondaries));
  }, [primaryOptions.join("|"), secondaryByPrimary, dimensionSlug, primaryQ, secondaryQ]);

  const records = useMemo(() => {
    const p = selectedPrimary;
    const secondarySet = selectedSecondaries.length ? new Set(selectedSecondaries) : null;
    return (allRecords || []).filter((r) => {
      if (p && safeTrim((r as any).primary_navigation) !== p) return false;
      if (secondarySet && !secondarySet.has(safeTrim((r as any).secondary_navigation))) return false;
      return true;
    });
  }, [allRecords, selectedPrimary, selectedSecondaries]);

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
      setReportsLoading(true);
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
        if (!cancelled) setReportsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ids.join(",")]);

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
      // Prefer local route first to avoid repeated 404s and UI flicker during dev.
      // Local route reads from the mounted volume and is typically the fastest path.
      let localRows: any[] | null = null;
      const local = await tryFetchPublicJson<any>(`/local/cross-analysis/excel-metrics/direct?ts=${Date.now()}`);
      if (local && Array.isArray(local.records)) localRows = local.records;

      // Second choice: read the persisted output JSON directly (static file).
      // This avoids re-triggering expensive extraction and works even if embedding/LLM is unavailable.
      const staticRows = localRows ? null : await loadAllRecordsFromStatic();

      // Fallback: if neither local nor static output is available, use backend API (may trigger extraction).
      let rows: any[] | null = localRows || staticRows;
      if (!rows) {
        try {
          const resp = await fetchJson<ExcelMetricsResponse>(`/api/cross-analysis/excel-metrics`, {
            method: "POST",
            body: JSON.stringify({ ids }),
          });
          rows = Array.isArray(resp?.records) ? resp.records : null;
        } catch (e) {
          console.warn("Failed to load records via backend API:", e);
          rows = null;
        }
      }

      if (rows && rows.length) {
        console.log(`[CrossAnalysis] Loaded ${rows.length} records`);

        // If ids parameter exists, try to filter by ids. If filtering yields nothing, show all.
        let filteredRecords = rows;
        if (ids.length >= 2) {
          const idsSet = new Set(ids);
          const matchedRecords = rows.filter((record: any) => {
            const recordId = record.id || record.file_id || "";
            const recordName = record.name || "";
            // 匹配 id 或 name
            return idsSet.has(recordId) || idsSet.has(recordName) || 
                   Array.from(idsSet).some(id => recordName.includes(id) || id.includes(recordName));
          });
          
          // 如果匹配到记录，使用匹配的记录；否则使用所有记录（避免空显示）
          if (matchedRecords.length > 0) {
            filteredRecords = matchedRecords;
            console.log(`[CrossAnalysis] Filtered to ${filteredRecords.length} records based on ids`);
          } else {
            console.log(`[CrossAnalysis] No records matched ids, showing all ${filteredRecords.length} records instead`);
          }
        } else {
          console.log(`[CrossAnalysis] No ids filter, showing all ${filteredRecords.length} records`);
        }
        
        const normalized = normalizeCrossRecords(filteredRecords);
        console.log(`[CrossAnalysis] Normalized to ${normalized.length} records`);
        setAllRecords(normalized);
      } else {
        console.warn(`[CrossAnalysis] No records available (static output missing and API returned empty)`);
        setAllRecords([]);
      }
    } catch (e: any) {
      setRecordsError(e?.message || "Failed to load records");
      setAllRecords([]);
    } finally {
      setRecordsLoading(false);
    }
  }, [ids]);

  // Auto-run on mount and whenever ids change.
  useEffect(() => {
    runExtract();
  }, [ids.join("|"), runExtract]);

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

  const columns: ColumnsType<TableRow> = useMemo(() => {
    const wrapCell = () => ({ style: { whiteSpace: "normal" as const, wordBreak: "break-word" as const } });

    return [
      {
        title: "Name",
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
        title: "Topic",
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
        title: "Sub-topic",
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
        title: "Data",
        dataIndex: "data",
        key: "data",
        width: 110,
        onCell: wrapCell,
        render: (v: string) => <span className="font-medium text-slate-900">{v ?? "—"}</span>,
      },
      {
        title: "Unit",
        dataIndex: "unit",
        key: "unit",
        width: 90,
        onCell: wrapCell,
        render: (v: string | null) => <span className="text-slate-600">{v || "—"}</span>,
      },
      {
        title: "Year",
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
        title: "Detail",
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
          const title = `${safeTrim(record?.name) || "Report"} · ${safeTrim(record?.topic) || "Evidence"}`;
          const qs = new URLSearchParams({
            file_id: fileId,
            page,
            name: title,
          }).toString();
          const href = `/cross-analysis/evidence?${qs}`;
          return fileId ? (
            <Link href={href} target="_blank" rel="noopener noreferrer">
              <span className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50">
                Evidence <span aria-hidden className="text-slate-400">↗</span>
              </span>
            </Link>
          ) : (
            <span className="text-slate-300">—</span>
          );
        },
      },
    ];
  }, [companyOptions, topicOptions, subTopicOptions, yearOptions, filterCompanies, filterTopics, filterSubTopics, filterYears, isMobile, reportKeyToFileId]);

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
                <span className="font-semibold text-slate-900">Sub-topic: </span>
                {sub}
              </div>
            ) : null}
            {detail ? (
              <div>
                <span className="font-semibold text-slate-900">Detail: </span>
                {detail}
              </div>
            ) : (
              <div className="text-slate-400">No additional detail.</div>
            )}
          </div>
        );
      },
      rowExpandable: () => true,
    } as const;
  }, [isMobile]);

  const onSelectPrimary = useCallback(
    (primary: string, secondaryOverride?: string) => {
      const sp = new URLSearchParams(searchParamsStr);
      sp.set("primary", primary);

      const secs = secondaryByPrimary.get(primary) || [];
      const nextSecs = secondaryOverride && secs.includes(secondaryOverride)
        ? [secondaryOverride]
        : secs.length
          ? [secs[0]]
          : [];
      if (nextSecs.length) sp.set("secondary", nextSecs.join(","));
      else sp.delete("secondary");

      clearTableFilters();

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
  (primary: string, secondaries: string[]) => {
    const next = new URLSearchParams(searchParamsStr);
    if (primary) next.set("primary", primary);
    else next.delete("primary");

    if (secondaries && secondaries.length) next.set("secondary", secondaries.join(","));
    else next.delete("secondary");

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

    // If user clicks a different primary, switch to it and select its first secondary by default.
    if (safeTrim(selectedPrimary) !== primary) {
      const secs = secondaryByPrimary.get(primary) || [];
      const nextSecondaries = secs.length ? [secs[0]] : [];
      setSelectedPrimary(primary);
      setSelectedSecondaries(nextSecondaries);
      router.replace(buildNavUrl(primary, nextSecondaries));
    }
  },
  [router, buildNavUrl, selectedPrimary, secondaryByPrimary]
);

const handleSelectSecondary = useCallback(
  (primary: string, secondary: string) => {
    setViewMode("issue");
    setExpandedPrimaries((prev) => ({ ...prev, [primary]: true }));
    setSelectedPrimary(primary);
    setSelectedSecondaries([secondary]);
    router.replace(buildNavUrl(primary, [secondary]));
  },
  [router, buildNavUrl]
);

const handleSelectDisclosure = useCallback(() => {
  setViewMode("disclosure");
}, []);

// Normalize URL (slug + query) once selection is resolved from data/URL,
// so navigation state remains stable even when users land on /cross-analysis/<dimension>?ids=...
useEffect(() => {
  if (!selectedPrimary) return;

  const pQ = primaryQ;
  const sQ = secondaryQ;
  const desiredSlug = slugify(selectedPrimary);
  const curSlug = safeTrim((params as any)?.dimension || "");
  const desiredSecondaryStr = (selectedSecondaries || []).join(",");

  const needsUpdate =
    pQ !== selectedPrimary ||
    (desiredSecondaryStr ? sQ !== desiredSecondaryStr : !!sQ) ||
    (curSlug && curSlug !== desiredSlug);

  if (needsUpdate) {
    router.replace(buildNavUrl(selectedPrimary, selectedSecondaries || []));
  }
}, [selectedPrimary, (selectedSecondaries || []).join("|"), primaryQ, secondaryQ, router, buildNavUrl, params]);

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

    return records.map((record, index) => {
      const numericValue = parseFloat(safeTrim((record as any).data)?.replace(/,/g, "") || "0");
      const formattedValue = isNaN(numericValue)
        ? safeTrim((record as any).data) || "N/A"
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
        ? fileIdToReportLabel.get(String(recordId)) || recordName || "Unknown"
        : recordName || "Unknown";

      return {
        id: index + 1,
        report: reportLabel,
        // Use Topic as the metric (requested)
        metric: safeTrim((record as any).topic) || "N/A",
        // Put sub-topic / detail into the detail column (wrappable)
        detail: [safeTrim((record as any).sub_topic), safeTrim((record as any).detail)]
          .filter(Boolean)
          .join(" — "),
        year: parseInt(safeTrim((record as any).year) || "0") || new Date().getFullYear(),
        value: formattedValue,
        unit: safeTrim((record as any).unit) || "",
        fileId: recordId, // 保存 file_id（优先使用记录的 id，否则通过名称查找）
        page: pageNumber, // 保存原始记录的 page
      };
    });
  }, [records, reports, fileIdToReportLabel, reportKeyToFileId]);

  // 获取报告名称列表（从 records 中提取唯一的报告名称）
  const reportNames = useMemo(() => {
    // Prefer report meta returned from backend.
    if (reports.length > 0) {
      return reports
        .map((r) => {
          const fn = safeTrim((r as any)?.filename);
          return (fn ? stripFileExt(fn) : "") || safeTrim((r as any)?.short_name) || safeTrim((r as any)?.display_name) || safeTrim((r as any)?.file_id);
        })
        .filter(Boolean);
    }

    // Fallback: if ids exist, at least show them.
    if (ids.length > 0) {
      return ids;
    }

    // Last resort: derive from records.
    const uniqueNames = new Set<string>();
    allRecords.forEach((record) => {
      const name = safeTrim((record as any).name);
      if (name) uniqueNames.add(name);
    });
    return Array.from(uniqueNames);
  }, [reports, allRecords, ids]);

  // Assign a stable color per company/report (used by all charts)
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
    reportNames.forEach((name, idx) => {
      map[name] = palette[idx % palette.length];
    });
    return map;
  }, [reportNames]);

  // Build charts that compare the same indicator (Topic) across different companies that have data.
  // If different indicators are disclosed, they become separate charts in a grid (max 4 per row).
  const metricCharts = useMemo<MetricChartSpec[]>(() => {
    if (!records.length) return [];

    const yearNum = (y: string | null | undefined) => {
      const n = Number(safeTrim(y));
      return Number.isFinite(n) ? n : -Infinity;
    };

    const byKey = new Map<
      string,
      {
        topic: string;
        unit: string | null;
        perCompany: Map<string, { value: number; year: string | null }>;
      }
    >();

    records.forEach((record) => {
      const topic = safeTrim((record as any).topic) || "Metric";
      const unit = safeTrim((record as any).unit) || null;

      const fid = safeTrim((record as any).id || (record as any).file_id);
      const fallback = safeTrim((record as any).name);
      const company = fid ? fileIdToReportLabel.get(fid) || fallback || fid : fallback || "Unknown";

      // Parse the first numeric value from `data`.
      const raw = safeTrim((record as any).data).replace(/,/g, "");
      const match = raw.match(/-?\d+(?:\.\d+)?/);
      if (!match) return;
      const value = Number(match[0]);
      if (!Number.isFinite(value)) return;

      const year = safeTrim((record as any).year) || null;

      const key = `${topic}||${unit || ""}`;
      if (!byKey.has(key)) {
        byKey.set(key, { topic, unit, perCompany: new Map() });
      }
      const bucket = byKey.get(key)!;
      const prev = bucket.perCompany.get(company);

      // Keep the most recent year per company for this Topic+Unit.
      if (!prev || yearNum(year) > yearNum(prev.year)) {
        bucket.perCompany.set(company, { value, year });
      }
    });

    const charts: MetricChartSpec[] = [];
    byKey.forEach((bucket, key) => {
      const points = Array.from(bucket.perCompany.entries()).map(([company, v]) => ({
        company,
        value: v.value,
        year: v.year,
      }));

      // Compare only metrics that have data for >=2 companies.
      if (points.length < 2) return;

      // Keep company order consistent with the header list when possible.
      points.sort((a, b) => {
        const ai = reportNames.indexOf(a.company);
        const bi = reportNames.indexOf(b.company);
        if (ai !== -1 && bi !== -1) return ai - bi;
        if (ai !== -1) return -1;
        if (bi !== -1) return 1;
        return a.company.localeCompare(b.company);
      });

      const years = Array.from(new Set(points.map((p) => safeTrim(p.year)))).filter(Boolean) as string[];
      const yearInfo = years.length === 1 ? `Year: ${years[0]}` : years.length > 1 ? "Years vary" : undefined;

      charts.push({
        key,
        topic: bucket.topic,
        unit: bucket.unit,
        yearInfo,
        points,
      });
    });

    charts.sort((a, b) => b.points.length - a.points.length || a.topic.localeCompare(b.topic));
    return charts;
  }, [records, fileIdToReportLabel, reportNames]);

  return (
    <div className="min-h-screen bg-gradient-to-b from-[#F8FAFC] to-[#FFFFFF] p-6 w-full">
      <div className="w-full flex gap-6">
        {/* Left Sidebar - 新样式 */}
        {recordsLoading ? (
          <div className="w-[320px] flex-shrink-0">
            <Skeleton active paragraph={{ rows: 6 }} />
          </div>
        ) : (
          <div className="w-[320px] flex-shrink-0">
            <NewSidebar
              primaryOptions={primaryOptions}
              secondaryByPrimary={secondaryByPrimary}
              selectedPrimary={selectedPrimary}
              selectedSecondaries={selectedSecondaries}
              expandedPrimaries={expandedPrimaries}
              viewMode={viewMode}
              onTogglePrimary={handleTogglePrimary}
              onSelectSecondary={handleSelectSecondary}
              onSelectDisclosure={handleSelectDisclosure}
            />
          </div>
        )}

        {/* Main Content Area - 新样式 */}
        <div className="flex-1 min-w-0 space-y-4">
          {/* Header Card */}
          <NewHeader
            dimension={viewMode === "disclosure" ? "Disclosure completeness" : `${selectedPrimary}${selectedSecondaries?.[0] ? " / " + selectedSecondaries[0] : ""}`}
            reports={reportNames}
            onRefresh={() => runExtract()}
          />

          {viewMode === "disclosure" ? (
              <DisclosureCompletenessComparison
                fileIds={ids}
                reports={reports}
                framework={selectedFramework}
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
                        name: row.report || "Evidence",
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
                  No records found
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}