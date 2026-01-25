"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Button, Card, Empty, Space, Table, Tag, Typography, Skeleton, Grid } from "antd";
import type { ColumnsType } from "antd/es/table";

import { getStoredAuth } from "@/lib/auth";
import type { CrossExtractedRecord, CrossReportSummary } from "@/features/crossAnalysis/types";
import { normalizeCrossRecords } from "@/features/crossAnalysis/recordAdapter";
import IssueComparisonCharts, { getComparableUnitCharts } from "@/components/cross-analysis/IssueComparisonCharts";
import { NewSidebar } from "@/components/cross-analysis/NewSidebar";
import { NewHeader } from "@/components/cross-analysis/NewHeader";
import { NewComparisonChart } from "@/components/cross-analysis/NewComparisonChart";
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
  const ids = useMemo(() => parseIds(searchParams.get("ids")), [searchParams]);

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

    const primaryFromQuery = safeTrim(searchParams.get("primary"));
    let desiredPrimary = "";
    if (primaryFromQuery && primaryOptions.includes(primaryFromQuery)) {
      desiredPrimary = primaryFromQuery;
    } else if (dimensionSlug) {
      desiredPrimary = primaryOptions.find((p) => slugify(p) === dimensionSlug) || "";
    }
    if (!desiredPrimary) desiredPrimary = primaryOptions[0];

    const secondaryOptions = secondaryByPrimary.get(desiredPrimary) || [];
    const secondaryRaw = safeTrim(searchParams.get("secondary"));
    let desiredSecondaries = secondaryRaw
      ? secondaryRaw
          .split(",")
          .map((x) => safeTrim(x))
          .filter(Boolean)
      : [];
    desiredSecondaries = desiredSecondaries.filter((s) => secondaryOptions.includes(s));
    if (!desiredSecondaries.length && secondaryOptions.length) desiredSecondaries = [secondaryOptions[0]];

    setExpandedPrimaries((prev) => ({ ...prev, [desiredPrimary]: true }));

    setSelectedPrimary((prev) => (prev === desiredPrimary ? prev : desiredPrimary));
    setSelectedSecondaries((prev) => (arraysEqual(prev, desiredSecondaries) ? prev : desiredSecondaries));
  }, [primaryOptions.join("|"), secondaryByPrimary, searchParams, dimensionSlug]);

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

  const runExtract = useCallback(async () => {
    setRecordsLoading(true);
    setRecordsError(null);
    try {
      // Prefer reading the persisted output JSON directly (static file).
      // This avoids re-triggering expensive extraction and works even if embedding/LLM is unavailable.
      const staticRows = await loadAllRecordsFromStatic();

      // Second choice: local frontend route that reads the file from a mounted volume.
      let localRows: any[] | null = null;
      if (!staticRows) {
        const local = await tryFetchPublicJson<any>(`/local/cross-analysis/excel-metrics/direct?ts=${Date.now()}`);
        if (local && Array.isArray(local.records)) localRows = local.records;
      }

      // Fallback: if static output is not available, use backend API (may trigger extraction).
      let rows: any[] | null = staticRows || localRows;
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
          const fileId = safeTrim(record?.id);
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
  }, [companyOptions, topicOptions, subTopicOptions, yearOptions, filterCompanies, filterTopics, filterSubTopics, filterYears, isMobile]);

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
      const sp = new URLSearchParams(searchParams.toString());
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
    [searchParams, router, secondaryByPrimary, clearTableFilters]
  );

  const onToggleSecondary = useCallback(
    (secondary: string, toggleMode: boolean) => {
      if (!selectedPrimary) return;
      const sp = new URLSearchParams(searchParams.toString());

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
    [selectedPrimary, selectedSecondaries, searchParams, router, secondaryByPrimary, clearTableFilters]
  );

  // 允许在没有 ids 的情况下也显示数据（从直接 JSON 文件加载）
  const isReady = true;

  // 映射 dimension 和 issue 到数据字段
  const dimensionMap: Record<string, string> = {
    environment: "Environment",
    social: "Social",
    governance: "Governance",
    "supply-chain": "Supply Chain",
    community: "Community",
  };

  const issueMap: Record<string, string[]> = {
    emissions: ["greenhouse gas emissions", "ghg emissions", "emissions", "ghg"],
    energy: ["energy", "energy management", "energy consumption", "renewable"],
    water: ["water", "wastewater", "water consumption", "water use"],
    waste: ["waste", "waste management", "waste generation"],
    labor: ["labor", "workforce", "employee", "workforce", "labor practices"],
    diversity: ["diversity", "inclusion", "diversity", "inclusion", "gender", "global female representation", "female representation", "female"],
    health: ["health", "safety", "health", "safety", "occupational"],
    ethics: ["ethics", "business ethics", "ethics", "ethical", "corruption", "contributions"],
    compliance: ["compliance", "regulatory", "compliance", "regulatory", "legal"],
    risk: ["risk", "risk management", "risk", "risk management"],
    sourcing: ["sourcing", "responsible sourcing", "sourcing", "supply chain"],
    suppliers: ["supplier", "suppliers", "supplier", "suppliers", "vendor"],
    engagement: ["engagement", "community engagement", "engagement", "community"],
    impact: ["impact", "social impact", "impact", "social impact"],
  };

  // 根据选中的 dimension 和 issue 过滤数据（用于新样式）
  const [selectedDimension, setSelectedDimension] = useState("environment");
  const [selectedIssue, setSelectedIssue] = useState("emissions");
  const [viewMode, setViewMode] = useState<"issue" | "disclosure">("issue");

  // 初始化时，根据 URL 参数或数据设置默认 dimension
  useEffect(() => {
    if (dimensionSlug) {
      // 从 URL 参数获取 dimension
      const dimensionFromSlug = Object.keys(dimensionMap).find(
        (key) => slugify(dimensionMap[key]) === dimensionSlug
      );
      if (dimensionFromSlug) {
        setSelectedDimension(dimensionFromSlug);
      }
    } else if (primaryOptions.length > 0) {
      // 根据数据设置默认 dimension
      const firstPrimary = primaryOptions[0];
      const dimensionFromPrimary = Object.keys(dimensionMap).find(
        (key) => dimensionMap[key].toLowerCase() === firstPrimary.toLowerCase()
      );
      if (dimensionFromPrimary) {
        setSelectedDimension(dimensionFromPrimary);
      }
    }
  }, [dimensionSlug, primaryOptions]);

  const handleSelectIssue = useCallback((dimensionId: string, issueId: string) => {
    setViewMode("issue");
    setSelectedDimension(dimensionId);
    setSelectedIssue(issueId);
    
    // 同步到现有的选择逻辑
    const primaryNav = dimensionMap[dimensionId];
    const issueKeywords = issueMap[issueId] || [];
    
    // 找到匹配的 primary
    const matchedPrimary = primaryOptions.find(p => 
      p.toLowerCase() === primaryNav?.toLowerCase()
    );
    
    if (matchedPrimary) {
      setSelectedPrimary(matchedPrimary);
      
      // 找到匹配的 secondary
      const secs = secondaryByPrimary.get(matchedPrimary) || [];
      const matchedSecondary = secs.find(s => {
        const sLower = s.toLowerCase();
        return issueKeywords.some(kw => sLower.includes(kw.toLowerCase()));
      });
      
      if (matchedSecondary) {
        setSelectedSecondaries([matchedSecondary]);
      }
    }
  }, [primaryOptions, secondaryByPrimary]);

  const handleSelectDisclosure = useCallback(() => {
    setViewMode("disclosure");
  }, []);


  // 根据新样式选择的 dimension 和 issue 过滤记录
  const filteredRecordsForNewStyle = useMemo(() => {
    if (!allRecords.length) {
      console.log(`[CrossAnalysis] No records to filter, allRecords.length = ${allRecords.length}`);
      return [];
    }

    const primaryNav = dimensionMap[selectedDimension];
    const issueKeywords = issueMap[selectedIssue] || [];

    console.log(`[CrossAnalysis] Filtering records: dimension=${selectedDimension} (${primaryNav}), issue=${selectedIssue} (${issueKeywords.join(', ')})`);

    const filtered = allRecords.filter((record) => {
      // 匹配 Primary Navigation（不区分大小写）
      const recordPrimaryNav = safeTrim((record as any).primary_navigation)?.toLowerCase() || "";
      const matchesDimension = primaryNav
        ? recordPrimaryNav === primaryNav.toLowerCase()
        : false;

      if (!matchesDimension) {
        return false;
      }

      // 匹配 Secondary Navigation（不区分大小写，使用包含匹配）
      const secondaryNav = safeTrim((record as any).secondary_navigation)?.toLowerCase() || "";
      const matchesIssue =
        issueKeywords.length === 0 ||
        issueKeywords.some((keyword) => {
          const kwLower = keyword.toLowerCase();
          return secondaryNav.includes(kwLower) || kwLower.includes(secondaryNav);
        });

      return matchesIssue;
    });

    console.log(`[CrossAnalysis] Filtered to ${filtered.length} records`);
    return filtered;
  }, [allRecords, selectedDimension, selectedIssue]);

  // 生成新样式需要的图表数据
  const newChartData = useMemo(() => {
    if (!filteredRecordsForNewStyle.length) return [];

    // 获取所有唯一的报告名称
    const allReportNames = new Set<string>();
    filteredRecordsForNewStyle.forEach((record) => {
      const fid = safeTrim((record as any).id || (record as any).file_id);
      const fallback = safeTrim((record as any).name);
      const name = fid ? fileIdToReportLabel.get(fid) || fallback || fid : fallback;
      if (name) allReportNames.add(name);
    });
    const reportNamesList = Array.from(allReportNames);

    // 按 Sub-topic 分组
    const topicGroups: Record<string, Record<string, number>> = {};
    const unitByCategory: Record<string, Set<string>> = {};

    filteredRecordsForNewStyle.forEach((record) => {
      const category = safeTrim((record as any).sub_topic) || safeTrim((record as any).topic) || "Other";
      const fid = safeTrim((record as any).id || (record as any).file_id);
      const fallback = safeTrim((record as any).name) || "Unknown";
      const recordName = fid ? fileIdToReportLabel.get(fid) || fallback || fid : fallback;

      // 记录每个 category 的单位（用于 yAxis 标题 & tooltip）
      const unit = safeTrim((record as any).unit);
      if (unit) {
        if (!unitByCategory[category]) unitByCategory[category] = new Set<string>();
        unitByCategory[category].add(unit);
      }

      const rawValue = safeTrim((record as any).data)?.replace(/,/g, "") || "0";
      const value = parseFloat(rawValue) || 0;

      if (isNaN(value) || value === 0) return;

      if (!topicGroups[category]) {
        topicGroups[category] = {};
      }

      if (topicGroups[category][recordName]) {
        topicGroups[category][recordName] += value;
      } else {
        topicGroups[category][recordName] = value;
      }
    });

    return Object.entries(topicGroups)
      .map(([category, reportValues]) => {
        const chartItem: any = { category };

        // 单位：如果同一 category 出现多个单位，则标记为 Multiple units
        const units = unitByCategory[category];
        const unit =
          units && units.size > 0
            ? units.size === 1
              ? Array.from(units)[0]
              : "Multiple units"
            : null;

        chartItem.unit = unit;

        reportNamesList.forEach((reportName) => {
          chartItem[reportName] = reportValues[reportName] || 0;
        });
        return chartItem;
      })
      .filter((item) => reportNamesList.some((reportName) => item[reportName] > 0))
      .sort((a, b) => {
        const firstReport = reportNamesList[0] || "";
        return (b[firstReport] || 0) - (a[firstReport] || 0);
      });
  }, [filteredRecordsForNewStyle, fileIdToReportLabel]);

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

    return filteredRecordsForNewStyle.map((record, index) => {
      const numericValue = parseFloat(safeTrim((record as any).data)?.replace(/,/g, "") || "0");
      const formattedValue = isNaN(numericValue)
        ? safeTrim((record as any).data) || "N/A"
        : numericValue.toLocaleString();

      // 提取 file_id 和 page
      // 优先使用记录的 id，如果不存在则通过报告名称查找
      const recordName = safeTrim((record as any).name) || "";
      const recordId = (record as any).id || (record as any).file_id || nameToFileIdMap.get(recordName) || "";
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
        metric: safeTrim((record as any).sub_topic) || safeTrim((record as any).topic) || "N/A",
        detail: safeTrim((record as any).detail) || "",
        year: parseInt(safeTrim((record as any).year) || "0") || new Date().getFullYear(),
        value: formattedValue,
        unit: safeTrim((record as any).unit) || "",
        fileId: recordId, // 保存 file_id（优先使用记录的 id，否则通过名称查找）
        page: pageNumber, // 保存原始记录的 page
      };
    });
  }, [filteredRecordsForNewStyle, reports, fileIdToReportLabel]);

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
              selectedDimension={selectedDimension}
              selectedIssue={selectedIssue}
              viewMode={viewMode}
              onSelectIssue={handleSelectIssue}
              onSelectDisclosure={handleSelectDisclosure}
            />
          </div>
        )}

        {/* Main Content Area - 新样式 */}
        <div className="flex-1 min-w-0 space-y-4">
          {/* Header Card */}
          <NewHeader
            dimension={viewMode === "disclosure" ? "Disclosure completeness" : selectedDimension}
            reports={reportNames}
            onRefresh={() => runExtract()}
          />

          {viewMode === "disclosure" ? (
            <DisclosureCompletenessComparison fileIds={ids} reports={reports} />
          ) : (
            <>
              {/* Comparison Chart Card */}
              {recordsLoading ? (
                <Skeleton active paragraph={{ rows: 8 }} />
              ) : newChartData.length > 0 ? (
                <NewComparisonChart data={newChartData} />
              ) : (
                <div className="bg-white rounded-2xl shadow-sm p-6 text-center text-[#64748B]">
                  No chart data available
                </div>
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
