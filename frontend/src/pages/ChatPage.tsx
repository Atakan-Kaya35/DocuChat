import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from 'react-oidc-context';
import { apiClient, AskResponse, AgentResponse, AgentTraceEntry, Citation, ChunkResponse } from '../api';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  citations?: Citation[];
  trace?: AgentTraceEntry[];  // Agent mode trace
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
  
  // Agent mode toggle
  const [agentMode, setAgentMode] = useState(false);
  const [expandedTraces, setExpandedTraces] = useState<Set<string>>(new Set());
  
  // Streaming agent thinking state
  const [streamingSteps, setStreamingSteps] = useState<AgentTraceEntry[]>([]);
  
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
    setStreamingSteps([]); // Clear previous streaming steps

    try {
      // Show retrieving state
      setChatState('retrieving');
      
      // Small delay to show retrieving state
      await new Promise(resolve => setTimeout(resolve, 300));
      
      // Show thinking state
      setChatState('thinking');
      
      let assistantMessage: Message;
      
      if (agentMode) {
        // Use streaming Agent API for real-time updates
        const collectedTrace: AgentTraceEntry[] = [];
        let finalResponse: AgentResponse | null = null;
        
        for await (const event of apiClient.runAgentStream(question)) {
          // Check if it's a trace entry or final response
          if ('type' in event && !('answer' in event)) {
            // It's a trace entry
            const traceEntry = event as AgentTraceEntry;
            collectedTrace.push(traceEntry);
            setStreamingSteps([...collectedTrace]);
          } else if ('answer' in event) {
            // It's the final response
            finalResponse = event as AgentResponse;
          }
        }
        
        if (finalResponse) {
          assistantMessage = {
            id: crypto.randomUUID(),
            role: 'assistant',
            content: finalResponse.answer,
            citations: finalResponse.citations,
            trace: finalResponse.trace,
            timestamp: new Date(),
          };
        } else {
          throw new Error('No response received from agent');
        }
      } else {
        // Call standard RAG API
        const response: AskResponse = await apiClient.ask(question);
        
        assistantMessage = {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: response.answer,
          citations: response.citations,
          timestamp: new Date(),
        };
      }
      
      setMessages(prev => [...prev, assistantMessage]);
      setStreamingSteps([]); // Clear streaming steps after message is added
      
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
      setStreamingSteps([]);
      inputRef.current?.focus();
    }
  }, [input, chatState, agentMode]);

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

  // Toggle trace visibility
  const toggleTrace = useCallback((messageId: string) => {
    setExpandedTraces(prev => {
      const next = new Set(prev);
      if (next.has(messageId)) {
        next.delete(messageId);
      } else {
        next.add(messageId);
      }
      return next;
    });
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
          {/* Agent Mode Toggle */}
          <button
            type="button"
            onClick={() => setAgentMode(!agentMode)}
            style={{
              ...styles.toggleButton,
              background: agentMode ? '#0066cc' : '#ccc',
            }}
            aria-pressed={agentMode}
            title={agentMode ? 'Agent Mode ON - Using multi-step reasoning' : 'Agent Mode OFF - Using simple RAG'}
          >
            <span
              style={{
                ...styles.toggleKnob,
                transform: agentMode ? 'translateX(16px)' : 'translateX(0)',
              }}
            />
          </button>
          <span style={styles.toggleText}>{agentMode ? '🤖 Agent' : '💬 RAG'}</span>
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
                  
                  {/* Agent Mode Trace Accordion */}
                  {message.trace && message.trace.length > 0 && (
                    <div style={styles.traceContainer}>
                      <button 
                        style={styles.traceToggle}
                        onClick={() => toggleTrace(message.id)}
                      >
                        {expandedTraces.has(message.id) ? '▼' : '▶'} Show Steps ({message.trace.length})
                      </button>
                      {expandedTraces.has(message.id) && (
                        <div style={styles.traceList}>
                          {message.trace.map((entry, idx) => (
                            <div key={idx} style={styles.traceEntry}>
                              <span style={styles.traceType}>{entry.type.toUpperCase()}</span>
                              {entry.tool && <span style={styles.traceTool}>{entry.tool}</span>}
                              {entry.steps && (
                                <ul style={styles.traceSteps}>
                                  {entry.steps.map((step, i) => (
                                    <li key={i}>{step}</li>
                                  ))}
                                </ul>
                              )}
                              {entry.input && (
                                <code style={styles.traceInput}>
                                  {JSON.stringify(entry.input)}
                                </code>
                              )}
                              {entry.outputSummary && (
                                <div style={styles.traceOutput}>{entry.outputSummary}</div>
                              )}
                              {entry.notes && (
                                <div style={styles.traceNotes}>{entry.notes}</div>
                              )}
                              {entry.error && (
                                <div style={styles.traceError}>{entry.error}</div>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
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
            {chatState === 'thinking' && agentMode && streamingSteps.length > 0 ? (
              <div style={styles.streamingContainer}>
                <div style={styles.streamingHeader}>🤖 Agent Thinking...</div>
                <div style={styles.streamingSteps}>
                  {streamingSteps.map((step, idx) => (
                    <div key={idx} style={styles.streamingStep}>
                      {step.type === 'plan' && step.steps && (
                        <div style={styles.stepPlan}>
                          <span style={styles.stepIcon}>📋</span>
                          <span>Plan: {step.steps.slice(0, 3).join(' → ')}{step.steps.length > 3 ? '...' : ''}</span>
                        </div>
                      )}
                      {step.type === 'tool_call' && step.tool === 'thinking' && (
                        <div style={styles.stepThinking}>
                          <span style={styles.stepIcon}>💭</span>
                          <span>{step.notes || 'Reasoning...'}</span>
                        </div>
                      )}
                      {step.type === 'tool_call' && step.tool === 'search_docs' && (
                        <div style={styles.stepTool}>
                          <span style={styles.stepIcon}>🔍</span>
                          <span>Searching: {(step.input?.query as string) || 'documents'}</span>
                          {step.outputSummary && <span style={styles.stepResult}> → {step.outputSummary}</span>}
                        </div>
                      )}
                      {step.type === 'tool_call' && step.tool === 'open_citation' && (
                        <div style={styles.stepTool}>
                          <span style={styles.stepIcon}>📖</span>
                          <span>Reading chunk...</span>
                          {step.outputSummary && <span style={styles.stepResult}> → {step.outputSummary}</span>}
                        </div>
                      )}
                      {step.type === 'tool_call' && step.tool === 'synthesizing' && (
                        <div style={styles.stepThinking}>
                          <span style={styles.stepIcon}>✍️</span>
                          <span>{step.notes || 'Generating answer...'}</span>
                        </div>
                      )}
                      {step.type === 'final' && (
                        <div style={styles.stepFinal}>
                          <span style={styles.stepIcon}>✅</span>
                          <span>Finalizing answer...</span>
                        </div>
                      )}
                      {step.type === 'error' && (
                        <div style={styles.stepError}>
                          <span style={styles.stepIcon}>⚠️</span>
                          <span>{step.error || 'An error occurred'}</span>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ) : chatState === 'thinking' ? (
              '🤔 Thinking...'
            ) : null}
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
  toggleButton: {
    width: '40px',
    height: '24px',
    borderRadius: '12px',
    border: 'none',
    cursor: 'pointer',
    position: 'relative' as const,
    transition: 'background 0.2s',
    padding: 0,
  },
  toggleKnob: {
    position: 'absolute' as const,
    top: '2px',
    left: '2px',
    width: '20px',
    height: '20px',
    background: 'white',
    borderRadius: '50%',
    boxShadow: '0 2px 4px rgba(0,0,0,0.2)',
    transition: 'transform 0.2s',
  },
  toggleText: {
    fontSize: '0.875rem',
    fontWeight: 600,
    color: '#333',
    marginRight: '0.5rem',
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
  streamingContainer: {
    textAlign: 'left',
    background: '#f8f9fa',
    borderRadius: '8px',
    padding: '1rem',
    maxWidth: '600px',
    margin: '0 auto',
  },
  streamingHeader: {
    fontWeight: 600,
    color: '#0066cc',
    marginBottom: '0.75rem',
    fontSize: '0.9rem',
  },
  streamingSteps: {
    display: 'flex',
    flexDirection: 'column' as const,
    gap: '0.5rem',
  },
  streamingStep: {
    fontSize: '0.85rem',
    color: '#444',
    padding: '0.25rem 0',
    borderBottom: '1px solid #e0e0e0',
  },
  stepPlan: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: '0.5rem',
    color: '#6c5ce7',
  },
  stepThinking: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.5rem',
    color: '#666',
    fontStyle: 'italic',
  },
  stepTool: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.5rem',
    flexWrap: 'wrap' as const,
    color: '#00b894',
  },
  stepResult: {
    color: '#636e72',
    fontSize: '0.8rem',
  },
  stepFinal: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.5rem',
    color: '#00b894',
    fontWeight: 500,
  },
  stepError: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.5rem',
    color: '#d63031',
  },
  stepIcon: {
    flexShrink: 0,
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
  // Trace styles for Agent Mode
  traceContainer: {
    marginTop: '1rem',
    paddingTop: '1rem',
    borderTop: '1px solid #e0e0e0',
  },
  traceToggle: {
    background: 'none',
    border: '1px solid #ccc',
    borderRadius: '4px',
    padding: '0.5rem 1rem',
    cursor: 'pointer',
    fontSize: '0.75rem',
    color: '#666',
    display: 'flex',
    alignItems: 'center',
    gap: '0.5rem',
  },
  traceList: {
    marginTop: '0.75rem',
    display: 'flex',
    flexDirection: 'column',
    gap: '0.5rem',
  },
  traceEntry: {
    padding: '0.75rem',
    background: '#f8f9fa',
    borderRadius: '6px',
    fontSize: '0.8rem',
    borderLeft: '3px solid #0066cc',
  },
  traceType: {
    fontWeight: 'bold',
    color: '#0066cc',
    marginRight: '0.5rem',
    textTransform: 'uppercase',
    fontSize: '0.7rem',
  },
  traceTool: {
    background: '#e0e0e0',
    padding: '0.15rem 0.4rem',
    borderRadius: '3px',
    fontSize: '0.7rem',
    fontFamily: 'monospace',
  },
  traceSteps: {
    margin: '0.5rem 0 0 1rem',
    padding: 0,
    fontSize: '0.8rem',
    color: '#444',
  },
  traceInput: {
    display: 'block',
    marginTop: '0.25rem',
    fontSize: '0.7rem',
    color: '#666',
    background: '#e8e8e8',
    padding: '0.25rem 0.5rem',
    borderRadius: '3px',
    wordBreak: 'break-all',
  },
  traceOutput: {
    marginTop: '0.25rem',
    fontSize: '0.75rem',
    color: '#28a745',
  },
  traceNotes: {
    marginTop: '0.25rem',
    fontSize: '0.75rem',
    color: '#666',
    fontStyle: 'italic',
  },
  traceError: {
    marginTop: '0.25rem',
    fontSize: '0.75rem',
    color: '#dc3545',
    fontWeight: 'bold',
  },
};
