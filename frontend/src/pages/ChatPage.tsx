import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from 'react-oidc-context';
import { apiClient, AskResponse, Citation, ChunkResponse } from '../api';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  citations?: Citation[];
  timestamp: Date;
}

type ChatState = 'idle' | 'retrieving' | 'thinking' | 'error';

/**
 * ChatPage - Main chat interface for asking questions about documents
 */
export default function ChatPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [chatState, setChatState] = useState<ChatState>('idle');
  const [error, setError] = useState<string | null>(null);
  
  // Modal state for viewing chunks
  const [selectedChunk, setSelectedChunk] = useState<ChunkResponse | null>(null);
  const [loadingChunk, setLoadingChunk] = useState(false);
  
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Set up API client token provider
  useEffect(() => {
    apiClient.setTokenProvider(() => auth.user?.access_token ?? null);
  }, [auth.user]);

  // Redirect if not authenticated
  useEffect(() => {
    if (!auth.isLoading && !auth.isAuthenticated) {
      navigate('/');
    }
  }, [auth.isLoading, auth.isAuthenticated, navigate]);

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Handle sending a message
  const handleSend = useCallback(async () => {
    const question = input.trim();
    if (!question || chatState !== 'idle') return;

    // Add user message
    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: 'user',
      content: question,
      timestamp: new Date(),
    };
    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setError(null);

    try {
      // Show retrieving state
      setChatState('retrieving');
      
      // Small delay to show retrieving state
      await new Promise(resolve => setTimeout(resolve, 300));
      
      // Show thinking state
      setChatState('thinking');
      
      // Call the API
      const response: AskResponse = await apiClient.ask(question);

      // Add assistant message
      const assistantMessage: Message = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: response.answer,
        citations: response.citations,
        timestamp: new Date(),
      };
      setMessages(prev => [...prev, assistantMessage]);
      
    } catch (err: unknown) {
      console.error('Ask failed:', err);
      const errorMessage = (err as { error?: string })?.error || 'Failed to get answer';
      setError(errorMessage);
      
      // Add error message
      const errorMsg: Message = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: `Error: ${errorMessage}`,
        timestamp: new Date(),
      };
      setMessages(prev => [...prev, errorMsg]);
    } finally {
      setChatState('idle');
      inputRef.current?.focus();
    }
  }, [input, chatState]);

  // Handle citation click
  const handleCitationClick = useCallback(async (citation: Citation) => {
    setLoadingChunk(true);
    try {
      const chunk = await apiClient.getChunk(citation.docId, citation.chunkIndex);
      setSelectedChunk(chunk);
    } catch (err) {
      console.error('Failed to load chunk:', err);
      setError('Failed to load citation source');
    } finally {
      setLoadingChunk(false);
    }
  }, []);

  // Close modal
  const closeModal = useCallback(() => {
    setSelectedChunk(null);
  }, []);

  // Handle key press
  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Loading state
  if (auth.isLoading) {
    return (
      <div style={styles.container}>
        <p>Loading...</p>
      </div>
    );
  }

  return (
    <div style={styles.container}>
      {/* Header */}
      <header style={styles.header}>
        <h1 style={styles.title}>DocuChat</h1>
        <div style={styles.headerActions}>
          <button style={styles.navButton} onClick={() => navigate('/')}>
            Documents
          </button>
          <button 
            style={styles.logoutButton} 
            onClick={() => auth.signoutRedirect()}
          >
            Logout
          </button>
        </div>
      </header>

      {/* Messages area */}
      <div style={styles.messagesContainer}>
        {messages.length === 0 ? (
          <div style={styles.emptyState}>
            <h2>Ask a question about your documents</h2>
            <p>Upload documents first, then ask questions about their content.</p>
          </div>
        ) : (
          <div style={styles.messagesList}>
            {messages.map((message) => (
              <div 
                key={message.id} 
                style={{
                  ...styles.messageWrapper,
                  justifyContent: message.role === 'user' ? 'flex-end' : 'flex-start',
                }}
              >
                <div 
                  style={{
                    ...styles.message,
                    ...(message.role === 'user' ? styles.userMessage : styles.assistantMessage),
                  }}
                >
                  <div style={styles.messageContent}>{message.content}</div>
                  
                  {/* Citations */}
                  {message.citations && message.citations.length > 0 && (
                    <div style={styles.citationsContainer}>
                      <div style={styles.citationsLabel}>Sources:</div>
                      {message.citations.map((citation, idx) => (
                        <button
                          key={citation.chunkId}
                          style={styles.citationButton}
                          onClick={() => handleCitationClick(citation)}
                          disabled={loadingChunk}
                        >
                          <span style={styles.citationNumber}>[{idx + 1}]</span>
                          <span style={styles.citationTitle}>
                            {citation.documentTitle} (chunk {citation.chunkIndex})
                          </span>
                          <div style={styles.citationSnippet}>
                            {citation.snippet.slice(0, 100)}...
                          </div>
                        </button>
                      ))}
                    </div>
                  )}
                  
                  {/* No sources state */}
                  {message.role === 'assistant' && 
                   message.citations !== undefined && 
                   message.citations.length === 0 && (
                    <div style={styles.noSources}>
                      No relevant chunks found in your documents.
                    </div>
                  )}
                </div>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}

        {/* Loading indicator */}
        {chatState !== 'idle' && (
          <div style={styles.loadingIndicator}>
            {chatState === 'retrieving' && '🔍 Retrieving relevant chunks...'}
            {chatState === 'thinking' && '🤔 Thinking...'}
          </div>
        )}
      </div>

      {/* Input area */}
      <div style={styles.inputContainer}>
        {error && <div style={styles.errorBanner}>{error}</div>}
        <div style={styles.inputWrapper}>
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="Ask a question about your documents..."
            style={styles.input}
            disabled={chatState !== 'idle'}
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || chatState !== 'idle'}
            style={{
              ...styles.sendButton,
              ...((!input.trim() || chatState !== 'idle') ? styles.sendButtonDisabled : {}),
            }}
          >
            {chatState !== 'idle' ? '...' : 'Send'}
          </button>
        </div>
      </div>

      {/* Chunk Modal */}
      {selectedChunk && (
        <div style={styles.modalOverlay} onClick={closeModal}>
          <div style={styles.modal} onClick={(e) => e.stopPropagation()}>
            <div style={styles.modalHeader}>
              <h3 style={styles.modalTitle}>
                {selectedChunk.filename} — Chunk {selectedChunk.chunkIndex}
              </h3>
              <button style={styles.modalClose} onClick={closeModal}>×</button>
            </div>
            <div style={styles.modalContent}>
              <pre style={styles.chunkText}>{selectedChunk.text}</pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    height: '100vh',
    maxWidth: '900px',
    margin: '0 auto',
    background: '#f5f5f5',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '1rem 1.5rem',
    background: 'white',
    borderBottom: '1px solid #e0e0e0',
  },
  title: {
    margin: 0,
    fontSize: '1.5rem',
    color: '#1a1a1a',
  },
  headerActions: {
    display: 'flex',
    gap: '0.5rem',
  },
  navButton: {
    padding: '0.5rem 1rem',
    background: '#f0f0f0',
    border: 'none',
    borderRadius: '6px',
    cursor: 'pointer',
    fontSize: '0.875rem',
  },
  logoutButton: {
    padding: '0.5rem 1rem',
    background: '#dc3545',
    color: 'white',
    border: 'none',
    borderRadius: '6px',
    cursor: 'pointer',
    fontSize: '0.875rem',
  },
  messagesContainer: {
    flex: 1,
    overflow: 'auto',
    padding: '1rem',
  },
  emptyState: {
    textAlign: 'center',
    padding: '3rem',
    color: '#666',
  },
  messagesList: {
    display: 'flex',
    flexDirection: 'column',
    gap: '1rem',
  },
  messageWrapper: {
    display: 'flex',
  },
  message: {
    maxWidth: '80%',
    padding: '1rem',
    borderRadius: '12px',
  },
  userMessage: {
    background: '#0066cc',
    color: 'white',
    borderBottomRightRadius: '4px',
  },
  assistantMessage: {
    background: 'white',
    color: '#1a1a1a',
    borderBottomLeftRadius: '4px',
    boxShadow: '0 1px 3px rgba(0,0,0,0.1)',
  },
  messageContent: {
    whiteSpace: 'pre-wrap',
    lineHeight: 1.5,
  },
  citationsContainer: {
    marginTop: '1rem',
    paddingTop: '1rem',
    borderTop: '1px solid #e0e0e0',
  },
  citationsLabel: {
    fontSize: '0.75rem',
    fontWeight: 'bold',
    color: '#666',
    marginBottom: '0.5rem',
    textTransform: 'uppercase',
  },
  citationButton: {
    display: 'block',
    width: '100%',
    textAlign: 'left',
    padding: '0.75rem',
    marginBottom: '0.5rem',
    background: '#f8f9fa',
    border: '1px solid #e0e0e0',
    borderRadius: '6px',
    cursor: 'pointer',
    transition: 'background 0.2s',
  },
  citationNumber: {
    fontWeight: 'bold',
    color: '#0066cc',
    marginRight: '0.5rem',
  },
  citationTitle: {
    fontSize: '0.875rem',
    color: '#333',
  },
  citationSnippet: {
    fontSize: '0.75rem',
    color: '#666',
    marginTop: '0.25rem',
    fontStyle: 'italic',
  },
  noSources: {
    marginTop: '1rem',
    padding: '0.75rem',
    background: '#fff3cd',
    border: '1px solid #ffc107',
    borderRadius: '6px',
    fontSize: '0.875rem',
    color: '#856404',
  },
  loadingIndicator: {
    textAlign: 'center',
    padding: '1rem',
    color: '#666',
    fontStyle: 'italic',
  },
  inputContainer: {
    padding: '1rem',
    background: 'white',
    borderTop: '1px solid #e0e0e0',
  },
  errorBanner: {
    padding: '0.5rem 1rem',
    marginBottom: '0.5rem',
    background: '#f8d7da',
    color: '#721c24',
    borderRadius: '6px',
    fontSize: '0.875rem',
  },
  inputWrapper: {
    display: 'flex',
    gap: '0.5rem',
  },
  input: {
    flex: 1,
    padding: '0.75rem 1rem',
    fontSize: '1rem',
    border: '1px solid #ddd',
    borderRadius: '8px',
    outline: 'none',
  },
  sendButton: {
    padding: '0.75rem 1.5rem',
    background: '#0066cc',
    color: 'white',
    border: 'none',
    borderRadius: '8px',
    cursor: 'pointer',
    fontSize: '1rem',
    fontWeight: 'bold',
  },
  sendButtonDisabled: {
    background: '#ccc',
    cursor: 'not-allowed',
  },
  modalOverlay: {
    position: 'fixed',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    background: 'rgba(0,0,0,0.5)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 1000,
  },
  modal: {
    background: 'white',
    borderRadius: '12px',
    width: '90%',
    maxWidth: '700px',
    maxHeight: '80vh',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },
  modalHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '1rem 1.5rem',
    borderBottom: '1px solid #e0e0e0',
  },
  modalTitle: {
    margin: 0,
    fontSize: '1.125rem',
    color: '#1a1a1a',
  },
  modalClose: {
    background: 'none',
    border: 'none',
    fontSize: '1.5rem',
    cursor: 'pointer',
    color: '#666',
    padding: '0.25rem 0.5rem',
  },
  modalContent: {
    padding: '1.5rem',
    overflow: 'auto',
    flex: 1,
  },
  chunkText: {
    margin: 0,
    whiteSpace: 'pre-wrap',
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
    fontSize: '0.875rem',
    lineHeight: 1.6,
    background: '#f8f9fa',
    padding: '1rem',
    borderRadius: '6px',
  },
};
