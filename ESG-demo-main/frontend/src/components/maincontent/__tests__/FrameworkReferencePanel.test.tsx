import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import FrameworkReferencePanel from "../FrameworkReferencePanel";

vi.mock("@/i18n/useT", () => ({
  useT: () => ({ lang: "en" }),
}));

const FRAMEWORK_LINKS = [
  {
    href: "https://www.ifrs.org/issued-standards/sasb-standards/",
    name: "View SASB as of Jan 2026",
  },
  {
    href: "https://www.globalreporting.org/standards/gri-standards-download-center/",
    name: "View GRI as of Jun 2026",
  },
  {
    href: "https://www.cdp.net/en/disclosure-2026",
    name: "View CDP as of Apr 2026",
  },
  {
    href: "https://standards.aasb.gov.au/sustainability-reporting-standards",
    name: "View AASB as of Nov 2025",
  },
] as const;

describe("FrameworkReferencePanel", () => {
  it("exposes the four dated framework references as exact links", () => {
    render(<FrameworkReferencePanel />);

    const panel = screen.getByTestId("standards-library");
    expect(within(panel).getAllByRole("link")).toHaveLength(FRAMEWORK_LINKS.length);

    for (const reference of FRAMEWORK_LINKS) {
      expect(
        within(panel).getByRole("link", { name: reference.name }),
      ).toHaveAttribute("href", reference.href);
    }
    expect(within(panel).queryByText("TCFD")).not.toBeInTheDocument();
  });

  it("opens every external reference safely and keeps each link keyboard focusable", () => {
    render(<FrameworkReferencePanel />);

    for (const reference of FRAMEWORK_LINKS) {
      const link = screen.getByRole("link", { name: reference.name });
      const relTokens = (link.getAttribute("rel") ?? "").split(/\s+/);

      expect(link).toHaveAttribute("target", "_blank");
      expect(relTokens).toEqual(expect.arrayContaining(["noopener", "noreferrer"]));

      link.focus();
      expect(link).toHaveFocus();
    }
  });

  it("associates the Standards Library region with its visible heading", () => {
    render(<FrameworkReferencePanel />);

    const panel = screen.getByRole("region", { name: "Standards Library" });
    const heading = document.getElementById("standards-library-title");

    expect(panel).toHaveAttribute("aria-labelledby", "standards-library-title");
    expect(heading).not.toBeNull();
    expect(heading).toBeVisible();
    expect(heading).toHaveTextContent("Standards Library");
  });

  it("does not impose a fixed pixel height on the dedicated-page content", () => {
    render(<FrameworkReferencePanel />);

    const panel = screen.getByTestId("standards-library");
    const fixedHeightClass = /^(?:[a-z]+:)*(?:min-|max-)?h-\[\d+(?:\.\d+)?px\]$/;

    const classTokens = panel.className.split(/\s+/);

    expect(panel.style.height).not.toMatch(/px$/);
    expect(panel.style.minHeight).not.toMatch(/px$/);
    expect(panel.style.maxHeight).not.toMatch(/px$/);
    expect(classTokens.some((token) => fixedHeightClass.test(token))).toBe(false);
  });
});
