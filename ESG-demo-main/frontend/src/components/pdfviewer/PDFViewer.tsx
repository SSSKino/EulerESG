"use client";
import React, { useState, useEffect } from "react";
import { Breadcrumb, Button, Tooltip, message } from "antd";
import { useRouter } from "next/navigation";
import { useFileStore } from "@/store/useFileStore";
import type { File } from "@/store/useFileStore";
import { canCrossAnalyzeFiles } from "@/store/useFileStore";
import MainContent from "@/components/dashboard/MainContent";
import FileTable from "./FileTable";
import LoadingModal from "./LoadingModal";
import { useT } from "@/i18n/useT";
import { apiService } from "@/lib/api";

export default function PDFViewer() {
  const { t } = useT();
  const router = useRouter();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [progress, setProgress] = useState(0);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [selectedRows, setSelectedRows] = useState<File[]>([]);
  const loadFilesFromBackend = useFileStore((state) => state.loadFilesFromBackend);

  // 组件加载时从后端获取文件列表
  useEffect(() => {
    loadFilesFromBackend();
  }, [loadFilesFromBackend]);

  useEffect(() => {
    if (progress === 100 && selectedFile && selectedFile.file_id) {
      void loadFilesFromBackend();
      let url = `/dashboard/chat?file_id=${encodeURIComponent(selectedFile.file_id)}`;
      if (selectedFile.analysis_scope_key) {
        url += `&scope=${encodeURIComponent(selectedFile.analysis_scope_key)}`;
      }
      router.push(url);
    }
  }, [progress, selectedFile, loadFilesFromBackend, router]);

  const handleChatClick = (file: File) => {
    setIsModalOpen(true);
    setProgress(0);
    setSelectedFile(file);
    useFileStore.getState().setSelectedFileId(file.file_id || null);
    apiService.prefetchAssessmentByFile(file.file_id, file.analysis_scope_key, false);

    // 2秒内完成进度条
    const interval = setInterval(() => {
      setProgress((prev) => {
        if (prev >= 100) {
          clearInterval(interval);
          setIsModalOpen(false);
          return 100;
        }
        return prev + 15;
      });
    }, 200);
  };

  const crossAnalysisAllowed = canCrossAnalyzeFiles(selectedRows);
  const selectedFrameworks = [...new Set(selectedRows.map((f) => (f.framework || "").trim()).filter(Boolean))];
  const crossAnalysisDisabledReason =
    selectedRows.length >= 2 && !crossAnalysisAllowed
      ? selectedFrameworks.length > 1
        ? t("files.crossAnalysisDifferentFrameworkDisabled")
        : t("files.crossAnalysisSameFramework")
      : undefined;

  const handleCrossAnalyze = () => {
    if (selectedRows.length < 2) {
      message.info(t("files.selectAtLeastTwoReports"));
      return;
    }
    if (!crossAnalysisAllowed) {
      message.warning(t("files.crossAnalysisSameFramework"));
      return;
    }
    const ids = [...new Set(selectedRows.map((f) => f.file_id).filter(Boolean) as string[])];
    if (ids.length < 2) {
      message.info(t("files.selectAtLeastTwoReports"));
      return;
    }
    // Directly enter Cross Analysis using the already-generated per-report assessment outputs.
    router.push(`/cross-analysis?ids=${encodeURIComponent(ids.join(","))}`);
  };

  return (
    <div className="app-page w-full">
      <div className="app-content">
        <Breadcrumb
          style={{ margin: "0 0 1rem" }}
          items={[{ title: t("files.breadcrumbDashboard") }]}
          className="!text-lg"
        />

        <MainContent />
        <div className="mb-4 flex items-center justify-end">
          <Tooltip title={crossAnalysisDisabledReason} placement="top">
            <span>
              <Button
                type="primary"
                className="!rounded-full !px-5"
                disabled={selectedRows.length < 2 || !crossAnalysisAllowed}
                onClick={handleCrossAnalyze}
              >
                {t("files.crossAnalysisBeta")}
              </Button>
            </span>
          </Tooltip>
        </div>
        <FileTable
          onChatClick={handleChatClick}
          selectedRows={selectedRows}
          onSelectionChange={setSelectedRows}
        />
      </div>
      <LoadingModal
        isOpen={isModalOpen}
        progress={progress}
        onClose={() => setIsModalOpen(false)}
      />
    </div>
  );
}
