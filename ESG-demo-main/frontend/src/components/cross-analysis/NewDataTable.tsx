"use client";

import { ChevronLeft, ChevronRight, Filter, X } from "lucide-react";
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useT } from "@/i18n/useT";

interface DataRow {
  id: number;
  report: string;
  metric: string;
  detail: string;
  year: number;
  value: string;
  unit: string;
  fileId?: string;
  page?: number | null;
  isNotDisclosed?: boolean;
}

interface NewDataTableProps {
  data: DataRow[];
  onViewEvidence?: (row: DataRow) => void;
}

function norm(v: any) {
  return String(v ?? "").trim();
}

function uniqSorted(arr: string[]) {
  return Array.from(new Set(arr.filter(Boolean))).sort((a, b) => a.localeCompare(b));
}

type DropdownPosition = { top: number; left: number; width: number };

function useDropdownPosition(open: boolean, anchorRef: React.RefObject<HTMLElement>, width = 248) {
  const [pos, setPos] = useState<DropdownPosition>({ top: 0, left: 0, width });

  useLayoutEffect(() => {
    if (!open || !anchorRef.current || typeof window === "undefined") return;

    const update = () => {
      const rect = anchorRef.current!.getBoundingClientRect();
      const desiredWidth = width;
      const maxLeft = Math.max(12, window.innerWidth - desiredWidth - 12);
      const left = Math.min(Math.max(12, rect.left), maxLeft);
      const top = rect.bottom + 8;
      setPos({ top, left, width: desiredWidth });
    };

    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open, anchorRef, width]);

  return pos;
}

type MultiFilterProps = {
  ariaLabel: string;
  options: string[];
  selected: string[];
  onChange: (next: string[]) => void;
};

