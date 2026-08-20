import React, { useState } from "react";

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ChatView from "../ChatView";

vi.mock("next/dynamic", () => ({
  default: () =>
    function MockPdfViewer() {
      return <div data-testid="pdf-viewer" />;
    },
}));

vi.mock("../AnalysisResults", () => ({
  default: ({ headerAction }: { headerAction?: React.ReactNode }) => (
    <div data-testid="analysis-results">
      <div data-testid="analysis-report-heading">{headerAction}</div>
    </div>
  ),
}));

vi.mock("../ComplianceSummaryDrawer", () => ({
  default: () => null,
}));

vi.mock("../ChatInterface", () => ({
  default: ({ onClose }: { onClose?: () => void }) => {
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

    const launcher = screen.getByRole("button", { name: "chat.aiAssistant" });
    const panel = screen.getByTestId("compliance-ai-assistant");

    expect(launcher).toHaveAttribute("aria-expanded", "false");
    expect(panel).toHaveAttribute("aria-hidden", "true");
    expect(panel).toHaveClass("fixed", "bottom-20");
    expect(document.querySelector(".ant-drawer")).not.toBeInTheDocument();

    fireEvent.click(launcher);

    expect(launcher).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("dialog", { name: "chat.aiAssistant" })).toBeVisible();
    expect(panel).toHaveAttribute("aria-modal", "false");
  });

  it("preserves the draft when minimized and closes with Escape", () => {
    renderChatView();

    const panel = screen.getByTestId("compliance-ai-assistant");
    fireEvent.click(screen.getByRole("button", { name: "chat.aiAssistant" }));

    const draft = screen.getByLabelText("Draft");
    fireEvent.change(draft, { target: { value: "unfinished question" } });
    fireEvent.click(screen.getByRole("button", { name: "assistant-close" }));

    expect(panel).toHaveAttribute("aria-hidden", "true");
    fireEvent.click(screen.getByRole("button", { name: "chat.aiAssistant" }));
    expect(screen.getByLabelText("Draft")).toHaveValue("unfinished question");

    fireEvent.keyDown(window, { key: "Escape" });
    expect(panel).toHaveAttribute("aria-hidden", "true");
  });
});
