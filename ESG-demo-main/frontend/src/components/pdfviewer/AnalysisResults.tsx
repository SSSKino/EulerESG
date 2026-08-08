import React, { useMemo, useEffect, useState } from "react";
import { Table, Tag, Popover, Spin, Alert } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useFileStore } from "@/store/useFileStore";
import { apiService } from "@/lib/api";
import { useT } from "@/i18n/useT";

type AnalysisDataItem = {
  metric_id: string;
  metric_name: string;
  disclosure_status: "fully_disclosed" | "partially_disclosed" | "not_disclosed";
  reasoning: string;
  unit?: string;
  category?: string;
  topic?: string;
  type?: string;
  value?: string | number | null;
  page?: string | number | null;
  context?: string | null;
  definition?: string | null;
  visualEvidence?: {
    asset_id: string;
    evidence_type?: string;
    caption?: string;
    confidence?: number;
    chart_data?: Record<string, unknown> | null;
    bbox?: number[] | null;
  } | null;
  tableEvidence?: {
    review_status?: string;
    structure_confidence?: number;
    ocr_confidence?: number;
    header_path?: string[];
    rowspan?: number;
    colspan?: number;
    parse_pass?: number;
    conflicts?: Array<Record<string, unknown>>;
  } | null;
};

interface AnalysisResultsProps {
  fileId?: string;
  scopeKey?: string;
  onPageNavigate?: (page: number) => void;
  /**
   * Controls whether the detailed results table is rendered.
   * The summary section is always shown.
   */
  showTable?: boolean;
}

// -------------------------
// Rendering helpers
// -------------------------
const formatNumber = (v: number) => {
  try {
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 }).format(v);
  } catch {
    return String(v);
  }
};

const VisualEvidencePreview: React.FC<{ fileId: string; assetId: string; alt?: string }> = ({
  fileId,
  assetId,
  alt,
}) => {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    apiService.getVisualAssetObjectUrl(fileId, assetId).then((objectUrl) => {
      if (!active) return;
      setUrl(objectUrl);
    }).catch(() => setUrl(null));
    return () => {
      active = false;
    };
  }, [fileId, assetId]);
  return url ? <img src={url} alt={alt || "Visual evidence"} className="mt-2 max-h-64 max-w-full rounded border object-contain" /> : null;
};

const normalizePage = (page: string | number | null | undefined): number | null => {
  if (page === null || page === undefined) return null;
  if (typeof page === "number") return Number.isFinite(page) ? page : null;
  const s = String(page).trim();
  if (!s) return null;
  // Accept formats: "12", "12, 13", "12-13", "p. 12"
  const firstToken = s.split(",")[0].trim();
  const rangeFirst = firstToken.split("-")[0].trim();
  const m = rangeFirst.match(/\d+/);
  if (!m) return null;
  const n = parseInt(m[0], 10);
  return Number.isFinite(n) ? n : null;
};

const EMPTY_VALUE_TOKENS = new Set(["", "-", "—", "n/a", "na", "null", "none", "not specified", "not available"]);

const isEmptyValue = (value: unknown) => {
  if (value === null || value === undefined) return true;
  if (typeof value === "number") return !Number.isFinite(value);
  const s = String(value).trim().toLowerCase();
  return EMPTY_VALUE_TOKENS.has(s);
};

const normalizeCategoryLabel = (raw: unknown): string => {
  const s = String(raw ?? "").trim();
  if (!s) return "";
  const lower = s.toLowerCase();
  if (lower === "quantitative") return "Quantitative";
  if (lower === "qualitative") return "Discussion and Analysis";
  if (lower === "discussion and analysis" || lower === "discussion") return "Discussion and Analysis";
  return s;
};

const formatDisplayValue = (value: unknown): string | null => {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") {
    return Number.isFinite(value) ? formatNumber(value) : null;
  }
  const s = String(value).trim();
  return isEmptyValue(s) ? null : s;
};

// Extract readable context text from various backend schemas.
// The backend may return:
// - a plain string
// - an object (evidence blob)
// - an array of evidence segments
const extractContextText = (raw: any): string => {
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
};

const analysisDataCache = new Map<string, AnalysisDataItem[]>();

const pickValue = (...vals: any[]) => {
  for (const v of vals) {
    if (v === null || v === undefined) continue;
    if (typeof v === "string" && v.trim() === "") continue;
    return v;
  }
  return null;
};

