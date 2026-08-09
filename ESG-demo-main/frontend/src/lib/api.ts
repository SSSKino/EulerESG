/**
 * ESG Backend API Service
 */

import { errorSummary } from "@/lib/logger";

// Prefer same-origin proxy via Next.js rewrites. If you need to bypass Next,
// set NEXT_PUBLIC_API_BASE_URL to a full backend URL.
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "";

class ApiRequestError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
  }
}

const wait = (milliseconds: number) =>
  new Promise<void>((resolve) => globalThis.setTimeout(resolve, milliseconds));

// Keep large multipart uploads out of the Next.js rewrite proxy. Resolve the
// exposed FastAPI port on the browser's current host so localhost and LAN
// access use the same code path without hard-coding a machine address.
const uploadApiBaseUrl = () => {
  const configured = process.env.NEXT_PUBLIC_UPLOAD_API_BASE_URL;
  if (configured) return configured.replace(/\/$/, "");
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return API_BASE_URL;
};

export interface AuthResponse {
  token: string;
  userId: number;
  name?: string;
}

export interface UploadResponse {
  status: string;
  report_id: string;
  file_id?: string;
  summary?: string;
  job_id?: string;
  message?: string;
  events_url?: string;
  processing_status_url?: string;
}

export interface ReportBatchUploadResponse {
  status: string;
  message?: string;
  batch_id: string;
  company_id: string;
  file_ids: string[];
  job_id: string;
  events_url?: string;
  processing_status_url?: string;
}

export interface CompanySummary {
  company_id: string;
  company_name: string;
  scope_config: {
    framework?: string;
    industry?: string;
    semi_industry?: string;
    gri_sector?: string;
    gri_topic?: string;
    scope_slugs?: string[];
  };
  report_ids: string[];
  report_count?: number;
  status: string;
  stale?: boolean;
  analysis_version?: number;
  assessment_outputs?: Array<{
    scope_key: string;
    json_filename: string;
    overall_score?: number;
    total_metrics?: number;
  }>;
}

export interface ReportBatchOptions {
  uploadMode: "single" | "multi";
  companyId?: string;
  companyName?: string;
  reportYears?: Array<number | null | undefined>;
  framework?: string;
  industry?: string;
  semiIndustry?: string;
  griSector?: string;
  griTopic?: string;
  scopeSlugs?: string;
}

export interface ReportJobEvent {
  job_id: string;
  file_id?: string;
  filename?: string;
  file_ids?: string[];
  company_id?: string;
  batch_id?: string;
  status: string;
  stage?: string;
  progress?: number;
  message?: string;
  error?: string | null;
  result?: any;
  seq?: number;
}

export interface MetricsUploadResponse {
  status: string;
  collection_id: string;
  metrics_count: number;
}

export interface ChatRequest {
  message: string;
  include_context?: boolean;
  session_id?: string;
  context?: any;
}

export interface ChatResponse {
  session_id: string;
  response: string;
  relevant_segments?: string[];
}

// Cross Analysis
export interface CrossReportMeta {
  file_id: string;
  filename?: string;
  uploaded_at?: string;
  framework?: string;
  industry?: string;
  semi_industry?: string;
}

export interface CrossSummaryResponse {
  reports: CrossReportMeta[];
  available: Record<string, string[]>;
}

export interface CrossCompareRequest {
  file_ids: string[];
  dimension: string;
  topic: string;
  metrics?: string[];
}

export interface CrossEvidenceSnippet {
  segment_id?: string;
  page_number?: number;
  content: string;
  evidence_type?: "text" | "table" | "chart" | "figure" | "image_text" | "chart_data";
  asset_id?: string;
  asset_url?: string;
  bbox?: [number, number, number, number] | null;
  caption?: string | null;
  confidence?: number | null;
  chart_data?: Record<string, unknown> | null;
}

export interface CrossMetricValue {
  name: string;
  value?: string | null;
  unit?: string | null;
  page?: number | null;
  evidence_segments?: string[];
}

export interface CrossCompareItem {
  meta: CrossReportMeta;
  metrics: CrossMetricValue[];
  summary: string;
  evidence: CrossEvidenceSnippet[];
}

export interface CrossCompareResponse {
  dimension: string;
  topic: string;
  results: CrossCompareItem[];
  insight: string;
}

export interface ComplianceAnalysisResponse {
  status: string;
  assessment: {
    report_id: string;
    total_metrics: number;
    overall_score: number;
    disclosure_summary: {
      fully_disclosed: number;
      partially_disclosed: number;
      not_disclosed: number;
    };
    report_path: string;
  };
}

