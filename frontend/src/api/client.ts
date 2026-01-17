/**
 * API Client with authentication support
 */

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

export interface ApiError {
  error: string;
  status: number;
}

export interface UserInfo {
  id: string;
  username: string;
  email: string | null;
  roles: string[];
}

export interface Document {
  id: string;
  filename: string;
  contentType: string;
  sizeBytes: number;
  status: 'UPLOADED' | 'QUEUED' | 'INDEXING' | 'INDEXED' | 'FAILED';
  createdAt: string;
  updatedAt: string;
  latestJob?: {
    id: string;
    status: string;
    stage: string;
    progress: number;
    errorMessage?: string;
  };
}

export interface DocumentUploadResponse {
  documentId: string;
  jobId: string;
  status: string;
  filename: string;
}

export interface DocumentListResponse {
  documents: Document[];
}

export interface Citation {
  docId: string;
  chunkId: string;
  chunkIndex: number;
  snippet: string;
  score: number;
  documentTitle: string;
}

export interface AskResponse {
  answer: string;
  citations: Citation[];
  model: string;
}

export interface ChunkResponse {
  docId: string;
  chunkId: string;
  chunkIndex: number;
  text: string;
  filename: string;
}

// Agent types
export interface AgentTraceEntry {
  type: 'plan' | 'tool_call' | 'final' | 'error';
  tool?: string;
  input?: Record<string, unknown>;
  outputSummary?: string;
  steps?: string[];
  notes?: string;
  error?: string;
}

export interface AgentResponse {
  answer: string;
  citations: Citation[];
  trace?: AgentTraceEntry[];
}

class ApiClient {
  private getAccessToken: (() => string | null) | null = null;

  /**
   * Set the function to retrieve the current access token
   */
  setTokenProvider(tokenProvider: () => string | null) {
    this.getAccessToken = tokenProvider;
  }

  /**
   * Get current access token for WebSocket auth
   */
  getToken(): string | null {
    return this.getAccessToken?.() ?? null;
  }

  /**
   * Make an authenticated API request
   */
  private async request<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const url = `${API_BASE_URL}${endpoint}`;
    
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
      ...options.headers,
    };

    // Add Authorization header if we have a token
    if (this.getAccessToken) {
      const token = this.getAccessToken();
      if (token) {
        (headers as Record<string, string>)['Authorization'] = `Bearer ${token}`;
      }
    }

    const response = await fetch(url, {
      ...options,
      headers,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ error: 'Unknown error' }));
      const error: ApiError = {
        error: errorData.error || `HTTP ${response.status}`,
        status: response.status,
      };
      throw error;
    }

    return response.json();
  }

  /**
   * Make an authenticated file upload request
   */
  private async uploadFile<T>(
    endpoint: string,
    formData: FormData
  ): Promise<T> {
    const url = `${API_BASE_URL}${endpoint}`;
    
    const headers: HeadersInit = {};

    // Add Authorization header if we have a token
    if (this.getAccessToken) {
      const token = this.getAccessToken();
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }
    }

    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: formData,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ error: 'Unknown error' }));
      const error: ApiError = {
        error: errorData.error || `HTTP ${response.status}`,
        status: response.status,
      };
      throw error;
    }

    return response.json();
  }

  /**
   * GET /api/me - Get current user info
   */
  async getMe(): Promise<UserInfo> {
    return this.request<UserInfo>('/me');
  }

  /**
   * GET /api/health - Health check
   */
  async healthCheck(): Promise<{ status: string }> {
    return this.request<{ status: string }>('/health');
  }

  /**
   * POST /api/docs/upload - Upload a document
   */
  async uploadDocument(file: File): Promise<DocumentUploadResponse> {
    const formData = new FormData();
    formData.append('file', file);
    return this.uploadFile<DocumentUploadResponse>('/docs/upload', formData);
  }

  /**
   * GET /api/docs - List user's documents
   */
  async listDocuments(): Promise<DocumentListResponse> {
    return this.request<DocumentListResponse>('/docs');
  }

  /**
   * POST /api/rag/ask - Ask a question about documents
   */
  async ask(question: string, options?: {
    topK?: number;
    temperature?: number;
    maxTokens?: number;
  }): Promise<AskResponse> {
    return this.request<AskResponse>('/rag/ask', {
      method: 'POST',
      body: JSON.stringify({
        question,
        topK: options?.topK,
        temperature: options?.temperature,
        maxTokens: options?.maxTokens,
      }),
    });
  }

  /**
   * GET /api/docs/:docId/chunks/:chunkIndex - Get a specific chunk
   */
  async getChunk(docId: string, chunkIndex: number): Promise<ChunkResponse> {
    return this.request<ChunkResponse>(`/docs/${docId}/chunks/${chunkIndex}`);
  }

  /**
   * POST /api/agent/run - Execute bounded agent with tools
   */
  async runAgent(question: string, options?: {
    returnTrace?: boolean;
  }): Promise<AgentResponse> {
    return this.request<AgentResponse>('/agent/run', {
      method: 'POST',
      body: JSON.stringify({
        question,
        mode: 'agent',
        returnTrace: options?.returnTrace ?? false,
      }),
    });
  }
}

// Singleton instance
export const apiClient = new ApiClient();