const normalizeStatus = (raw: any): AnalysisDataItem["disclosure_status"] => {
  const s = String(raw ?? "").trim().toLowerCase();
  if (!s) return "not_disclosed";
  if (s.includes("fully")) return "fully_disclosed";
  if (s.includes("partial")) return "partially_disclosed";
  if (s.includes("not")) return "not_disclosed";
  if (s === "fully_disclosed") return "fully_disclosed";
  if (s === "partially_disclosed") return "partially_disclosed";
  if (s === "not_disclosed") return "not_disclosed";
  return "not_disclosed";
};

const convertAssessmentData = (assessment: any): AnalysisDataItem[] =>
  (assessment?.metric_analyses || [])
    .filter((item: any) => {
      const id =
        item?.metric_id ?? item?.metric_code ?? item?.metricId ?? item?.Code ?? item?.code;
      const name = item?.metric_name ?? item?.Metric ?? item?.metric;
      if (!id || !name) {
        console.warn("Skipping metric with missing required fields:", item);
        return false;
      }
      return true;
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

      const value = pickValue(
        item?.value,
        item?.Value,
        item?.data,
        item?.Data
      );

      const page = pickValue(
        item?.page,
        item?.Page,
        item?.page_number,
        item?.pageNumber,
        item?.evidence?.page,
        item?.evidence_segments?.[0]?.page_number,
        item?.evidence_segments?.[0]?.page
      );

      const contextRaw = pickValue(
        item?.context,
        item?.Context,
        item?.specific_data_found,
        item?.specificDataFound,
        item?.evidence,
        item?.evidence_text,
        item?.evidenceText
      );
      const visualEvidence = (item?.evidence_sources || []).find(
        (source: any) => source && typeof source === "object" && source.asset_id
      ) || null;
      const tableEvidence = (item?.evidence_sources || []).find(
        (source: any) => source && typeof source === "object" && source.review_status
      ) || null;

      return {
        metric_id,
        metric_name,
        disclosure_status,
        reasoning: String(
          pickValue(item?.reasoning, item?.["LLM Analysis"], item?.Reasoning, item?.analysis, item?.Analysis) ?? ""
        ),
        unit: pickValue(item?.unit, item?.Unit) ?? "",
        category: normalizeCategoryLabel(pickValue(item?.category, item?.Category) ?? ""),
        topic: pickValue(item?.topic, item?.Topic) ?? "",
        type: pickValue(item?.type, item?.Type) ?? "",
        value: value ?? null,
        page: page ?? null,
        context: extractContextText(contextRaw) || null,
        definition:
          typeof (item?.definition ?? item?.Definition) === "string"
            ? String(item?.definition ?? item?.Definition).trim() || null
            : null,
        visualEvidence,
        tableEvidence,
      };
    });

