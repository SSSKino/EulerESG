import { describe, expect, it } from "vitest";

import { ACTIVE_FRAMEWORK_OPTIONS, isActiveFramework } from "../frameworkOptions";

describe("active framework options", () => {
  it("offers only the supported frameworks for new work", () => {
    expect(ACTIVE_FRAMEWORK_OPTIONS.map((option) => option.value)).toEqual([
      "SASB",
      "GRI",
      "CDP",
    ]);
    expect(isActiveFramework("TCFD")).toBe(false);
    expect(isActiveFramework(" cdp ")).toBe(true);
  });
});
