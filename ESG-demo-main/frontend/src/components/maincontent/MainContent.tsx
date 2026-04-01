"use client";

import React, { useState } from "react";
import { Form, message } from "antd";
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

function normStrList(v: unknown): string[] {
  if (Array.isArray(v)) return v.map((x) => String(x).trim()).filter(Boolean);
  if (v != null && String(v).trim()) return [String(v).trim()];
  return [];
}

function formatScopeLabel(values: UploadOptions, semiVals: string[], griTopics: string[]) {
  if (values.framework === "GRI") {
    return [values.griSector, ...griTopics]
      .filter(Boolean)
      .map((s) => String(s).replace(/_/g, " "))
      .join(" · ");
  }
  return semiVals.map((s) => s.replace(/_/g, " ")).join(" · ");
}

const MainContent = () => {
  const { t, lang } = useT();
  const locale = lang === "zh" ? "zh-CN" : "en-US";

  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedUploadFiles, setSelectedUploadFiles] = useState<UploadFile[]>([]);
  const [selectedIndustry, setSelectedIndustry] = useState<string>("");
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
      const now = new Date();

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

      queuedFiles.forEach((uploadFile, index) => {
        const fileExtension = uploadFile.name?.split(".").pop()?.toUpperCase() || t("upload.unknown");
        const item = {
          key: uploadFile.uid,
          name: uploadFile.name || t("upload.unknownFile"),
          size:
            typeof uploadFile.size === "number"
              ? `${(uploadFile.size / 1024).toFixed(2)} KB`
              : t("upload.unknown"),
          dateUploaded: now.toLocaleDateString(locale),
          uploadedAtMs: now.getTime() + index,
          type: fileExtension,
          status: "pending" as const,
          pages: "-",
          industry:
            isGRI
              ? (values.griSector || "").replace(/_/g, " ")
              : isCDP
                ? "CDP"
                : isTCFD
                  ? "TCFD"
                  : values.industry || "",
          semiIndustry: formatScopeLabel(values, semiVals, griTopics),
          framework: values.framework || "",
          gri_sector: values.griSector ?? "",
          gri_topic: griTopics[0] ?? "",
        };
        store.addFile(item);
      });

      setIsModalOpen(false);
      setSelectedUploadFiles([]);
      setSelectedIndustry("");
      form.resetFields();

      void message.loading(t("upload.uploadingBatch", { count: String(queuedFiles.length) }), 0);

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

          store.removeFileByKey(uploadFile.uid);
          if (response?.file_id) {
            apiService.prefetchAssessmentByFile(response.file_id, undefined);
          }
          successCount += 1;
        } catch (error: any) {
          failedCount += 1;
          store.updateFileStatus(uploadFile.uid, "failed");
          console.error("Upload error:", error);
        }
      }

      message.destroy();

      await store.loadFilesFromBackend();

      if (successCount > 0) {
        void message.success(
          t("upload.uploadBatchComplete", {
            success: String(successCount),
            failed: String(failedCount),
          })
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

  return (
    <>
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
