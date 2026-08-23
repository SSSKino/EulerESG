import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DisclosureCompletenessComparison from "../DisclosureCompletenessComparison";

const mocks = vi.hoisted(() => ({
  getAssessmentByFile: vi.fn(),
  prefetchAssessmentByFile: vi.fn(),
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push }),
}));

vi.mock("@/lib/api", () => ({
  apiService: {
    getAssessmentByFile: mocks.getAssessmentByFile,
    prefetchAssessmentByFile: mocks.prefetchAssessmentByFile,
  },
}));

vi.mock("@/i18n/useT", () => ({
  useT: () => ({
    t: (key: string, vars?: Record<string, unknown>) =>
      vars?.page ? `${key}:${vars.page}` : key,
  }),
}));

vi.mock("antd", async () => {
  const ReactModule = await import("react");
  return {
    Alert: ({ title }: { title?: React.ReactNode }) =>
      ReactModule.createElement("div", null, title),
    Modal: ({ children, open }: React.PropsWithChildren<{ open?: boolean }>) =>
      open
        ? ReactModule.createElement("div", { "data-testid": "opening-modal" }, children)
        : null,
    Popover: ({ children }: React.PropsWithChildren) =>
      ReactModule.createElement(ReactModule.Fragment, null, children),
    Progress: ({ percent }: { percent?: number }) =>
      ReactModule.createElement("div", { "data-progress": percent }),
    Select: () => ReactModule.createElement("div"),
    Spin: () => ReactModule.createElement("div", { role: "status" }),
    Table: () => ReactModule.createElement("div", { "data-testid": "comparison-table" }),
    Tag: ({ children }: React.PropsWithChildren) =>
      ReactModule.createElement("span", null, children),
  };
});

const reports = [
  {
    confidence: 1,
    display_name: "Report A",
    file_id: "report-a",
    filename: "Report A.pdf",
    has_assessment: true,
    short_name: "A",
  },
  {
    confidence: 1,
    display_name: "Report B",
    file_id: "report-b",
    filename: "Report B.pdf",
    has_assessment: true,
    short_name: "B",
  },
  {
    confidence: 1,
    display_name: "Report C",
    file_id: "report-c",
    filename: "Report C.pdf",
    has_assessment: true,
    short_name: "C",
  },
];

describe("DisclosureCompletenessComparison report navigation", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mocks.getAssessmentByFile.mockResolvedValue({ metric_analyses: [] });
    mocks.prefetchAssessmentByFile.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  it("releases the blocking progress modal even when client navigation does not unmount the page", async () => {
    render(
      <DisclosureCompletenessComparison
        fileIds={["report-a", "report-b"]}
        reports={reports}
      />,
    );

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    fireEvent.click(screen.getByRole("button", { name: "Report A" }));
    expect(screen.getByTestId("opening-modal")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_500);
    });

    expect(mocks.prefetchAssessmentByFile).toHaveBeenCalledWith(
      "report-a",
      undefined,
      false,
    );
    expect(mocks.push).toHaveBeenCalledWith(
      "/dashboard/chat?file_id=report-a",
    );
    expect(screen.queryByTestId("opening-modal")).not.toBeInTheDocument();
  });

  it("reloads only the reports in the current Cross Analysis selection", async () => {
    const view = render(
      <DisclosureCompletenessComparison
        fileIds={["report-a", "report-b"]}
        reports={reports}
      />,
    );

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mocks.getAssessmentByFile).toHaveBeenCalledTimes(2);
    expect(
      mocks.getAssessmentByFile.mock.calls.map(([fileId]) => fileId).sort(),
    ).toEqual(["report-a", "report-b"]);
    expect(screen.getByRole("button", { name: "Report A" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Report B" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Report C" })).not.toBeInTheDocument();

    mocks.getAssessmentByFile.mockClear();
    view.rerender(
      <DisclosureCompletenessComparison
        fileIds={["report-b", "report-c"]}
        reports={reports}
      />,
    );

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mocks.getAssessmentByFile).toHaveBeenCalledTimes(2);
    expect(
      mocks.getAssessmentByFile.mock.calls.map(([fileId]) => fileId).sort(),
    ).toEqual(["report-b", "report-c"]);
    expect(screen.queryByRole("button", { name: "Report A" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Report B" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Report C" })).toBeInTheDocument();
  });
});
