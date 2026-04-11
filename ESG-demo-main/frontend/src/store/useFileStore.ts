// src/store/useFileStore.ts
import { create } from "zustand";
import { persist } from "zustand/middleware";
import { apiService } from "@/lib/api";

export interface File {
  key: string;
  name: string;
  size: string;
  dateUploaded: string;
  type: string;
  status: "pending" | "ready" | "failed" | "partial";
  url?: string;
  industry?: string;
  semiIndustry?: string;
  framework?: string;
  /** GRI sector slug (for cross-analysis compatibility: same sector+topic only). */
  gri_sector?: string;
  /** GRI topic slug (for cross-analysis compatibility: same sector+topic only). */
  gri_topic?: string;
  file_id?: string;
  backend_status?: string;
  /** Multi-scope compliance progress (from GET /api/files enrichment). */
  scope_analysis_completed?: number;
  scope_analysis_total?: number;
  scope_analysis_partial?: boolean;
  scope_analysis_all_done?: boolean;
  scope_analysis_unknown_total?: boolean;
  /** When set, this row is one scope of a multi-scope upload; used for assessment + chat URL. */
  analysis_scope_key?: string;
  // Keep as string for AntD Table rendering, but accept backend numeric variants during mapping.
  pages?: string;
  /** Exact client-side upload key used to reconcile the optimistic row with backend metadata. */
  client_upload_key?: string;
  /** Epoch ms from upload_time (or client clock when queued); newest-first sorting. */
  uploadedAtMs?: number;
}

/**
 * Whether the given files can be used together for cross-analysis:
 * - Same framework only.
 * - For GRI, same Sector + same Topic only.
 * - For CDP / TCFD, same topic slug (semiIndustry) only.
 */
export function canCrossAnalyzeFiles(files: File[]): boolean {
  if (files.length < 2) return false;
  const frameworks = files.map((f) => (f.framework || "").trim());
  if (new Set(frameworks).size > 1) return false;
  if (frameworks[0] === "GRI") {
    const sectors = files.map((f) => (f.gri_sector ?? f.industry ?? "").toString().trim());
    const topics = files.map((f) => (f.gri_topic ?? f.semiIndustry ?? "").toString().trim());
    if (new Set(sectors).size > 1 || new Set(topics).size > 1) return false;
  }
  if (frameworks[0] === "CDP" || frameworks[0] === "TCFD") {
    const topics = files.map((f) => (f.semiIndustry ?? "").toString().trim());
    if (topics.some((x) => !x)) return false;
    if (new Set(topics).size > 1) return false;
  }
  return true;
}

/**
 * Backends are not always consistent in naming page count fields.
 * This helper normalizes common variants into a displayable string.
 */
/** Convert backend gri_sector/gri_topic slug to display label (e.g. oil_and_gas_sector -> Oil And Gas Sector) */
function slugToLabel(slug: string | undefined | null): string {
  if (slug == null || slug === "") return "";
  return slug
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();
}

function normalizeTotalPages(file: any): string {
  const candidates = [
    file?.total_pages,
    file?.totalPages,
    file?.page_count,
    file?.pageCount,
    file?.pages,
    file?.pages_count,
    file?.num_pages,
    file?.n_pages,
    file?.metadata?.total_pages,
    file?.meta?.total_pages,
  ];

  const val = candidates.find((v) => v !== undefined && v !== null && v !== "");
  if (val === undefined || val === null || val === "") return "-";

  // Accept number-like strings.
  const n = typeof val === "string" ? Number(val) : val;
  if (typeof n === "number" && Number.isFinite(n)) return String(Math.trunc(n));
  return String(val);
}


function normalizeMatchString(value: string | undefined | null): string {
  return (value ?? "").toString().trim().toLowerCase();
}

function parseDisplaySizeToBytes(size: string | undefined | null): number {
  if (!size) return 0;
  const m = String(size).trim().match(/^([\d.]+)\s*(KB|MB|GB|B)$/i);
  if (!m) return 0;
  const value = Number(m[1]);
  if (!Number.isFinite(value)) return 0;
  const unit = m[2].toUpperCase();
  if (unit == 'GB') return value * 1024 * 1024 * 1024;
  if (unit == 'MB') return value * 1024 * 1024;
  if (unit == 'KB') return value * 1024;
  return value;
}

