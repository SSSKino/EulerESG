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
  status: "pending" | "ready" | "failed";
  url?: string;
  industry?: string;
  semiIndustry?: string;
  framework?: string;
  file_id?: string;
  backend_status?: string;
  // Keep as string for AntD Table rendering, but accept backend numeric variants during mapping.
  pages?: string;
}

/**
 * Backends are not always consistent in naming page count fields.
 * This helper normalizes common variants into a displayable string.
 */
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

interface FileStore {
  files: File[];
  selectedFileId: string | null;
  loading: boolean;
  lastRefresh: number;
  addFile: (file: File) => void;
  deleteFile: (fileId: string) => Promise<void>;
  updateFileStatus: (fileId: string, status: "pending" | "ready" | "failed") => void;
  updateFilePages: (fileId: string, pages: number) => void;
  setSelectedFileId: (fileId: string | null) => void;
  loadFilesFromBackend: () => Promise<void>;
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
          files: [...state.files, { ...file, status: "pending" }],
        })),
      updateFileStatus: (fileId, status) =>
        set((state) => ({
          files: state.files.map((file) =>
            file.file_id === fileId ? { ...file, status } : file
          ),
        })),
      updateFilePages: (fileId, pages) =>
        set((state) => ({
          files: state.files.map((file) =>
            file.file_id === fileId ? { ...file, pages: pages.toString() } : file
          ),
        })),
      deleteFile: async (fileId) => {
        try {
          console.log('Deleting file from backend:', fileId);
          const result = await apiService.deleteFile(fileId);
          console.log('Delete result:', result);

          // Remove from local state
          set((state) => ({
            files: state.files.filter((file) => file.file_id !== fileId),
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
      loadFilesFromBackend: async () => {
        try {
          set({ loading: true });
          console.log('Loading files from backend...');
          const response = await apiService.getFiles();
          console.log('Backend response:', response);
          if (response.status === 'success') {
            const backendFiles = response.files.map((file: any) => {
              console.log('Mapping file:', file);
              return {
                key: file.file_id,
                name: file.original_name,
                size: `${(file.file_size / 1024).toFixed(2)} KB`,
                dateUploaded: file.upload_time?.split('T')?.[0] || 'Unknown',
                type: file.original_name?.split('.')?.pop()?.toUpperCase() || 'Unknown',
                status: file.status === 'processed' ? 'ready' as const :
                       file.status === 'failed' ? 'failed' as const : 'pending' as const,
                file_id: file.file_id,
                backend_status: file.status,
                industry: file.industry || 'Unknown',
                semiIndustry: file.semi_industry || 'Unknown',
                // Normalize page count across backend field variants.
                pages: normalizeTotalPages(file),
                framework: file.framework || 'SASB'
              };
            });
            console.log('Mapped files:', backendFiles);
            
            // 合并现有的前端文件和后端文件
            // 保留前端添加的文件（可能还在上传中），更新已有的后端文件
            set((state) => {
              const existingFiles = state.files;
              const backendFileIds = new Set(backendFiles.map(f => f.file_id));

              // 保留前端文件中不在后端的文件（上传中的文件）
              const frontendOnlyFiles = existingFiles.filter(f => f.file_id && !backendFileIds.has(f.file_id));

              // 合并文件列表
              const mergedFiles = [...backendFiles, ...frontendOnlyFiles];

              // 检测是否有变化
              const hasChanges =
                state.files.length !== mergedFiles.length ||
                mergedFiles.some(newFile => {
                  const existingFile = state.files.find(f => f.file_id === newFile.file_id);
                  return !existingFile ||
                         existingFile.status !== newFile.status ||
                         existingFile.backend_status !== newFile.backend_status;
                });

              if (hasChanges) {
                console.log('🔄 File list updated - changes detected');
              }

              return {
                files: mergedFiles,
                lastRefresh: Date.now()
              };
            });
          }
        } catch (error) {
          console.error('Failed to load files from backend:', error);
        } finally {
          set({ loading: false });
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
