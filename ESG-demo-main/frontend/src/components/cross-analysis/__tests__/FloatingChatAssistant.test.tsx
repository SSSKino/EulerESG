import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FloatingChatAssistant from "@/components/cross-analysis/FloatingChatAssistant";

const apiMocks = vi.hoisted(() => ({
  sendMessage: vi.fn(),
}));

vi.mock("antd", () => ({
  Drawer: ({ children, open }: { children: React.ReactNode; open?: boolean }) => (
    <div data-testid="assistant-drawer" data-open={String(Boolean(open))}>
      {children}
    </div>
  ),
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

type MockPointerEventInit = MouseEventInit & {
  isPrimary?: boolean;
  pointerId?: number;
  pointerType?: string;
};

const dispatchPointerEvent = (
  target: Node,
  type: string,
  init: MockPointerEventInit,
) => {
  const event = new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    ...init,
  });
  Object.defineProperties(event, {
    isPrimary: { configurable: true, value: init.isPrimary ?? true },
    pointerId: { configurable: true, value: init.pointerId ?? 1 },
    pointerType: { configurable: true, value: init.pointerType ?? "mouse" },
  });
  fireEvent(target, event);
};

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
    expect(launcher).toHaveClass(
      "dashboard-chat-launcher",
      "draggable-assistant-launcher",
      "fixed",
    );
    expect(launcher).toHaveAttribute("data-draggable-assistant", "true");
    expect(launcher).not.toHaveClass("right-6", "bottom-20");
  });

  it("drags within the viewport without treating the release as an open click", () => {
    render(<FloatingChatAssistant />);

    vi.spyOn(window, "innerWidth", "get").mockReturnValue(800);
    vi.spyOn(window, "innerHeight", "get").mockReturnValue(600);

    const launcher = screen.getByRole("button", { name: "AI Assistant" });
    vi.spyOn(launcher, "getBoundingClientRect").mockReturnValue({
      bottom: 548,
      height: 48,
      left: 24,
      right: 184,
      top: 500,
      width: 160,
      x: 24,
      y: 500,
      toJSON: () => ({}),
    });

    dispatchPointerEvent(launcher, "pointerdown", {
      button: 0,
      clientX: 50,
      clientY: 520,
      pointerId: 9,
    });
    dispatchPointerEvent(launcher, "pointermove", {
      button: 0,
      clientX: 760,
      clientY: -100,
      pointerId: 9,
    });
    dispatchPointerEvent(launcher, "pointerup", {
      button: 0,
      clientX: 760,
      clientY: -100,
      pointerId: 9,
    });

    expect(launcher).toHaveStyle({
      bottom: "auto",
      left: "632px",
      right: "auto",
      top: "8px",
    });
    expect(launcher).toHaveAttribute("data-dragging", "false");

    fireEvent.click(launcher, { detail: 1 });
    expect(screen.getByTestId("assistant-drawer")).toHaveAttribute(
      "data-open",
      "false",
    );

    fireEvent.click(launcher);
    expect(screen.getByTestId("assistant-drawer")).toHaveAttribute(
      "data-open",
      "true",
    );
    expect(launcher).toHaveStyle({ left: "632px", top: "8px" });
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
