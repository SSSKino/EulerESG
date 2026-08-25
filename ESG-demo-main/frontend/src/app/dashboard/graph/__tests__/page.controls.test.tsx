import type { PropsWithChildren } from "react";

import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  dynamicIndex: 0,
  getCompanies: vi.fn(),
  getReportDisclosureGraph: vi.fn(),
  loadFilesFromBackend: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/dynamic", async () => {
  const React = await import("react");
  return {
    default: () => {
      const index = mocks.dynamicIndex++;
      if (index === 0) {
        return React.forwardRef(function MockGraphCanvas() {
          return React.createElement("div", { "data-testid": "mock-graph-canvas" });
        });
      }
      return function MockAssistant() {
        return React.createElement("div", { "data-testid": "mock-ai-assistant" });
      };
    },
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
  useSearchParams: () => new URLSearchParams("owner=reports&file_id=file-1"),
}));

vi.mock("antd", async () => {
  const React = await import("react");
  const Empty = Object.assign(
    ({ children, description }: PropsWithChildren<{ description?: React.ReactNode }>) =>
      React.createElement("div", null, description, children),
    { PRESENTED_IMAGE_SIMPLE: "simple" },
  );
  return {
    Button: ({ children, ...props }: PropsWithChildren<Record<string, unknown>>) =>
      React.createElement("button", props, children),
    Drawer: ({ children, open }: PropsWithChildren<{ open?: boolean }>) =>
      open ? React.createElement("aside", null, children) : null,
    Empty,
    Modal: ({ children, open }: PropsWithChildren<{ open?: boolean }>) =>
      open ? React.createElement("div", { role: "dialog" }, children) : null,
    Select: ({
      "aria-label": ariaLabel,
      maxTagCount,
      maxTagPlaceholder,
      mode,
      value,
    }: {
      "aria-label"?: string;
      maxTagCount?: number | string;
      maxTagPlaceholder?: (omittedValues: unknown[]) => React.ReactNode;
      mode?: string;
      value?: unknown;
    }) => {
      const selectedValues = Array.isArray(value) ? value : [];
      const summary = mode === "multiple" && maxTagCount === 0 && selectedValues.length
        ? maxTagPlaceholder?.(selectedValues.map((selectedValue) => ({ value: selectedValue })))
        : null;
      return React.createElement(
        "div",
        {
          "aria-label": ariaLabel,
          "data-max-tag-count": maxTagCount,
          "data-mode": mode,
          role: "combobox",
        },
        summary,
      );
    },
    Skeleton: () => React.createElement("div", { role: "progressbar" }),
    Switch: ({ "aria-label": ariaLabel }: { "aria-label"?: string }) =>
      React.createElement("button", { "aria-label": ariaLabel, role: "switch" }),
    Tag: ({ children }: PropsWithChildren) => React.createElement("span", null, children),
    Tooltip: ({ children }: PropsWithChildren) => React.createElement(React.Fragment, null, children),
  };
});

vi.mock("@/lib/auth", () => ({
  getStoredAuth: () => ({ userId: "test-user" }),
}));

vi.mock("@/lib/api", () => ({
  apiService: {
    getCompanies: mocks.getCompanies,
    getCompanyDisclosureGraph: vi.fn(),
    getCompanyDisclosureGraphNeighbors: vi.fn(),
    getReportDisclosureGraph: mocks.getReportDisclosureGraph,
    getReportDisclosureGraphNeighbors: vi.fn(),
  },
}));

vi.mock("@/lib/logger", () => ({
  errorSummary: (error: unknown) => String(error),
}));

vi.mock("@/store/useFileStore", () => ({
  useFileStore: (selector: (state: Record<string, unknown>) => unknown) =>
    selector({
      files: [
        {
          key: "file-1",
          name: "Example ESG Report.pdf",
          size: "1 MB",
          dateUploaded: "2026-08-24",
          type: "pdf",
          status: "ready",
          file_id: "file-1",
          report_year: 2024,
        },
      ],
      loadFilesFromBackend: mocks.loadFilesFromBackend,
    }),
}));

describe("Graph Exploration Kumu control placement", () => {
  beforeEach(() => {
    mocks.dynamicIndex = 0;
    mocks.getCompanies.mockResolvedValue({ companies: [] });
    mocks.getReportDisclosureGraph.mockResolvedValue({
      schema_version: "1.0",
      graph_revision: "revision-1",
      nodes: [],
      edges: [],
      stats: { node_count: 0, edge_count: 0 },
      truncated: false,
    });
  });

  it("exposes the attachment's top-left, top-right, and bottom-left control regions", async () => {
    const { default: GraphExplorationPage } = await import("../page");
    render(<GraphExplorationPage />);

    const title = screen.getByTestId("graph-map-title");
    expect(title).toHaveTextContent("ESG Metrics Analysis Graph");

    const search = screen.getByTestId("graph-search-control");
    expect(within(search).getByRole("combobox", { name: "Search graph" })).toBeVisible();

    const rightControls = screen.getByTestId("graph-right-controls");
    expect(rightControls).toContainElement(search);

    const controls = screen.getByTestId("graph-map-controls");
    expect(rightControls).toContainElement(controls);
    expect(within(controls).getByRole("button", { name: "Zoom in" })).toBeVisible();
    expect(within(controls).getByRole("button", { name: "View settings" })).toBeVisible();

    const legend = await screen.findByTestId("graph-status-legend");
    await waitFor(() => {
      expect(legend).toHaveTextContent("Disclosed");
      expect(legend).toHaveTextContent("Partially disclosed");
      expect(legend).toHaveTextContent("Not disclosed");
    });
  });

  it("summarizes multi-select filters by count instead of selected labels", async () => {
    const { default: GraphExplorationPage } = await import("../page");
    render(<GraphExplorationPage />);

    const multiSelectLabels = [
      "Reports",
      "Framework",
      "Year",
      "Topic",
      "Disclosure status",
    ];
    multiSelectLabels.forEach((label) => {
      const select = screen.getByRole("combobox", { name: label });
      expect(select).toHaveAttribute("data-mode", "multiple");
      expect(select).toHaveAttribute("data-max-tag-count", "0");
    });

    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "Reports" })).toHaveTextContent("1 item filtered");
      expect(screen.getByRole("combobox", { name: "Reports" })).not.toHaveTextContent("Example ESG Report.pdf");
    });
  });
});
