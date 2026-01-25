import React, { useMemo, useEffect, useState } from "react";
import { Table, Tag, Popover, Spin, Alert } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useFileStore } from "@/store/useFileStore";
import { apiService } from "@/lib/api";

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

const isEmptyValue = (value: unknown) => {
  if (value === null || value === undefined) return true;
  if (typeof value === "number") return false; // 0 is valid
  const s = String(value).trim();
  return s.length === 0;
};

const AnalysisResults: React.FC<AnalysisResultsProps> = ({
  fileId,
  onPageNavigate,
  showTable = true,
}) => {
  const files = useFileStore((state) => state.files);
  const currentFile = files.find((file) => file.file_id === fileId);
  const industry = currentFile?.industry;
  const semiIndustry = currentFile?.semiIndustry;
  
  const [analysisData, setAnalysisData] = useState<AnalysisDataItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchAnalysisData = async () => {
      if (!currentFile?.file_id) {
        setError("No file selected");
        return;
      }
      
      setLoading(true);
      setError(null);
      try {
        // First try to get by file_id, if failed then get latest assessment results
        let assessment;
        try {
          assessment = await apiService.getAssessmentByFile(currentFile.file_id);
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
          setError("No analysis available. Please upload and analyze first.");
        } else {
          setError("Failed to load analysis data. Please ensure the report has been processed and analysis has been completed.");
        }
        setAnalysisData([]);
      } finally {
        setLoading(false);
      }
    };

    fetchAnalysisData();
  }, [currentFile?.file_id, fileId]);

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

    return [
      {
        title: "Metric",
        dataIndex: "metric_name",
        key: "metric_name",
        width: 200,
      },
      {
        title: "Status",
        dataIndex: "disclosure_status",
        key: "disclosure_status",
        width: 120,
        render: (status: string) => {
          let color = "default";
          let text = status;
          if (status === "fully_disclosed") {
            color = "success";
            text = "Fully Disclosed";
          } else if (status === "partially_disclosed") {
            color = "warning";
            text = "Partially Disclosed";
          } else if (status === "not_disclosed") {
            color = "error";
            text = "Not Disclosed";
          }
          return <Tag color={color}>{text}</Tag>;
        },
        filters: [
          { text: "Fully Disclosed", value: "fully_disclosed" },
          { text: "Partially Disclosed", value: "partially_disclosed" },
          { text: "Not Disclosed", value: "not_disclosed" },
        ],
        onFilter: (value, record) => record.disclosure_status === value,
      },
      {
        title: "Category",
        dataIndex: "category",
        key: "category",
        width: 110,
        render: (category?: string) =>
            category ? (
              <Tag color={getCategoryColor(category)}>{category}</Tag>
            ) : (
              <span className="text-gray-400">-</span>
            ),
        filters: categoryOptions,
        onFilter: (value, record) => (record.category || "") === value,
      },
      {
        title: "Unit",
        dataIndex: "unit",
        key: "unit",
        width: 90,
      },
      {
        title: "Type",
        dataIndex: "type",
        key: "type",
        width: 120,
      },
      {
        title: "Value",
        dataIndex: "value",
        key: "value",
        // Keep more room for value text and evidence indicators.
        width: 420,
        render: (_value: string | number | null, record: AnalysisDataItem) => {
          const isNotDisclosed = record.disclosure_status === "not_disclosed";

          // If not disclosed, keep value empty and hide evidence/page/LLM hover indicators.
          if (isNotDisclosed) {
            return <span className="text-gray-400"></span>;
          }

          const empty = isEmptyValue(record.value);
          const displayValue = empty
            ? "Not specified"
            : typeof record.value === "number"
              ? formatNumber(record.value)
              : String(record.value);

          const evidenceContent = (
            <div className="max-w-md p-2">
              <div className="text-sm">
                <div>
                  <span className="font-semibold">Metric:</span> {record.metric_name}
                </div>
                {!isEmptyValue(record.unit) && (
                  <div>
                    <span className="font-semibold">Unit:</span> {record.unit}
                  </div>
                )}
              </div>
              {record.context ? (
                <div className="mt-2 text-sm whitespace-pre-wrap">{record.context}</div>
              ) : (
                <div className="mt-2 text-xs text-gray-500">No evidence excerpt available.</div>
              )}
            </div>
          );

          const llmContent = (
            <div className="max-w-md p-2">
              <h4 className="font-semibold mb-2">LLM Analysis</h4>
              {record.reasoning ? (
                <p className="text-sm whitespace-pre-wrap">{record.reasoning}</p>
              ) : (
                <p className="text-sm text-gray-500">No analysis text available.</p>
              )}
            </div>
          );

          const pageText = !isEmptyValue(record.page) ? String(record.page) : "";
          const pageNumber = normalizePage(record.page);
          const pageClickable = pageNumber !== null && !!onPageNavigate;
          const pageLabel = pageNumber !== null ? String(pageNumber) : pageText;

          return (
            <div className="flex items-center gap-2 w-full">
              {/* Left: value (takes remaining width) */}
              <div className="flex-1 min-w-0">
                <Popover content={evidenceContent} title="Evidence" trigger="hover" mouseEnterDelay={0.2}>
                  <span
                    className={
                      record.context || !empty
                        ? "cursor-help underline decoration-dotted"
                        : "text-gray-400"
                    }
                  >
                    {displayValue}
                  </span>
                </Popover>
              </div>

              {/* Right: hover (LLM) + page, right-aligned */}
              <div className="ml-auto flex items-center gap-2 shrink-0">
                {(record.reasoning || record.reasoning === "") && (
                  <Popover content={llmContent} title="LLM Analysis" trigger="hover" mouseEnterDelay={0.2}>
                    <span
                      className="inline-flex items-center justify-center w-4 h-4 rounded-full border border-gray-300 text-gray-700 text-[11px] font-semibold leading-none cursor-help select-none"
                      aria-label="LLM analysis"
                      onClick={(e) => e.stopPropagation()}
                    >
                      !
                    </span>
                  </Popover>
                )}

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
                    title={pageClickable ? "Jump to page" : undefined}
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
  [data, onPageNavigate]
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
            : "Industry Analysis"}
        </h2>
        <div className="bg-white rounded-lg shadow-sm p-6 text-center">
          <Spin size="large" />
          <p className="mt-4 text-gray-600">Loading analysis results...</p>
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
            : "Industry Analysis"}
        </h2>
        <Alert
          message="Error"
          description={error}
          type="error"
          showIcon
          action={
            <button
              onClick={() => window.location.reload()}
              className="px-4 py-2 bg-red-500 text-white rounded hover:bg-red-600">
              Retry
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
          : "Industry Analysis"}
      </h2>
      <div className="bg-white rounded-lg shadow-sm p-6 hover:scale-[1.02] hover:shadow-lg transition-transform duration-300">
        <h3 className="text-xl font-semibold mb-6 text-gray-800">
          Summary
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
                      label: "Not Disclosed/Discussed",
                    },
                    {
                      color: "text-yellow-500",
                      value: group.yellow,
                      percent: yellowPct,
                      label: "Disclosed/Discussed But Not Clear",
                    },
                    {
                      color: "text-green-500",
                      value: group.green,
                      percent: greenPct,
                      label: "Disclosed/Discussed",
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
        <div className="bg-white rounded-lg shadow-sm p-4 hover:scale-[1.01] hover:shadow-lg transition-transform duration-300">
          <h3 className="text-lg font-semibold mb-3 text-gray-800">
            Results
          </h3>
          <Table
            columns={columns}
            dataSource={data}
            className="w-full"
            scroll={{ x: "max-content", y: 300 }}
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