import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FloatingChatAssistant from "@/components/cross-analysis/FloatingChatAssistant";

const apiMocks = vi.hoisted(() => ({
  sendMessage: vi.fn(),
}));

vi.mock("antd", () => ({
  App: {
    useApp: () => ({ message: { error: vi.fn() } }),
  },
}));

vi.mock("@/components/pdfviewer/ChatInterface", () => ({
  default: ({
    onClose,
    onSendMessage,
  }: {
    onClose?: () => void;
    onSendMessage: (message: string) => Promise<void>;
  }) => (
    <div>
      <button type="button" onClick={() => void onSendMessage("test question")}>
        Send test question
      </button>
      <button type="button" onClick={onClose}>
        Close test assistant
      </button>
    </div>
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
    const panel = screen.getByTestId("floating-ai-assistant");
    expect(launcher).toHaveTextContent("AI Assistant");
    expect(launcher).toHaveClass(
      "dashboard-chat-launcher",
      "draggable-assistant-launcher",
      "fixed",
    );
    expect(launcher).toHaveAttribute("data-draggable-assistant", "true");
    expect(launcher).toHaveAttribute("aria-controls", "floating-ai-assistant");
    expect(launcher).toHaveAttribute("aria-expanded", "false");
    expect(launcher).not.toHaveClass("right-6", "bottom-20");
    expect(panel).toHaveAttribute("aria-hidden", "true");
    expect(panel).toHaveAttribute("aria-modal", "false");
    expect(panel).toHaveClass(
      "dashboard-chat-panel",
      "fixed",
      "invisible",
      "pointer-events-none",
    );
  });

  it("opens and closes a non-modal floating panel without a drawer or page mask", () => {
    render(<FloatingChatAssistant />);

    const launcher = screen.getByRole("button", { name: "AI Assistant" });
    const panel = screen.getByTestId("floating-ai-assistant");

    expect(document.querySelector(".ant-drawer")).not.toBeInTheDocument();
    expect(document.querySelector(".ant-drawer-mask")).not.toBeInTheDocument();
    expect(
      document.querySelector('[data-slot="sheet-overlay"]'),
    ).not.toBeInTheDocument();

    fireEvent.click(launcher);
    expect(launcher).toHaveAttribute("aria-expanded", "true");
    expect(panel).toHaveAttribute("aria-hidden", "false");
    expect(panel).toHaveAttribute("aria-modal", "false");
    expect(panel).toHaveClass("visible", "opacity-100");
    expect(panel).not.toHaveClass("invisible", "pointer-events-none");
    expect(screen.getByRole("dialog", { name: "chat.aiAssistant" })).toBe(panel);

    fireEvent.click(launcher);
    expect(launcher).toHaveAttribute("aria-expanded", "false");
    expect(panel).toHaveAttribute("aria-hidden", "true");
    expect(panel).toHaveClass("invisible", "pointer-events-none", "opacity-0");

    fireEvent.click(launcher);
    fireEvent.click(screen.getByRole("button", { name: "Close test assistant" }));
    expect(panel).toHaveAttribute("aria-hidden", "true");

    fireEvent.click(launcher);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(launcher).toHaveAttribute("aria-expanded", "false");
    expect(panel).toHaveAttribute("aria-hidden", "true");
  });

  it("anchors the compact panel to the launcher and clamps it inside the viewport", () => {
    vi.spyOn(window, "innerWidth", "get").mockReturnValue(360);
    vi.spyOn(window, "innerHeight", "get").mockReturnValue(640);
    render(<FloatingChatAssistant />);

    const launcher = screen.getByRole("button", { name: "AI Assistant" });
    const panel = screen.getByTestId("floating-ai-assistant");
    vi.spyOn(launcher, "getBoundingClientRect").mockReturnValue({
      bottom: 610,
      height: 40,
      left: 310,
      right: 350,
      top: 570,
      width: 40,
      x: 310,
      y: 570,
      toJSON: () => ({}),
    });

    fireEvent.click(launcher);

    expect(panel).toHaveAttribute("data-placement", "top");
    expect(panel).toHaveStyle({
      bottom: "auto",
      height: "435px",
      left: "12px",
      right: "auto",
      top: "123px",
      transformOrigin: "bottom center",
      width: "336px",
    });

    const left = Number.parseFloat(panel.style.left);
    const top = Number.parseFloat(panel.style.top);
    const width = Number.parseFloat(panel.style.width);
    const height = Number.parseFloat(panel.style.height);
    expect(left).toBeGreaterThanOrEqual(12);
    expect(top).toBeGreaterThanOrEqual(12);
    expect(left + width).toBeLessThanOrEqual(360 - 12);
    expect(top + height).toBeLessThanOrEqual(640 - 12);
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
    expect(screen.getByTestId("floating-ai-assistant")).toHaveAttribute(
      "aria-hidden",
      "true",
    );

    fireEvent.click(launcher);
    expect(screen.getByTestId("floating-ai-assistant")).toHaveAttribute(
      "aria-hidden",
      "false",
    );
    expect(launcher).toHaveStyle({ left: "632px", top: "8px" });
  });

  it("uses generic mode on the homepage and preserves the server chat session", async () => {
    render(<FloatingChatAssistant includeContext={false} />);

    fireEvent.click(screen.getByRole("button", { name: "AI Assistant" }));
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
