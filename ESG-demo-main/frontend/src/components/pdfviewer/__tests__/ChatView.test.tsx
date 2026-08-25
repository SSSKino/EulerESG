import React, { useState } from "react";

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ChatView from "../ChatView";

const mocks = vi.hoisted(() => ({ dynamicIndex: 0 }));

vi.mock("next/dynamic", () => ({
  default: () => {
    const index = mocks.dynamicIndex++;
    if (index === 0) {
      return function MockPdfViewer({
        fileUrl,
        targetPage,
        targetPageNonce,
      }: {
        fileUrl: string;
        targetPage: number;
        targetPageNonce: number;
      }) {
        return (
          <div
            data-file-url={fileUrl}
            data-target-page={targetPage}
            data-target-page-nonce={targetPageNonce}
            data-testid="pdf-viewer"
          />
        );
      };
    }
    if (index === 1) {
      return function MockDynamicChatInterface({ onClose }: { onClose?: () => void }) {
        const [draft, setDraft] = useState("");
        return (
          <div>
            <label htmlFor="chat-draft">Draft</label>
            <input
              id="chat-draft"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
            />
            <button type="button" onClick={onClose}>
              assistant-close
            </button>
          </div>
        );
      };
    }
    return function MockDynamicSummaryDrawer() {
      return null;
    };
  },
}));

vi.mock("../AnalysisResults", () => ({
  default: ({
    headerAction,
    onPageNavigate,
  }: {
    headerAction?: React.ReactNode;
    onPageNavigate?: (target: {
      page: number;
      fileId?: string;
      reportName?: string;
    }) => void;
  }) => (
    <div data-testid="analysis-results">
      <div data-testid="analysis-report-heading">{headerAction}</div>
      <button type="button" onClick={() => onPageNavigate?.({ page: 77 })}>
        jump-page-77
      </button>
      <button
        type="button"
        onClick={() => onPageNavigate?.({
          page: 9,
          fileId: "report-b",
          reportName: "Report B.pdf",
        })}
      >
        jump-source-report
      </button>
    </div>
  ),
}));

vi.mock("../ComplianceSummaryDrawer", () => ({
  default: () => null,
}));

vi.mock("../ChatInterface", () => ({
  default: function MockChatInterface({ onClose }: { onClose?: () => void }) {
    const [draft, setDraft] = useState("");
    return (
      <div>
        <label htmlFor="chat-draft">Draft</label>
        <input
          id="chat-draft"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button type="button" onClick={onClose}>
          assistant-close
        </button>
      </div>
    );
  },
}));

vi.mock("@/i18n/useT", () => ({
  useT: () => ({ t: (key: string) => key }),
}));

const renderChatView = () =>
  render(
    <ChatView
      activeFile={null}
      fileId="report-1"
      messages={[{ text: "Hello", isUser: false }]}
      onSendMessage={vi.fn()}
      onClearChat={vi.fn()}
    />,
  );

describe("ChatView floating assistant", () => {
  it("places Generate beside the report heading rather than the Analysis card title", () => {
    renderChatView();

    const generate = screen.getByRole("button", { name: "analysis.generateSummary" });
    expect(screen.getByTestId("analysis-report-heading")).toContainElement(generate);
    expect(screen.getByText("chat.analysis").parentElement?.parentElement).not.toContainElement(generate);
  });

  it("opens as a non-modal floating panel instead of a right-side drawer", () => {
    renderChatView();

    const launcher = screen.getByRole("button", { name: "AI Assistant" });
    const panel = screen.getByTestId("compliance-ai-assistant");

    expect(launcher).toHaveAttribute("aria-expanded", "false");
    expect(launcher).toHaveClass(
      "dashboard-chat-launcher",
      "draggable-assistant-launcher",
      "fixed",
    );
    expect(launcher).toHaveAttribute("data-draggable-assistant", "true");
    expect(launcher).not.toHaveClass("right-6");
    expect(panel).toHaveAttribute("aria-hidden", "true");
    expect(panel).toHaveClass("dashboard-chat-panel", "fixed");
    expect(panel).not.toHaveClass("origin-bottom-right", "sm:right-6");
    expect(document.querySelector(".ant-drawer")).not.toBeInTheDocument();

    fireEvent.click(launcher);

    expect(launcher).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("dialog", { name: "chat.aiAssistant" })).toBeVisible();
    expect(panel).toHaveAttribute("aria-modal", "false");
  });

  it("preserves the draft when minimized and closes with Escape", () => {
    renderChatView();

    const panel = screen.getByTestId("compliance-ai-assistant");
    fireEvent.click(screen.getByRole("button", { name: "AI Assistant" }));

    const draft = screen.getByLabelText("Draft");
    fireEvent.change(draft, { target: { value: "unfinished question" } });
    fireEvent.click(screen.getByRole("button", { name: "assistant-close" }));

    expect(panel).toHaveAttribute("aria-hidden", "true");
    fireEvent.click(screen.getByRole("button", { name: "AI Assistant" }));
    expect(screen.getByLabelText("Draft")).toHaveValue("unfinished question");

    fireEvent.keyDown(window, { key: "Escape" });
    expect(panel).toHaveAttribute("aria-hidden", "true");
  });

  it("does not carry a page jump into a different report", async () => {
    const view = render(
      <ChatView
        activeFile={null}
        fileId="report-a"
        messages={[]}
        onSendMessage={vi.fn()}
        onClearChat={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "jump-page-77" }));
    expect(screen.getByTestId("pdf-viewer")).toHaveAttribute("data-target-page", "77");

    view.rerender(
      <ChatView
        activeFile={null}
        fileId="report-b"
        messages={[]}
        onSendMessage={vi.fn()}
        onClearChat={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("pdf-viewer")).toHaveAttribute(
        "data-file-url",
        "/api/files/report-b/pdf",
      );
      expect(screen.getByTestId("pdf-viewer")).toHaveAttribute("data-target-page", "1");
      expect(screen.getByTestId("pdf-viewer")).toHaveAttribute("data-target-page-nonce", "0");
    });
  });

  it("opens evidence in its source report instead of the currently viewed PDF", () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    renderChatView();

    fireEvent.click(screen.getByRole("button", { name: "jump-source-report" }));

    expect(open).toHaveBeenCalledWith(
      "/cross-analysis/evidence?file_id=report-b&page=9&name=Report+B.pdf",
      "_blank",
      "noopener,noreferrer",
    );
    open.mockRestore();
  });
});
