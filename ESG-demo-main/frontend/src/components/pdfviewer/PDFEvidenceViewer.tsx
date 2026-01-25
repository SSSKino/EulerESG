"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import { Button, InputNumber, Space, Typography } from "antd";
import { Minus, Plus, RotateCcw, ChevronLeft, ChevronRight } from "lucide-react";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import { getStoredAuth } from "@/lib/auth";

// pdf.js worker
pdfjs.GlobalWorkerOptions.workerSrc = "/pdfjs/pdf.worker.min.js";

const { Text } = Typography;

export type PDFEvidenceViewerProps = {
  fileUrl: string;
  initialPage?: number;
  height?: string | number;
  /**
   * Default zoom multiplier (relative to the chosen fit mode).
   * - 1.0 means "fit" (width or page)
   */
  defaultZoom?: number;
  /**
   * Fit mode:
   * - "width": fit page width (common reading)
   * - "page": fit whole page into the viewport (no scrolling by default)
   */
  fitTo?: "width" | "page";
};

type PageSize = { w: number; h: number } | null;

export default function PDFEvidenceViewer({
  fileUrl,
  initialPage = 1,
  height = "72vh",
  defaultZoom = 1.0,
  fitTo = "width",
}: PDFEvidenceViewerProps) {
  const [numPages, setNumPages] = useState<number>(0);
  const [page, setPage] = useState<number>(Math.max(1, initialPage));
  const [zoom, setZoom] = useState<number>(Math.min(2.6, Math.max(0.6, defaultZoom)));
  const [containerWidth, setContainerWidth] = useState<number>(0);
  const [containerHeight, setContainerHeight] = useState<number>(0);
  const [pageSize, setPageSize] = useState<PageSize>(null);

  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setPage(Math.max(1, initialPage));
  }, [initialPage]);

  // Pass auth header for protected PDF endpoint
  const pdfOptions = useMemo(() => {
    const auth = getStoredAuth();
    const token = auth?.token;
    if (!token) return undefined;
    return { httpHeaders: { Authorization: `Bearer ${token}` } };
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const measure = () => {
      setContainerWidth(el.clientWidth);
      setContainerHeight(el.clientHeight);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const clampPage = (p: number) => {
    if (!numPages) return Math.max(1, p);
    return Math.min(Math.max(1, p), numPages);
  };

  const zoomIn = () => setZoom((s) => Math.min(2.6, Math.round((s + 0.1) * 10) / 10));
  const zoomOut = () => setZoom((s) => Math.max(0.6, Math.round((s - 0.1) * 10) / 10));
  const resetZoom = () => setZoom(Math.min(2.6, Math.max(0.6, defaultZoom)));

  const onPageLoadSuccess = useCallback((p: any) => {
    try {
      const vp = p.getViewport({ scale: 1 });
      if (vp?.width && vp?.height) setPageSize({ w: vp.width, h: vp.height });
    } catch {
      // ignore
    }
  }, []);

  const pageScale = useMemo(() => {
    if (!containerWidth || !containerHeight || !pageSize) return undefined;

    // Gutter keeps page from touching card edges.
    const gutter = 20;
    const wScale = Math.max(0.1, (containerWidth - gutter) / pageSize.w);
    const hScale = Math.max(0.1, (containerHeight - gutter) / pageSize.h);

    const base = fitTo === "page" ? Math.min(wScale, hScale) : wScale;
    return Math.max(0.1, base) * zoom;
  }, [containerWidth, containerHeight, pageSize, fitTo, zoom]);

  const overflowMode = useMemo(() => {
    // Fit-to-page aims for "no scroll" at zoom=1; allow scroll once user zooms in.
    if (fitTo === "page") return zoom <= 1.001 ? "hidden" : "auto";
    return "auto";
  }, [fitTo, zoom]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 12,
          padding: "8px 10px",
        }}
      >
        <Space size={6} wrap>
          <Button
            size="small"
            onClick={() => setPage((p) => clampPage(p - 1))}
            icon={<ChevronLeft size={16} />}
            disabled={page <= 1}
          />
          <Button
            size="small"
            onClick={() => setPage((p) => clampPage(p + 1))}
            icon={<ChevronRight size={16} />}
            disabled={!!numPages && page >= numPages}
          />
          <Text style={{ fontSize: 12, opacity: 0.75 }}>Page</Text>
          <InputNumber
            size="small"
            min={1}
            max={numPages || 9999}
            value={page}
            onChange={(v) => setPage(clampPage(typeof v === "number" ? v : page))}
            style={{ width: 88 }}
          />
          <Text style={{ fontSize: 12, opacity: 0.65 }}>/ {numPages || "—"}</Text>
        </Space>

        <Space size={6} wrap>
          <Button size="small" onClick={zoomOut} icon={<Minus size={16} />} />
          <Text style={{ fontSize: 12, opacity: 0.75 }}>{Math.round(zoom * 100)}%</Text>
          <Button size="small" onClick={zoomIn} icon={<Plus size={16} />} />
          <Button size="small" onClick={resetZoom} icon={<RotateCcw size={16} />} />
        </Space>
      </div>

      <div
        ref={containerRef}
        style={{
          flex: 1,
          overflow: overflowMode,
          height,
          padding: 8,
          background: "rgba(255,255,255,0.7)",
          borderRadius: 12,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Document
          file={fileUrl}
          options={pdfOptions}
          onLoadSuccess={({ numPages: n }) => setNumPages(n)}
          loading={<div style={{ padding: 16, opacity: 0.7 }}>Loading…</div>}
          error={<div style={{ padding: 16 }}>Failed to load PDF.</div>}
        >
          <Page
            pageNumber={clampPage(page)}
            scale={pageScale}
            onLoadSuccess={onPageLoadSuccess}
            renderTextLayer
            renderAnnotationLayer
          />
        </Document>
      </div>
    </div>
  );
}