function sameLogicalUpload(frontendFile: File, backendFile: File): boolean {
  if (frontendFile.file_id) return frontendFile.file_id === backendFile.file_id;

  const frontClientKey = normalizeMatchString(frontendFile.client_upload_key);
  const backClientKey = normalizeMatchString(backendFile.client_upload_key);
  if (frontClientKey && backClientKey) {
    return frontClientKey === backClientKey;
  }

  const sameName = normalizeMatchString(frontendFile.name) === normalizeMatchString(backendFile.name);
  if (!sameName) return false;

  const frontSizeBytes = parseDisplaySizeToBytes(frontendFile.size);
  const backSizeBytes = parseDisplaySizeToBytes(backendFile.size);
  if (frontSizeBytes > 0 && backSizeBytes > 0) {
    const delta = Math.abs(frontSizeBytes - backSizeBytes);
    if (delta > 2048) return false;
  } else if (normalizeMatchString(frontendFile.size) !== normalizeMatchString(backendFile.size)) {
    return false;
  }

  if (normalizeMatchString(frontendFile.framework) !== normalizeMatchString(backendFile.framework)) {
    return false;
  }
  if (normalizeMatchString(frontendFile.industry) !== normalizeMatchString(backendFile.industry)) {
    return false;
  }
  if (normalizeMatchString(frontendFile.semiIndustry) !== normalizeMatchString(backendFile.semiIndustry)) {
    return false;
  }

  const frontMs = frontendFile.uploadedAtMs ?? 0;
  const backMs = backendFile.uploadedAtMs ?? 0;
  if (frontMs > 0 && backMs > 0) {
    const TEN_MINUTES_MS = 10 * 60 * 1000;
    return Math.abs(frontMs - backMs) <= TEN_MINUTES_MS;
  }

  return true;
}

function parseUploadTimeMs(raw: string | undefined | null): number {
  if (raw == null || raw === "") return 0;
  const ms = Date.parse(raw);
  return Number.isFinite(ms) ? ms : 0;
}

function mapBackendReportStatus(file: any): "pending" | "ready" | "failed" | "partial" {
  const raw = file?.status;
  if (raw === "failed") return "failed";
  if (raw === "processed") return "ready";
  const partial = file?.scope_analysis_partial === true;
  const allDone = file?.scope_analysis_all_done === true;
  if (partial) return "partial";
  if (allDone) return "ready";
  return "pending";
}

/** Backend sends one record per PDF; expand to one table row per scope when scope_rows.length > 1. */
function expandMultiScopeBackendRows(file: any, mapped: File): File[] {
  if (file?.status === "failed") return [mapped];
  const sr = file?.scope_rows;
  if (!Array.isArray(sr) || sr.length <= 1) return [mapped];

  return sr.map((r: any) => {
    const sk = String(r?.scope_key ?? "").trim();
    const label = typeof r?.label === "string" && r.label.trim() ? r.label.trim() : slugToLabel(sk);
    const ready = r?.ready === true;
    const fw = (mapped.framework || "").trim();
    return {
      ...mapped,
      key: `${file.file_id}::${sk}`,
      semiIndustry: label,
      gri_topic: fw === "GRI" && sk ? sk : mapped.gri_topic,
      analysis_scope_key: sk || undefined,
      status: ready ? ("ready" as const) : ("pending" as const),
      scope_analysis_completed: undefined,
      scope_analysis_total: undefined,
      scope_analysis_partial: false,
      scope_analysis_all_done: ready,
      scope_analysis_unknown_total: false,
    };
  });
}

interface FileStore {
  files: File[];
  selectedFileId: string | null;
  loading: boolean;
  lastRefresh: number;
  addFile: (file: File) => void;
  removeFileByKey: (key: string) => void;
  deleteFile: (fileId: string, scopeKey?: string) => Promise<void>;
  updateFileStatus: (
    fileIdOrKey: string,
    status: "pending" | "ready" | "failed" | "partial",
  ) => void;
  updateFilePages: (fileIdOrKey: string, pages: number) => void;
  setSelectedFileId: (fileId: string | null) => void;
  loadFilesFromBackend: (options?: { silent?: boolean }) => Promise<void>;
  setLoading: (loading: boolean) => void;
  clearFiles: () => void;
}

