import React, { useEffect, useMemo, useState } from "react";
import { Table, Button, Space, Tag, Popconfirm, Badge } from "antd";
import type { ColumnsType, TablePaginationConfig } from "antd/es/table";
import { DeleteOutlined, CommentOutlined, SyncOutlined } from "@ant-design/icons";
import { useFileStore } from "@/store/useFileStore";
import type { File } from "@/store/useFileStore";
import { useT } from "@/i18n/useT";

interface FileTableProps {
  onChatClick: (file: File) => void;
  selectedRows: File[];
  onSelectionChange: (rows: File[]) => void;
}

function uploadSortKey(f: File): number {
  if (typeof f.uploadedAtMs === "number" && f.uploadedAtMs > 0) return f.uploadedAtMs;
  const p = Date.parse(f.dateUploaded);
  return Number.isFinite(p) ? p : 0;
}

function sortFilesForDisplay(files: File[]): File[] {
  return [...files].sort((a, b) => {
    const tb = uploadSortKey(b);
    const ta = uploadSortKey(a);
    if (tb !== ta) return tb - ta;
    const nc = (a.name || "").localeCompare(b.name || "");
    if (nc !== 0) return nc;
    return (a.key || "").localeCompare(b.key || "");
  });
}

const FileTable: React.FC<FileTableProps> = ({ onChatClick, selectedRows, onSelectionChange }) => {
  const { t, lang } = useT();
  const locale = lang === "zh" ? "zh-CN" : "en-US";

  const files = useFileStore((state) => state.files);
  const loading = useFileStore((state) => state.loading);
  const lastRefresh = useFileStore((state) => state.lastRefresh);
  const loadFilesFromBackend = useFileStore((state) => state.loadFilesFromBackend);

  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  useEffect(() => {
    const timer = setInterval(() => {
      const state = useFileStore.getState();
      const hasUnfinished = state.files.some((f) => f.status === "pending" || f.status === "partial");
      if (hasUnfinished && !state.loading) {
        void state.loadFilesFromBackend();
      }
    }, 4000);
    return () => clearInterval(timer);
  }, []);

  const dataSource = useMemo(() => sortFilesForDisplay(files), [files]);

  useEffect(() => {
    const totalPages = Math.max(1, Math.ceil(dataSource.length / pageSize));
    setCurrentPage((prev) => Math.min(prev, totalPages));
  }, [dataSource.length, pageSize]);

  const statusText = (file: File) => {
    const s = file.status;
    if (s === "ready") return t("files.status.ready");
    if (s === "failed") return t("files.status.failed");
    if (s === "partial") {
      const unk = file.scope_analysis_unknown_total === true;
      const done = Number(file.scope_analysis_completed ?? 0);
      const total = Number(file.scope_analysis_total ?? 0);
      if (unk || !total) {
        return t("files.status.partialUnknown", { n: String(done) });
      }
      return t("files.status.partial", { done: String(done), total: String(total) });
    }
    return t("files.status.pending");
  };

  const statusColor = (s: string | undefined) => {
    if (s === "ready") return "success";
    if (s === "failed") return "error";
    if (s === "partial") return "processing";
    return "warning";
  };

  const frameworkTagColor = (fw: string | undefined) => {
    const f = (fw || "").trim();
    if (f === "SASB") return "blue";
    if (f === "GRI") return "green";
    if (f === "TCFD") return "purple";
    if (f === "CDP") return "gold";
    return "default";
  };

  const renderUnknown = (v: any) =>
    v && v !== "Unknown" && v !== "未知" ? v : t("common.unknown");

  const columns: ColumnsType<File> = [
    { title: t("files.columns.name"), dataIndex: "name", key: "name" },
    { title: t("files.columns.size"), dataIndex: "size", key: "size" },
    {
      title: t("files.columns.dateUploaded"),
      dataIndex: "dateUploaded",
      key: "dateUploaded",
      render: (v: any) => (v && v !== "Unknown" && v !== "未知" ? v : t("common.unknown")),
    },
    {
      title: t("files.columns.type"),
      dataIndex: "type",
      key: "type",
      render: (v: any) => (v && v !== "Unknown" && v !== "未知" ? v : t("common.unknown")),
    },
    {
      title: t("files.columns.pages"),
      dataIndex: "pages",
      key: "pages",
      render: (_: any, record: File) =>
        record.pages && record.pages !== "-" ? record.pages : t("common.na"),
    },
    {
      title: t("files.columns.framework"),
      dataIndex: "framework",
      key: "framework",
      render: (v: string | undefined) => {
        const fw = (v || "").trim() || t("common.unknown");
        return <Tag color={frameworkTagColor(v)}>{fw}</Tag>;
      },
    },
    {
      title: t("files.columns.industry"),
      dataIndex: "industry",
      key: "industry",
      render: (v: any, record: File) => {
        const fw = (record.framework || "").trim();
        if (fw === "CDP" || fw === "TCFD") {
          return renderUnknown(record.semiIndustry);
        }
        return renderUnknown(v);
      },
    },
    {
      title: t("files.columns.subOption"),
      dataIndex: "semiIndustry",
      key: "semiIndustry",
      render: (v: any, record: File) => {
        const fw = (record.framework || "").trim();
        return fw === "CDP" || fw === "TCFD" ? t("common.na") : renderUnknown(v);
      },
    },
    {
      title: t("files.columns.status"),
      key: "status",
      render: (_: any, file: File) => <Tag color={statusColor(file.status)}>{statusText(file)}</Tag>,
    },
    {
      title: t("files.columns.actions"),
      key: "actions",
      render: (_: any, file: File) => (
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
              await useFileStore.getState().deleteFile(file.file_id!, file.analysis_scope_key);
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

  const lastUpdatedText =
    lastRefresh > 0
      ? t("files.lastUpdated", { time: new Date(lastRefresh).toLocaleTimeString(locale) })
      : null;

  const pagination: TablePaginationConfig = {
    current: currentPage,
    pageSize,
    defaultPageSize: 10,
    showSizeChanger: true,
    pageSizeOptions: ["10", "20", "50", "100"],
    showLessItems: false,
    showTotal: (total) => `${total}`,
    onChange: (page, nextPageSize) => {
      const resolvedPageSize = nextPageSize || pageSize;
      if (resolvedPageSize !== pageSize) {
        setPageSize(resolvedPageSize);
        const firstIndex = (page - 1) * resolvedPageSize;
        const nextCurrent = Math.floor(firstIndex / resolvedPageSize) + 1;
        setCurrentPage(nextCurrent);
      } else {
        setCurrentPage(page);
      }
    },
  };

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
      <div className="overflow-x-auto px-1 pb-2">
        {dataSource.length === 0 && !loading ? (
          <div className="p-6 text-center text-gray-500">{t("common.noDataAvailable")}</div>
        ) : (
          <Table
            columns={columns}
            dataSource={dataSource}
            pagination={pagination}
            scroll={{ x: "max-content", scrollToFirstRowOnChange: true }}
            size="small"
            className="w-full dashboard-file-table"
            rowKey={(record) => record.key}
            loading={loading}
            rowSelection={{
              selectedRowKeys: selectedRows.map((r) => r.key),
              onChange: (_keys, rows) => onSelectionChange(rows as File[]),
            }}
          />
        )}
      </div>
    </div>
  );
};

export default FileTable;
