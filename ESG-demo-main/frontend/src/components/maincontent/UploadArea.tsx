import React, { useRef } from "react";
import { Upload } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import type { UploadFile } from "antd/es/upload/interface";
import { useT } from "@/i18n/useT";

const { Dragger } = Upload;

interface UploadAreaProps {
  onBeforeUpload: (files: UploadFile[]) => void;
  uploadMode: "single" | "multi";
}

const UploadArea: React.FC<UploadAreaProps> = ({
  onBeforeUpload,
  uploadMode,
}) => {
  const { t } = useT();
  const batchKeyRef = useRef<string>("");

  const props = {
    name: "file",
    multiple: uploadMode === "multi",
    accept: ".pdf,application/pdf",
    showUploadList: false,
    beforeUpload: (file: any, fileList: any[]) => {
      const list = (fileList || []) as UploadFile[];
      const batchKey = list.map((item) => `${item.uid}:${item.name}`).join("|");
      const firstUid = list[0]?.uid;
      if (file?.uid === firstUid && batchKey && batchKeyRef.current !== batchKey) {
        batchKeyRef.current = batchKey;
        onBeforeUpload(list);
        window.setTimeout(() => {
          if (batchKeyRef.current === batchKey) batchKeyRef.current = "";
        }, 0);
      }
      return Upload.LIST_IGNORE;
    },
  };

  const modeLabel = uploadMode === "multi"
    ? t("upload.multiReport")
    : t("upload.singleReport");

  return (
    <div className="h-full w-full" data-testid="upload-area-shell">
      <Dragger
        {...props}
        aria-label={t("upload.draggerText")}
        className="group h-full w-full overflow-hidden rounded-xl [&_.ant-upload]:h-full [&_.ant-upload-btn]:h-full [&_.ant-upload-drag-container]:h-full"
        style={{
          height: "100%",
          borderColor: "#cbd5e1",
          borderRadius: 14,
          background: "rgba(248, 250, 252, 0.72)",
        }}
      >
        <div className="flex min-h-[180px] flex-col items-center justify-center px-6 py-8 text-center">
          <span className="mb-3 inline-flex h-11 w-11 items-center justify-center rounded-full bg-blue-50 text-[20px] text-[#2274BC] transition-colors group-hover:bg-blue-100">
            <InboxOutlined />
          </span>
          <p className="m-0 text-[15px] font-semibold tracking-[-0.01em] text-slate-800">
            {t("upload.draggerText")}
          </p>
          <p className="mb-0 mt-1.5 text-xs text-slate-500">PDF · {modeLabel}</p>
        </div>
      </Dragger>
    </div>
  );
};

export default UploadArea;