function MultiSelectFilter({ ariaLabel, options, selected, onChange }: MultiFilterProps) {
  const { t } = useT();
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const pos = useDropdownPosition(open, anchorRef as React.RefObject<HTMLElement>, 252);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (!open) return;
      const target = e.target as Node;
      if (anchorRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const toggle = (opt: string) => {
    if (selected.includes(opt)) {
      onChange(selected.filter((x) => x !== opt));
    } else {
      onChange([...selected, opt]);
    }
  };

  return (
    <div ref={anchorRef} className="inline-flex">
      <button
        type="button"
        aria-label={ariaLabel}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className={`p-1 rounded hover:bg-[var(--brand-primary-soft)] transition-colors ${selected.length ? "text-[var(--brand-accent)]" : "text-[#CBD5E1]"}`}
      >
        <Filter className="w-3.5 h-3.5" />
      </button>

      {open && typeof document !== "undefined"
        ? createPortal(
            <div
              ref={panelRef}
              className="fixed bg-white border border-slate-200 rounded-xl shadow-lg p-3 z-[1200]"
              style={{ top: pos.top, left: pos.left, width: pos.width, maxHeight: "72vh" }}
            >
              <div className="space-y-1.5 overflow-y-auto overflow-x-visible pr-1" style={{ maxHeight: "56vh" }}>
                {options.length === 0 ? (
                  <div className="text-sm text-slate-500 py-2 text-center">{t("common.noOptions")}</div>
                ) : (
                  options.map((opt) => (
                    <label key={opt} className="flex items-start gap-2 py-1.5 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={selected.includes(opt)}
                        onChange={() => toggle(opt)}
                        className="mt-0.5 shrink-0 accent-[var(--brand-primary)]"
                      />
                      <span className="text-sm text-slate-700 break-words text-left leading-5">{opt}</span>
                    </label>
                  ))
                )}
              </div>

              <div className="flex items-center justify-between mt-3 pt-2 border-t border-slate-200">
                <button type="button" onClick={() => onChange([])} className="text-sm text-slate-600 hover:text-slate-900">
                  {t("common.clear")}
                </button>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="px-3 py-1.5 rounded-lg bg-[var(--brand-primary)] text-white text-sm font-medium hover:bg-[var(--brand-primary-hover)]"
                >
                  {t("common.apply")}
                </button>
              </div>
            </div>,
            document.body
          )
        : null}
    </div>
  );
}

type TextFilterProps = {
  ariaLabel: string;
  value: string;
  onChange: (next: string) => void;
};

function TextFilter({ ariaLabel, value, onChange }: TextFilterProps) {
  const { t } = useT();
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const pos = useDropdownPosition(open, anchorRef as React.RefObject<HTMLElement>, 252);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (!open) return;
      const target = e.target as Node;
      if (anchorRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div ref={anchorRef} className="inline-flex">
      <button
        type="button"
        aria-label={ariaLabel}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className={`p-1 rounded hover:bg-[var(--brand-primary-soft)] transition-colors ${value.trim() ? "text-[var(--brand-accent)]" : "text-[#CBD5E1]"}`}
      >
        <Filter className="w-3.5 h-3.5" />
      </button>

      {open && typeof document !== "undefined"
        ? createPortal(
            <div
              ref={panelRef}
              className="fixed bg-white border border-slate-200 rounded-xl shadow-lg p-3 z-[1200]"
              style={{ top: pos.top, left: pos.left, width: pos.width }}
            >
              <div className="text-xs font-semibold text-slate-600 mb-2 text-left">{t("common.contains")}</div>
              <div className="flex items-center gap-1.5">
                <input
                  value={value}
                  onChange={(e) => onChange(e.target.value)}
                  placeholder={t("common.typeToFilter")}
                  className="w-full px-3 py-2 text-sm border border-slate-200 rounded-lg outline-none focus:border-slate-400"
                />
                <button
                  type="button"
                  aria-label={t("common.clear")}
                  onClick={() => onChange("")}
                  className="p-2 rounded-lg hover:bg-[var(--brand-primary-soft)] text-slate-500"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              <div className="flex items-center justify-end mt-3 pt-2 border-t border-slate-200">
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="px-3 py-1.5 rounded-lg bg-[var(--brand-primary)] text-white text-sm font-medium hover:bg-[var(--brand-primary-hover)]"
                >
                  {t("common.apply")}
                </button>
              </div>
            </div>,
            document.body
          )
        : null}
    </div>
  );
}

const DETAIL_CLAMP_LEN = 140;

function DetailCell({ text }: { text: string }) {
  const { t } = useT();
  const [expanded, setExpanded] = useState(false);
  const trimmed = norm(text);
  const needsClamp = trimmed.length > DETAIL_CLAMP_LEN;

  if (!trimmed) {
    return <span className="text-[var(--brand-subtle)]">—</span>;
  }

  return (
    <div className="text-left">
      <p className={`text-sm leading-relaxed text-[var(--brand-muted)] ${!expanded && needsClamp ? "line-clamp-2" : ""}`}>
        {trimmed}
      </p>
      {needsClamp ? (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 text-xs font-medium text-[var(--brand-primary)] hover:text-[var(--brand-primary-hover)]"
        >
          {expanded ? t("common.collapse") : t("common.showMore")}
        </button>
      ) : null}
    </div>
  );
}

export function NewDataTable({ data, onViewEvidence }: NewDataTableProps) {
  const { t } = useT();
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 12;

  const reportOptions = useMemo(() => uniqSorted(data.map((d) => norm(d.report))), [data]);
  const metricOptions = useMemo(() => uniqSorted(data.map((d) => norm(d.metric))), [data]);
  const yearOptions = useMemo(
    () => Array.from(new Set(data.map((d) => String(d.year)).filter(Boolean))).sort((a, b) => Number(a) - Number(b)),
    [data]
  );
  const unitOptions = useMemo(() => uniqSorted(data.map((d) => norm(d.unit))), [data]);

  const [reportFilter, setReportFilter] = useState<string[]>([]);
  const [metricFilter, setMetricFilter] = useState<string[]>([]);
  const [yearFilter, setYearFilter] = useState<string[]>([]);
  const [unitFilter, setUnitFilter] = useState<string[]>([]);
  const [detailQuery, setDetailQuery] = useState<string>("");
  const [valueQuery, setValueQuery] = useState<string>("");

  useEffect(() => setCurrentPage(1), [data.length]);
  useEffect(() => setCurrentPage(1), [reportFilter, metricFilter, yearFilter, unitFilter, detailQuery, valueQuery]);

  const filteredData = useMemo(() => {
    const dq = detailQuery.trim().toLowerCase();
    const vq = valueQuery.trim().toLowerCase();

    return data.filter((row) => {
      if (reportFilter.length && !reportFilter.includes(norm(row.report))) return false;
      if (metricFilter.length && !metricFilter.includes(norm(row.metric))) return false;
      if (yearFilter.length && !yearFilter.includes(String(row.year))) return false;
      if (unitFilter.length && !unitFilter.includes(norm(row.unit))) return false;

      if (dq) {
        const hay = `${norm(row.detail)} ${norm(row.metric)}`.toLowerCase();
        if (!hay.includes(dq)) return false;
      }
      if (vq) {
        const hay = `${norm(row.value)} ${norm(row.unit)}`.toLowerCase();
        if (!hay.includes(vq)) return false;
      }
      return true;
    });
  }, [data, reportFilter, metricFilter, yearFilter, unitFilter, detailQuery, valueQuery]);

  const totalPages = Math.ceil(filteredData.length / itemsPerPage) || 1;
  const startIndex = (currentPage - 1) * itemsPerPage;
  const endIndex = startIndex + itemsPerPage;
  const currentData = filteredData.slice(startIndex, endIndex);

  const goToNextPage = () => {
    if (currentPage < totalPages) setCurrentPage((p) => p + 1);
  };

  const goToPreviousPage = () => {
    if (currentPage > 1) setCurrentPage((p) => p - 1);
  };

  return (
    <div className="app-card w-full overflow-hidden">
      <div className="border-b border-black/6 px-5 py-4">
        <h2 className="text-lg font-semibold text-[var(--brand-text)]">{t("crossAnalysis.results")}</h2>
      </div>

      <div className="cross-analysis-table w-full overflow-x-auto px-5 py-4">
        <table className="w-full table-fixed">
          <thead>
            <tr className="border-b border-black/6">
              <th className="w-[12%] px-2 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-[var(--brand-subtle)]">
                <div className="flex items-center gap-1.5">
                  <span>{t("crossAnalysis.table.report")}</span>
                  <MultiSelectFilter
                    ariaLabel={t("crossAnalysis.table.filterReport")}
                    options={reportOptions}
                    selected={reportFilter}
                    onChange={setReportFilter}
                  />
                </div>
              </th>

              <th className="w-[16%] px-2 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-[var(--brand-subtle)]">
                <div className="flex items-center gap-1.5">
                  <span>{t("crossAnalysis.table.metric")}</span>
                  <MultiSelectFilter
                    ariaLabel={t("crossAnalysis.table.filterMetric")}
                    options={metricOptions}
                    selected={metricFilter}
                    onChange={setMetricFilter}
                  />
                </div>
              </th>

              <th className="w-[5%] px-2 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-[var(--brand-subtle)]">
                <div className="flex items-center gap-1.5">
                  <span>{t("crossAnalysis.table.year")}</span>
                  <MultiSelectFilter
                    ariaLabel={t("crossAnalysis.table.filterYear")}
                    options={yearOptions}
                    selected={yearFilter}
                    onChange={setYearFilter}
                  />
                </div>
              </th>

              <th className="w-[10%] px-2 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-[var(--brand-subtle)]">
                <div className="flex items-center gap-1.5">
                  <span>{t("crossAnalysis.table.value")}</span>
                  <TextFilter ariaLabel={t("crossAnalysis.table.filterValue")} value={valueQuery} onChange={setValueQuery} />
                </div>
              </th>

              <th className="w-[9%] px-2 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-[var(--brand-subtle)]">
                <div className="flex items-center gap-1.5">
                  <span>{t("crossAnalysis.table.unit")}</span>
                  <MultiSelectFilter
                    ariaLabel={t("crossAnalysis.table.filterUnit")}
                    options={unitOptions}
                    selected={unitFilter}
                    onChange={setUnitFilter}
                  />
                </div>
              </th>

              <th className="w-[42%] px-2 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-[var(--brand-subtle)]">
                <div className="flex items-center gap-1.5">
                  <span>{t("crossAnalysis.table.detail")}</span>
                  <TextFilter ariaLabel={t("crossAnalysis.table.filterDetail")} value={detailQuery} onChange={setDetailQuery} />
                </div>
              </th>

              <th className="w-[6%] px-2 py-2.5 text-center text-xs font-semibold uppercase tracking-wide text-[var(--brand-subtle)]">
                {t("crossAnalysis.table.evidence")}
              </th>
            </tr>
          </thead>

          <tbody>
            {currentData.length === 0 ? (
              <tr>
                <td colSpan={7} className="py-10 text-center text-[var(--brand-subtle)]">
                  {t("common.noDataAvailable")}
                </td>
              </tr>
            ) : (
              currentData.map((row) => (
                <tr
                  key={row.id}
                  className="border-b border-black/4 transition-colors hover:bg-[var(--brand-primary-soft)]/50"
                >
                  <td className="px-2 py-3 align-top text-sm font-medium text-[var(--brand-text)]">{row.report}</td>
                  <td className="px-2 py-3 align-top text-sm text-[var(--brand-text)]">{row.metric}</td>
                  <td className="px-2 py-3 align-top text-sm tabular-nums text-[var(--brand-muted)]">{row.year}</td>
                  <td className="px-2 py-3 align-top text-sm">
                    {(row as DataRow).isNotDisclosed ? (
                      <span
                        className="inline-flex items-center rounded-full bg-red-50 px-2 py-0.5 text-xs font-medium text-red-600"
                        title={t("crossAnalysis.notDisclosed")}
                      >
                        N/D
                      </span>
                    ) : (
                      <span className="font-semibold tabular-nums text-[var(--brand-text)]">{row.value}</span>
                    )}
                  </td>
                  <td className="px-2 py-3 align-top text-sm text-[var(--brand-subtle)]">{row.unit || "—"}</td>
                  <td className="px-2 py-3 align-top">
                    <DetailCell text={row.detail} />
                  </td>
                  <td className="px-2 py-3 text-center align-top">
                    <button
                      type="button"
                      className="text-sm font-medium text-[var(--brand-primary)] hover:text-[var(--brand-primary-hover)]"
                      onClick={() => onViewEvidence?.(row)}
                    >
                      {t("common.view")}
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between border-t border-black/6 px-5 py-3">
        <p className="text-sm text-[var(--brand-subtle)]">
          {filteredData.length === 0 ? (
            <>{t("common.showingZeroEntries")}</>
          ) : (
            <>{t("common.showingRange", { from: startIndex + 1, to: Math.min(endIndex, filteredData.length), total: filteredData.length })}</>
          )}
        </p>

        <div className="flex items-center justify-center gap-1.5">
          <button
            onClick={goToPreviousPage}
            disabled={currentPage === 1}
            className={`p-2 rounded-lg border border-[#E2E8F0] ${currentPage === 1 ? "text-[#CBD5E1] cursor-not-allowed" : "text-[var(--brand-subtle)] hover:bg-[var(--brand-primary-soft)]"}`}
          >
            <ChevronLeft className="w-4 h-4" />
          </button>

          {Array.from({ length: totalPages }, (_, i) => i + 1).map((page) => (
            <button
              key={page}
              onClick={() => setCurrentPage(page)}
              className={`px-2.5 py-1.5 rounded-lg text-sm font-medium ${currentPage === page ? "bg-[var(--brand-primary)] text-white" : "text-[var(--brand-subtle)] hover:bg-[var(--brand-primary-soft)]"}`}
            >
              {page}
            </button>
          ))}

          <button
            onClick={goToNextPage}
            disabled={currentPage === totalPages}
            className={`p-2 rounded-lg border border-[#E2E8F0] ${currentPage === totalPages ? "text-[#CBD5E1] cursor-not-allowed" : "text-[var(--brand-subtle)] hover:bg-[var(--brand-primary-soft)]"}`}
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
