/**
 * ESG Backend API Service
 */

// Prefer same-origin proxy via Next.js rewrites. If you need to bypass Next,
// set NEXT_PUBLIC_API_BASE_URL to a full backend URL.
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "";

export interface AuthResponse {
  token: string;
  userId: number;
  name?: string;
}

export interface UploadResponse {
  status: string;
  report_id: string;
  file_id?: string;
  summary: string;
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
      const errorData = await response.json().catch(() => ({ detail: "Unknown error" }));
      throw new Error(errorData.detail || errorData.error || `HTTP ${response.status}`);
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

  // Delete file; optional scope_key removes one multi-scope compliance row only (keeps PDF).
  async deleteFile(fileId: string, scopeKey?: string) {
    const q =
      scopeKey && String(scopeKey).trim()
        ? `?scope_key=${encodeURIComponent(String(scopeKey).trim())}`
        : "";
    const url = `${API_BASE_URL}/api/files/${fileId}${q}`;
    console.log(`Calling DELETE ${url}`);
    const result = await this.fetchWithError(url, {
      method: "DELETE",
    });
    console.log("Delete API response:", result);
    return result;
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
    scopeSlugs?: string,
    clientUploadKey?: string
  ): Promise<UploadResponse> {
    const formData = new FormData();
    formData.append("file", file);
    if (framework) formData.append("framework", framework);
    if (industry) formData.append("industry", industry);
    if (semiIndustry) formData.append("semiIndustry", semiIndustry);
    if (griSector) formData.append("griSector", griSector);
    if (griTopic) formData.append("griTopic", griTopic);
    if (scopeSlugs) formData.append("scopeSlugs", scopeSlugs);
    if (clientUploadKey) formData.append("clientUploadKey", clientUploadKey);
    return this.fetchWithError(`${API_BASE_URL}/api/upload-report`, {
      method: "POST",
      body: formData,
    });
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
  async getReportByFileId(fileId: string) {
    return this.fetchWithError(`${API_BASE_URL}/api/reports/${fileId}`);
  }
}

export const apiService = new APIService();
