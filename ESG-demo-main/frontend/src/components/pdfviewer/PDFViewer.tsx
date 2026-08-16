"use client";
import React, { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useFileStore } from "@/store/useFileStore";
import type { File, ReportCatalogMode } from "@/store/useFileStore";
import MainContent from "../maincontent/MainContent";
import FileTable from "./FileTable";
import { apiService } from "@/lib/api";

export default function PDFViewer() {
  const router = useRouter();
  const [selectedRows, setSelectedRows] = useState<File[]>([]);
  const [reportCatalogMode, setReportCatalogMode] = useState<ReportCatalogMode>("single");
  const loadFilesFromBackend = useFileStore((state) => state.loadFilesFromBackend);

  // 组件加载时从后端获取文件列表
  useEffect(() => {
    loadFilesFromBackend({ showLoading: true });
  }, [loadFilesFromBackend]);

  useEffect(() => {
    router.prefetch("/dashboard/chat");
  }, [router]);

  const handleChatClick = (file: File) => {
    if (!file.file_id) return;
    useFileStore.getState().setSelectedFileId(file.file_id || null);
    apiService.prefetchAssessmentByFile(
      file.file_id,
      file.analysis_scope_key,
      false,
      true,
    );

    let url = `/dashboard/chat?file_id=${encodeURIComponent(file.file_id)}`;
    if (file.analysis_scope_key) {
      url += `&scope=${encodeURIComponent(file.analysis_scope_key)}`;
    }
    router.push(url);
  };

  const handleReportCatalogModeChange = (mode: ReportCatalogMode) => {
    setReportCatalogMode(mode);
    setSelectedRows([]);
  };

  return (
    <div className="w-full flex flex-col justify-start items-center mx-auto pt-1 bg-gray-50 min-h-screen">
      <div className="w-[95%]">
        <MainContent
          uploadMode={reportCatalogMode}
          onUploadModeChange={handleReportCatalogModeChange}
        />
        <FileTable
          onChatClick={handleChatClick}
          selectedRows={selectedRows}
          onSelectionChange={setSelectedRows}
          reportCatalogMode={reportCatalogMode}
        />
      </div>
    </div>
  );
}
