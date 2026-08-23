import type { PropsWithChildren } from "react";

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DashboardSidebar from "../DashboardSidebar";

const mocks = vi.hoisted(() => ({
  clearFiles: vi.fn(),
  getCrossAnalysisReports: vi.fn().mockResolvedValue({ reports: [] }),
  getStandardsCatalog: vi.fn().mockResolvedValue({ frameworks: [] }),
  loadFilesFromBackend: vi.fn(),
  pathname: "/dashboard",
  prefetch: vi.fn(),
  prefetchAssessmentByFile: vi.fn(),
  prefetchCrossAnalysis: vi.fn(),
  push: vi.fn(),
  search: "",
}));

vi.mock("next/navigation", () => ({
  usePathname: () => mocks.pathname,
  useRouter: () => ({
    prefetch: mocks.prefetch,
    push: mocks.push,
  }),
  useSearchParams: () => new URLSearchParams(mocks.search),
}));

vi.mock("next/image", async () => {
  const React = await import("react");
  return {
    default: ({ alt = "", ...props }: Record<string, unknown>) =>
      React.createElement("img", { alt, ...props }),
  };
});

vi.mock("@/components/ui/avatar", async () => {
  const React = await import("react");
  return {
    Avatar: ({ children, ...props }: PropsWithChildren<Record<string, unknown>>) =>
      React.createElement("div", props, children),
    AvatarFallback: ({ children, ...props }: PropsWithChildren<Record<string, unknown>>) =>
      React.createElement("span", props, children),
  };
});

vi.mock("@/components/ui/button", async () => {
  const React = await import("react");
  return {
    Button: ({ children, ...props }: PropsWithChildren<Record<string, unknown>>) =>
      React.createElement("button", props, children),
  };
});

vi.mock("@/components/ui/dropdown-menu", async () => {
  const React = await import("react");
  const Wrapper = ({ children }: PropsWithChildren) => React.createElement(React.Fragment, null, children);
  const Div = ({ children }: PropsWithChildren<Record<string, unknown>>) =>
    React.createElement("div", null, children);
  return {
    DropdownMenu: Wrapper,
    DropdownMenuContent: Div,
    DropdownMenuItem: Div,
    DropdownMenuLabel: Div,
    DropdownMenuSeparator: () => React.createElement("hr"),
    DropdownMenuSub: Wrapper,
    DropdownMenuSubContent: Div,
    DropdownMenuSubTrigger: Div,
    DropdownMenuTrigger: Wrapper,
  };
});

vi.mock("antd", async () => {
  const React = await import("react");
  return {
    App: {
      useApp: () => ({
        message: {
          info: vi.fn(),
          warning: vi.fn(),
        },
      }),
    },
    Modal: ({ children, onOk, open, title }: any) =>
      open
        ? React.createElement(
            "div",
            { "aria-label": String(title), role: "dialog" },
            children,
            React.createElement(
              "button",
              { onClick: onOk, type: "button" },
              "Confirm selection",
            ),
          )
        : null,
    Table: ({ dataSource, rowKey, rowSelection }: any) =>
      React.createElement(
        "div",
        {
          "data-selection-type": rowSelection.type,
          "data-testid": "report-selector",
        },
        React.createElement(
          "button",
          {
            onClick: () =>
              rowSelection.onChange(
                dataSource.map((row: unknown) =>
                  typeof rowKey === "function" ? rowKey(row) : (row as any)[rowKey],
                ),
              ),
            type: "button",
          },
          "Select all reports",
        ),
      ),
    Tag: ({ children }: PropsWithChildren) => React.createElement("span", null, children),
  };
});

vi.mock("@/lib/auth", () => ({
  clearAuth: vi.fn(),
  getStoredAuth: () => ({ name: "Test User" }),
}));

