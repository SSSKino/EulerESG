import React, { useRef } from "react";
import { Layout, Upload } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import type { UploadFile } from "antd/es/upload/interface";
import { useT } from "@/i18n/useT";

const { Content } = Layout;
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
    <Layout
      style={{
        margin: 12,
        padding: "0 12px 12px",
        background: "#fff",
        borderRadius: 10,
      }}
    >
      <Content
        style={{
          padding: 12,
          margin: 0,
          minHeight: 180,
          background: "#fff",
          borderRadius: 8,
        }}
      >
        <Dragger
          {...props}
          style={{
            padding: "20px 0",
          }}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">{t("upload.draggerText")}</p>
        </Dragger>
      </Content>
    </Layout>
  );
};

export default UploadArea;