const AnalysisResults: React.FC<AnalysisResultsProps> = ({
  fileId,
  scopeKey,
  onPageNavigate,
  showTable = true,
}) => {
  const { t } = useT();

  const files = useFileStore((state) => state.files);
  const currentFile =
    files.find(
      (file) =>
        file.file_id === fileId &&
        ((scopeKey && file.analysis_scope_key === scopeKey) || (!scopeKey && !file.analysis_scope_key))
    ) || files.find((file) => file.file_id === fileId);
  const industry = currentFile?.industry;
  const semiIndustry = currentFile?.semiIndustry;

  const cacheKey = `${fileId || ""}::${scopeKey || ""}`;
  const [analysisData, setAnalysisData] = useState<AnalysisDataItem[]>(() =>
    analysisDataCache.get(cacheKey) || []
  );
  const [loading, setLoading] = useState(() => !!fileId && !analysisDataCache.has(cacheKey));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const cached = analysisDataCache.get(cacheKey);
    if (cached) {
      setAnalysisData(cached);
      setLoading(false);
      setError(null);
    }

    const fetchAnalysisData = async () => {
      if (!currentFile?.file_id) {
        setAnalysisData([]);
        setLoading(false);
        setError(t("analysis.noFileSelected"));
        return;
      }

      if (!cached) {
        setLoading(true);
      }
      setError(null);
      try {
        const assessment = await apiService.getAssessmentByFile(currentFile.file_id, scopeKey, false);
        const convertedData = convertAssessmentData(assessment);
        analysisDataCache.set(cacheKey, convertedData);
        setAnalysisData(convertedData);

        if (assessment?.status === "not_analyzed") {
          setError(t("analysis.noAnalysisAvailable"));
        }
      } catch (err) {
        console.error("Failed to fetch assessment data:", err);
        const isNotAnalyzed =
          err &&
          typeof err === "object" &&
          "message" in err &&
          typeof (err as any).message === "string" &&
          (((err as any).message as string).includes("404") ||
            ((err as any).message as string).toLowerCase().includes("no analysis"));

        if (isNotAnalyzed) {
          setError(t("analysis.noAnalysisAvailable"));
        } else {
          setError(t("analysis.failedToLoad"));
        }
        setAnalysisData([]);
      } finally {
        setLoading(false);
      }
    };

    void fetchAnalysisData();
  }, [cacheKey, currentFile?.file_id, scopeKey, t]);
  const getCategoryColor = (category: string) => {
    switch (category) {
      case "Quantitative":
        return "blue";
      case "Discussion and Analysis":
        return "purple";
      default:
        return "default";
    }
  };

  const data = analysisData.map((item, index) => ({
    ...item,
    key: `${item.metric_id}-${index}`,
  }));

  const columns: ColumnsType<AnalysisDataItem> = useMemo(
  () => {
    const categoryOptions = Array.from(
      new Set(
        (data || [])
          .map((d) => (d.category || "").trim())
          .filter((c) => c.length > 0)
      )
    )
      .sort((a, b) => a.localeCompare(b))
      .map((c) => ({ text: c, value: c }));

    const unitOptions = Array.from(
      new Set(
        (data || [])
          .map((d) => (d.unit || "").trim())
          .filter((u) => u.length > 0)
      )
    )
      .sort((a, b) => a.localeCompare(b))
      .map((u) => ({ text: u, value: u }));

    const typeOptions = Array.from(
      new Set(
        (data || [])
          .map((d) => (d.type || "").trim())
          .filter((t) => t.length > 0)
      )
    )
      .sort((a, b) => a.localeCompare(b))
      .map((t) => ({ text: t, value: t }));

    return [
      {
        title: t("analysis.columns.metric"),
        dataIndex: "metric_name",
        key: "metric_name",
        width: 200,
        render: (_value: string, record: AnalysisDataItem) => {
          const definitionText = (record.definition || "").trim();
          const definitionContent = definitionText ? (
            <div className="max-w-md p-2 text-sm whitespace-pre-wrap">{definitionText}</div>
          ) : null;

          return (
            <div className="flex items-center gap-2 w-full">
              <div className="min-w-0 flex-1 whitespace-normal break-words">{record.metric_name}</div>
              {definitionText && (
                <div className="shrink-0 self-center">
                  <Popover content={definitionContent} title={null} trigger="hover" mouseEnterDelay={0.2}>
                    <span
                      className="inline-flex items-center justify-center w-4 h-4 rounded-full border border-gray-300 text-gray-700 text-[11px] font-semibold leading-none cursor-pointer select-none"
                      aria-label={t("analysis.columns.metric")}
                      onClick={(e) => e.stopPropagation()}
                    >
                      !
                    </span>
                  </Popover>
                </div>
              )}
            </div>
          );
        },
      },
      {
        title: t("analysis.columns.status"),
        dataIndex: "disclosure_status",
        key: "disclosure_status",
        width: 120,
        render: (status: string) => {
          let color = "default";
          let text = status;
          if (status === "fully_disclosed") {
            color = "success";
            text = t("analysis.status.fully");
          } else if (status === "partially_disclosed") {
            color = "warning";
            text = t("analysis.status.partial");
          } else if (status === "not_disclosed") {
            color = "error";
            text = t("analysis.status.not");
          }
          return <Tag color={color}>{text}</Tag>;
        },
        filters: [
          { text: t("analysis.status.fully"), value: "fully_disclosed" },
          { text: t("analysis.status.partial"), value: "partially_disclosed" },
          { text: t("analysis.status.not"), value: "not_disclosed" },
        ],
        onFilter: (value, record) => record.disclosure_status === value,
      },
      {
        title: t("analysis.columns.category"),
        dataIndex: "category",
        key: "category",
        width: 110,
        render: (category?: string) =>
            category ? (
              <Tag color={getCategoryColor(category)}>{category === "Quantitative" ? t("analysis.tags.quantitative") : category === "Discussion and Analysis" ? t("analysis.tags.discussion") : category}</Tag>
            ) : (
              <span className="text-gray-400">-</span>
            ),
        filters: categoryOptions,
        onFilter: (value, record) => (record.category || "") === value,
      },
      {
        title: t("analysis.columns.unit"),
        dataIndex: "unit",
        key: "unit",
        width: 90,
        filters: unitOptions,
        onFilter: (value, record) => (record.unit || "") === value,
      },
      {
        title: t("analysis.columns.type"),
        dataIndex: "type",
        key: "type",
        width: 120,
        filters: typeOptions,
        onFilter: (value, record) => (record.type || "") === value,
      },
      {
        title: t("analysis.columns.value"),
        dataIndex: "value",
        key: "value",
        // Keep more room for value text and evidence indicators.
        width: 420,
        render: (_value: string | number | null, record: AnalysisDataItem) => {
          const status = record.disclosure_status;
          const category = normalizeCategoryLabel(record.category);
          const reasoningText = String(record.reasoning || "").trim();
          const contextText = record.context ? String(record.context).trim() : "";
          const hasContext = !!contextText;
          const formattedValue = formatDisplayValue(record.value);
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
            <div className="max-w-md p-2">
              <div className="text-xs font-semibold text-gray-700">{t("analysis.columns.context")}</div>
              {hasContext ? (
                <div className="mt-1 text-sm whitespace-pre-wrap">{contextText}</div>
              ) : (
                <div className="mt-1 text-xs text-gray-500">{t("analysis.noEvidenceExcerpt")}</div>
              )}
              {fileId && record.visualEvidence?.asset_id && (
                <>
                  <VisualEvidencePreview
                    fileId={fileId}
                    assetId={record.visualEvidence.asset_id}
                    alt={record.visualEvidence.caption || record.metric_name}
                  />
                  <div className="mt-1 text-xs text-gray-500">
                    {record.visualEvidence.evidence_type || "visual"}
                    {typeof record.visualEvidence.confidence === "number"
                      ? ` · ${Math.round(record.visualEvidence.confidence * 100)}%`
                      : ""}
                  </div>
                </>
              )}
              {record.tableEvidence && (
                <div className="mt-2 rounded border border-gray-200 bg-gray-50 p-2 text-xs">
                  <Tag color={record.tableEvidence.review_status === "needs_review" ? "warning" : "success"}>
                    {record.tableEvidence.review_status === "needs_review"
                      ? ("表格结构待复核")
                      : ("表格结构已校验")}
                  </Tag>
                  <div className="mt-1 text-gray-600">
                    {typeof record.tableEvidence.structure_confidence === "number"
                      ? `Structure ${Math.round(record.tableEvidence.structure_confidence * 100)}% `
                      : ""}
                    {typeof record.tableEvidence.ocr_confidence === "number"
                      ? `OCR ${Math.round(record.tableEvidence.ocr_confidence * 100)}%`
                      : ""}
                  </div>
                  {!!record.tableEvidence.conflicts?.length && (
                    <div className="mt-1 text-amber-700">
                      {record.tableEvidence.conflicts.length} conflict(s); value not treated as confirmed.
                    </div>
                  )}
                </div>
              )}
            </div>
          );

          const pageText = !isEmptyValue(record.page) ? String(record.page) : "";
          const pageNumber = normalizePage(record.page);
          const pageClickable = pageNumber !== null && !!onPageNavigate;
          const pageLabel = pageNumber !== null ? String(pageNumber) : pageText;

          return (
            <div className="flex items-center gap-2 w-full">
              <div className="min-w-0 whitespace-pre-wrap break-words">{displayValue}</div>

              <div className="flex items-center gap-2 shrink-0 min-w-[56px] pr-3">
                <Popover content={contextContent} title={null} trigger="hover" mouseEnterDelay={0.2}>
                  <span
                    className="inline-flex items-center justify-center w-4 h-4 rounded-full border border-gray-300 text-gray-700 text-[11px] font-semibold leading-none cursor-pointer select-none"
                    aria-label={t("analysis.columns.context")}
                    onClick={(e) => e.stopPropagation()}
                  >
                    !
                  </span>
                </Popover>

                {!isEmptyValue(record.page) && (
                  <span
                    className={
                      pageClickable
                        ? "text-blue-500 cursor-pointer hover:underline text-xs"
                        : "text-gray-500 text-xs"
                    }
                    onClick={(e) => {
                      e.stopPropagation();
                      if (pageClickable && pageNumber !== null) {
                        onPageNavigate?.(pageNumber);
                      }
                    }}
                    title={pageClickable ? t("analysis.jumpToPage") : undefined}
                  >
                    {pageLabel}
                  </span>
                )}
              </div>
            </div>
          );
        },
      },
    ];
  },
  [data, fileId, onPageNavigate, t]
);


  const getAnalysisSummary = (data: AnalysisDataItem[]) => {
    // Since backend doesn't distinguish between disclosure and activity metrics,
    // we'll only show disclosure metrics to avoid empty activity section
    const disclosureData = data;
    
    const getStats = (group: AnalysisDataItem[]) => {
      const red = group.filter(
        (item) => item.disclosure_status === "not_disclosed"
      ).length;
      const yellow = group.filter(
        (item) => item.disclosure_status === "partially_disclosed"
      ).length;
      const green = group.filter(
        (item) => item.disclosure_status === "fully_disclosed"
      ).length;
      return { red, yellow, green };
    };

    return {
      disclosure: getStats(disclosureData),
      // Remove activity section since backend doesn't provide this distinction
    };
  };

  const summary = getAnalysisSummary(data);
  // console.log("data.length", summary.disclosure);

  if (loading) {
    return (
      <div className="flex flex-col gap-6">
        <h1 className="text-2xl font-bold text-gray-800 !my-0">
          {currentFile?.name} ({currentFile?.framework})
        </h1>
        <h2 className="text-xl font-semibold text-gray-800 !my-0">
          {industry && semiIndustry
            ? `${industry} - ${semiIndustry}`
            : t("analysis.industryAnalysis")}
        </h2>
        <div className="bg-white rounded-lg shadow-sm p-6 text-center">
          <Spin size="large" />
          <p className="mt-4 text-gray-600">{t("analysis.loadingResults")}</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col gap-6">
        <h1 className="text-2xl font-bold text-gray-800 !my-0">
          {currentFile?.name} ({currentFile?.framework})
        </h1>
        <h2 className="text-xl font-semibold text-gray-800 !my-0">
          {industry && semiIndustry
            ? `${industry} - ${semiIndustry}`
            : t("analysis.industryAnalysis")}
        </h2>
        <Alert
          message={t("common.error")}
          description={error}
          type="error"
          showIcon
          action={
            <button
              onClick={() => window.location.reload()}
              className="px-4 py-2 bg-red-500 text-white rounded hover:bg-red-600">
              {t("common.retry")}
            </button>
          }
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold text-gray-800 !my-0">
        {currentFile?.name} ({currentFile?.framework})
      </h1>
      <h2 className="text-xl font-semibold text-gray-800 !my-0">
        {industry && semiIndustry
          ? `${industry} - ${semiIndustry}`
          : t("analysis.industryAnalysis")}
      </h2>
      <div className="bg-white rounded-lg shadow-sm p-6 hover:scale-[1.02] hover:shadow-lg transition-transform duration-300">
        <h3 className="text-xl font-semibold mb-6 text-gray-800">
          {t("analysis.summaryTitle")}
        </h3>
        <div className="flex flex-col gap-8">
          {summary.disclosure && (() => {
            const group = summary.disclosure;
            const total = group.red + group.yellow + group.green;
            
            // Only render if we have data
            if (total === 0) {
              return null;
            }
            
            const redPct = ((group.red / total) * 100).toFixed(1);
            const yellowPct = ((group.yellow / total) * 100).toFixed(1);
            const greenPct = ((group.green / total) * 100).toFixed(1);

            return (
              <div key="disclosure">
                <div className="flex flex-wrap gap-4">
                  {[
                    {
                      color: "text-red-500",
                      value: group.red,
                      percent: redPct,
                      label: t("analysis.summary.not"),
                    },
                    {
                      color: "text-yellow-500",
                      value: group.yellow,
                      percent: yellowPct,
                      label: t("analysis.summary.partial"),
                    },
                    {
                      color: "text-green-500",
                      value: group.green,
                      percent: greenPct,
                      label: t("analysis.summary.disclosed"),
                    },
                  ].map((item) => (
                    <div
                      className="flex-1 min-w-[200px] flex flex-col items-center gap-2"
                      key={item.label}>
                      <div className={`text-4xl font-bold ${item.color}`}>
                        {item.percent}%
                      </div>
                      <div className="text-lg text-gray-600">
                        ({item.value}/{total})
                      </div>
                      <div className="mt-1 text-md text-center font-semibold">
                        {item.label}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })()}
        </div>
      </div>
      {showTable && (
        <div className="bg-white rounded-lg shadow-sm p-6 hover:scale-[1.01] hover:shadow-lg transition-transform duration-300">
          <h3 className="text-xl font-semibold mb-6 text-gray-800">
            {t("analysis.resultsTitle")}
          </h3>
          <Table
            columns={columns}
            dataSource={data}
            className="w-full analysis-results-table"
            scroll={{ y: 300 }}
            tableLayout="fixed"
            pagination={false}
            rowKey="key"
          />
        </div>
      )}
    </div>
  );
};

export default AnalysisResults;
