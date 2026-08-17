import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import CrossAnalysisDimensionPage from "../page";

const mocks = vi.hoisted(() => ({
  getCrossAnalysisDisclosedCache: vi.fn(),
  getCrossAnalysisReports: vi.fn(),
  navigationSlot: null as HTMLElement | null,
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ dimension: "environment" }),
  useRouter: () => ({ push: mocks.push }),
  useSearchParams: () =>
    new URLSearchParams(
      "ids=report-a%2Creport-b&primary=Environment&secondary=Energy",
    ),
}));

vi.mock("next/dynamic", () => ({
  default: () => () => null,
}));

vi.mock("antd", async () => {
  const React = await import("react");
  return {
    Button: ({ children, ...props }: React.PropsWithChildren<Record<string, unknown>>) =>
      React.createElement("button", props, children),
    Modal: () => null,
    Skeleton: () => React.createElement("div", { "data-testid": "skeleton" }),
  };
});

vi.mock("@/lib/api", () => ({
  apiService: {
    getCrossAnalysisDisclosedCache: mocks.getCrossAnalysisDisclosedCache,
    getCrossAnalysisReports: mocks.getCrossAnalysisReports,
  },
}));

vi.mock("@/features/crossAnalysis/recordAdapter", () => ({
  normalizeCrossRecords: (records: unknown[]) => records,
}));

vi.mock("@/components/cross-analysis/CrossAnalysisNavigationPortal", () => ({
  useCrossAnalysisNavigationSlot: () => mocks.navigationSlot,
}));

vi.mock("@/components/cross-analysis/NewSidebar", async () => {
  const React = await import("react");
  return {
    NewSidebar: () =>
      React.createElement("div", {
        "data-testid": "ported-cross-analysis-directory",
      }),
  };
});

vi.mock("@/components/cross-analysis/NewHeader", () => ({
  NewHeader: () => null,
}));

vi.mock("@/i18n/useT", () => ({
  useT: () => ({ t: (key: string) => key }),
}));

describe("CrossAnalysisDimensionPage navigation directory", () => {
  beforeEach(() => {
    vi.clearAllMocks();

    mocks.navigationSlot = document.createElement("div");
    mocks.navigationSlot.dataset.testid = "cross-analysis-navigation-slot";
    document.body.appendChild(mocks.navigationSlot);

    mocks.getCrossAnalysisReports.mockResolvedValue({
      reports: [
        {
          display_name: "Report A",
          file_id: "report-a",
          filename: "report-a.pdf",
          framework: "SASB",
          industry: "Software & IT Services",
          semi_industry: "Application Software",
          short_name: "A",
        },
        {
          display_name: "Report B",
          file_id: "report-b",
          filename: "report-b.pdf",
          framework: "SASB",
          industry: "Software & IT Services",
          semi_industry: "Application Software",
          short_name: "B",
        },
      ],
    });
    mocks.getCrossAnalysisDisclosedCache.mockResolvedValue({
      records: [
        {
          data: "75",
          file_id: "report-a",
          name: "Report A",
          primary_navigation: "Environment",
          secondary_navigation: "Environment",
          topic: "Energy",
          unit: "%",
          year: "2025",
        },
      ],
    });
  });

  afterEach(() => {
    mocks.navigationSlot?.remove();
    mocks.navigationSlot = null;
  });

  it("portals exactly one directory into the global sidebar and leaves none in the page content", async () => {
    const view = render(<CrossAnalysisDimensionPage />);

    const directory = await screen.findByTestId(
      "ported-cross-analysis-directory",
    );

    expect(screen.getAllByTestId("ported-cross-analysis-directory")).toHaveLength(1);
    expect(directory.parentElement).toBe(mocks.navigationSlot);
    expect(
      view.container.querySelector('[data-testid="ported-cross-analysis-directory"]'),
    ).not.toBeInTheDocument();
  });
});
