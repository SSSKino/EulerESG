"use client";
import { useMemo, useState, useEffect, useRef } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import { getStoredAuth } from "@/lib/auth";
import { useT } from "@/i18n/useT";

// 设置 PDF.js worker
pdfjs.GlobalWorkerOptions.workerSrc = "/pdfjs/pdf.worker.min.js";

interface PDFChatViewerProps {
  fileUrl: string;
  targetPage?: number;
  // Used to force a new navigation request even if targetPage does not change.
  targetPageNonce?: number;
}

const PDFChatViewer: React.FC<PDFChatViewerProps> = ({
  fileUrl,
  targetPage,
  targetPageNonce,
}) => {
  const { t } = useT();
  const [numPages, setNumPages] = useState<number>();
  const [pageNumber, setPageNumber] = useState<number>(1);
  const [containerWidth, setContainerWidth] = useState<number>(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<{ [key: number]: HTMLDivElement | null }>({});
  const pendingTargetPage = useRef<number | null>(null);

  // react-pdf(pdf.js) 拉取 PDF 时不会自动带上我们业务的 Authorization header，
  // 如果后端 /api/files/{id}/pdf 受保护（依赖 get_current_user），就会 403。
  // 这里显式把 token 传给 pdf.js。
  const pdfOptions = useMemo(() => {
    const auth = getStoredAuth();
    const token = auth?.token;
    if (!token) return undefined;
    return { httpHeaders: { Authorization: `Bearer ${token}` } };
  }, []);

  const scrollToPage = (page: number) => {
    const container = containerRef.current;
    const el = pageRefs.current[page];
    if (!container || !el) return false;

    // Compute scroll position relative to the scroll container (more reliable
    // than offsetTop when layout/positioning changes).
    const containerRect = container.getBoundingClientRect();
    const elRect = el.getBoundingClientRect();
    const top = elRect.top - containerRect.top + container.scrollTop;

    container.scrollTo({
      top: Math.max(0, top - 16),
      behavior: "smooth",
    });
    return true;
  };

  useEffect(() => {
    if (!targetPage) return;

    // Clamp to valid range once numPages is known.
    const clamped = numPages ? Math.min(Math.max(1, targetPage), numPages) : targetPage;
    pendingTargetPage.current = clamped;
    setPageNumber(clamped);

    let rafId: number | null = null;
    let tries = 0;
    const MAX_TRIES = 120; // ~2s at 60fps

    const attempt = () => {
      const page = pendingTargetPage.current;
      if (page === null) return;

      if (scrollToPage(page)) {
        pendingTargetPage.current = null;
        return;
      }

      tries += 1;
      if (tries < MAX_TRIES) {
        rafId = window.requestAnimationFrame(attempt);
      }
    };

    attempt();

    return () => {
      if (rafId !== null) window.cancelAnimationFrame(rafId);
    };
  }, [targetPage, targetPageNonce, numPages]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const measure = () => {
      // Account for horizontal padding (px-2 => 0.5rem * 2 = 16px) so the Page fits without overflow.
      const w = el.clientWidth ? Math.max(0, el.clientWidth - 16) : 0;
      setContainerWidth(w);
    };

    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  function onDocumentLoadSuccess({ numPages }: { numPages: number }): void {
    setNumPages(numPages);
  }

  return (
    <div className="flex flex-col w-full h-full">
      <div
        ref={containerRef}
        className="overflow-y-auto h-full w-full px-2">
        <Document
          file={fileUrl}
          options={pdfOptions}
          onLoadSuccess={onDocumentLoadSuccess}>
          {Array.from({ length: numPages || 0 }, (_el, index) => (
            <div
              key={`page_${index + 1}`}
              ref={(el) => {
                pageRefs.current[index + 1] = el;
              }}>
              <Page
                pageNumber={index + 1}
                width={containerWidth > 0 ? containerWidth : undefined}
                className="mb-4"
                renderTextLayer={true}
                renderAnnotationLayer={true}
              />
            </div>
          ))}
        </Document>
      </div>
      <p className="mt-2 text-sm text-gray-600">
        {t("common.page")} {pageNumber} / {numPages || "—"}
      </p>
    </div>
  );
};

export default PDFChatViewer;
