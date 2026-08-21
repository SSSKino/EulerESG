// app/dashboard/chat/page.tsx
"use client";
import React, { Suspense, useEffect, useMemo, useState } from "react";
import { message } from "antd";
import { useSearchParams } from "next/navigation";
import ChatView from "@/components/pdfviewer/ChatView";
import { useFileStore } from "@/store/useFileStore";
import { apiService, type ChatResponse } from "@/lib/api";
import { useT } from "@/i18n/useT";
import { errorSummary } from "@/lib/logger";

interface Message {
  text: string;
  isUser: boolean;
}

function ChatPageContent() {
  const { t } = useT();
  const searchParams = useSearchParams();
  const [messages, setMessages] = useState<Message[]>(() => [
    { text: t("chat.welcomeMessage"), isUser: false },
  ]);

  const files = useFileStore((state) => state.files);
  const selectedFileId = useFileStore((state) => state.selectedFileId);
  const setSelectedFileId = useFileStore((state) => state.setSelectedFileId);
  const loadFilesFromBackend = useFileStore((state) => state.loadFilesFromBackend);

  const queryFileId = searchParams.get("file_id");
  const queryScope = searchParams.get("scope");
  const requestedFileId = queryFileId || selectedFileId || undefined;

  useEffect(() => {
    // Update the default greeting when switching language (only if the chat is still fresh)
    setMessages((prev) => {
      if (prev.length !== 1) return prev;
      if (prev[0]?.isUser) return prev;
      return [{ text: t("chat.welcomeMessage"), isUser: false }];
    });
  }, [t]);

  // Ensure report context exists even after refresh or direct navigation.
  useEffect(() => {
    if (queryFileId && queryFileId !== selectedFileId) {
      setSelectedFileId(queryFileId);
    }
  }, [queryFileId, selectedFileId, setSelectedFileId]);

  useEffect(() => {
    // If store does not have files yet (e.g., hard refresh), fetch from backend.
    if (!files || files.length === 0) {
      loadFilesFromBackend();
    }
  }, [files, loadFilesFromBackend]);

  const currentFile = useMemo(() => {
    const id = requestedFileId;
    if (!id) return null;
    const cands = files.filter((f) => f.file_id === id);
    if (cands.length === 0) return null;
    if (queryScope) {
      const hit = cands.find((f) => f.analysis_scope_key === queryScope);
      if (hit) return hit;
    }
    return cands[0];
  }, [files, requestedFileId, queryScope]);

  const handleSendMessage = async (userMessage: string) => {
    const effectiveFileId = queryFileId || selectedFileId;
    // 添加用户消息
    setMessages(prev => [...prev, { text: userMessage, isUser: true }]);

    // 添加加载状态
    setMessages(prev => [...prev, { text: t("chat.thinking"), isUser: false }]);

    try {
      // 调用后端API：如果选中了文件，则使用 /api/chat/{file_id}（带报告/评估上下文）；
      // 否则回落到全局 /api/chat。
      const response: ChatResponse = effectiveFileId
        ? await apiService.sendMessageForFile(effectiveFileId, {
            message: userMessage,
            include_context: true,
            session_id: `file:${effectiveFileId}`
          })
        : await apiService.sendMessage({
            message: userMessage,
            include_context: true
          });

      // 移除加载消息，添加真实响应
      setMessages(prev => {
        const newMessages = prev.slice(0, -1); // 移除加载消息
        return [...newMessages, { text: response.response, isUser: false }];
      });

      // 如果有相关段落，可以在这里处理
      if (response.relevant_segments && response.relevant_segments.length > 0) {
      }

    } catch (error) {
      console.error(`Chat request failed: ${errorSummary(error)}`);
      message.error(t("chat.failedToSend", { error: String(error) }));
      
      // 移除加载消息，添加错误消息
      setMessages(prev => {
        const newMessages = prev.slice(0, -1);
        return [...newMessages, { 
          text: "Sorry, I can't answer your question at the moment. Please try again later.", 
          isUser: false 
        }];
      });
    }
  };

  const handleClearChat = () => {
    setMessages([
      {
        text: t("chat.welcomeMessage"),
        isUser: false,
      },
    ]);
  };

  return (
    <div className="w-full flex flex-col justify-start items-center mx-auto pt-1 min-h-screen">
      <div className="w-[95%]">
        <ChatView
          activeFile={currentFile}
          fileId={requestedFileId}
          scopeKey={queryScope || undefined}
          messages={messages}
          onSendMessage={handleSendMessage}
          onClearChat={handleClearChat}
        />
      </div>
    </div>
  );
}

export default function ChatPage() {
  return (
    <Suspense
      fallback={<div aria-busy="true" className="min-h-screen w-full bg-white" />}
    >
      <ChatPageContent />
    </Suspense>
  );
}
