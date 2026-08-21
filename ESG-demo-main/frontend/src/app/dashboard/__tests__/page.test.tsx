import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  dynamicOptions: [] as Array<Record<string, unknown> | undefined>,
}));

vi.mock("next/dynamic", () => ({
  default: (
    _loader: () => Promise<unknown>,
    options?: Record<string, unknown>,
  ) => {
    mocks.dynamicOptions.push(options);
    return function MockFloatingChatAssistant({
      includeContext,
    }: {
      includeContext?: boolean;
    }) {
      return (
        <button
          type="button"
          data-include-context={String(includeContext)}
        >
          AI Assistant
        </button>
      );
    };
  },
}));

vi.mock("@/components/pdfviewer/PDFViewer", () => ({
  default: () => <main data-testid="dashboard-files" />,
}));

vi.mock("@/components/status/FloatingStatusButton", () => ({
  default: () => null,
}));

describe("DashboardPage", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("keeps a generic AI Assistant available on the homepage via a client-only chunk", async () => {
    vi.stubEnv("NEXT_PUBLIC_SHOW_DEV_TOOLS", "false");
    const { default: DashboardPage } = await import("../page");
    render(<DashboardPage />);

    expect(screen.getByTestId("dashboard-files")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "AI Assistant" })).toHaveAttribute(
      "data-include-context",
      "false",
    );
    expect(mocks.dynamicOptions).toContainEqual({ ssr: false });
  });
});
