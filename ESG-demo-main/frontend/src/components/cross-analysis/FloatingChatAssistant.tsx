"use client";

import React, { useCallback, useState } from "react";
import { Drawer, message as antMessage } from "antd";
import { MessageCircle } from "lucide-react";
import ChatInterface from "@/components/pdfviewer/ChatInterface";
import { apiService, type ChatResponse } from "@/lib/api";
import { useT } from "@/i18n/useT";

interface Message {
  text: string;
  isUser: boolean;
}

interface FloatingChatAssistantProps {
  includeContext?: boolean;
}

export default function FloatingChatAssistant({
  includeContext = true,
}: FloatingChatAssistantProps) {
  const { t } = useT();
  const [open, setOpen] = useState(false);
  const [sessionId, setSessionId] = useState<string>();
  const [messages, setMessages] = useState<Message[]>(() => [
    { text: t("chat.welcomeMessage"), isUser: false },
  ]);

  const handleSendMessage = useCallback(
    async (userMessage: string) => {
      setMessages((prev) => [...prev, { text: userMessage, isUser: true }]);
      setMessages((prev) => [...prev, { text: t("chat.thinking"), isUser: false }]);

      try {
        const response: ChatResponse = await apiService.sendMessage({
          message: userMessage,
          include_context: includeContext,
          session_id: sessionId,
        });
        setSessionId(response.session_id);
        setMessages((prev) => {
          const withoutLoading = prev.slice(0, -1);
          return [...withoutLoading, { text: response.response, isUser: false }];
        });
      } catch (error) {
        console.error("Chat error:", error);
        antMessage.error(t("chat.failedToSend", { error: String(error) }));
        setMessages((prev) => {
          const withoutLoading = prev.slice(0, -1);
          return [
            ...withoutLoading,
            {
              text: t("chat.genericError"),
              isUser: false,
            },
          ];
        });
      }
    },
    [includeContext, sessionId, t]
  );

  const handleClearChat = useCallback(() => {
    setSessionId(undefined);
    setMessages([{ text: t("chat.welcomeMessage"), isUser: false }]);
  }, [t]);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="dashboard-chat-launcher fixed z-[51] flex items-center gap-2 rounded-full bg-slate-700 px-4 py-3 text-white shadow-lg transition hover:bg-slate-600 focus:outline-none focus:ring-2 focus:ring-slate-500 focus:ring-offset-2"
        aria-label="AI Assistant"
      >
        <MessageCircle className="h-5 w-5" />
        <span className="text-sm font-medium">AI Assistant</span>
      </button>

      <Drawer
        title={t("chat.aiAssistant")}
        placement="left"
        width={400}
        onClose={() => setOpen(false)}
        open={open}
        destroyOnClose={false}
        styles={{
          body: {
            padding: 0,
            display: "flex",
            flexDirection: "column",
            height: "calc(100% - 55px)",
            overflowY: "auto",
            overscrollBehaviorY: "contain",
            WebkitOverflowScrolling: "touch",
          },
        }}
      >
        <div className="flex h-full flex-col min-h-0">
          <ChatInterface
            messages={messages}
            onSendMessage={handleSendMessage}
            onClearChat={handleClearChat}
            onReferenceClick={() => {}}
          />
        </div>
      </Drawer>
    </>
  );
}