vi.mock("@/lib/api", () => ({
  apiService: {
    getCrossAnalysisReports: mocks.getCrossAnalysisReports,
    getStandardsCatalog: mocks.getStandardsCatalog,
    prefetchAssessmentByFile: mocks.prefetchAssessmentByFile,
    prefetchCrossAnalysis: mocks.prefetchCrossAnalysis,
  },
}));

vi.mock("@/i18n/useAppLang", () => ({
  useAppLang: () => ({ lang: "en", setLang: vi.fn() }),
}));

vi.mock("@/i18n/useT", () => ({
  useT: () => ({
    t: (key: string) =>
      ({
        "common.cancel": "Cancel",
        "crossAnalysis.disclosureCompleteness": "Disclosure Completeness",
        "files.columns.dateUploaded": "Uploaded",
        "files.columns.framework": "Framework",
        "files.columns.industry": "Industry",
        "files.columns.name": "Name",
        "files.columns.size": "Size",
        "files.columns.status": "Status",
        "files.columns.subOption": "Sub-option",
        "files.columns.type": "Type",
        "files.status.ready": "Ready",
        "nav.goToAllFiles": "Go to all files",
        "nav.logout": "Log out",
        "nav.settings": "Settings",
        "nav.userMenu": "User menu",
      })[key] ?? key,
  }),
}));

vi.mock("@/store/useFileStore", () => {
  const reports = [
    {
      dateUploaded: "2026-08-17",
      file_id: "report-a",
      framework: "SASB",
      key: "report-a",
      name: "Report A",
      size: "1 MB",
      status: "ready",
      type: "PDF",
    },
    {
      dateUploaded: "2026-08-17",
      file_id: "report-b",
      framework: "SASB",
      key: "report-b",
      name: "Report B",
      size: "1 MB",
      status: "ready",
      type: "PDF",
    },
  ];
  const state = {
    clearFiles: mocks.clearFiles,
    files: reports,
    loadFilesFromBackend: mocks.loadFilesFromBackend,
  };
  const useFileStore = Object.assign(
    (selector: (value: typeof state) => unknown) => selector(state),
    { getState: () => state },
  );
  return {
    canCrossAnalyzeFiles: () => true,
    useFileStore,
  };
});

