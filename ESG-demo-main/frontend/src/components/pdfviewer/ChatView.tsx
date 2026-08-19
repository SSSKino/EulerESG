import React, { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { Drawer } from "antd";
import AnalysisResults from "./AnalysisResults";
import type { AnalysisDataItem } from "./AnalysisResults";
import ChatInterface from "./ChatInterface";
import ComplianceSummaryDrawer from "./ComplianceSummaryDrawer";
import { ChevronDown, ChevronUp, FileText, MessageCircle } from "lucide-react";
import { useT } from "@/i18n/useT";
import type { File as FileData } from "@/store/useFileStore";

interface CollapsibleSectionProps {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
  className?: string;
  headerActions?: React.ReactNode;
}

const CollapsibleSection: React.FC<CollapsibleSectionProps> = ({
  title,
  children,
  defaultOpen = true,
  className = "",
  headerActions,
}) => {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  return (
    <div
      className={`bg-white rounded-lg shadow-sm border border-gray-100 overflow-hidden transition-[box-shadow,border-color,opacity,transform] duration-300 ease-[var(--motion-fluid)] ${className}`}
    >
      <div className="flex justify-between items-center p-3 bg-gray-50 border-b border-gray-100">
        <div
          className="flex items-center gap-2 cursor-pointer select-none flex-grow"
          onClick={() => setIsOpen(!isOpen)}
        >
          {isOpen ? (
            <ChevronUp className="w-4 h-4 text-gray-500" />
          ) : (
            <ChevronDown className="w-4 h-4 text-gray-500" />
          )}
          <h3 className="text-md font-semibold text-gray-800 truncate">{title}</h3>
        </div>
        {headerActions && <div className="ml-2">{headerActions}</div>}
      </div>
      {isOpen && <div className="p-4 h-full">{children}</div>}
    </div>
  );
};

const PDFReportViewer = dynamic(() => import("./PDFChatViewer"), { ssr: false });
const MemoizedPDFReportViewer = React.memo(PDFReportViewer);
// Use same-origin proxy via Next.js rewrites by default.
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "";

interface Message {
  text: string;
  isUser: boolean;
}

interface ChatViewProps {
  activeFile: FileData | null;
  fileId?: string;
  scopeKey?: string;
  messages: Message[];
  onSendMessage: (message: string) => void;
  onClearChat: () => void;
}

const ChatView: React.FC<ChatViewProps> = ({
  activeFile,
  fileId,
  scopeKey,
  messages,
  onSendMessage,
  onClearChat,
}) => {
  const { t } = useT();
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [analysisMetrics, setAnalysisMetrics] = useState<AnalysisDataItem[]>([]);
  const [showAnalysisTable, setShowAnalysisTable] = useState<boolean>(true);

  const [targetPage, setTargetPage] = useState<number | undefined>(undefined);
  // Used to force a new navigation request even if the user clicks the same
  // page number repeatedly.
  const [targetPageNonce, setTargetPageNonce] = useState<number>(0);
  const effectiveFileId = fileId || activeFile?.file_id;
  const effectiveScopeKey = scopeKey || activeFile?.analysis_scope_key;

  useEffect(() => {
    setAnalysisMetrics([]);
    setSummaryOpen(false);
  }, [effectiveFileId, effectiveScopeKey]);

  const handleAnalysisDataChange = useCallback((items: AnalysisDataItem[]) => {
    setAnalysisMetrics(items);
  }, []);

  const navigateToPage = useCallback((page: number) => {
    setTargetPage(page);
    setTargetPageNonce((n) => n + 1);
  }, []);

  return (
    <div className="flex flex-col gap-6">
      {/* Analysis: Summary always visible; Results table can be collapsed upward */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-100 overflow-hidden transition-[box-shadow,border-color] duration-300 ease-[var(--motion-fluid)] w-full hover:shadow-lg">
        <div className="flex justify-between items-center p-3 bg-gray-50 border-b border-gray-100">
          <div className="flex items-center gap-2 select-none flex-grow">
            <h3 className="text-md font-semibold text-gray-800 truncate">{t("chat.analysis")}</h3>
</div>

          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={() => setSummaryOpen(true)}
              disabled={analysisMetrics.length === 0}
              className="inline-flex h-8 items-center gap-1.5 rounded-full bg-[#2274BC] px-4 text-xs font-semibold text-white shadow-sm transition-[transform,background-color,box-shadow] duration-200 ease-[var(--motion-fluid)] hover:-translate-y-px hover:bg-[#1b63a3] hover:shadow-md focus:outline-none focus:ring-2 focus:ring-[#2274BC] focus:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:shadow-none disabled:hover:translate-y-0"
              title={t("analysis.generateSummaryTooltip")}
              aria-haspopup="dialog"
            >
              <FileText className="h-3.5 w-3.5" />
              {t("analysis.generateSummary")}
            </button>
            <button
              type="button"
              onClick={() => setShowAnalysisTable((s) => !s)}
              className="rounded-md p-1 transition-colors hover:bg-gray-200"
              title={showAnalysisTable ? t("chat.hideAnalysisResults") : t("chat.showAnalysisResults")}
            >
              {showAnalysisTable ? (
                <ChevronUp className="h-4 w-4 text-gray-600" />
              ) : (
                <ChevronDown className="h-4 w-4 text-gray-600" />
              )}
            </button>
          </div>
        </div>

        <div className="p-4">
          <AnalysisResults
            fileId={effectiveFileId}
            scopeKey={effectiveScopeKey}
            onPageNavigate={navigateToPage}
            showTable={showAnalysisTable}
            onDataChange={handleAnalysisDataChange}
          />
        </div>
      </div>

      <div className="min-h-[600px]">
        <CollapsibleSection
          title={activeFile?.name || t("chat.documentViewer")}
          defaultOpen={true}
          className="w-full hover:shadow-lg"
        >
          {effectiveFileId && (!activeFile?.type || activeFile.type.toUpperCase() === "PDF") ? (
            <div className="overflow-hidden rounded-lg h-[70vh] min-h-[600px]">
              <MemoizedPDFReportViewer
                fileUrl={`${API_BASE_URL}/api/files/${effectiveFileId}/pdf`}
                targetPage={targetPage || 1}
                targetPageNonce={targetPageNonce}
                height="100%"
                defaultZoom={1}
              />
            </div>
          ) : (
            <div className="h-64 flex items-center justify-center">
              <p className="text-gray-500">
                {!activeFile?.file_id ? t("chat.fileNotAvailable") : t("chat.unsupportedFileType")}
              </p>
            </div>
          )}
        </CollapsibleSection>
      </div>

      <button
        type="button"
        onClick={() => setAssistantOpen(true)}
        className="fixed bottom-6 right-6 z-40 flex h-12 items-center gap-2 rounded-full bg-[#2274BC] px-4 text-white shadow-lg transition-[transform,background-color,box-shadow] duration-200 ease-[var(--motion-fluid)] hover:-translate-y-0.5 hover:bg-[#1b63a3] hover:shadow-xl focus:outline-none focus:ring-2 focus:ring-[#2274BC] focus:ring-offset-2"
        aria-label={t("chat.aiAssistant")}
      >
        <MessageCircle className="h-5 w-5" />
        <span className="text-sm font-medium">{t("chat.aiAssistant")}</span>
      </button>

      <Drawer
        title={t("chat.aiAssistant")}
        placement="right"
        width={440}
        open={assistantOpen}
        onClose={() => setAssistantOpen(false)}
        destroyOnClose={false}
        styles={{ body: { padding: 0, display: "flex", flexDirection: "column", height: "calc(100% - 55px)" } }}
      >
        <div className="flex h-full min-h-0 flex-col">
          <ChatInterface
            messages={messages}
            onSendMessage={onSendMessage}
            onClearChat={onClearChat}
            onReferenceClick={(page) => {
              navigateToPage(page);
              setAssistantOpen(false);
            }}
          />
        </div>
      </Drawer>

      <ComplianceSummaryDrawer
        metrics={analysisMetrics}
        open={summaryOpen}
        onClose={() => setSummaryOpen(false)}
        reportName={activeFile?.name}
      />
    </div>
  );
};

export default ChatView;
