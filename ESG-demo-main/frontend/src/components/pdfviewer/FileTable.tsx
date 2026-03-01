import React, { useEffect, useMemo } from "react";
import { Table, Button, Space, Tag, Popconfirm, Badge } from "antd";
import type { ColumnsType } from "antd/es/table";
import { DeleteOutlined, CommentOutlined, SyncOutlined } from "@ant-design/icons";
import { useFileStore } from "@/store/useFileStore";
import type { File } from "@/store/useFileStore";
import { useT } from "@/i18n/useT";

interface FileTableProps {
  onChatClick: (file: File) => void;
  selectedRows: File[];
  onSelectionChange: (rows: File[]) => void;
}

const FileTable: React.FC<FileTableProps> = ({ onChatClick, selectedRows, onSelectionChange }) => {
  const { t, lang } = useT();
  const locale = lang === "zh" ? "zh-CN" : "en-US";

  const files = useFileStore((state) => state.files);
  const loading = useFileStore((state) => state.loading);
  const lastRefresh = useFileStore((state) => state.lastRefresh);
  const loadFilesFromBackend = useFileStore((state) => state.loadFilesFromBackend);

  useEffect(() => {
    loadFilesFromBackend();
  }, [loadFilesFromBackend]);

  const columns: ColumnsType<File> = useMemo(() => {
    const statusText = (s: string | undefined) => {
      if (s === "ready") return t("files.status.ready");
      if (s === "failed") return t("files.status.failed");
      return t("files.status.pending");
    };

    const statusColor = (s: string | undefined) => {
      if (s === "ready") return "success";
      if (s === "failed") return "error";
      return "warning";
    };

    return [
      { title: t("files.columns.name"), dataIndex: "name", key: "name" },
      { title: t("files.columns.size"), dataIndex: "size", key: "size" },
      { title: t("files.columns.dateUploaded"), dataIndex: "dateUploaded", key: "dateUploaded", render: (v: any) => (v && v !== "Unknown" && v !== "未知" ? v : t("common.unknown")) },
      { title: t("files.columns.type"), dataIndex: "type", key: "type", render: (v: any) => (v && v !== "Unknown" && v !== "未知" ? v : t("common.unknown")) },
      {
        title: t("files.columns.pages"),
        dataIndex: "pages",
        key: "pages",
        render: (_, record) => (record.pages && record.pages !== "-" ? record.pages : t("common.na")),
      },
      { title: t("files.columns.industry"), dataIndex: "industry", key: "industry", render: (v: any) => (v && v !== "Unknown" && v !== "未知" ? v : t("common.unknown")) },
      { title: t("files.columns.subIndustry"), dataIndex: "semiIndustry", key: "semiIndustry", render: (v: any) => (v && v !== "Unknown" && v !== "未知" ? v : t("common.unknown")) },
      {
        title: t("files.columns.framework"),
        dataIndex: "framework",
        key: "framework",
        render: (framework: string) => (
          <Tag
            color={
              framework === "SASB"
                ? "blue"
                : framework === "GRI"
                  ? "green"
                  : framework === "TCFD"
                    ? "purple"
                    : "default"
            }
          >
            {framework}
          </Tag>
        ),
      },
      {
        title: t("files.columns.status"),
        key: "status",
        render: (_, file) => <Tag color={statusColor(file.status)}>{statusText(file.status)}</Tag>,
      },
      {
        title: t("files.columns.actions"),
        key: "actions",
        render: (_, file) => (
          <Space>
            <Button
              type="primary"
              size="small"
              icon={<CommentOutlined />}
              onClick={() => onChatClick(file)}
              disabled={file.status !== "ready"}
            >
              {t("files.actions.chat")}
            </Button>
            <Popconfirm
              title={t("files.deleteTitle")}
              description={t("files.deleteDesc")}
              onConfirm={async () => {
                await useFileStore.getState().deleteFile(file.file_id!);
              }}
              okText={t("common.yes")}
              cancelText={t("common.no")}
            >
              <Button type="default" danger size="small" icon={<DeleteOutlined />}>
                {t("files.actions.delete")}
              </Button>
            </Popconfirm>
          </Space>
        ),
      },
    ];
  }, [t]);

  const lastUpdatedText =
    lastRefresh > 0
      ? t("files.lastUpdated", { time: new Date(lastRefresh).toLocaleTimeString(locale) })
      : null;

  return (
    <div className="mt-4 bg-white rounded-lg shadow-sm">
      <div className="p-3 border-b border-gray-200 flex justify-between items-center">
        <h3 className="text-lg font-semibold text-gray-700">{t("files.title")}</h3>
        <div className="flex items-center space-x-2">
          {loading && <Badge status="processing" text={t("common.loading")} />}
          <Button
            size="small"
            icon={<SyncOutlined spin={loading} />}
            onClick={() => loadFilesFromBackend()}
            disabled={loading}
          >
            {t("common.refresh")}
          </Button>
          {lastUpdatedText && <span className="text-xs text-gray-500">{lastUpdatedText}</span>}
        </div>
      </div>
      <div className="overflow-x-auto">
        <Table
          columns={columns}
          dataSource={files}
          pagination={false}
          className="w-full"
          rowKey={(record) => record.file_id || record.key}
          loading={loading}
          rowSelection={{
            selectedRowKeys: selectedRows.map((r) => r.file_id || r.key),
            onChange: (_keys, rows) => onSelectionChange(rows as File[]),
          }}
        />
      </div>
    </div>
  );
};

export default FileTable;
