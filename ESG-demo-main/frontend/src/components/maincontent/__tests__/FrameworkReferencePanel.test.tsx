import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import FrameworkReferencePanel from "../FrameworkReferencePanel";

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
    href: "https://www.fsb-tcfd.org/recommendations/",
    name: "View TCFD as of Oct 2023",
  },
  {
    href: "https://www.cdp.net/en/disclosure-2026",
    name: "View CDP as of Apr 2026",
  },
] as const;

const AASB_PLACEHOLDER_NAME = "View AASB as of Nov 2025";

describe("FrameworkReferencePanel", () => {
  it("exposes the four dated framework references as exact links", () => {
    render(<FrameworkReferencePanel />);

    const panel = screen.getByRole("complementary");
    expect(within(panel).getAllByRole("link")).toHaveLength(FRAMEWORK_LINKS.length);

    for (const reference of FRAMEWORK_LINKS) {
      expect(
        within(panel).getByRole("link", { name: reference.name }),
      ).toHaveAttribute("href", reference.href);
    }
  });

  it("renders AASB as a disabled, non-clickable placeholder", () => {
    render(<FrameworkReferencePanel />);

    const panel = screen.getByRole("complementary");
    const placeholder = within(panel).getByRole("group", {
      name: AASB_PLACEHOLDER_NAME,
    });

    expect(placeholder).toHaveAttribute("aria-disabled", "true");
    expect(placeholder).toHaveAttribute("data-framework-placeholder", "AASB");
    expect(placeholder).not.toHaveAttribute("href");
    expect(placeholder).not.toHaveAttribute("target");
    expect(
      within(panel).queryByRole("link", { name: AASB_PLACEHOLDER_NAME }),
    ).not.toBeInTheDocument();
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

  it("associates the complementary landmark with its visible heading", () => {
    render(<FrameworkReferencePanel />);

    const panel = screen.getByRole("complementary");
    const heading = document.getElementById("framework-views-title");

    expect(panel).toHaveAttribute("aria-labelledby", "framework-views-title");
    expect(heading).not.toBeNull();
    expect(heading).toBeVisible();
    expect(heading).not.toHaveTextContent(/^\s*$/);
  });

  it("stretches with the grid without imposing a fixed pixel height", () => {
    render(<FrameworkReferencePanel />);

    const panel = screen.getByRole("complementary");
    const fixedHeightClass = /^(?:[a-z]+:)*(?:min-|max-)?h-\[\d+(?:\.\d+)?px\]$/;

    const classTokens = panel.className.split(/\s+/);

    expect(panel).toHaveClass("self-stretch");
    expect(panel.style.height).not.toMatch(/px$/);
    expect(panel.style.minHeight).not.toMatch(/px$/);
    expect(panel.style.maxHeight).not.toMatch(/px$/);
    expect(classTokens.some((token) => fixedHeightClass.test(token))).toBe(false);
  });
});
