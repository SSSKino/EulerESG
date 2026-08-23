import { describe, expect, it } from "vitest";

import {
  convertAssessmentData,
  getEmptyQuantitativeValueTranslationKey,
} from "../AnalysisResults";

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
