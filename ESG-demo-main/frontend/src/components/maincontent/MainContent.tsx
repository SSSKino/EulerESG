"use client";

import React, { useState } from "react";
import { Form, message, Progress } from "antd";
import type { UploadFile } from "antd/es/upload/interface";
import { useFileStore } from "@/store/useFileStore";
import { apiService } from "@/lib/api";
import UploadArea from "./UploadArea";
import UploadOptionsModal from "./UploadOptionsModal";
import { useT } from "@/i18n/useT";

interface UploadOptions {
  category: string;
  description: string;
  tags: string[];
  industry: string;
  semiIndustry: string | string[];
  framework: string;
  griSector?: string;
  griTopics?: string[];
}

type ActiveReportJob = {
  jobId: string;
  fileName: string;
  status: string;
  stage?: string;
  message?: string;
  progress?: number;
  error?: string | null;
};

function normStrList(v: unknown): string[] {
  if (Array.isArray(v)) return v.map((x) => String(x).trim()).filter(Boolean);
  if (v != null && String(v).trim()) return [String(v).trim()];
  return [];
}

function userFacingJobMessage(event: any): string {
  const status = String(event?.status || "").toLowerCase();
  const stage = String(event?.stage || "").toLowerCase();

  if (status === "failed" || stage === "failed") {
    return "Processing failed. Please try again.";
  }
  if (status === "success" || status === "partial_success" || stage === "completed") {
    return "Processing completed.";
  }

  const stageMessages: Record<string, string> = {
    queued: "Waiting to start.",
    started: "Starting document processing.",
    saving: "Uploading document.",
    file_saved: "Document uploaded.",
    pdf_processing: "Reading document content.",
    ocr_start: "Reading document content.",
    ocr_queued: "Reading document content.",
    ocr_batch_processing: "Reading document content.",
    ocr_merging: "Organizing extracted content.",
    pdf_processed: "Document content extracted.",
    summary_ready: "Preparing summary.",
    assessment_start: "Starting disclosure assessment.",
    assessment_scope: "Analyzing disclosure information.",
    assessment_scope_done: "Disclosure assessment updated.",
    completed: "Processing completed.",
  };

  return stageMessages[stage] || "Processing document.";
}

function formatReportJobMessage(fileName: string, event: any): string {
  const progress = typeof event?.progress === "number" ? ` ${Math.round(event.progress)}%` : "";
  return `${fileName}: ${userFacingJobMessage(event)}${progress}`;
}

