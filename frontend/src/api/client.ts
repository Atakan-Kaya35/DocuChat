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
  rewritten_query?: string;  // Present when refine_prompt was enabled
  rerank_used?: boolean;     // Whether reranking was applied
  rerank_latency_ms?: number; // Rerank step latency in ms
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
  rewritten_query?: string;  // Present when refine_prompt was enabled
  rerank_used?: boolean;     // Whether reranking was applied
  rerank_latency_ms?: number; // Rerank step latency in ms
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
   * POST /api/rag/rewrite - Rewrite a query for better retrieval
   * Called separately to show refined query immediately
   */
  async rewriteQuery(question: string): Promise<{
    rewritten_query: string;
    original_query: string;
    fallback?: boolean;
  }> {
    return this.request('/rag/rewrite', {
      method: 'POST',
      body: JSON.stringify({ question }),
    });
  }

  /**
   * POST /api/rag/ask - Ask a question about documents
   */
  async ask(question: string, options?: {
    topK?: number;
    temperature?: number;
    maxTokens?: number;
    refinePrompt?: boolean;
    rerank?: boolean;
  }): Promise<AskResponse> {
    return this.request<AskResponse>('/rag/ask', {
      method: 'POST',
      body: JSON.stringify({
        question,
        topK: options?.topK,
        temperature: options?.temperature,
        maxTokens: options?.maxTokens,
        refine_prompt: options?.refinePrompt,
        rerank: options?.rerank,
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
    refinePrompt?: boolean;
    rerank?: boolean;
  }): Promise<AgentResponse> {
    return this.request<AgentResponse>('/agent/run', {
      method: 'POST',
      body: JSON.stringify({
        question,
        mode: 'agent',
        returnTrace: options?.returnTrace ?? false,
        refine_prompt: options?.refinePrompt,
        rerank: options?.rerank,
      }),
    });
  }

  /**
   * POST /api/agent/stream - Execute bounded agent with SSE streaming
   * 
   * Yields trace events as they occur, then yields final AgentResponse.
   */
  async *runAgentStream(
    question: string,
    options?: {
      refinePrompt?: boolean;
      rerank?: boolean;
    },
    signal?: AbortSignal
  ): AsyncGenerator<AgentTraceEntry | AgentResponse, void, unknown> {
    const url = `${API_BASE_URL}/agent/stream`;

    const headers: HeadersInit = {
      'Content-Type': 'application/json',
    };

    if (this.getAccessToken) {
      const token = this.getAccessToken();
      if (token) {
        (headers as Record<string, string>)['Authorization'] = `Bearer ${token}`;
      }
    }

    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        question,
        refine_prompt: options?.refinePrompt,
        rerank: options?.rerank,
      }),
      signal,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ error: 'Unknown error' }));
      throw { error: errorData.error || `HTTP ${response.status}`, status: response.status };
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw { error: 'No response body', status: 500 };
    }

    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();

      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Parse SSE events from buffer
      const lines = buffer.split('\n');
      buffer = lines.pop() || ''; // Keep incomplete line in buffer

      let eventType = '';
      let eventData = '';

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          eventType = line.slice(7).trim();
        } else if (line.startsWith('data: ')) {
          eventData = line.slice(6);
        } else if (line === '' && eventType && eventData) {
          // End of event
          try {
            const parsed = JSON.parse(eventData);

            if (eventType === 'trace') {
              yield parsed as AgentTraceEntry;
            } else if (eventType === 'complete') {
              yield parsed as AgentResponse;
            } else if (eventType === 'error') {
              throw { error: parsed.error, status: 500 };
            }
          } catch (e) {
            if ((e as { error?: string }).error) throw e;
            console.error('Failed to parse SSE event:', eventData);
          }

          eventType = '';
          eventData = '';
        }
      }
    }
  }
}

// Singleton instance
export const apiClient = new ApiClient();
