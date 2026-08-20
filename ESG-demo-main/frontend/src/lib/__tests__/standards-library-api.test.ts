import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiService, type StandardsLibraryCatalogResponse } from "@/lib/api";

const catalog: StandardsLibraryCatalogResponse = {
  frameworks: [
    {
      id: "sasb",
      name: "SASB",
      as_of: "Jan 2026",
      source_url: "https://example.test/sasb",
      available: true,
      scope_count: 1,
      group_label: "Industry groups",
      scope_label: "Industry",
      groups: [
        {
          id: "industries",
          label: "Industries",
          scopes: [{ id: "Hardware", label: "Hardware" }],
        },
      ],
    },
  ],
};

const jsonResponse = (payload: unknown, ok = true, status = 200) =>
  ({
    ok,
    status,
    json: vi.fn().mockResolvedValue(payload),
    text: vi.fn().mockResolvedValue(JSON.stringify(payload)),
  }) as unknown as Response;

describe("Standards Library API client", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("auth_token", "standards-token");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads and caches the authenticated catalog request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(catalog));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiService.getStandardsCatalog(true)).resolves.toEqual(catalog);
    await expect(apiService.getStandardsCatalog()).resolves.toEqual(catalog);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/standards-library/catalog");
    expect(options.method).toBe("GET");
    expect(new Headers(options.headers).get("Authorization")).toBe(
      "Bearer standards-token",
    );
  });

  it("drops a failed catalog request so retry can load fresh data", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: "temporary failure" }, false, 500))
      .mockResolvedValueOnce(jsonResponse(catalog));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiService.getStandardsCatalog(true)).rejects.toThrow("temporary failure");
    await expect(apiService.getStandardsCatalog()).resolves.toEqual(catalog);

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("encodes framework, group, and scope selections in the metrics request", async () => {
    const metrics = {
      framework: {
        id: "sasb",
        name: "SASB",
        as_of: "Jan 2026",
        source_url: "https://example.test/sasb",
        group_label: "Industry groups",
        scope_label: "Industry",
      },
      group: { id: "industries", label: "Industries" },
      scope: { id: "Hardware & Semiconductors", label: "Hardware & Semiconductors" },
      total_metrics: 1,
      metrics: [{ id: "metric-1", code: "TC-HW-1", name: "Metric one" }],
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(metrics));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await expect(
      apiService.getStandardMetrics(
        "sasb/library",
        "industry groups",
        "Hardware & Semiconductors",
        controller.signal,
      ),
    ).resolves.toEqual(metrics);

    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "/api/standards-library/sasb%2Flibrary/metrics?scope_id=Hardware+%26+Semiconductors&group_id=industry+groups",
    );
    expect(options).toMatchObject({ method: "GET", signal: controller.signal });
  });
});
