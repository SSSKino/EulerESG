import React, { useState } from "react";
import dynamic from "next/dynamic";
import AnalysisResults from "./AnalysisResults";
import ChatInterface from "./ChatInterface";
import { PanelLeft, ChevronDown, ChevronUp } from "lucide-react";

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
      className={`bg-white rounded-lg shadow-sm border border-gray-100 overflow-hidden transition-all duration-300 ${className}`}
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

const PDFChatViewer = dynamic(() => import("./PDFChatViewer"), { ssr: false });
// Use same-origin proxy via Next.js rewrites by default.
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "";

interface FileData {
  key: string;
  name: string;
  size: string;
  dateUploaded: string;
  type: string;
  tableStatus: string;
  imageStatus: string;
  url?: string;
  status: string;
  industry?: string;
  semiIndustry?: string;
  file_id?: string;
  filename?: string;
  framework?: string;
}

interface Message {
  text: string;
  isUser: boolean;
}

interface ChatViewProps {
  activeFile: FileData | null;
  messages: Message[];
  onSendMessage: (message: string) => void;
  onClearChat: () => void;
}

// balanced: PDF 1/2, AI 1/2
// pdfShrunk: PDF 1/4, AI 3/4 (AI expanded to the left)
// aiShrunk: AI 1/4, PDF 3/4 (PDF expanded to the right)
type WidthMode = "balanced" | "pdfShrunk" | "aiShrunk";

const ChatView: React.FC<ChatViewProps> = ({
  activeFile,
  messages,
  onSendMessage,
  onClearChat,
}) => {
  const [widthMode, setWidthMode] = useState<WidthMode>("balanced");
  const [showAnalysisTable, setShowAnalysisTable] = useState<boolean>(true);

  const [targetPage, setTargetPage] = useState<number | undefined>(undefined);
  // Used to force a new navigation request even if the user clicks the same
  // page number repeatedly.
  const [targetPageNonce, setTargetPageNonce] = useState<number>(0);

  const pdfWidthClass =
    widthMode === "pdfShrunk"
      ? "w-full md:w-1/4"
      : widthMode === "aiShrunk"
        ? "w-full md:w-3/4"
        : "w-full md:w-1/2";

  const aiWidthClass =
    widthMode === "aiShrunk"
      ? "w-full md:w-1/4"
      : widthMode === "pdfShrunk"
        ? "w-full md:w-3/4"
        : "w-full md:w-1/2";

  const ensurePdfReadable = () => {
    // If AI is currently focused (PDF is shrunk), restore to a readable layout
    // when the user navigates to a page reference.
    if (widthMode === "pdfShrunk") setWidthMode("balanced");
  };

  const navigateToPage = (page: number) => {
    setTargetPage(page);
    setTargetPageNonce((n) => n + 1);
    ensurePdfReadable();
  };

  return (
    <div className="flex flex-col gap-6">
      {/* Analysis: Summary always visible; Results table can be collapsed upward */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-100 overflow-hidden transition-all duration-300 w-full hover:shadow-lg">
        <div className="flex justify-between items-center p-3 bg-gray-50 border-b border-gray-100">
          <div className="flex items-center gap-2 select-none flex-grow">
            <h3 className="text-md font-semibold text-gray-800 truncate">Analysis</h3>
</div>

          <button
            onClick={() => setShowAnalysisTable((s) => !s)}
            className="p-1 hover:bg-gray-200 rounded-md transition-colors"
            title={showAnalysisTable ? "Hide analysis results" : "Show analysis results"}
          >
            {showAnalysisTable ? (
              <ChevronUp className="w-4 h-4 text-gray-600" />
            ) : (
              <ChevronDown className="w-4 h-4 text-gray-600" />
            )}
          </button>
        </div>

        <div className="p-4">
          <AnalysisResults
            fileId={activeFile?.file_id}
            onPageNavigate={(page) => navigateToPage(page)}
            showTable={showAnalysisTable}
          />
        </div>
      </div>

      {/* PDF + AI split view with width focus controls */}
      <div className="flex flex-col md:flex-row gap-6 min-h-[600px]">
        <CollapsibleSection
          title={activeFile?.name || "Document Viewer"}
          defaultOpen={true}
          className={`${pdfWidthClass} hover:shadow-lg`}
          headerActions={
            <button
              onClick={(e) => {
                e.stopPropagation();
                // Expand PDF to the right (AI shrinks)
                setWidthMode((m) => (m === "aiShrunk" ? "balanced" : "aiShrunk"));
              }}
              className="p-1 hover:bg-gray-200 rounded-md transition-colors"
              title={widthMode === "aiShrunk" ? "Restore split view" : "Expand PDF width"}
            >
              <PanelLeft className="w-5 h-5 text-gray-600" />
            </button>
          }
        >
          {activeFile?.type?.toUpperCase() === "PDF" && activeFile?.file_id ? (
            <div className="overflow-hidden rounded-lg h-[70vh] min-h-[600px]">
              <PDFChatViewer
                fileUrl={`${API_BASE_URL}/api/files/${activeFile.file_id}/pdf`}
                targetPage={targetPage}
                targetPageNonce={targetPageNonce}
              />
            </div>
          ) : (
            <div className="h-64 flex items-center justify-center">
              <p className="text-gray-500">
                {!activeFile?.file_id ? "File not available" : "Unsupported file type"}
              </p>
            </div>
          )}
        </CollapsibleSection>

        <CollapsibleSection
          title="AI Assistant"
          defaultOpen={true}
          className={`${aiWidthClass} hover:shadow-lg`}
          headerActions={
            <button
              onClick={(e) => {
                e.stopPropagation();
                // Expand AI to the left (PDF shrinks)
                setWidthMode((m) => (m === "pdfShrunk" ? "balanced" : "pdfShrunk"));
              }}
              className="p-1 hover:bg-gray-200 rounded-md transition-colors"
              title={widthMode === "pdfShrunk" ? "Restore split view" : "Expand AI width"}
            >
              <PanelLeft className="w-5 h-5 text-gray-600" />
            </button>
          }
        >
          <div className="h-[70vh] min-h-[600px] flex flex-col">
            <ChatInterface
              messages={messages}
              onSendMessage={onSendMessage}
              onClearChat={onClearChat}
              onReferenceClick={(page) => navigateToPage(page)}
            />
          </div>
        </CollapsibleSection>
      </div>
    </div>
  );
};

export default ChatView;