export const useFileStore = create<FileStore>()(
  persist(
    (set, get) => ({
      files: [],
      selectedFileId: null,
      loading: false,
      lastRefresh: 0,
      setLoading: (loading) => set({ loading }),
      clearFiles: () =>
        set(() => ({
          files: [],
          selectedFileId: null,
          lastRefresh: 0,
        })),
      addFile: (file) =>
        set((state) => ({
          files: [...state.files, { ...file, status: file.status ?? "pending" }],
        })),
      removeFileByKey: (key) =>
        set((state) => ({
          files: state.files.filter((file) => file.key !== key),
        })),
      updateFileStatus: (fileIdOrKey, status) =>
        set((state) => ({
          files: state.files.map((file) =>
            file.file_id === fileIdOrKey || file.key === fileIdOrKey ? { ...file, status } : file
          ),
        })),
      updateFilePages: (fileIdOrKey, pages) =>
        set((state) => ({
          files: state.files.map((file) =>
            file.file_id === fileIdOrKey || file.key === fileIdOrKey
              ? { ...file, pages: pages.toString() }
              : file
          ),
        })),
      deleteFile: async (fileId, scopeKey) => {
        try {
          console.log("Deleting file from backend:", fileId, scopeKey);
          const result = await apiService.deleteFile(fileId, scopeKey);
          console.log("Delete result:", result);

          const sk = scopeKey && String(scopeKey).trim() ? String(scopeKey).trim() : "";
          set((state) => ({
            files: state.files.filter((file) => {
              if (file.file_id !== fileId) return true;
              if (sk) return file.analysis_scope_key !== sk;
              return false;
            }),
          }));

          // Refresh file list from backend
          await get().loadFilesFromBackend();
        } catch (error) {
          console.error('Failed to delete file from backend:', error);
          throw error;
        }
      },
      setSelectedFileId: (fileId) =>
        set(() => ({
          selectedFileId: fileId,
        })),
      loadFilesFromBackend: async (options) => {
        const silent = options?.silent === true;
        try {
          if (!silent) {
            set({ loading: true });
          }
          console.log('Loading files from backend...');
          const response = await apiService.getFiles();
          console.log('Backend response:', response);
          if (response.status === 'success') {
            const backendFiles: File[] = [];
            for (const file of response.files as any[]) {
              console.log('Mapping file:', file);
              const mapped: File = {
                key: file.file_id,
                name: file.original_name,
                size: (typeof file.file_size === "number" && Number.isFinite(file.file_size)) ? `${(file.file_size / 1024).toFixed(2)} KB` : "-",
                dateUploaded: file.upload_time?.split("T")?.[0] || "",
                uploadedAtMs: parseUploadTimeMs(file.upload_time),
                client_upload_key:
                  typeof file.client_upload_key === "string" && file.client_upload_key.trim()
                    ? file.client_upload_key.trim()
                    : undefined,
                type: file.original_name?.split('.')?.pop()?.toUpperCase() || '',
                status: mapBackendReportStatus(file),
                file_id: file.file_id,
                backend_status: file.status,
                industry:
                  file.framework === "GRI" && (file.gri_sector || file.gri_topic)
                    ? slugToLabel(file.gri_sector) || "GRI"
                    : file.framework === "CDP"
                      ? "CDP"
                      : file.framework === "TCFD"
                        ? "TCFD"
                        : (file.industry || ""),
                semiIndustry:
                  file.framework === "GRI" && (file.gri_sector || file.gri_topic)
                    ? slugToLabel(file.gri_topic)
                    : (file.semi_industry || ""),
                pages: normalizeTotalPages(file),
                framework: file.framework || "SASB",
                gri_sector: file.gri_sector ?? undefined,
                gri_topic: file.gri_topic ?? undefined,
                scope_analysis_completed:
                  typeof file.scope_analysis_completed === "number"
                    ? file.scope_analysis_completed
                    : undefined,
                scope_analysis_total:
                  typeof file.scope_analysis_total === "number"
                    ? file.scope_analysis_total
                    : undefined,
                scope_analysis_partial: file.scope_analysis_partial === true,
                scope_analysis_all_done: file.scope_analysis_all_done === true,
                scope_analysis_unknown_total: file.scope_analysis_unknown_total === true,
              };
              backendFiles.push(...expandMultiScopeBackendRows(file, mapped));
            }
            console.log('Mapped files:', backendFiles);
            
            // 合并现有的前端文件和后端文件
            // 保留前端添加的文件（可能还在上传中），更新已有的后端文件
            set((state) => {
              const existingFiles = state.files;
              const backendFileIds = new Set(
                backendFiles.map((f: File) => f.file_id).filter(Boolean) as string[],
              );

              // 保留前端临时文件，但如果后端已经返回了同一次上传的真实记录，则移除临时占位行
              const frontendOnlyFiles = existingFiles.filter((f: File) => {
                if (!f.file_id) {
                  if (!(f.status === "pending" || f.status === "failed")) {
                    return false;
                  }
                  const matchedBackendFile = backendFiles.find((bf: File) => sameLogicalUpload(f, bf));
                  return !matchedBackendFile;
                }
                return !backendFileIds.has(f.file_id);
              });

              // 合并文件列表
              const mergedFiles = [...backendFiles, ...frontendOnlyFiles];

              // 检测是否有变化
              const hasChanges =
                state.files.length !== mergedFiles.length ||
                mergedFiles.some((newFile) => {
                  const existingFile = state.files.find((f) => f.key === newFile.key);
                  return !existingFile ||
                         existingFile.status !== newFile.status ||
                         existingFile.backend_status !== newFile.backend_status ||
                         existingFile.scope_analysis_completed !== newFile.scope_analysis_completed ||
                         existingFile.scope_analysis_total !== newFile.scope_analysis_total ||
                         existingFile.scope_analysis_partial !== newFile.scope_analysis_partial ||
                         existingFile.analysis_scope_key !== newFile.analysis_scope_key;
                });

              if (hasChanges) {
                console.log('🔄 File list updated - changes detected');
                return {
                  files: mergedFiles,
                  lastRefresh: Date.now(),
                };
              }

              return state;
            });
          }
        } catch (error) {
          console.error('Failed to load files from backend:', error);
        } finally {
          if (!silent) {
            set({ loading: false });
          }
        }
      }
    }),
    {
      name: "file-storage",
      partialize: (state) => ({
        selectedFileId: state.selectedFileId,
      }),
    }
  )
);
