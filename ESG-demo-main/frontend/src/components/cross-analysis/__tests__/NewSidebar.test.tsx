import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { NewSidebar } from "../NewSidebar";

vi.mock("@/i18n/useT", () => ({
  useT: () => ({
    t: (key: string) =>
      ({
        "crossAnalysis.disclosureCompleteness": "Disclosure Completeness",
        "crossAnalysis.navigation": "Navigation",
        "crossAnalysis.noSecondaryNav": "No secondary navigation",
      })[key] ?? key,
  }),
}));

describe("NewSidebar", () => {
  it("keeps Disclosure Completeness out of the report-level navigation", () => {
    render(
      <NewSidebar
        expandedPrimaries={{ Environment: false }}
        onSelectSecondary={vi.fn()}
        onSelectTertiary={vi.fn()}
        onTogglePrimary={vi.fn()}
        primaryOptions={["Environment"]}
        secondaryByPrimary={new Map([["Environment", ["Energy"]]])}
        selectedPrimary="Environment"
        selectedSecondaries={[]}
        selectedTertiary={null}
        tertiaryByPrimaryAndSecondary={new Map()}
      />,
    );

    expect(
      screen.queryByRole("button", { name: "Disclosure Completeness" }),
    ).not.toBeInTheDocument();
  });

  it("renders an embedded directory without the old standalone card chrome", () => {
    const { container } = render(
      <NewSidebar
        embedded
        expandedPrimaries={{ Environment: true }}
        onSelectSecondary={vi.fn()}
        onSelectTertiary={vi.fn()}
        onTogglePrimary={vi.fn()}
        primaryOptions={["Environment"]}
        secondaryByPrimary={new Map([["Environment", ["Energy"]]])}
        selectedPrimary="Environment"
        selectedSecondaries={["Energy"]}
        selectedTertiary={null}
        tertiaryByPrimaryAndSecondary={new Map()}
      />,
    );

    const primaryItem = screen.getByRole("button", { name: "Environment" });

    expect(primaryItem).toBeVisible();
    expect(screen.getByRole("button", { name: "Energy" })).toBeVisible();
    expect(screen.queryByText("Navigation")).not.toBeInTheDocument();

    const directory = container.firstElementChild;
    expect(directory).toHaveClass("pl-5");
    expect(primaryItem).toHaveClass("w-full", "px-3", "rounded-xl");
    expect(directory).not.toHaveClass("w-[320px]");
    expect(directory).not.toHaveClass("rounded-2xl");
    expect(directory).not.toHaveClass("shadow-sm");
  });
});