describe("DashboardSidebar disclosure-completeness navigation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.pathname = "/dashboard";
    mocks.search = "";
    mocks.getCrossAnalysisReports.mockResolvedValue({ reports: [] });
    window.localStorage.clear();
  });

  it("configures vertical boundary chaining for the sidebar", () => {
    mocks.pathname = "/dashboard/chat";
    render(<DashboardSidebar />);

    expect(
      screen.getByRole("navigation", { name: "Dashboard navigation" }),
    ).toHaveClass("overflow-y-auto", "overscroll-y-auto");
    expect(
      screen.getByRole("navigation", { name: "Dashboard navigation" }),
    ).not.toHaveClass("overscroll-contain");
  });

  it("prefetches heavy report routes only after navigation intent", () => {
    render(<DashboardSidebar />);

    expect(mocks.prefetch).not.toHaveBeenCalled();

    const compliance = screen.getByRole("button", { name: "Compliance" });
    fireEvent.mouseEnter(compliance);
    fireEvent.focus(compliance);
    expect(mocks.prefetch).toHaveBeenCalledTimes(1);
    expect(mocks.prefetch).toHaveBeenCalledWith("/dashboard/chat");

    const crossAnalysis = screen.getByRole("button", { name: "Cross Analysis" });
    fireEvent.mouseEnter(crossAnalysis);
    expect(mocks.prefetch).toHaveBeenCalledTimes(2);
    expect(mocks.prefetch).toHaveBeenCalledWith("/cross-analysis");
  });

  it("aligns the expanded Disclosure child with embedded primary directory items without an icon gap", () => {
    render(<DashboardSidebar />);

    const crossAnalysis = screen.getByRole("button", { name: "Cross Analysis" });
    const subnavigation = screen.getByRole("group", { name: "Cross Analysis" });
    const disclosure = within(subnavigation).getByRole("button", {
      name: "Disclosure Completeness",
    });

    expect(crossAnalysis).toBeVisible();
    expect(crossAnalysis.nextElementSibling).toBe(subnavigation);
    expect(subnavigation).toHaveAttribute(
      "data-testid",
      "cross-analysis-subnavigation",
    );
    expect(disclosure).toBeVisible();
    expect(disclosure).toHaveAttribute(
      "data-testid",
      "disclosure-completeness-nav",
    );
    expect(disclosure).toHaveClass(
      "ml-5",
      "w-[calc(100%-1.25rem)]",
      "px-3",
      "rounded-xl",
      "border",
      "border-transparent",
    );
    expect(disclosure).not.toHaveClass("pl-8");
    expect(disclosure.querySelector("svg")).not.toBeInTheDocument();
    expect(
      disclosure.className
        .split(/\s+/)
        .some((className) => className.startsWith("gap-")),
    ).toBe(false);
    expect(within(disclosure).getByText("Disclosure Completeness")).toHaveClass(
      "text-sm",
      "font-medium",
    );
    expect(
      within(subnavigation).queryByTestId("cross-analysis-navigation-slot"),
    ).not.toBeInTheDocument();
  });

  it("reuses multi-report selection and opens the disclosure view directly", async () => {
    render(<DashboardSidebar />);

    fireEvent.click(
      screen.getByRole("button", { name: "Disclosure Completeness" }),
    );
    expect(await screen.findByRole("dialog")).toBeVisible();
    expect(screen.getByTestId("report-selector")).toHaveAttribute(
      "data-selection-type",
      "checkbox",
    );

    fireEvent.click(screen.getByRole("button", { name: "Select all reports" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm selection" }));

    expect(mocks.push).toHaveBeenCalledTimes(1);

    const target = String(mocks.push.mock.calls[0]?.[0]);
    const url = new URL(target, "http://localhost");
    expect(url.pathname).toBe("/cross-analysis");
    expect(url.searchParams.get("ids")).toBe("report-a,report-b");
    expect(url.searchParams.get("view")).toBe("disclosure");
  });

  it("uses the reports already selected in Cross Analysis without reopening the selector", () => {
    mocks.pathname = "/cross-analysis";
    mocks.search =
      "ids=report-a%2Creport-b&primary=Environment&secondary=Energy&framework=SASB&industry=Software%20%26%20IT%20Services&semiIndustry=Application%20Software";

    render(<DashboardSidebar />);

    fireEvent.click(
      screen.getByRole("button", { name: "Disclosure Completeness" }),
    );

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mocks.push).toHaveBeenCalledTimes(1);

    const target = String(mocks.push.mock.calls[0]?.[0]);
    const url = new URL(target, "http://localhost");
    expect(url.pathname).toBe("/cross-analysis");
    expect(url.searchParams.get("ids")).toBe("report-a,report-b");
    expect(url.searchParams.get("view")).toBe("disclosure");
    expect(url.searchParams.get("framework")).toBe("SASB");
    expect(url.searchParams.get("industry")).toBe("Software & IT Services");
    expect(url.searchParams.get("semiIndustry")).toBe("Application Software");
  });

  it("marks only the Disclosure child current while retaining its Cross Analysis parent relationship", () => {
    mocks.pathname = "/cross-analysis";
    mocks.search = "view=disclosure";

    render(<DashboardSidebar />);

    const subnavigation = screen.getByRole("group", { name: "Cross Analysis" });
    const disclosure = within(subnavigation).getByRole("button", {
      name: "Disclosure Completeness",
    });
    const crossAnalysis = screen.getByRole("button", { name: "Cross Analysis" });
    expect(disclosure).toHaveClass("bg-[#ececec]");
    expect(disclosure).toHaveAttribute("aria-current", "page");
    expect(crossAnalysis).not.toHaveClass("bg-[#ececec]");
    expect(crossAnalysis).not.toHaveAttribute("aria-current");
    expect(crossAnalysis.nextElementSibling).toBe(subnavigation);
    expect(
      within(subnavigation).queryByTestId("cross-analysis-navigation-slot"),
    ).not.toBeInTheDocument();
  });
});

