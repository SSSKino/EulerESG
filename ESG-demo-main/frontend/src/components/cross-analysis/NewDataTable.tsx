"use client";

import { ChevronLeft, ChevronRight, Filter, X } from "lucide-react";
import React, { useEffect, useMemo, useRef, useState } from "react";

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

type MultiFilterProps = {
  ariaLabel: string;
  options: string[];
  selected: string[];
  onChange: (next: string[]) => void;
};

function MultiSelectFilter({ ariaLabel, options, selected, onChange }: MultiFilterProps) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (!open) return;
      const t = e.target as Node;
      if (ref.current && !ref.current.contains(t)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const shown = useMemo(() => {
    const qq = q.trim().toLowerCase();
    if (!qq) return options;
    return options.filter((o) => o.toLowerCase().includes(qq));
  }, [options, q]);

  const toggle = (opt: string) => {
    if (selected.includes(opt)) {
      onChange(selected.filter((x) => x !== opt));
    } else {
      onChange([...selected, opt]);
    }
  };

  return (
    <div ref={ref} className="relative inline-flex">
      <button
        type="button"
        aria-label={ariaLabel}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className={`p-1 rounded hover:bg-slate-100 transition-colors ${selected.length ? "text-[#3B82F6]" : "text-[#CBD5E1]"}`}
      >
        <Filter className="w-3 h-3" />
      </button>

      {open ? (
        <div className="absolute right-0 top-full mt-2 w-64 bg-white border border-slate-200 rounded-xl shadow-lg p-3 z-50">
          <div className="flex items-center gap-2">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search…"
              className="w-full px-3 py-2 text-sm border border-slate-200 rounded-lg outline-none focus:border-slate-400"
            />
            <button
              type="button"
              aria-label="Clear"
              onClick={() => setQ("")}
              className="p-2 rounded-lg hover:bg-slate-100 text-slate-500"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          <div className="mt-2 max-h-56 overflow-auto pr-1">
            {shown.length === 0 ? (
              <div className="text-sm text-slate-500 py-3 text-center">No options</div>
            ) : (
              shown.map((opt) => (
                <label key={opt} className="flex items-center gap-2 py-1.5 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={selected.includes(opt)}
                    onChange={() => toggle(opt)}
                    className="accent-blue-500"
                  />
                  <span className="text-sm text-slate-700 break-words">{opt}</span>
                </label>
              ))
            )}
          </div>

          <div className="flex items-center justify-between mt-3 pt-2 border-t border-slate-200">
            <button
              type="button"
              onClick={() => onChange([])}
              className="text-sm text-slate-600 hover:text-slate-900"
            >
              Clear
            </button>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="px-3 py-1.5 rounded-lg bg-[#3B82F6] text-white text-sm font-medium hover:bg-[#2563EB]"
            >
              Apply
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

type TextFilterProps = {
  ariaLabel: string;
  value: string;
  onChange: (next: string) => void;
};

function TextFilter({ ariaLabel, value, onChange }: TextFilterProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (!open) return;
      const t = e.target as Node;
      if (ref.current && !ref.current.contains(t)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div ref={ref} className="relative inline-flex">
      <button
        type="button"
        aria-label={ariaLabel}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className={`p-1 rounded hover:bg-slate-100 transition-colors ${value.trim() ? "text-[#3B82F6]" : "text-[#CBD5E1]"}`}
      >
        <Filter className="w-3 h-3" />
      </button>

      {open ? (
        <div className="absolute right-0 top-full mt-2 w-64 bg-white border border-slate-200 rounded-xl shadow-lg p-3 z-50">
          <div className="text-xs font-semibold text-slate-600 mb-2">Contains</div>
          <div className="flex items-center gap-2">
            <input
              value={value}
              onChange={(e) => onChange(e.target.value)}
              placeholder="Type to filter…"
              className="w-full px-3 py-2 text-sm border border-slate-200 rounded-lg outline-none focus:border-slate-400"
            />
            <button
              type="button"
              aria-label="Clear"
              onClick={() => onChange("")}
              className="p-2 rounded-lg hover:bg-slate-100 text-slate-500"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          <div className="flex items-center justify-end mt-3 pt-2 border-t border-slate-200">
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="px-3 py-1.5 rounded-lg bg-[#3B82F6] text-white text-sm font-medium hover:bg-[#2563EB]"
            >
              Apply
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function NewDataTable({ data, onViewEvidence }: NewDataTableProps) {
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

  // Reset to first page on dataset change
  useEffect(() => setCurrentPage(1), [data.length]);

  // Reset to first page on filter change
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
    <div className="bg-white rounded-2xl shadow-sm p-6 w-full">
      <div className="w-full">
        <table className="w-full table-fixed">
          <thead>
            <tr className="border-b border-[#E2E8F0]">
              <th className="text-left py-3 px-3 text-xs font-semibold text-[#64748B] uppercase tracking-wide w-[18%] whitespace-nowrap">
                <div className="flex items-center gap-2">
                  Report
                  <MultiSelectFilter
                    ariaLabel="Filter report"
                    options={reportOptions}
                    selected={reportFilter}
                    onChange={setReportFilter}
                  />
                </div>
              </th>

              <th className="text-left py-3 px-3 text-xs font-semibold text-[#64748B] uppercase tracking-wide w-[20%] whitespace-nowrap">
                <div className="flex items-center gap-2">
                  Metric
                  <MultiSelectFilter
                    ariaLabel="Filter metric"
                    options={metricOptions}
                    selected={metricFilter}
                    onChange={setMetricFilter}
                  />
                </div>
              </th>

              <th className="text-left py-3 px-3 text-xs font-semibold text-[#64748B] uppercase tracking-wide w-[26%] whitespace-nowrap">
                <div className="flex items-center gap-2">
                  Detail
                  <TextFilter ariaLabel="Filter detail" value={detailQuery} onChange={setDetailQuery} />
                </div>
              </th>

              <th className="text-left py-3 px-3 text-xs font-semibold text-[#64748B] uppercase tracking-wide w-[8%] whitespace-nowrap">
                <div className="flex items-center gap-2">
                  Year
                  <MultiSelectFilter
                    ariaLabel="Filter year"
                    options={yearOptions}
                    selected={yearFilter}
                    onChange={setYearFilter}
                  />
                </div>
              </th>

              <th className="text-right py-3 px-3 text-xs font-semibold text-[#64748B] uppercase tracking-wide w-[12%] whitespace-nowrap">
                <div className="flex items-center justify-end gap-2">
                  Value
                  <TextFilter ariaLabel="Filter value" value={valueQuery} onChange={setValueQuery} />
                </div>
              </th>

              <th className="text-left py-3 px-3 text-xs font-semibold text-[#64748B] uppercase tracking-wide w-[10%] whitespace-nowrap">
                <div className="flex items-center gap-2">
                  Unit
                  <MultiSelectFilter
                    ariaLabel="Filter unit"
                    options={unitOptions}
                    selected={unitFilter}
                    onChange={setUnitFilter}
                  />
                </div>
              </th>

              <th className="text-left py-3 px-3 text-xs font-semibold text-[#64748B] uppercase tracking-wide w-[6%] whitespace-nowrap">
                Evidence
              </th>
            </tr>
          </thead>

          <tbody>
            {currentData.length === 0 ? (
              <tr>
                <td colSpan={7} className="py-8 text-center text-[#64748B]">
                  No data available
                </td>
              </tr>
            ) : (
              currentData.map((row, index) => (
                <tr
                  key={row.id}
                  className={`border-b border-[#E2E8F0] ${index % 2 === 0 ? "bg-white" : "bg-[#F8FAFC]"} hover:bg-[#F1F5F9] transition-colors`}
                >
                  <td className="py-3 px-3 text-sm text-[#0F172A] break-words whitespace-normal">{row.report}</td>
                  <td className="py-3 px-3 text-sm text-[#0F172A] break-words whitespace-normal">{row.metric}</td>
                  <td className="py-3 px-3 text-sm text-[#64748B] break-words whitespace-normal">{row.detail}</td>
                  <td className="py-3 px-3 text-sm text-[#0F172A]">{row.year}</td>
                  <td className="py-3 px-3 text-sm font-bold text-[#0F172A] text-right break-words whitespace-normal">{row.value}</td>
                  <td className="py-3 px-3 text-sm text-[#64748B] break-words whitespace-normal">{row.unit}</td>
                  <td className="py-3 px-3">
                    <button
                      className="text-sm text-[#3B82F6] hover:text-[#2563EB] font-medium"
                      onClick={() => onViewEvidence?.(row)}
                    >
                      View
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between mt-6 pt-4 border-t border-[#E2E8F0]">
        <p className="text-sm text-[#64748B]">
          {filteredData.length === 0 ? (
            <>Showing 0 to 0 of 0 entries</>
          ) : (
            <>Showing {startIndex + 1} to {Math.min(endIndex, filteredData.length)} of {filteredData.length} entries</>
          )}
        </p>

        <div className="flex items-center gap-2">
          <button
            onClick={goToPreviousPage}
            disabled={currentPage === 1}
            className={`p-2 rounded-lg border border-[#E2E8F0] ${currentPage === 1 ? "text-[#CBD5E1] cursor-not-allowed" : "text-[#64748B] hover:bg-[#F8FAFC]"}`}
          >
            <ChevronLeft className="w-4 h-4" />
          </button>

          {Array.from({ length: totalPages }, (_, i) => i + 1).map((page) => (
            <button
              key={page}
              onClick={() => setCurrentPage(page)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium ${currentPage === page ? "bg-[#3B82F6] text-white" : "text-[#64748B] hover:bg-[#F8FAFC]"}`}
            >
              {page}
            </button>
          ))}

          <button
            onClick={goToNextPage}
            disabled={currentPage === totalPages}
            className={`p-2 rounded-lg border border-[#E2E8F0] ${currentPage === totalPages ? "text-[#CBD5E1] cursor-not-allowed" : "text-[#64748B] hover:bg-[#F8FAFC]"}`}
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
