import React, { useRef } from "react";
import { Upload } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import type { UploadFile } from "antd/es/upload/interface";
import { useT } from "@/i18n/useT";

const { Dragger } = Upload;

interface UploadAreaProps {
  onBeforeUpload: (files: UploadFile[]) => void;
}

const UploadArea: React.FC<UploadAreaProps> = ({ onBeforeUpload }) => {
  const { t } = useT();
  const batchKeyRef = useRef<string>("");

  const props = {
    name: "file",
    multiple: true,
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

  return (
    <div className="app-card mb-4 overflow-hidden p-4 md:p-5">
      <Dragger {...props} className="app-upload-dragger" style={{ padding: "28px 0" }}>
        <p className="ant-upload-drag-icon">
          <InboxOutlined style={{ color: "var(--brand-primary)" }} />
        </p>
        <p className="ant-upload-text text-[var(--brand-text)]">{t("upload.draggerText")}</p>
      </Dragger>
    </div>
  );
};

export default UploadArea;
