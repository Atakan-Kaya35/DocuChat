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
  title: string;
  status: 'PENDING' | 'PROCESSING' | 'INDEXED' | 'FAILED';
  created_at: string;
  updated_at: string;
}

export interface DocumentUploadResponse {
  document: Document;
  job_id: string;
}

export interface DocumentListResponse {
  documents: Document[];
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
   * POST /api/documents/upload - Upload a document
   */
  async uploadDocument(file: File): Promise<DocumentUploadResponse> {
    const formData = new FormData();
    formData.append('file', file);
    return this.uploadFile<DocumentUploadResponse>('/documents/upload', formData);
  }

  /**
   * GET /api/documents - List user's documents
   */
  async listDocuments(): Promise<DocumentListResponse> {
    return this.request<DocumentListResponse>('/documents');
  }
}

// Singleton instance
export const apiClient = new ApiClient();
