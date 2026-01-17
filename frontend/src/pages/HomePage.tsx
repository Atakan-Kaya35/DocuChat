import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from 'react-oidc-context';
import { apiClient, Document, UserInfo } from '../api';
import { useIndexingSocket, IndexProgressEvent, ConnectionStatus } from '../ws';

interface UploadProgress {
  documentId: string;
  jobId: string;
  stage: string | null;
  progress: number;
  message: string | null;
  status: 'uploading' | 'processing' | 'complete' | 'failed';
}

/**
 * HomePage - Main landing page with login/logout, user info, and document upload
 */
export default function HomePage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [userInfo, setUserInfo] = useState<UserInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Set up the API client with the token provider
  useEffect(() => {
    apiClient.setTokenProvider(() => auth.user?.access_token ?? null);
  }, [auth.user]);

  // WebSocket event handlers
  const handleProgress = useCallback((event: IndexProgressEvent) => {
    console.log('[HomePage] Progress event:', event);
    setUploadProgress({
      documentId: event.document_id,
      jobId: event.job_id,
      stage: event.stage,
      progress: event.progress,
      message: event.message,
      status: 'processing',
    });
  }, []);

  const handleComplete = useCallback((event: IndexProgressEvent) => {
    console.log('[HomePage] Complete event:', event);
    setUploadProgress({
      documentId: event.document_id,
      jobId: event.job_id,
      stage: event.stage,
      progress: 100,
      message: 'Indexing complete!',
      status: 'complete',
    });
    // Refresh document list
    loadDocuments();
    // Clear progress after a delay
    setTimeout(() => setUploadProgress(null), 3000);
  }, []);

  const handleFailed = useCallback((event: IndexProgressEvent) => {
    console.log('[HomePage] Failed event:', event);
    setUploadProgress({
      documentId: event.document_id,
      jobId: event.job_id,
      stage: event.stage,
      progress: event.progress,
      message: event.message || 'Indexing failed',
      status: 'failed',
    });
  }, []);

  // WebSocket connection
  const { status: wsStatus } = useIndexingSocket({
    token: auth.user?.access_token ?? null,
    onProgress: handleProgress,
    onComplete: handleComplete,
    onFailed: handleFailed,
  });

  // Load documents
  const loadDocuments = useCallback(async () => {
    try {
      const response = await apiClient.listDocuments();
      setDocuments(response.documents);
    } catch (err) {
      console.error('Failed to load documents:', err);
    }
  }, []);

  // Fetch user info and documents when authenticated
  useEffect(() => {
    if (auth.isAuthenticated && auth.user) {
      setLoading(true);
      setError(null);
      
      Promise.all([apiClient.getMe(), apiClient.listDocuments()])
        .then(([userData, docsData]) => {
          setUserInfo(userData);
          setDocuments(docsData.documents);
          setLoading(false);
        })
        .catch((err) => {
          console.error('Failed to fetch data:', err);
          setError(err.error || 'Failed to fetch data');
          setLoading(false);
        });
    } else {
      setUserInfo(null);
      setDocuments([]);
    }
  }, [auth.isAuthenticated, auth.user]);

  // Handle file upload
  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    setUploadProgress({
      documentId: '',
      jobId: '',
      stage: null,
      progress: 0,
      message: 'Uploading...',
      status: 'uploading',
    });

    try {
      const response = await apiClient.uploadDocument(file);
      console.log('[HomePage] Upload response:', response);
      setUploadProgress({
        documentId: response.documentId,
        jobId: response.jobId,
        stage: null,
        progress: 0,
        message: 'Processing...',
        status: 'processing',
      });
    } catch (err: unknown) {
      console.error('Upload failed:', err);
      const errorMessage = (err as { error?: string })?.error || 'Upload failed';
      setUploadProgress({
        documentId: '',
        jobId: '',
        stage: null,
        progress: 0,
        message: errorMessage,
        status: 'failed',
      });
    }

    // Reset file input
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  // Get status color
  const getStatusColor = (status: Document['status']) => {
    switch (status) {
      case 'INDEXED': return '#28a745';
      case 'INDEXING': return '#ffc107';
      case 'QUEUED': return '#17a2b8';
      case 'UPLOADED': return '#6c757d';
      case 'FAILED': return '#dc3545';
      default: return '#6c757d';
    }
  };

  // Get WebSocket status indicator
  const getWsStatusIndicator = (status: ConnectionStatus) => {
    const colors = {
      connected: '#28a745',
      connecting: '#ffc107',
      disconnected: '#6c757d',
      error: '#dc3545',
    };
    return colors[status];
  };

  // Show loading state
  if (auth.isLoading) {
    return (
      <div style={styles.container}>
        <div style={styles.card}>
          <h1 style={styles.title}>DocuChat</h1>
          <p>Loading authentication...</p>
        </div>
      </div>
    );
  }

  // Authenticated view
  if (auth.isAuthenticated) {
    return (
      <div style={styles.container}>
        <div style={styles.card}>
          <h1 style={styles.title}>DocuChat</h1>
          
          {/* WebSocket Status */}
          <div style={styles.wsStatus}>
            <span
              style={{
                ...styles.wsIndicator,
                backgroundColor: getWsStatusIndicator(wsStatus),
              }}
            />
            <span style={styles.wsLabel}>
              {wsStatus === 'connected' ? 'Live updates' : wsStatus}
            </span>
          </div>

          <div style={styles.userSection}>
            {loading ? (
              <p>Loading user info...</p>
            ) : error ? (
              <div style={styles.error}>
                <p>Error: {error}</p>
                <button style={styles.button} onClick={() => window.location.reload()}>
                  Retry
                </button>
              </div>
            ) : userInfo ? (
              <>
                <div style={styles.avatar}>
                  {userInfo.username.charAt(0).toUpperCase()}
                </div>
                <h2 style={styles.username}>
                  Logged in as <strong>{userInfo.username}</strong>
                </h2>
                {userInfo.email && (
                  <p style={styles.email}>{userInfo.email}</p>
                )}
                <div style={styles.roles}>
                  {userInfo.roles.map((role) => (
                    <span key={role} style={styles.roleTag}>
                      {role}
                    </span>
                  ))}
                </div>
                <p style={styles.userId}>User ID: {userInfo.id}</p>
              </>
            ) : null}
          </div>

          {/* Document Upload Section */}
          <div style={styles.uploadSection}>
            <h3 style={styles.sectionTitle}>Upload Document</h3>
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.txt,.md,.doc,.docx"
              onChange={handleFileUpload}
              style={styles.fileInput}
              disabled={uploadProgress?.status === 'uploading' || uploadProgress?.status === 'processing'}
            />
            
            {/* Progress Bar */}
            {uploadProgress && (
              <div style={styles.progressContainer}>
                <div style={styles.progressInfo}>
                  <span style={styles.progressStage}>
                    {uploadProgress.stage || uploadProgress.status}
                  </span>
                  <span style={styles.progressPercent}>{uploadProgress.progress}%</span>
                </div>
                <div style={styles.progressBar}>
                  <div
                    style={{
                      ...styles.progressFill,
                      width: `${uploadProgress.progress}%`,
                      backgroundColor:
                        uploadProgress.status === 'failed'
                          ? '#dc3545'
                          : uploadProgress.status === 'complete'
                          ? '#28a745'
                          : '#0066cc',
                    }}
                  />
                </div>
                {uploadProgress.message && (
                  <p style={styles.progressMessage}>{uploadProgress.message}</p>
                )}
              </div>
            )}
          </div>

          {/* Documents List */}
          <div style={styles.documentsSection}>
            <h3 style={styles.sectionTitle}>Your Documents ({documents.length})</h3>
            {documents.length === 0 ? (
              <p style={styles.noDocuments}>No documents yet. Upload one to get started!</p>
            ) : (
              <ul style={styles.documentList}>
                {documents.map((doc) => (
                  <li key={doc.id} style={styles.documentItem}>
                    <span style={styles.documentTitle}>{doc.filename}</span>
                    <span
                      style={{
                        ...styles.documentStatus,
                        backgroundColor: getStatusColor(doc.status),
                      }}
                    >
                      {doc.status}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div style={styles.actionButtons}>
            <button
              style={{ ...styles.button, ...styles.chatButton }}
              onClick={() => navigate('/chat')}
            >
              💬 Chat with Documents
            </button>
            <button
              style={{ ...styles.button, ...styles.logoutButton }}
              onClick={() => auth.signoutRedirect()}
            >
              Logout
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Not authenticated view
  return (
    <div style={styles.container}>
      <div style={styles.card}>
        <h1 style={styles.title}>DocuChat</h1>
        <p style={styles.subtitle}>Chat with your documents using AI</p>
        
        <button
          style={styles.button}
          onClick={() => {
            console.log('[HomePage] Login button clicked');
            console.log('[HomePage] Starting signin redirect...');
            auth.signinRedirect()
              .then(() => {
                console.log('[HomePage] Redirect initiated (page should redirect now)');
              })
              .catch((err) => {
                console.error('[HomePage] Signin redirect failed:', err);
                alert(`Login failed: ${err.message}\n\nCheck console for details.`);
              });
          }}
        >
          Login with Keycloak
        </button>
        
        <button
          style={styles.registerButton}
          onClick={() => {
            // Construct Keycloak registration URL
            const keycloakUrl = import.meta.env.VITE_KEYCLOAK_URL || 'http://localhost';
            const realm = import.meta.env.VITE_KEYCLOAK_REALM || 'docuchat';
            const clientId = import.meta.env.VITE_KEYCLOAK_CLIENT_ID || 'docuchat-frontend';
            const redirectUri = encodeURIComponent(`${window.location.origin}/callback`);
            const registrationUrl = `${keycloakUrl}/realms/${realm}/protocol/openid-connect/registrations?client_id=${clientId}&redirect_uri=${redirectUri}&response_type=code&scope=openid`;
            console.log('[HomePage] Redirecting to registration:', registrationUrl);
            window.location.href = registrationUrl;
          }}
        >
          Create Account
        </button>
        
        <p style={styles.orDivider}>or</p>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    minHeight: '100vh',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '1rem',
  },
  card: {
    background: 'white',
    borderRadius: '12px',
    padding: '2rem',
    boxShadow: '0 4px 6px rgba(0, 0, 0, 0.1)',
    textAlign: 'center',
    maxWidth: '400px',
    width: '100%',
  },
  title: {
    fontSize: '2rem',
    marginBottom: '0.5rem',
    color: '#1a1a1a',
  },
  subtitle: {
    color: '#666',
    marginBottom: '1.5rem',
  },
  button: {
    background: '#0066cc',
    color: 'white',
    border: 'none',
    padding: '0.75rem 1.5rem',
    fontSize: '1rem',
    borderRadius: '6px',
    cursor: 'pointer',
    transition: 'background 0.2s',
    width: '100%',
  },
  registerButton: {
    background: 'transparent',
    color: '#0066cc',
    border: '2px solid #0066cc',
    padding: '0.75rem 1.5rem',
    fontSize: '1rem',
    borderRadius: '6px',
    cursor: 'pointer',
    transition: 'all 0.2s',
    width: '100%',
    marginTop: '0.75rem',
  },
  orDivider: {
    color: '#999',
    fontSize: '0.875rem',
    margin: '0.75rem 0 0 0',
  },
  actionButtons: {
    display: 'flex',
    flexDirection: 'column',
    gap: '0.75rem',
    marginTop: '1.5rem',
  },
  chatButton: {
    background: '#28a745',
  },
  logoutButton: {
    background: '#dc3545',
  },
  userSection: {
    marginTop: '1rem',
  },
  avatar: {
    width: '60px',
    height: '60px',
    borderRadius: '50%',
    background: '#0066cc',
    color: 'white',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: '1.5rem',
    fontWeight: 'bold',
    margin: '0 auto 1rem',
  },
  username: {
    fontSize: '1.25rem',
    marginBottom: '0.25rem',
  },
  email: {
    color: '#666',
    marginBottom: '0.5rem',
  },
  roles: {
    display: 'flex',
    gap: '0.5rem',
    justifyContent: 'center',
    marginTop: '0.5rem',
    flexWrap: 'wrap',
  },
  roleTag: {
    background: '#e0e0e0',
    padding: '0.25rem 0.75rem',
    borderRadius: '999px',
    fontSize: '0.875rem',
    color: '#333',
  },
  userId: {
    marginTop: '1rem',
    fontSize: '0.75rem',
    color: '#999',
  },
  error: {
    color: '#dc3545',
    marginBottom: '1rem',
  },
  wsStatus: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '0.5rem',
    marginBottom: '1rem',
  },
  wsIndicator: {
    width: '8px',
    height: '8px',
    borderRadius: '50%',
  },
  wsLabel: {
    fontSize: '0.75rem',
    color: '#666',
    textTransform: 'capitalize' as const,
  },
  uploadSection: {
    marginTop: '1.5rem',
    padding: '1rem',
    background: '#f8f9fa',
    borderRadius: '8px',
    textAlign: 'left' as const,
  },
  sectionTitle: {
    fontSize: '1rem',
    marginBottom: '0.75rem',
    color: '#333',
  },
  fileInput: {
    width: '100%',
    padding: '0.5rem',
    border: '1px solid #ddd',
    borderRadius: '4px',
    fontSize: '0.875rem',
  },
  progressContainer: {
    marginTop: '1rem',
  },
  progressInfo: {
    display: 'flex',
    justifyContent: 'space-between',
    marginBottom: '0.25rem',
  },
  progressStage: {
    fontSize: '0.75rem',
    color: '#666',
    textTransform: 'uppercase' as const,
  },
  progressPercent: {
    fontSize: '0.75rem',
    fontWeight: 'bold',
    color: '#333',
  },
  progressBar: {
    width: '100%',
    height: '8px',
    background: '#e0e0e0',
    borderRadius: '4px',
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    transition: 'width 0.3s ease',
  },
  progressMessage: {
    fontSize: '0.75rem',
    color: '#666',
    marginTop: '0.5rem',
  },
  documentsSection: {
    marginTop: '1.5rem',
    textAlign: 'left' as const,
  },
  noDocuments: {
    color: '#666',
    fontSize: '0.875rem',
  },
  documentList: {
    listStyle: 'none',
    padding: 0,
    margin: 0,
  },
  documentItem: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '0.75rem',
    background: '#f8f9fa',
    borderRadius: '4px',
    marginBottom: '0.5rem',
  },
  documentTitle: {
    fontSize: '0.875rem',
    color: '#333',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap' as const,
    maxWidth: '200px',
  },
  documentStatus: {
    fontSize: '0.625rem',
    color: 'white',
    padding: '0.125rem 0.5rem',
    borderRadius: '999px',
    textTransform: 'uppercase' as const,
    fontWeight: 'bold',
  },
};
