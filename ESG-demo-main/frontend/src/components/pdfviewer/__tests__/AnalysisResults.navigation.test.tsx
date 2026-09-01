import { describe, expect, it } from "vitest";

import {
  convertAssessmentData,
  getEmptyQuantitativeValueTranslationKey,
  getMetricDefinitionText,
  getTableQualityTranslationKey,
  getTableReviewStatusColor,
  getTableReviewStatusTranslationKey,
  normalizeTableQualityCodes,
} from "../AnalysisResults";
import { tWithLang } from "@/i18n/useT";

const metric = (page: unknown, evidenceSources: unknown[] = []) => ({
  metric_id: "metric-1",
  metric_code: "TC-SI-130a.1",
  metric_name: "Energy consumed",
  disclosure_status: "fully_disclosed",
  reasoning: "Disclosed",
  page,
  evidence_sources: evidenceSources,
});

describe("AnalysisResults evidence navigation", () => {
  it("uses the multiple-values label for an ambiguous disclosed breakdown", () => {
    const [item] = convertAssessmentData({
      metric_analyses: [
        {
          ...metric(89),
          value: "n/a",
          value_status: "ambiguous",
        },
      ],
    });

    expect(item.value_status).toBe("ambiguous");
    expect(getEmptyQuantitativeValueTranslationKey(item.value_status)).toBe(
      "analysis.summary.multipleValues",
    );
  });

  it("keeps the generic placeholder for other empty values", () => {
    expect(getEmptyQuantitativeValueTranslationKey("none")).toBe(
      "analysis.summary.notSpecified",
    );
  });

  it.each([
    ["needs_review", "表格结构待复核", "Table structure needs review", "warning"],
    ["verified", "表格结构已校验", "Table structure verified", "success"],
    ["unverified", "表格结构尚未校验", "Table structure not yet verified", "processing"],
    ["unknown", "表格结构状态未知", "Table structure status unknown", "default"],
  ])(
    "localizes the table review status %s",
    (reviewStatus, expectedZh, expectedEn, expectedColor) => {
      const key = getTableReviewStatusTranslationKey(reviewStatus);
      expect(tWithLang("zh", key)).toBe(expectedZh);
      expect(tWithLang("en", key)).toBe(expectedEn);
      expect(getTableReviewStatusColor(reviewStatus)).toBe(expectedColor);
    },
  );

  it("treats a missing table review status as unknown", () => {
    expect(getTableReviewStatusTranslationKey(undefined)).toBe(
      "analysis.tableReviewStatus.unknown",
    );
    expect(getTableReviewStatusColor(undefined)).toBe("default");
  });

  it.each([
    [
      "missing_header",
      "reason" as const,
      "无法可靠识别表头",
      "The table header could not be identified reliably",
    ],
    [
      "missing_ocr_confidence",
      "note" as const,
      "OCR 结果未提供可用的置信度",
      "No usable OCR confidence was provided",
    ],
    [
      "future_reason",
      "reason" as const,
      "其他表格质量问题（future reason）",
      "Other table quality issue (future reason)",
    ],
  ])(
    "localizes the table quality %s",
    (code, kind, expectedZh, expectedEn) => {
      const key = getTableQualityTranslationKey(code, kind);
      const vars = { detail: code.replace(/[_-]+/g, " ") };
      expect(tWithLang("zh", key, vars)).toBe(expectedZh);
      expect(tWithLang("en", key, vars)).toBe(expectedEn);
    },
  );

  it("preserves and de-duplicates table quality details from evidence sources", () => {
    const [item] = convertAssessmentData({
      metric_analyses: [
        metric(12, [
          {
            review_status: "needs_review",
            quality_reasons: ["missing_header", "missing_header"],
            quality_notes: ["missing_ocr_confidence"],
          },
        ]),
      ],
    });

    expect(item.tableEvidence?.quality_reasons).toEqual(["missing_header", "missing_header"]);
    expect(item.tableEvidence?.quality_notes).toEqual(["missing_ocr_confidence"]);
    expect(normalizeTableQualityCodes(item.tableEvidence?.quality_reasons)).toEqual([
      "missing_header",
    ]);
  });

  it("uses the complete simple definition before the technical definition", () => {
    const simpleDefinition = [
      "Report the current-period energy consumed.",
      "Include the required scope, calculation basis, and breakdowns.",
    ].join("\n\n");
    const [item] = convertAssessmentData({
      metric_analyses: [
        {
          ...metric(12),
          simple_definition: simpleDefinition,
          definition: "Long technical definition that should not be displayed.",
        },
      ],
    });

    expect(item.simple_definition).toBe(simpleDefinition);
    expect(getMetricDefinitionText(item)).toBe(simpleDefinition);
  });

  it("falls back to the technical definition for legacy assessments", () => {
    const [item] = convertAssessmentData({
      metric_analyses: [
        {
          ...metric(12),
          definition: "Legacy definition, including its final sentence.",
        },
      ],
    });

    expect(getMetricDefinitionText(item)).toBe(
      "Legacy definition, including its final sentence.",
    );
  });

  it("keeps the report identity belonging to the selected evidence page", () => {
    const [item] = convertAssessmentData({
      metric_analyses: [
        metric(9, [
          {
            data_page: 8,
            source_report_id: "report-a",
            source_report_name: "Report A.pdf",
          },
          {
            data_page: 9,
            source_report_id: "report-b",
            source_report_name: "Report B.pdf",
          },
        ]),
      ],
    });

    expect(item.evidenceTarget).toEqual({
      page: 9,
      fileId: "report-b",
      reportName: "Report B.pdf",
    });
  });

  it.each([
    ["p. 12", 12],
    ["Page 12-13", 12],
    [0, null],
    [-2, null],
    [1.5, null],
    ["FY2024", null],
  ])("validates explicit physical page value %p", (page, expected) => {
    const [item] = convertAssessmentData({
      metric_analyses: [metric(page)],
    });
    expect(item.evidenceTarget?.page ?? null).toBe(expected);
  });
});
