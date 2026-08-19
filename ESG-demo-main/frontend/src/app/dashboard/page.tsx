"use client";
import PDFViewer from "@/components/pdfviewer/PDFViewer";
import FloatingStatusButton from "@/components/status/FloatingStatusButton";

const SHOW_DEV_TOOLS = /^(1|true|yes|on)$/i.test(
  process.env.NEXT_PUBLIC_SHOW_DEV_TOOLS?.trim() ?? "",
);

export default function DashboardPage() {
  return (
    <>
      <PDFViewer />
      {SHOW_DEV_TOOLS && <FloatingStatusButton />}
    </>
  );
}
