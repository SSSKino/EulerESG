"use client";
import React, { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useFileStore } from "@/store/useFileStore";
import type { File } from "@/store/useFileStore";
import MainContent from "../maincontent/MainContent";
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
    loadFilesFromBackend({ showLoading: true });
  }, [loadFilesFromBackend]);

  useEffect(() => {
    if (progress === 100 && selectedFile && selectedFile.file_id) {
      void loadFilesFromBackend({ showLoading: false });
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

  return (
    <div className="w-full flex flex-col justify-start items-center mx-auto pt-1 bg-gray-50 min-h-screen">
      <div className="w-[95%]">
        <MainContent />
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
