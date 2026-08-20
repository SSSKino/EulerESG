import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import StandardsLibraryPage from "../page";

vi.mock("@/i18n/useT", () => ({
  useT: () => ({
    lang: "en",
    t: (key: string) => key,
  }),
}));

const STANDARD_LIBRARY_LINKS = [
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

describe("StandardsLibraryPage", () => {
  it("renders the dedicated Standards Library page with four exact safe links", () => {
    render(<StandardsLibraryPage />);

    const main = screen.getByRole("main");
    expect(main).toBeVisible();
    expect(screen.getByRole("heading", { name: "Standards Library", level: 1 })).toBeVisible();
    expect(screen.getAllByRole("link")).toHaveLength(STANDARD_LIBRARY_LINKS.length);

    for (const reference of STANDARD_LIBRARY_LINKS) {
      const link = screen.getByRole("link", { name: reference.name });
      const relTokens = (link.getAttribute("rel") ?? "").split(/\s+/);

      expect(link).toHaveAttribute("href", reference.href);
      expect(link).toHaveAttribute("target", "_blank");
      expect(relTokens).toEqual(expect.arrayContaining(["noopener", "noreferrer"]));
      link.focus();
      expect(link).toHaveFocus();
    }
    expect(screen.queryByText("TCFD")).not.toBeInTheDocument();
  });
});
