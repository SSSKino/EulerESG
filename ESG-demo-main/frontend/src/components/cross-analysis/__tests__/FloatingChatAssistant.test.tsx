import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FloatingChatAssistant from "@/components/cross-analysis/FloatingChatAssistant";

const apiMocks = vi.hoisted(() => ({
  sendMessage: vi.fn(),
}));

vi.mock("antd", () => ({
  Drawer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  message: { error: vi.fn() },
}));

vi.mock("@/components/pdfviewer/ChatInterface", () => ({
  default: ({ onSendMessage }: { onSendMessage: (message: string) => Promise<void> }) => (
    <button type="button" onClick={() => void onSendMessage("test question")}>
      Send test question
    </button>
  ),
}));

vi.mock("@/lib/api", () => ({
  apiService: { sendMessage: apiMocks.sendMessage },
}));

vi.mock("@/i18n/useT", () => ({
  useT: () => ({ t: (key: string) => key }),
}));

describe("FloatingChatAssistant", () => {
  beforeEach(() => {
    apiMocks.sendMessage.mockReset();
    apiMocks.sendMessage.mockResolvedValue({
      session_id: "assistant-session",
      response: "answer",
    });
  });

  it("uses the shared floating lower-left launcher with the requested label", () => {
    render(<FloatingChatAssistant />);

    const launcher = screen.getByRole("button", { name: "AI Assistant" });
    expect(launcher).toHaveTextContent("AI Assistant");
    expect(launcher).toHaveClass("dashboard-chat-launcher", "fixed");
    expect(launcher).not.toHaveClass("right-6", "bottom-20");
  });

  it("uses generic mode on the homepage and preserves the server chat session", async () => {
    render(<FloatingChatAssistant includeContext={false} />);

    const send = screen.getByRole("button", { name: "Send test question" });
    fireEvent.click(send);
    await waitFor(() => expect(apiMocks.sendMessage).toHaveBeenCalledTimes(1));
    expect(apiMocks.sendMessage).toHaveBeenNthCalledWith(1, {
      message: "test question",
      include_context: false,
      session_id: undefined,
    });

    fireEvent.click(send);
    await waitFor(() => expect(apiMocks.sendMessage).toHaveBeenCalledTimes(2));
    expect(apiMocks.sendMessage).toHaveBeenNthCalledWith(2, {
      message: "test question",
      include_context: false,
      session_id: "assistant-session",
    });
  });
});
