"use client";
import React, { useState, useEffect } from "react";
import { Breadcrumb, Button, message } from "antd";
import { useRouter } from "next/navigation";
import { useFileStore } from "@/store/useFileStore";
import type { File } from "@/store/useFileStore";
import MainContent from "../maincontent/MainContent";
import FileTable from "./FileTable";
import LoadingModal from "./LoadingModal";

export default function PDFViewer() {
  const router = useRouter();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [progress, setProgress] = useState(0);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [showAnalysisModal, setShowAnalysisModal] = useState(false);
  const [analysisFile, setAnalysisFile] = useState<File | null>(null);
  const [selectedRows, setSelectedRows] = useState<File[]>([]);
  const updateFileStatus = useFileStore((state) => state.updateFileStatus);
  const loadFilesFromBackend = useFileStore((state) => state.loadFilesFromBackend);
  const loading = useFileStore((state) => state.loading);

  // 组件加载时从后端获取文件列表
  useEffect(() => {
    loadFilesFromBackend();
  }, [loadFilesFromBackend]);

  useEffect(() => {
    if (progress === 100 && selectedFile && selectedFile.file_id) {
      updateFileStatus(selectedFile.file_id, "ready");
      router.push("/dashboard/chat");
    }
  }, [progress, selectedFile, updateFileStatus, router]);

  const handleChatClick = (file: File) => {
    setIsModalOpen(true);
    setProgress(0);
    setSelectedFile(file);
    useFileStore.getState().setSelectedFileId(file.file_id || null);

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

  const handleAnalysisClick = (file: File) => {
    setAnalysisFile(file);
    setShowAnalysisModal(true);
  };

  const handleCrossAnalyze = () => {
    if (selectedRows.length < 2) {
      message.info("Please select at least two reports to compare");
      return;
    }
    const ids = selectedRows
      .map((f) => f.file_id || f.key)
      .filter(Boolean);
    if (ids.length < 2) {
      message.info("Please select at least two reports to compare");
      return;
    }
    router.push(`/cross-analysis/environment?ids=${encodeURIComponent(ids.join(","))}`);
  };

  return (
    <div className="w-full flex flex-col justify-start items-center mx-auto pt-1 bg-gray-50 min-h-screen">
      <div className="w-[95%]">
        <Breadcrumb
          style={{ margin: 20 }}
          items={[{ title: "Files" }, { title: "All Files" }]}
          className="mb-2 !text-lg"
        />

        <MainContent />
        <div className="flex items-center justify-end mb-4">
          <Button
            type="primary"
            disabled={selectedRows.length < 2}
            onClick={handleCrossAnalyze}
          >
            Cross Analysis (beta)
          </Button>
        </div>
        <FileTable
          onChatClick={handleChatClick}
          onAnalysisClick={handleAnalysisClick}
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