const MainContent = () => {
  const { t } = useT();

  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedUploadFiles, setSelectedUploadFiles] = useState<UploadFile[]>([]);
  const [selectedIndustry, setSelectedIndustry] = useState<string>("");
  const [activeReportJobs, setActiveReportJobs] = useState<Record<string, ActiveReportJob>>({});
  const [form] = Form.useForm<UploadOptions>();

  const handleBeforeUpload = (files: UploadFile[]) => {
    if (!files.length) return;
    setSelectedUploadFiles(files);
    setIsModalOpen(true);
  };

  const handleModalOk = async () => {
    try {
      await form.validateFields();
      const queuedFiles = [...selectedUploadFiles];
      const values = form.getFieldsValue();

      if (!queuedFiles.length) {
        setIsModalOpen(false);
        form.resetFields();
        return;
      }

      const isGRI = values.framework === "GRI";
      const isCDP = values.framework === "CDP";
      const isTCFD = values.framework === "TCFD";
      const griTopics = normStrList(values.griTopics);
      const semiVals = normStrList(values.semiIndustry);
      const scopeSlugs =
        isGRI && griTopics.length > 1
          ? JSON.stringify(griTopics)
          : !isGRI && semiVals.length > 1
            ? JSON.stringify(semiVals)
            : undefined;
      const store = useFileStore.getState();

      setIsModalOpen(false);
      setSelectedUploadFiles([]);
      setSelectedIndustry("");
      form.resetFields();

      const batchMessageKey = `upload-batch-${Date.now()}`;
      void message.open({
        key: batchMessageKey,
        type: "loading",
        content: t("upload.uploadingBatch", { count: String(queuedFiles.length) }),
        duration: 0,
      });

      let successCount = 0;
      let failedCount = 0;

      for (const uploadFile of queuedFiles) {
        try {
          const file = uploadFile.originFileObj ?? uploadFile;
          if (!(file instanceof File)) {
            throw new Error(t("upload.invalidFile"));
          }

          const response = await apiService.uploadReport(
            file,
            values.framework,
            isCDP ? "CDP" : isTCFD ? "TCFD" : values.industry,
            isGRI ? "" : semiVals[0] ?? "",
            values.griSector,
            griTopics[0] ?? "",
            scopeSlugs
          );

          if (response?.file_id) {
            void store.loadFilesFromBackend();
          }

          if (response?.job_id) {
            const jobMessageKey = `report-job-${response.job_id}`;
            void message.open({
              key: jobMessageKey,
              type: "loading",
              content: `${file.name}: ${response.message || "Processing started."}`,
              duration: 0,
            });
            setActiveReportJobs((prev) => ({
              ...prev,
              [response.job_id!]: {
                jobId: response.job_id!,
                fileName: file.name,
                status: "processing",
                stage: "queued",
                message: response.message || "Processing started.",
                progress: 0,
              },
            }));
            apiService.subscribeReportJob(response.job_id, {
              onEvent: (event) => {
                setActiveReportJobs((prev) => ({
                  ...prev,
                  [response.job_id!]: {
                    jobId: response.job_id!,
                    fileName: file.name,
                    status: event.status,
                    stage: event.stage,
                    message: userFacingJobMessage(event),
                    progress: event.progress,
                    error: event.error,
                  },
                }));
                void message.open({
                  key: jobMessageKey,
                  type: "loading",
                  content: formatReportJobMessage(file.name, event),
                  duration: 0,
                });
              },
              onDone: async (event) => {
                message.destroy(jobMessageKey);
                setActiveReportJobs((prev) => ({
                  ...prev,
                  [response.job_id!]: {
                    jobId: response.job_id!,
                    fileName: file.name,
                    status: event.status,
                    stage: event.stage || "completed",
                    message: userFacingJobMessage(event),
                    progress: 100,
                  },
                }));
                window.setTimeout(() => {
                  setActiveReportJobs((prev) => {
                    const next = { ...prev };
                    delete next[response.job_id!];
                    return next;
                  });
                }, 8000);
                void message.success(`${file.name}: ${event.message || "Processing completed."}`);
                if (response.file_id) {
                  apiService.invalidateAssessmentByFileCache(response.file_id);
                  apiService.prefetchAssessmentByFile(response.file_id, undefined, true);
                }
                await store.loadFilesFromBackend();
              },
              onError: async (event) => {
                message.destroy(jobMessageKey);
                const errorText = "Processing failed. Please try again.";
                setActiveReportJobs((prev) => ({
                  ...prev,
                  [response.job_id!]: {
                    ...(prev[response.job_id!] || { jobId: response.job_id!, fileName: file.name }),
                    status: "failed",
                    stage: "failed",
                    message: errorText,
                    error: errorText,
                  },
                }));
                void message.error(`${file.name}: ${errorText}`);
                await store.loadFilesFromBackend();
              },
            });
          }

          successCount += 1;
        } catch (error: any) {
          failedCount += 1;
          console.error("Upload error:", error);
        }
      }

      message.destroy(batchMessageKey);

      await store.loadFilesFromBackend();

      if (successCount > 0) {
        void message.success(
          `Queued ${successCount} file(s) for background processing${failedCount ? `, ${failedCount} failed to upload` : ""}.`
        );
      }
      if (successCount === 0 && failedCount > 0) {
        void message.error(t("upload.uploadFailed", { error: t("upload.batchUploadFailed") }));
      }
    } catch (error) {
      console.error("Validation failed:", error);
      void message.error(t("upload.fillRequiredFields"));
    }
  };

  const handleModalCancel = () => {
    setIsModalOpen(false);
    setSelectedUploadFiles([]);
    setSelectedIndustry("");
    form.resetFields();
  };

  const activeJobList = Object.values(activeReportJobs);

  return (
    <>
      {activeJobList.length > 0 && (
        <div className="fixed bottom-4 right-4 z-[9999] w-[420px] max-w-[calc(100vw-2rem)] space-y-3">
          {activeJobList.map((job) => {
            const percent = Math.max(0, Math.min(100, Math.round(Number(job.progress ?? 0))));
            return (
              <div key={job.jobId} className="rounded-xl border border-slate-200 bg-white/95 p-4 shadow-xl backdrop-blur">
                <div className="mb-2 flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-slate-900">{job.fileName}</div>
                    <div className="mt-1 text-xs text-slate-600">{job.message || job.stage || "Processing"}</div>
                  </div>
                  <div className="shrink-0 text-xs font-medium text-slate-700">{percent}%</div>
                </div>
                <Progress percent={percent} size="small" status={job.status === "failed" ? "exception" : job.status === "success" || job.status === "partial_success" ? "success" : "active"} />
              </div>
            );
          })}
        </div>
      )}
      <UploadArea onBeforeUpload={handleBeforeUpload} />
      <UploadOptionsModal
        isOpen={isModalOpen}
        selectedUploadFiles={selectedUploadFiles}
        selectedIndustry={selectedIndustry}
        onOk={handleModalOk}
        onCancel={handleModalCancel}
        onIndustryChange={setSelectedIndustry}
        form={form}
      />
    </>
  );
};

export default MainContent;
