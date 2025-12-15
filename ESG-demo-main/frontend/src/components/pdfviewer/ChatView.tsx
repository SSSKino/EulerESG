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
    <div className={`bg-white rounded-lg shadow-sm border border-gray-100 overflow-hidden transition-all duration-300 ${className}`}>
      <div className="flex justify-between items-center p-3 bg-gray-50 border-b border-gray-100">
        <div 
          className="flex items-center gap-2 cursor-pointer select-none flex-grow" 
          onClick={() => setIsOpen(!isOpen)}
        >
           {isOpen ? <ChevronUp className="w-4 h-4 text-gray-500" /> : <ChevronDown className="w-4 h-4 text-gray-500" />}
          <h3 className="text-md font-semibold text-gray-800 truncate">{title}</h3>
        </div>
        {headerActions && <div className="ml-2">{headerActions}</div>}
      </div>
      {isOpen && <div className="p-4 h-full">{children}</div>}
    </div>
  );
};

const PDFChatViewer = dynamic(() => import("./PDFChatViewer"), { ssr: false });
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000';

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
  // file_id?: string; // backup
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

const ChatView: React.FC<ChatViewProps> = ({
  activeFile,
  messages,
  onSendMessage,
  onClearChat,
}) => {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [targetPage, setTargetPage] = useState<number | undefined>(undefined);
  console.log("targetPage", targetPage);

  return (
    <div className="flex flex-col gap-6">
      <CollapsibleSection title="Analysis Results" defaultOpen={true}>
        <AnalysisResults
          fileId={activeFile?.file_id}
          onPageNavigate={(page) => {
            setTargetPage(page);
            if (isCollapsed) {
              setIsCollapsed(false);
            }
          }}
        />
      </CollapsibleSection>

      <div className="flex flex-col md:flex-row gap-6 min-h-[600px]">
        <CollapsibleSection
          title={activeFile?.name || "Document Viewer"}
          defaultOpen={true}
          // Dynamic width class based on isCollapsed state
          className={`${
            isCollapsed ? "w-full md:w-1/4" : "w-full md:w-1/2"
          } hover:shadow-lg`}
          // Pass the existing width-toggle button into the headerActions
          headerActions={
            <button
              onClick={(e) => {
                e.stopPropagation(); // Prevent folding when clicking this button
                setIsCollapsed(!isCollapsed);
              }}
              className="p-1 hover:bg-gray-200 rounded-md transition-colors"
              title={isCollapsed ? "Expand PDF Width" : "Shrink PDF Width"}
            >
              <PanelLeft className="w-5 h-5 text-gray-600" />
            </button>
          }
          >
          {activeFile?.type?.toUpperCase() === "PDF" && activeFile?.file_id ? (
            <div className="overflow-hidden rounded-lg h-[600px]">
              <PDFChatViewer
                fileUrl={`${API_BASE_URL}/api/files/${activeFile.file_id}/pdf`}
                targetPage={targetPage}
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
          className={`${
            isCollapsed ? "w-full md:w-3/4" : "w-full md:w-1/2"
          } hover:shadow-lg`}
        >
          <div className="h-[600px] flex flex-col">
            <ChatInterface
                messages={messages}
                onSendMessage={onSendMessage}
                onClearChat={onClearChat}
                onReferenceClick={(page) => setTargetPage(page)}
            />
          </div>
        </CollapsibleSection>

      </div>
    </div>
  );
};

export default ChatView;