export interface SystemStatus {
  status: string;
  components: {
    report_loaded: boolean;
    metrics_loaded: boolean;
    assessment_available: boolean;
    llm_configured: boolean;
  };
  report_info?: {
    document_id: string;
    segments_count: number;
  };
  metrics_info?: {
    collection_id: string;
    metrics_count: number;
  };
}

type GriOptionsResponse = {
  sectors: { slug: string; label: string }[];
  topicsBySector: Record<string, { slug: string; label: string }[]>;
};

class APIService {
  private assessmentByFileCache = new Map<string, Promise<any>>();
  private griOptionsCache: Promise<GriOptionsResponse> | null = null;
  private visualManifestCache = new Map<string, { etag?: string; data: any }>();
  private visualObjectUrlCache = new Map<string, Promise<string>>();

  private getAuthToken(): string | null {
    if (typeof window === "undefined") return null;
    return localStorage.getItem("auth_token");
  }

  private withAuth(options?: RequestInit): RequestInit {
    const headers = new Headers(options?.headers || {});
    const token = this.getAuthToken();
    if (token && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${token}`);
    }
    return { ...options, headers };
  }

  private async fetchWithError(url: string, options?: RequestInit) {
    const response = await fetch(url, this.withAuth(options));
    if (!response.ok) {
      const responseText = await response.text();
      let errorData: any = {};
      try {
        errorData = responseText ? JSON.parse(responseText) : {};
      } catch {
        // Next's proxy can return a plain-text/HTML 5xx response while the
        // backend is restarting. Do not expose that response body to the UI.
      }
      const rawDetail = errorData.detail ?? errorData.error;
      const detail = Array.isArray(rawDetail)
        ? rawDetail
            .map((item) => {
              if (typeof item === "string") return item;
              const location = Array.isArray(item?.loc) ? item.loc.join(".") : "request";
              return `${location}: ${item?.msg || JSON.stringify(item)}`;
            })
            .join("; ")
        : typeof rawDetail === "string"
          ? rawDetail
          : rawDetail
            ? JSON.stringify(rawDetail)
            : response.status >= 500
              ? `Backend temporarily unavailable (HTTP ${response.status})`
              : `HTTP ${response.status}`;
      throw new ApiRequestError(detail, response.status);
    }
    return response.json();
  }

  private assessmentCacheKey(fileId: string, scope?: string) {
    return `${fileId}::${(scope || "").trim()}`;
  }

  invalidateAssessmentByFileCache(fileId?: string, scope?: string) {
    if (fileId) {
      this.assessmentByFileCache.delete(this.assessmentCacheKey(fileId, scope));
      return;
    }
    this.assessmentByFileCache.clear();
  }

  async register(name: string, email: string, password: string): Promise<AuthResponse> {
    return this.fetchWithError(`${API_BASE_URL}/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, email, password }),
    });
  }

  async login(email: string, password: string): Promise<AuthResponse> {
    return this.fetchWithError(`${API_BASE_URL}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
  }

  // Get system status
  async getSystemStatus(): Promise<SystemStatus> {
    return this.fetchWithError(`${API_BASE_URL}/api/system/status`);
  }

  // Get file list
  async getFiles(fileType?: string, status?: string) {
    const params = new URLSearchParams();
    if (fileType) params.append("file_type", fileType);
    if (status) params.append("status", status);

    const url = `${API_BASE_URL}/api/files${params.toString() ? "?" + params.toString() : ""}`;
    return this.fetchWithError(url);
  }

  async getVisualAssets(fileId: string) {
    const cached = this.visualManifestCache.get(fileId);
    const headers = new Headers();
    if (cached?.etag) headers.set("If-None-Match", cached.etag);
    const response = await fetch(
      `${API_BASE_URL}/api/files/${fileId}/visual-assets`,
      this.withAuth({ headers })
    );
    if (response.status === 304 && cached) return cached.data;
    if (!response.ok) throw new Error(`Unable to load visual manifest (${response.status})`);
    const data = await response.json();
    this.visualManifestCache.set(fileId, { etag: response.headers.get("ETag") || undefined, data });
    return data;
  }

  async getVisualAssetBlob(fileId: string, assetId: string): Promise<Blob> {
    const response = await fetch(
      `${API_BASE_URL}/api/files/${fileId}/visual-assets/${encodeURIComponent(assetId)}`,
      this.withAuth()
    );
    if (!response.ok) throw new Error(`Unable to load visual evidence (${response.status})`);
    return response.blob();
  }

  getVisualAssetObjectUrl(fileId: string, assetId: string): Promise<string> {
    const key = `${fileId}::${assetId}`;
    const cached = this.visualObjectUrlCache.get(key);
    if (cached) return cached;
    const request = this.getVisualAssetBlob(fileId, assetId)
      .then((blob) => URL.createObjectURL(blob))
      .catch((error) => {
        this.visualObjectUrlCache.delete(key);
        throw error;
      });
    this.visualObjectUrlCache.set(key, request);
    return request;
  }

  invalidateVisualAssetCache(fileId?: string) {
    for (const [key, promise] of this.visualObjectUrlCache.entries()) {
      if (fileId && !key.startsWith(`${fileId}::`)) continue;
      promise.then((url) => URL.revokeObjectURL(url)).catch(() => undefined);
      this.visualObjectUrlCache.delete(key);
    }
    if (fileId) this.visualManifestCache.delete(fileId);
    else this.visualManifestCache.clear();
  }

  async reprocessReport(fileId: string): Promise<UploadResponse> {
    const result = await this.fetchWithError(`${API_BASE_URL}/api/reports/${fileId}/reprocess`, { method: "POST" });
    this.invalidateAssessmentByFileCache(fileId);
    this.invalidateVisualAssetCache(fileId);
    return result;
  }

  // Delete file; optional scope_key removes one multi-scope compliance row only (keeps PDF).
  async deleteFile(fileId: string, scopeKey?: string) {
    const q =
      scopeKey && String(scopeKey).trim()
        ? `?scope_key=${encodeURIComponent(String(scopeKey).trim())}`
        : "";
    const url = `${API_BASE_URL}/api/files/${fileId}${q}`;
    // DELETE is idempotent, so retry brief backend/proxy outages. This also
    // covers the case where the backend deleted the file but the response was
    // lost while the container or proxy was restarting.
    const maxAttempts = 3;

    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      try {
        const result = await this.fetchWithError(url, { method: "DELETE" });
        if (!scopeKey) this.invalidateVisualAssetCache(fileId);
        return result;
      } catch (error) {
        const status = error instanceof ApiRequestError ? error.status : undefined;

        // DELETE's requested end state is already satisfied when the record is
        // gone. Treat 404 as idempotent success so stale dashboard rows can be
        // removed instead of reappearing forever after a refresh race.
        if (status === 404) {
          if (!scopeKey) this.invalidateVisualAssetCache(fileId);
          return { status: "success", already_deleted: true };
        }

        const transient = status === undefined || status >= 500;
        if (!transient || attempt === maxAttempts) throw error;

        await wait(300 * 2 ** (attempt - 1));
      }
    }

    throw new Error("Unable to delete file");
  }

  // Upload PDF report (SASB: industry + semiIndustry; GRI: griSector + griTopic)
  // scopeSlugs: JSON string array of extra scopes — one PDF encode, separate compliance JSON per slug.
  async uploadReport(
    file: File,
    framework?: string,
    industry?: string,
    semiIndustry?: string,
    griSector?: string,
    griTopic?: string,
    scopeSlugs?: string
  ): Promise<UploadResponse> {
    const formData = new FormData();
    formData.append("file", file);
    if (framework) formData.append("framework", framework);
    if (industry) formData.append("industry", industry);
    if (semiIndustry) formData.append("semiIndustry", semiIndustry);
    if (griSector) formData.append("griSector", griSector);
    if (griTopic) formData.append("griTopic", griTopic);
    if (scopeSlugs) formData.append("scopeSlugs", scopeSlugs);
    return this.fetchWithError(`${uploadApiBaseUrl()}/api/upload-report`, {
      method: "POST",
      body: formData,
    });
  }

  async uploadReportBatch(
    files: File[],
    options: ReportBatchOptions
  ): Promise<ReportBatchUploadResponse> {
    const formData = new FormData();
    files.forEach((file) => formData.append("files", file));
    formData.append("uploadMode", options.uploadMode);
    if (options.companyId) formData.append("companyId", options.companyId);
    if (options.companyName) formData.append("companyName", options.companyName);
    if (options.reportYears) {
      formData.append(
        "reportYears",
        JSON.stringify(options.reportYears.map((year) => year ?? null))
      );
    }
    if (options.framework) formData.append("framework", options.framework);
    if (options.industry) formData.append("industry", options.industry);
    if (options.semiIndustry) formData.append("semiIndustry", options.semiIndustry);
    if (options.griSector) formData.append("griSector", options.griSector);
    if (options.griTopic) formData.append("griTopic", options.griTopic);
    if (options.scopeSlugs) formData.append("scopeSlugs", options.scopeSlugs);
    return this.fetchWithError(`${uploadApiBaseUrl()}/api/report-batches`, {
      method: "POST",
      body: formData,
    });
  }

  async getCompanies(): Promise<{ status: string; companies: CompanySummary[] }> {
    return this.fetchWithError(`${API_BASE_URL}/api/companies`);
  }

  async getCompany(companyId: string) {
    return this.fetchWithError(`${API_BASE_URL}/api/companies/${encodeURIComponent(companyId)}`);
  }

  async getCompanyAssessment(companyId: string, scope?: string) {
    const query = scope ? `?scope=${encodeURIComponent(scope)}` : "";
    return this.fetchWithError(
      `${API_BASE_URL}/api/companies/${encodeURIComponent(companyId)}/assessment${query}`
    );
  }

  async retryReportBatch(batchId: string): Promise<ReportBatchUploadResponse> {
    return this.fetchWithError(
      `${API_BASE_URL}/api/report-batches/${encodeURIComponent(batchId)}/retry`,
      { method: "POST" }
    );
  }


  async getReportJobStatus(jobId: string): Promise<{ status: string; job: ReportJobEvent }> {
    return this.fetchWithError(`${API_BASE_URL}/api/report-jobs/${encodeURIComponent(jobId)}`);
  }

  subscribeReportJob(
    jobId: string,
    handlers: {
      onEvent?: (event: ReportJobEvent) => void;
      onDone?: (event: ReportJobEvent) => void;
      onError?: (event: ReportJobEvent | Error) => void;
    }
  ): () => void {
    if (typeof window === "undefined") return () => {};

    let closed = false;
    let terminal = false;
    let lastSeq = 0;
    let source: EventSource | null = null;
    let pollTimer: number | null = null;

    const handleData = (data: ReportJobEvent | null) => {
      if (!data || closed || terminal) return;
      const seq = Number(data.seq || 0);
      if (seq && seq < lastSeq) return;
      if (seq) lastSeq = seq;

      handlers.onEvent?.(data);
      if (["success", "partial_success", "failed"].includes(data.status)) {
        terminal = true;
        if (data.status === "failed") handlers.onError?.(data);
        else handlers.onDone?.(data);
        cleanup();
      }
    };

    const parse = (event: MessageEvent): ReportJobEvent | null => {
      if (!event || typeof event.data !== "string" || !event.data.trim()) return null;
      try {
        return JSON.parse(event.data) as ReportJobEvent;
      } catch (error) {
        console.warn(`Failed to parse report job SSE event: ${errorSummary(error)}`);
        return null;
      }
    };

    const startPollingFallback = () => {
      // Next.js rewrites or proxies can sometimes buffer/close SSE streams.
      // Polling keeps the UI progress moving even when EventSource is unavailable.
      if (pollTimer) return;
      pollTimer = window.setInterval(async () => {
        if (closed || terminal) return;
        try {
          const result = await this.getReportJobStatus(jobId);
          handleData(result.job);
        } catch (error) {
          // Keep polling. A transient network failure should not hide progress forever.
          console.warn(`Report job polling failed: ${errorSummary(error)}`);
        }
      }, 2000);
    };

    const token = this.getAuthToken();
    const qs = token ? `?token=${encodeURIComponent(token)}` : "";

    try {
      source = new EventSource(`${API_BASE_URL}/api/report-jobs/${encodeURIComponent(jobId)}/events${qs}`);
      source.addEventListener("snapshot", (event) => handleData(parse(event as MessageEvent)));
      source.addEventListener("progress", (event) => handleData(parse(event as MessageEvent)));
      source.addEventListener("done", (event) => handleData(parse(event as MessageEvent)));
      source.addEventListener("error", (event) => {
        const data = parse(event as MessageEvent);
        if (data && data.status === "failed") {
          handleData(data);
          return;
        }
        // Browser EventSource also emits generic error events during reconnects.
        // Do not fail the upload UI here. Polling remains active as fallback.
        startPollingFallback();
      });
    } catch (error) {
      console.warn(`Report job SSE failed to start; using polling fallback: ${errorSummary(error)}`);
      startPollingFallback();
    }

    // Always enable polling fallback. If SSE works, duplicate events are ignored by seq/status.
    startPollingFallback();

    function cleanup() {
      closed = true;
      if (source) {
        source.close();
        source = null;
      }
      if (pollTimer) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    return cleanup;
  }

  async getAssessmentScopesForFile(fileId: string): Promise<{
    file_id: string;
    framework?: string;
    default_scope_key?: string | null;
    outputs: { scope_key: string; json_filename: string; overall_score: number }[];
  }> {
    return this.fetchWithError(`${API_BASE_URL}/api/assessment/${fileId}/scopes`, {
      method: "GET",
    });
  }

  // GRI options for Sector / Topic dropdowns
  async getGriOptions(): Promise<GriOptionsResponse> {
    if (!this.griOptionsCache) {
      this.griOptionsCache = this.fetchWithError(`${API_BASE_URL}/api/gri/options`, {
        method: "GET",
      }).catch((error) => {
        this.griOptionsCache = null;
        throw error;
      }) as Promise<GriOptionsResponse>;
    }
    return this.griOptionsCache;
  }

  // Upload ESG metrics - REMOVED: This function was never used and had misleading logic
  // that allowed uploading without a file (using "default metrics"), which could mask errors

  // Execute compliance analysis - REMOVED: This function was never called by the frontend

  // Send chat message
  async sendMessage(request: ChatRequest): Promise<ChatResponse> {
    return this.fetchWithError(`${API_BASE_URL}/api/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    });
  }

  // Send chat message for a specific file (uses /api/chat/{file_id})
  async sendMessageForFile(fileId: string, request: ChatRequest): Promise<ChatResponse> {
    return this.fetchWithError(`${API_BASE_URL}/api/chat/${fileId}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    });
  }

  // Cross Analysis summary
  async getCrossSummary(ids: string): Promise<CrossSummaryResponse> {
    return this.fetchWithError(`${API_BASE_URL}/api/cross/summary?ids=${encodeURIComponent(ids)}`);
  }

  // Cross Analysis compare
  async crossCompare(payload: CrossCompareRequest): Promise<CrossCompareResponse> {
    return this.fetchWithError(`${API_BASE_URL}/api/cross/compare`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  // 获取详细评估结果
  async getAssessment() {
    return this.fetchWithError(`${API_BASE_URL}/api/assessment`);
  }

  async getAssessmentByFile(fileId: string, scope?: string, forceRefresh = false) {
    const qs = scope ? `?scope=${encodeURIComponent(scope)}` : "";
    const cacheKey = this.assessmentCacheKey(fileId, scope);
    if (forceRefresh) {
      this.assessmentByFileCache.delete(cacheKey);
    } else {
      const cached = this.assessmentByFileCache.get(cacheKey);
      if (cached) return cached;
    }

    const request = this.fetchWithError(`${API_BASE_URL}/api/assessment/${fileId}${qs}`)
      .then((payload) => {
        const analyses = Array.isArray((payload as any)?.metric_analyses)
          ? (payload as any).metric_analyses
          : [];
        const status = String((payload as any)?.status ?? "").trim().toLowerCase();
        const shouldCache = !(status === "not_analyzed" || (analyses.length === 0 && status !== "success"));
        if (!shouldCache) {
          this.assessmentByFileCache.delete(cacheKey);
        }
        return payload;
      })
      .catch((error) => {
        this.assessmentByFileCache.delete(cacheKey);
        throw error;
      });

    this.assessmentByFileCache.set(cacheKey, request);
    return request;
  }

  prefetchAssessmentByFile(fileId?: string, scope?: string, forceRefresh = false) {
    if (!fileId) return;
    void this.getAssessmentByFile(fileId, scope, forceRefresh).catch(() => undefined);
  }

  // 获取最新的评估结果
  async getLatestAssessment() {
    return this.fetchWithError(`${API_BASE_URL}/api/assessment/latest`);
  }

  // 获取聊天历史 - REMOVED: This function was never called by the frontend

  // 获取最新的合规报告
  async getLatestReport() {
    return this.fetchWithError(`${API_BASE_URL}/api/reports/latest`);
  }

  // 根据文件ID获取合规报告
  async getReportByFileId(fileId: string, scope?: string) {
    const qs = scope ? `?scope=${encodeURIComponent(scope)}` : "";
    return this.fetchWithError(`${API_BASE_URL}/api/reports/${fileId}${qs}`);
  }
}

export const apiService = new APIService();