describe("DashboardSidebar favourites navigation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.pathname = "/dashboard";
    mocks.search = "";
    window.localStorage.clear();
  });

  it("opens the favourites report directory", () => {
    render(<DashboardSidebar />);

    fireEvent.click(screen.getByRole("button", { name: "Favourite" }));

    expect(mocks.push).toHaveBeenCalledTimes(1);
    expect(mocks.push).toHaveBeenCalledWith("/dashboard/favourite");
  });

  it("marks Favourite as the current directory on its route", () => {
    mocks.pathname = "/dashboard/favourite";

    render(<DashboardSidebar />);

    const favourite = screen.getByRole("button", { name: "Favourite" });
    expect(favourite).toHaveAttribute("aria-current", "page");
    expect(favourite).toHaveClass("bg-[#ececec]");
    expect(screen.getByRole("button", { name: "Homepage" })).not.toHaveAttribute(
      "aria-current",
    );
  });
});

describe("DashboardSidebar Standards Library", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getStandardsCatalog.mockResolvedValue({ frameworks: [] });
    mocks.pathname = "/dashboard";
    mocks.search = "";
    window.localStorage.clear();
  });

  it("places a single Standards Library navigation button directly after Favourite", () => {
    render(<DashboardSidebar />);

    const favourite = screen.getByRole("button", { name: "Favourite" });
    const library = screen.getByRole("button", { name: "Standards Library" });

    expect(favourite.nextElementSibling).toBe(library);
    expect(library).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Standards Library" })).not.toBeInTheDocument();
    expect(screen.queryAllByRole("link")).toHaveLength(0);

    fireEvent.click(library);
    expect(mocks.push).toHaveBeenCalledTimes(1);
    expect(mocks.push).toHaveBeenCalledWith("/dashboard/standards-library");
  });

  it("marks Standards Library as current only on its dedicated route", () => {
    mocks.pathname = "/dashboard/standards-library";

    render(<DashboardSidebar />);

    const library = screen.getByRole("button", { name: "Standards Library" });
    expect(library).toHaveAttribute("aria-current", "page");
    expect(library).toHaveClass("bg-[#ececec]");
    expect(screen.getByRole("button", { name: "Homepage" })).not.toHaveAttribute(
      "aria-current",
    );
    expect(screen.getByRole("button", { name: "Favourite" })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("keeps the Standards Library navigation operable when collapsed", async () => {
    render(<DashboardSidebar />);

    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeVisible();
    });

    const library = screen.getByRole("button", { name: "Standards Library" });
    expect(library).toBeVisible();
    expect(library).toHaveAttribute("title", "Standards Library");
    library.focus();
    expect(library).toHaveFocus();

    fireEvent.click(library);
    expect(mocks.push).toHaveBeenCalledWith("/dashboard/standards-library");
  });

  it("deduplicates route and catalog prefetch across pointer and keyboard intent", async () => {
    render(<DashboardSidebar />);

    const library = screen.getByRole("button", { name: "Standards Library" });
    fireEvent.mouseEnter(library);
    library.focus();

    await waitFor(() => {
      expect(mocks.getStandardsCatalog).toHaveBeenCalledTimes(1);
    });
    expect(mocks.prefetch).toHaveBeenCalledWith("/dashboard/standards-library");
  });

  it("absorbs a failed catalog prefetch and allows the next intent to retry", async () => {
    mocks.getStandardsCatalog
      .mockRejectedValueOnce(new Error("prefetch unavailable"))
      .mockResolvedValueOnce({ frameworks: [] });
    render(<DashboardSidebar />);

    const library = screen.getByRole("button", { name: "Standards Library" });
    fireEvent.mouseEnter(library);
    await waitFor(() => {
      expect(mocks.getStandardsCatalog).toHaveBeenCalledTimes(1);
    });

    fireEvent.focus(library);
    await waitFor(() => {
      expect(mocks.getStandardsCatalog).toHaveBeenCalledTimes(2);
    });
    expect(library).toBeVisible();
  });
});

