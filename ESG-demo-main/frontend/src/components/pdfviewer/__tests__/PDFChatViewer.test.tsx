import type { ReactNode } from "react";

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PDFChatViewer from "../PDFChatViewer";
import { MockIntersectionObserver } from "@/test/setup";

vi.mock("@/lib/auth", () => ({
  getStoredAuth: () => null,
}));

vi.mock("@/i18n/useT", () => ({
  useT: () => ({
    t: (key: string) =>
      ({
        "common.error": "Failed to load PDF.",
        "common.failedToLoadPdf": "Failed to load PDF.",
        "common.loading": "Loading PDF...",
        "common.page": "Page",
      })[key] ?? key,
  }),
}));

vi.mock("react-pdf", async () => {
  const React = await import("react");

  type DocumentProps = {
    children?: ReactNode;
    error?: ReactNode;
    file?: unknown;
    loading?: ReactNode;
    onLoadError?: (error: Error) => void;
    onLoadSuccess?: (document: { numPages: number }) => void;
  };

  type PageProps = {
    onLoadSuccess?: (page: {
      getViewport: (options?: { scale?: number }) => {
        height: number;
        width: number;
      };
      height: number;
      width: number;
    }) => void;
    onRenderSuccess?: () => void;
    pageNumber: number;
    scale?: number;
    width?: number;
  };

  const sourceName = (file: unknown): string => {
    if (typeof file === "string") return file;
    if (file && typeof file === "object" && "url" in file) {
      return String((file as { url: unknown }).url);
    }
    return "";
  };

  const mockPdfPage = (pageNumber: number) => ({
    getViewport: ({ scale = 1 }: { scale?: number } = {}) => ({
      height: (pageNumber % 5 === 0 ? 612 : 792) * scale,
      width: (pageNumber % 5 === 0 ? 792 : 612) * scale,
    }),
  });

  const Document = ({
    children,
    error,
    file,
    loading,
    onLoadError,
    onLoadSuccess,
  }: DocumentProps) => {
    const source = sourceName(file);
    const isLoading = source.includes("loading");
    const isError = source.includes("error");

    React.useEffect(() => {
      let cancelled = false;
      if (isLoading) return;
      if (isError) {
        queueMicrotask(() => {
          if (!cancelled) onLoadError?.(new Error("mock PDF load failure"));
        });
      } else {
        const numPages = source.includes("ten-pages") ? 10 : 116;
        const pdfDocument = {
          getPage: vi.fn(async (pageNumber: number) => mockPdfPage(pageNumber)),
          numPages,
        };
        queueMicrotask(() => {
          if (!cancelled) onLoadSuccess?.(pdfDocument);
        });
      }

      return () => {
        cancelled = true;
      };
      // The source is the mock's document identity. Callback identities from the
      // viewer are intentionally excluded to match pdf.js firing once per load.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [source]);

    if (isLoading) {
      return (
        <div data-testid="mock-pdf-loading">
          {loading ?? <div role="status">Loading PDF...</div>}
        </div>
      );
    }

    if (isError) {
      return (
        <div data-testid="mock-pdf-error">
          {error ?? <div role="alert">Failed to load PDF.</div>}
        </div>
      );
    }

    return (
      <div data-file={source} data-testid="mock-pdf-document">
        {children}
      </div>
    );
  };

  const Page = ({
    onLoadSuccess,
    onRenderSuccess,
    pageNumber,
    scale,
    width,
  }: PageProps) => {
    React.useEffect(() => {
      const baseWidth = 612;
      const baseHeight = 792;
      onLoadSuccess?.({
        getViewport: ({ scale: viewportScale = 1 } = {}) => ({
          height: baseHeight * viewportScale,
          width: baseWidth * viewportScale,
        }),
        height: baseHeight,
        width: baseWidth,
      });
      onRenderSuccess?.();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [pageNumber]);

    return (
      <div
        className="react-pdf__Page"
        data-mock-page-number={pageNumber}
        data-mock-scale={scale ?? ""}
        data-mock-width={width ?? ""}>
        Mock page {pageNumber}
      </div>
    );
  };

  return {
    Document,
    Page,
    pdfjs: {
      GlobalWorkerOptions: {},
    },
  };
});

const renderedPages = (): HTMLElement[] =>
  Array.from(document.querySelectorAll<HTMLElement>(".react-pdf__Page"));

const renderedPageNumbers = (): number[] =>
  renderedPages().map((page) => Number(page.dataset.mockPageNumber));

const pageSlots = (): HTMLElement[] =>
  Array.from(document.querySelectorAll<HTMLElement>("[data-page-number]"));

const pageSlot = (pageNumber: number): HTMLElement | null =>
  document.querySelector<HTMLElement>(`[data-page-number="${pageNumber}"]`);

const expectDocumentReady = async (numPages: number): Promise<void> => {
  await waitFor(() => {
    expect(pageSlots()).toHaveLength(numPages);
  });
};

const expectCurrentPage = async (
  pageNumber: number,
  numPages: number,
): Promise<void> => {
  await waitFor(() => {
    expect(
      screen.getByText(
        new RegExp(`Page\\s+${pageNumber}\\s*\\/\\s*${numPages}`),
      ),
    ).toBeInTheDocument();
  });
};

describe("PDFChatViewer continuous rendering", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("keeps a 116-page document continuous while mounting no more than nine heavy pages", async () => {
    render(<PDFChatViewer fileUrl="report-116.pdf" />);

    await expectDocumentReady(116);

    expect(screen.getByRole("region")).toBeInTheDocument();
    expect(screen.getByTestId("pdf-scroll-container")).toBeInTheDocument();
    expect(renderedPages().length).toBeGreaterThan(0);
    expect(renderedPages().length).toBeLessThanOrEqual(9);
    expect(renderedPageNumbers()).toContain(1);

    for (const renderedPage of renderedPages()) {
      const renderedBoundary = renderedPage.closest<HTMLElement>(
        '[data-rendered="true"]',
      );
      expect(renderedBoundary).not.toBeNull();
    }
  });

  it("lazily swaps rendered pages when a later page enters the viewport", async () => {
    render(<PDFChatViewer fileUrl="report-116.pdf" />);
    await expectDocumentReady(116);

    const thirtiethSlot = pageSlot(30);
    expect(thirtiethSlot).not.toBeNull();

    await waitFor(() => {
      expect(
        MockIntersectionObserver.instances.some((observer) =>
          observer.targets.has(thirtiethSlot!),
        ),
      ).toBe(true);
    });

    let wasObserved = false;
    act(() => {
      wasObserved = MockIntersectionObserver.trigger(thirtiethSlot!, {
        intersectionRatio: 1,
        isIntersecting: true,
      });
    });

    expect(wasObserved).toBe(true);
    await waitFor(() => {
      expect(renderedPageNumbers()).toContain(30);
    });
    expect(renderedPages().length).toBeLessThanOrEqual(9);
  });

  it("clamps target pages and repeats a same-page jump when the nonce changes", async () => {
    const view = render(
      <PDFChatViewer
        fileUrl="report-116.pdf"
        targetPage={999}
        targetPageNonce={1}
      />,
    );

    await expectDocumentReady(116);
    await expectCurrentPage(116, 116);
    await waitFor(() => {
      expect(renderedPageNumbers()).toContain(116);
    });

    const scrollTo = vi.mocked(HTMLElement.prototype.scrollTo);
    await waitFor(() => {
      expect(scrollTo).toHaveBeenCalled();
    });
    const firstJumpCallCount = scrollTo.mock.calls.length;

    view.rerender(
      <PDFChatViewer
        fileUrl="report-116.pdf"
        targetPage={999}
        targetPageNonce={2}
      />,
    );

    await waitFor(() => {
      expect(scrollTo.mock.calls.length).toBeGreaterThan(firstJumpCallCount);
    });

    view.rerender(
      <PDFChatViewer
        fileUrl="report-116.pdf"
        targetPage={0}
        targetPageNonce={3}
      />,
    );
    await expectCurrentPage(1, 116);
    await waitFor(() => {
      expect(renderedPageNumbers()).toContain(1);
    });
  });

  it("commits direct page input on Enter and clamps it to the document", async () => {
    render(<PDFChatViewer fileUrl="report-116.pdf" />);
    await expectDocumentReady(116);

    const pageInput = screen.getByRole("spinbutton", { name: "Page number" });
    fireEvent.focus(pageInput);
    fireEvent.change(pageInput, { target: { value: "48" } });
    fireEvent.keyDown(pageInput, { key: "Enter" });

    await expectCurrentPage(48, 116);
    await waitFor(() => {
      expect(renderedPageNumbers()).toContain(48);
    });

    const updatedPageInput = screen.getByRole("spinbutton", { name: "Page number" });
    fireEvent.focus(updatedPageInput);
    fireEvent.change(updatedPageInput, { target: { value: "999" } });
    fireEvent.blur(updatedPageInput);
    await expectCurrentPage(116, 116);
  });

  it("zooms in fixed steps, clamps both bounds, and resets to 100 percent", async () => {
    render(<PDFChatViewer fileUrl="report-116.pdf" />);
    await expectDocumentReady(116);

    const zoomIn = screen.getByRole("button", { name: "Zoom in" });
    const zoomOut = screen.getByRole("button", { name: "Zoom out" });
    const resetZoom = screen.getByRole("button", { name: "Reset zoom" });

    expect(screen.getByText("100%")).toBeInTheDocument();

    fireEvent.click(zoomIn);
    expect(screen.getByText("110%")).toBeInTheDocument();

    for (let index = 0; index < 30; index += 1) {
      fireEvent.click(zoomIn);
    }
    expect(screen.getByText("260%")).toBeInTheDocument();
    expect(zoomIn).toBeDisabled();

    fireEvent.click(resetZoom);
    expect(screen.getByText("100%")).toBeInTheDocument();

    fireEvent.click(zoomOut);
    expect(screen.getByText("90%")).toBeInTheDocument();

    for (let index = 0; index < 30; index += 1) {
      fireEvent.click(zoomOut);
    }
    expect(screen.getByText("60%")).toBeInTheDocument();
    expect(zoomOut).toBeDisabled();
    expect(renderedPages().length).toBeLessThanOrEqual(9);
  });

  it("uses one Ctrl-wheel zoom step inside the scroll container", async () => {
    render(<PDFChatViewer fileUrl="report-116.pdf" />);
    await expectDocumentReady(116);

    const scrollContainer = screen.getByTestId("pdf-scroll-container");
    fireEvent.wheel(scrollContainer, { ctrlKey: true, deltaY: -100 });
    expect(screen.getByText("110%")).toBeInTheDocument();

    fireEvent.wheel(scrollContainer, { ctrlKey: true, deltaY: 100 });
    expect(screen.getByText("100%")).toBeInTheDocument();
  });

  it("drops the previous virtual window and page state when the file changes", async () => {
    const view = render(
      <PDFChatViewer
        fileUrl="first-report.pdf"
        targetPage={77}
        targetPageNonce={1}
      />,
    );
    await expectDocumentReady(116);
    await expectCurrentPage(77, 116);
    await waitFor(() => {
      expect(renderedPageNumbers()).toContain(77);
    });

    view.rerender(<PDFChatViewer fileUrl="ten-pages.pdf" />);

    await expectDocumentReady(10);
    await expectCurrentPage(1, 10);
    expect(renderedPageNumbers()).not.toContain(77);
    expect(renderedPageNumbers()).toContain(1);
    expect(renderedPages().length).toBeLessThanOrEqual(9);
  });

  it("shows loading and error fallbacks and can recover with another file", async () => {
    const view = render(<PDFChatViewer fileUrl="loading.pdf" />);

    expect(screen.getByRole("status")).toHaveTextContent(/loading pdf/i);
    expect(renderedPages()).toHaveLength(0);

    view.rerender(<PDFChatViewer fileUrl="error.pdf" />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/failed to load pdf/i);
    });
    expect(renderedPages()).toHaveLength(0);

    view.rerender(<PDFChatViewer fileUrl="ten-pages.pdf" />);
    await expectDocumentReady(10);
    await expectCurrentPage(1, 10);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
