"use client";
import React, { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  buildComplianceAnalysisHref,
  useFileStore,
} from "@/store/useFileStore";
import type { File, ReportCatalogMode } from "@/store/useFileStore";
import MainContent from "../maincontent/MainContent";
import FileTable from "./FileTable";
import { apiService } from "@/lib/api";
import { useEnsureReportFiles } from "@/hooks/useEnsureReportFiles";
import { warmAppRoute } from "@/lib/routeWarmup";

const REPORT_CATALOG_MODE: ReportCatalogMode = "single";

const warmPDFPreviewAssets = () => {
  void import("./PDFChatViewer").catch(() => undefined);
  if (document.querySelector('link[data-pdf-worker-prefetch="true"]')) return;
  const workerPrefetch = document.createElement("link");
  workerPrefetch.rel = "prefetch";
  workerPrefetch.as = "script";
  workerPrefetch.href = "/pdfjs/pdf.worker.min.js";
  workerPrefetch.dataset.pdfWorkerPrefetch = "true";
  document.head.appendChild(workerPrefetch);
};

export default function PDFViewer() {
  const router = useRouter();
  const [selectedRows, setSelectedRows] = useState<File[]>([]);
  useEnsureReportFiles();

  useEffect(() => {
    const timer = window.setTimeout(() => {
      warmAppRoute(router, "/dashboard/chat");
      if (process.env.NODE_ENV !== "test") {
        void import("./ChatView").catch(() => undefined);
        // The PDF.js worker is a separate asset and otherwise starts loading
        // only after both dynamic component chunks have executed.
        warmPDFPreviewAssets();
      }
    }, 400);
    return () => window.clearTimeout(timer);
  }, [router]);

  const handleChatClick = useCallback((file: File) => {
    if (!file.file_id) return;
    useFileStore.getState().setComplianceSelection(
      file.file_id,
      file.analysis_scope_key,
    );
    apiService.prefetchAssessmentByFile(
      file.file_id,
      file.analysis_scope_key,
      false,
      true,
    );
    if (process.env.NODE_ENV !== "test") {
      warmPDFPreviewAssets();
    }

    router.push(
      buildComplianceAnalysisHref(file.file_id, file.analysis_scope_key),
    );
  }, [router]);

  return (
    <div className="w-full flex flex-col justify-start items-center mx-auto pt-1 bg-gray-50 min-h-screen">
      <div className="w-[95%]">
        <MainContent uploadMode={REPORT_CATALOG_MODE} />
        <FileTable
          onChatClick={handleChatClick}
          selectedRows={selectedRows}
          onSelectionChange={setSelectedRows}
          reportCatalogMode={REPORT_CATALOG_MODE}
        />
      </div>
    </div>
  );
}
