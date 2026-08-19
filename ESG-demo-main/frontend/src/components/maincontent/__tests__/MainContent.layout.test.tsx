import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MainContent from "../MainContent";

const mocks = vi.hoisted(() => ({
  getFieldsValue: vi.fn(() => ({})),
  resetFields: vi.fn(),
  validateFields: vi.fn(),
}));

vi.mock("antd", async () => {
  const React = await import("react");

  const Content = ({ children, style }: Record<string, any>) =>
    React.createElement(
      "div",
      { "data-testid": "mock-layout-content", style },
      children,
    );
  const Layout = Object.assign(
    ({ children, className, style }: Record<string, any>) =>
      React.createElement(
        "div",
        { className, "data-testid": "upload-area-layout", style },
        children,
      ),
    { Content },
  );
  const Dragger = ({ children, style }: Record<string, any>) =>
    React.createElement(
      "div",
      { "data-testid": "mock-upload-dragger", style },
      children,
    );

  return {
    Form: {
      useForm: () => [
        {
          getFieldsValue: mocks.getFieldsValue,
          resetFields: mocks.resetFields,
          validateFields: mocks.validateFields,
        },
      ],
    },
    Progress: () => React.createElement("div", { "data-testid": "progress" }),
    Layout,
    Upload: {
      Dragger,
      LIST_IGNORE: Symbol("LIST_IGNORE"),
    },
    message: {
      destroy: vi.fn(),
      error: vi.fn(),
      open: vi.fn(),
      success: vi.fn(),
    },
  };
});

vi.mock("@ant-design/icons", async () => {
  const React = await import("react");
  return {
    InboxOutlined: () =>
      React.createElement("span", { "aria-hidden": "true" }),
  };
});

vi.mock("@/i18n/useT", () => ({
  useT: () => ({ t: (key: string) => key }),
}));

vi.mock("@/lib/api", () => ({
  apiService: {
    subscribeReportJob: vi.fn(),
    uploadReport: vi.fn(),
    uploadReportBatch: vi.fn(),
  },
}));

vi.mock("@/store/useFileStore", () => ({
  useFileStore: {
    getState: () => ({ loadFilesFromBackend: vi.fn() }),
  },
}));

vi.mock("../UploadOptionsModal", () => ({
  default: () => null,
}));

vi.mock("../FrameworkReferencePanel", () => ({
  default: () => (
    <aside
      aria-labelledby="framework-views-title"
      data-testid="mock-framework-reference-panel"
    >
      <h2 id="framework-views-title">Framework references</h2>
    </aside>
  ),
}));

describe("MainContent upload and framework reference layout", () => {
  beforeEach(() => {
    mocks.getFieldsValue.mockReturnValue({});
  });

  it("stacks on small screens and becomes a two-column grid on large screens", () => {
    render(<MainContent uploadMode="single" />);

    const layout = screen.getByTestId("upload-framework-layout");
    const classTokens = layout.className.split(/\s+/);

    expect(classTokens).toEqual(
      expect.arrayContaining([
        "grid",
        "grid-cols-1",
        "items-stretch",
      ]),
    );
    expect(classTokens.some((token) => token.startsWith("lg:grid-cols-"))).toBe(true);
  });

  it("keeps an equal-height upload column before the framework reference aside", () => {
    render(<MainContent uploadMode="single" />);

    const layout = screen.getByTestId("upload-framework-layout");
    const dropzoneRegion = screen.getByTestId("upload-dropzone-region");
    const uploadArea = dropzoneRegion.firstElementChild as HTMLElement | null;
    const referencePanel = screen.getByTestId("mock-framework-reference-panel");

    expect(dropzoneRegion.tagName).toBe("SECTION");
    expect(dropzoneRegion).toHaveClass("flex", "h-full", "min-w-0");
    expect(uploadArea).toBeInstanceOf(HTMLElement);
    expect(uploadArea).toHaveClass("h-full", "w-full");
    expect(dropzoneRegion).toContainElement(uploadArea);
    expect(referencePanel.tagName).toBe("ASIDE");
    expect(Array.from(layout.children)).toEqual([dropzoneRegion, referencePanel]);
  });
});