describe("DashboardSidebar cross-analysis navigation directory", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getCrossAnalysisReports.mockResolvedValue({ reports: [] });
    mocks.pathname = "/cross-analysis";
    mocks.search = "ids=report-a%2Creport-b&primary=Environment";
    window.localStorage.clear();
  });

  it("keeps the directory inside the Cross Analysis group after Disclosure Completeness", () => {
    render(<DashboardSidebar />);

    const crossAnalysis = screen.getByRole("button", { name: "Cross Analysis" });
    const subnavigation = screen.getByRole("group", { name: "Cross Analysis" });
    const disclosure = within(subnavigation).getByRole("button", {
      name: "Disclosure Completeness",
    });
    const navigationSlot = within(subnavigation).getByTestId(
      "cross-analysis-navigation-slot",
    );
    const favourite = screen.getByRole("button", { name: "Favourite" });

    expect(crossAnalysis).toHaveClass("bg-[#ececec]");
    expect(crossAnalysis).toHaveAttribute("aria-current", "page");
    expect(crossAnalysis.nextElementSibling).toBe(subnavigation);
    expect(navigationSlot).toBeVisible();
    expect(subnavigation).not.toHaveClass("flex-1");
    expect(navigationSlot).not.toHaveClass("flex-1");
    expect(navigationSlot).toHaveClass(
      "max-h-[min(320px,36vh)]",
      "overflow-y-auto",
      "overscroll-y-auto",
    );
    expect(navigationSlot).not.toHaveClass("overscroll-contain");
    expect(subnavigation.nextElementSibling).toBe(favourite);
    expect(subnavigation).toContainElement(disclosure);
    expect(subnavigation).toContainElement(navigationSlot);
    expect(
      disclosure.compareDocumentPosition(navigationSlot) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("does not show the Cross Analysis directory in Disclosure Completeness", () => {
    mocks.search = "ids=report-a%2Creport-b&view=disclosure";

    render(<DashboardSidebar />);

    const subnavigation = screen.getByRole("group", { name: "Cross Analysis" });

    expect(within(subnavigation).queryByTestId("cross-analysis-navigation-slot")).not.toBeInTheDocument();
    expect(within(subnavigation).getByRole("button", { name: "Disclosure Completeness" })).toHaveClass(
      "bg-[#ececec]",
    );
  });

  it("keeps the Disclosure child operable while hiding the directory when collapsed", async () => {
    render(<DashboardSidebar />);

    expect(screen.getByTestId("cross-analysis-navigation-slot")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));

    await waitFor(() => {
      const subnavigation = screen.getByRole("group", { name: "Cross Analysis" });
      const disclosure = within(subnavigation).getByRole("button", {
        name: "Disclosure Completeness",
      });

      expect(subnavigation).toBeVisible();
      expect(disclosure).toBeVisible();
      expect(disclosure).toHaveAttribute("title", "Disclosure Completeness");
      expect(disclosure).toHaveClass("h-9", "w-full", "justify-center", "px-2");
      expect(disclosure.querySelector("svg")).toBeInTheDocument();
      expect(
        within(disclosure).queryByText("Disclosure Completeness"),
      ).not.toBeInTheDocument();
      expect(
        within(subnavigation).getByTestId("cross-analysis-navigation-slot"),
      ).not.toBeVisible();
    });

    fireEvent.click(
      within(screen.getByRole("group", { name: "Cross Analysis" })).getByRole(
        "button",
        { name: "Disclosure Completeness" },
      ),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const target = String(mocks.push.mock.calls[0]?.[0]);
    const url = new URL(target, "http://localhost");
    expect(url.searchParams.get("ids")).toBe("report-a,report-b");
    expect(url.searchParams.get("view")).toBe("disclosure");
  });
});
