"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import FileTable from "@/components/pdfviewer/FileTable";
import { useT } from "@/i18n/useT";
import { apiService } from "@/lib/api";
import { useFileStore } from "@/store/useFileStore";
import type { File } from "@/store/useFileStore";
import { useEnsureReportFiles } from "@/hooks/useEnsureReportFiles";

export default function FavouriteReportsPage() {
  const router = useRouter();
  const { lang } = useT();
  const [selectedRows, setSelectedRows] = useState<File[]>([]);
  useEnsureReportFiles();

  useEffect(() => {
    router.prefetch("/dashboard/chat");
  }, [router]);

  const openAnalysis = useCallback((file: File) => {
    if (!file.file_id) return;
    useFileStore.getState().setSelectedFileId(file.file_id);
    apiService.prefetchAssessmentByFile(
      file.file_id,
      file.analysis_scope_key,
      false,
      true,
    );

    const scope = file.analysis_scope_key
      ? `&scope=${encodeURIComponent(file.analysis_scope_key)}`
      : "";
    router.push(
      `/dashboard/chat?file_id=${encodeURIComponent(file.file_id)}${scope}`,
    );
  }, [router]);

  return (
    <main className="min-h-screen w-full bg-gray-50 pt-1">
      <div className="mx-auto w-[95%]">
        <FileTable
          onChatClick={openAnalysis}
          selectedRows={selectedRows}
          onSelectionChange={setSelectedRows}
          reportCatalogMode="single"
          favouritesOnly
          title={lang === "zh" ? "收藏报告" : "Favourite reports"}
          emptyText={
            lang === "zh"
              ? "暂无收藏报告。可在主页报告右侧的更多操作中添加收藏。"
              : "No favourite reports yet. Add one from a report's More actions menu on the homepage."
          }
        />
      </div>
    </main>
  );
}
