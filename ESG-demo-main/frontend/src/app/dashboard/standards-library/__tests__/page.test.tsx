import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import StandardsLibraryPage from "../page";

vi.mock("@/components/maincontent/FrameworkReferencePanel", () => ({
  default: () => (
    <section aria-label="Standards Library" data-testid="framework-reference-panel">
      Standards Library panel
    </section>
  ),
}));

describe("StandardsLibraryPage", () => {
  it("places the interactive Standards Library panel in the page's main content", () => {
    render(<StandardsLibraryPage />);

    const main = screen.getByRole("main");
    const panel = within(main).getByRole("region", { name: "Standards Library" });

    expect(main).toBeVisible();
    expect(panel).toBeVisible();
    expect(panel).toHaveAttribute("data-testid", "framework-reference-panel");
  });
});
