import React, { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import AnalysisResults from "./AnalysisResults";
import type { AnalysisDataItem, EvidencePageTarget } from "./AnalysisResults";
import { ChevronDown, ChevronUp, FileText, MessageCircle, X } from "lucide-react";
import { useT } from "@/i18n/useT";
import type { File as FileData } from "@/store/useFileStore";
import { useDraggableFloating } from "@/hooks/useDraggableFloating";

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
const ChatInterface = dynamic(() => import("./ChatInterface"), {
  ssr: false,
  loading: () => (
    <div
      aria-busy="true"
      aria-label="Loading AI Assistant"
      className="h-full min-h-64 animate-pulse bg-slate-50"
    />
  ),
});
const ComplianceSummaryDrawer = dynamic(
  () => import("./ComplianceSummaryDrawer"),
  { ssr: false },
);
// Use same-origin proxy via Next.js rewrites by default.
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "";

interface Message {
  text: string;
  isUser: boolean;
}

type PageNavigation = {
  documentKey: string;
  nonce: number;
  page: number;
};

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
  const [assistantLoaded, setAssistantLoaded] = useState(false);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [analysisMetrics, setAnalysisMetrics] = useState<AnalysisDataItem[]>([]);
  const [showAnalysisTable, setShowAnalysisTable] = useState<boolean>(true);
  const {
    draggableProps: assistantDragProps,
    draggableRef: assistantButtonRef,
  } = useDraggableFloating<HTMLButtonElement>();

  const effectiveFileId = fileId || activeFile?.file_id;
  const effectiveScopeKey = scopeKey || activeFile?.analysis_scope_key;
  const documentKey = `${effectiveFileId || ""}::${effectiveScopeKey || ""}`;
  const [pageNavigation, setPageNavigation] = useState<PageNavigation | null>(null);
  const activePageNavigation = pageNavigation?.documentKey === documentKey
    ? pageNavigation
    : null;

  useEffect(() => {
    setAnalysisMetrics([]);
    setSummaryOpen(false);
    setAssistantOpen(false);
  }, [effectiveFileId, effectiveScopeKey]);

  const handleAnalysisDataChange = useCallback((items: AnalysisDataItem[]) => {
    setAnalysisMetrics(items);
  }, []);

  const closeAssistant = useCallback(() => {
    setAssistantOpen(false);
    window.requestAnimationFrame(() => assistantButtonRef.current?.focus());
  }, [assistantButtonRef]);

  useEffect(() => {
    if (!assistantOpen) return;

    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeAssistant();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [assistantOpen, closeAssistant]);

  const navigateToPage = useCallback((target: EvidencePageTarget) => {
    if (target.fileId && target.fileId !== effectiveFileId) {
      const params = new URLSearchParams({
        file_id: target.fileId,
        page: String(target.page),
      });
      if (target.reportName) params.set("name", target.reportName);
      window.open(
        `/cross-analysis/evidence?${params.toString()}`,
        "_blank",
        "noopener,noreferrer",
      );
      return;
    }
    setPageNavigation((previous) => ({
      documentKey,
      nonce: (previous?.nonce || 0) + 1,
      page: target.page,
    }));
  }, [documentKey, effectiveFileId]);

  // AnalysisResults is memoized and can contain a large Ant table. Keep this
  // element stable so chat message updates do not force that table to render.
  const summaryAction = useMemo(() => (
    <button
      type="button"
      onClick={() => {
        setAssistantOpen(false);
        setSummaryOpen(true);
      }}
      disabled={analysisMetrics.length === 0}
      className="inline-flex h-8 items-center gap-1.5 rounded-full bg-[#2274BC] px-4 text-xs font-semibold text-white shadow-sm transition-[transform,background-color,box-shadow] duration-200 ease-[var(--motion-fluid)] hover:-translate-y-px hover:bg-[#1b63a3] hover:shadow-md focus:outline-none focus:ring-2 focus:ring-[#2274BC] focus:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:shadow-none disabled:hover:translate-y-0"
      title={t("analysis.generateSummaryTooltip")}
      aria-haspopup="dialog"
    >
      <FileText className="h-3.5 w-3.5" />
      {t("analysis.generateSummary")}
    </button>
  ), [analysisMetrics.length, t]);

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
            headerAction={summaryAction}
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
                targetPage={activePageNavigation?.page || 1}
                targetPageNonce={activePageNavigation?.nonce || 0}
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

      <section
        id="compliance-ai-assistant"
        role="dialog"
        aria-modal="false"
        aria-label={t("chat.aiAssistant")}
        aria-hidden={!assistantOpen}
        data-testid="compliance-ai-assistant"
        className={`dashboard-chat-panel fixed z-50 flex h-[min(620px,calc(100dvh-7rem))] min-h-0 flex-col overflow-hidden rounded-2xl border border-slate-200/90 bg-white shadow-[0_24px_80px_rgba(15,23,42,0.22)] transition-[opacity,transform,visibility] duration-300 ease-[var(--motion-fluid)] ${
          assistantOpen
            ? "visible translate-y-0 scale-100 opacity-100"
            : "invisible pointer-events-none translate-y-3 scale-[0.98] opacity-0"
        }`}
      >
        <div className="flex h-full min-h-0 flex-col">
          {assistantLoaded ? (
            <ChatInterface
              messages={messages}
              onSendMessage={onSendMessage}
              onClearChat={onClearChat}
              onClose={closeAssistant}
              onReferenceClick={(page) => {
                navigateToPage({ page });
                closeAssistant();
              }}
            />
          ) : null}
        </div>
      </section>

      <button
        ref={assistantButtonRef}
        type="button"
        {...assistantDragProps}
        onClick={() => {
          setSummaryOpen(false);
          setAssistantLoaded(true);
          setAssistantOpen((open) => !open);
        }}
        className={`dashboard-chat-launcher draggable-assistant-launcher fixed z-[51] flex h-12 items-center gap-2 rounded-full px-4 text-white shadow-lg transition-[transform,background-color,box-shadow] duration-200 ease-[var(--motion-fluid)] hover:-translate-y-0.5 hover:shadow-xl focus:outline-none focus:ring-2 focus:ring-[#2274BC] focus:ring-offset-2 ${
          assistantOpen ? "bg-slate-800 hover:bg-slate-700" : "bg-[#2274BC] hover:bg-[#1b63a3]"
        }`}
        aria-label={assistantOpen ? t("common.close") : "AI Assistant"}
        aria-controls="compliance-ai-assistant"
        aria-expanded={assistantOpen}
        title={t("chat.dragAssistantHint")}
      >
        {assistantOpen ? <X className="h-5 w-5" /> : <MessageCircle className="h-5 w-5" />}
        <span className="dashboard-chat-launcher-label text-sm font-medium">
          {assistantOpen ? t("common.close") : "AI Assistant"}
        </span>
      </button>

      {summaryOpen ? (
        <ComplianceSummaryDrawer
          metrics={analysisMetrics}
          open
          onClose={() => setSummaryOpen(false)}
          reportName={activeFile?.name}
        />
      ) : null}
    </div>
  );
};

export default ChatView;
