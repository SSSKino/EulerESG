"use client";
import PDFViewer from "@/components/pdfviewer/PDFViewer";
import FloatingStatusButton from "@/components/status/FloatingStatusButton";
import CrossAnalysisFrameworkLauncher from "@/components/dashboard/CrossAnalysisFrameworkLauncher";

export default function DashboardPage() {
  return (
    <>
      <CrossAnalysisFrameworkLauncher />
      <PDFViewer />
      <FloatingStatusButton />
    </>
  );
}
