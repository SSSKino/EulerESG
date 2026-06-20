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

export default function FloatingChatAssistant() {
  const { t } = useT();
  const [open, setOpen] = useState(false);
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
          include_context: true,
        });
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
    [t]
  );

  const handleClearChat = useCallback(() => {
    setMessages([{ text: t("chat.welcomeMessage"), isUser: false }]);
  }, [t]);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed bottom-20 left-6 z-40 flex items-center gap-2 rounded-full bg-[var(--brand-secondary)] px-5 py-3 text-white shadow-[0_8px_24px_rgb(0_0_0_/_0.12)] transition hover:bg-[var(--brand-secondary-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--brand-primary)] focus:ring-offset-2"
        aria-label={t("crossAnalysis.floatingAssistant")}
      >
        <MessageCircle className="h-5 w-5" />
        <span className="text-sm font-medium">{t("crossAnalysis.floatingAssistant")}</span>
      </button>

      <Drawer
        title={t("chat.aiAssistant")}
        placement="left"
        width={420}
        onClose={() => setOpen(false)}
        open={open}
        destroyOnClose={false}
        className="app-drawer"
        styles={{
          header: { borderBottom: "1px solid rgba(0,0,0,0.06)" },
          body: { padding: 0, display: "flex", flexDirection: "column", height: "calc(100% - 55px)", background: "var(--brand-surface)" },
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
