import React, { useMemo, useEffect, useState } from "react";
import { Table, Tag, Spin, Alert, Select, Space } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useFileStore } from "@/store/useFileStore";
import { apiService } from "@/lib/api";
import { useT } from "@/i18n/useT";

type AnalysisDataItem = {
  metric_id: string;
  metric_name: string;
  disclosure_status: 'fully_disclosed' | 'partially_disclosed' | 'not_disclosed';
  reasoning: string;
  unit?: string;
  category?: string;
  topic?: string;
  type?: string;
  value?: string | number | null;
  page?: string | number | null;
  context?: string | null;
};

interface AnalysisResultsProps {
  fileId?: string;
  /** When the file list row is one scope of a multi-scope upload, load that assessment JSON. */
  preferredScopeKey?: string;
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

const isEmptyValue = (value: unknown) => {
  if (value === null || value === undefined) return true;
  if (typeof value === "number") return false; // 0 is valid
  const s = String(value).trim();
  return s.length === 0;
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
      // Sometimes evidence itself is nested.
      const nested = pickTextFromObj(cand);
      if (nested) return nested;
    }

    // As a last resort, stringify a small subset.
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

const AnalysisResults: React.FC<AnalysisResultsProps> = ({
  fileId,
  preferredScopeKey,
  onPageNavigate,
  showTable = true,
}) => {
  const { t } = useT();

  const files = useFileStore((state) => state.files);
  const currentFile =
    files.find((f) => {
      if (f.file_id !== fileId) return false;
      const ps = (preferredScopeKey || "").trim();
      if (ps) return f.analysis_scope_key === ps;
      return !f.analysis_scope_key;
    }) ?? files.find((f) => f.file_id === fileId);
  const industry = currentFile?.industry;
  const semiIndustry = currentFile?.semiIndustry;
  
  const [analysisData, setAnalysisData] = useState<AnalysisDataItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [assessmentScopeOptions, setAssessmentScopeOptions] = useState<
    { scope_key: string; json_filename: string; overall_score: number }[]
  >([]);
  const [selectedAssessmentScope, setSelectedAssessmentScope] = useState<string | undefined>(undefined);

  useEffect(() => {
    const ps = (preferredScopeKey || "").trim();
    setSelectedAssessmentScope(ps || undefined);
  }, [fileId, preferredScopeKey]);

  useEffect(() => {
    const fetchAnalysisData = async () => {
      if (!currentFile?.file_id) {
        setError(t("analysis.noFileSelected"));
        return;
      }

      setLoading(true);
      setError(null);
      try {
        let scopeOpts: { scope_key: string; json_filename: string; overall_score: number }[] = [];
        try {
          const sc = await apiService.getAssessmentScopesForFile(currentFile.file_id);
          scopeOpts = sc.outputs || [];
        } catch {
          scopeOpts = [];
        }
        setAssessmentScopeOptions(scopeOpts);

        const scopeQuery =
          selectedAssessmentScope ||
          (scopeOpts.length ? scopeOpts[0].scope_key : undefined);

        let assessment;
        try {
          assessment = await apiService.getAssessmentByFile(
            currentFile.file_id,
            scopeOpts.length ? scopeQuery : undefined
          );
        } catch (fileIdError) {
          console.log('Failed to get assessment by file_id, trying latest:', fileIdError);
          try {
            assessment = await apiService.getLatestAssessment();
          } catch (latestError) {
            console.error('Failed to get latest assessment:', latestError);
            throw new Error('Failed to fetch assessment data from all sources');
          }
        }
        
        if (assessment && assessment.metric_analyses) {
          // Convert backend data format to frontend format
          // Filter out metrics with missing required fields (indicates data corruption)
          const pick = (...vals: any[]) => {
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
  // fallback for enums like FULLY_DISCLOSED / PARTIALLY_DISCLOSED
  if (s === "fully_disclosed") return "fully_disclosed";
  if (s === "partially_disclosed") return "partially_disclosed";
  if (s === "not_disclosed") return "not_disclosed";
  return "not_disclosed";
};

const convertedData: AnalysisDataItem[] = (assessment.metric_analyses || [])
  // Accept both new schema (metric_id/metric_name/value/page) and legacy schema (Code/Metric/Value/Page)
  .filter((item: any) => {
    const id =
      item?.metric_id ??
      item?.metric_code ??
      item?.metricId ??
      item?.Code ??
      item?.code;
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
        item?.["Model Disclosure Status"]
    );

    // Keep 0 as valid values: use pick() instead of ||.
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

    // reasoning is the LLM analysis; legacy files may not have it.
    const reasoning = String(
      pick(item?.reasoning, item?.Reasoning, item?.analysis, item?.Analysis) ?? ""
    );

    // context/evidence excerpt for hover
    const contextRaw = pick(
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
      // Normalize evidence/context to readable text (avoid rendering [object Object]).
      context: extractContextText(contextRaw) || null,
    };
  });
setAnalysisData(convertedData);

        } else {
          setAnalysisData([]);
        }
      } catch (err) {
        console.error('Failed to fetch assessment data:', err);
        // Check if it's a 404 (no assessment available)
        const isNotAnalyzed =
          err &&
          typeof err === "object" &&
          "message" in err &&
          typeof (err as any).message === "string" &&
          ((err as any).message.includes("404") || (err as any).message.toLowerCase().includes("no analysis"));

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

    fetchAnalysisData();
  }, [currentFile?.file_id, currentFile?.analysis_scope_key, fileId, preferredScopeKey, selectedAssessmentScope, t]);

  const data = analysisData.map((item, index) => ({
    ...item,
    key: `${item.metric_id}-${index}`,
  }));

  const columns: ColumnsType<AnalysisDataItem> = useMemo(
  () => {
    return [
      {
        title: t("analysis.columns.metric"),
        dataIndex: "metric_name",
        key: "metric_name",
        width: 200,
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
        title: t("analysis.columns.page"),
        dataIndex: "page",
        key: "page",
        width: 100,
        render: (page: string | number | null) => {
          if (isEmptyValue(page)) return "n/a";
          return String(page);
        },
      },
      {
        title: t("analysis.columns.value"),
        dataIndex: "value",
        key: "value",
        width: 160,
        render: (value: string | number | null) => {
          if (isEmptyValue(value)) return "n/a";
          return typeof value === "number" ? formatNumber(value) : String(value);
        },
      },
      {
        title: t("analysis.columns.context"),
        dataIndex: "context",
        key: "context",
        width: 420,
        render: (context: string | null) => {
          const text = (context || "").trim();
          return text || "n/a";
        },
      },
    ];
  },
  [t]
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
      {assessmentScopeOptions.length > 1 && (
        <div className="bg-white rounded-lg shadow-sm px-4 py-3">
          <Space wrap align="center">
            <span className="text-sm text-gray-600">{t("analysis.scopeLabel")}</span>
            <Select
              style={{ minWidth: 220 }}
              value={selectedAssessmentScope ?? assessmentScopeOptions[0]?.scope_key}
              options={assessmentScopeOptions.map((o) => ({
                label: o.scope_key.replace(/_/g, " "),
                value: o.scope_key,
              }))}
              onChange={(v) => setSelectedAssessmentScope(v)}
            />
          </Space>
        </div>
      )}
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