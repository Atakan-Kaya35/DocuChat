/**
 * WebSocket hook for real-time indexing progress updates
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { IndexProgressEvent, ConnectionStatus, WebSocketMessage } from './types';

const WS_BASE_URL = import.meta.env.VITE_WS_BASE_URL || 'ws://localhost:8000';
const WS_ENDPOINT = '/ws/indexing';
const RECONNECT_DELAY = 3000;
const MAX_RECONNECT_ATTEMPTS = 5;

interface UseIndexingSocketOptions {
  token: string | null;
  onProgress?: (event: IndexProgressEvent) => void;
  onComplete?: (event: IndexProgressEvent) => void;
  onFailed?: (event: IndexProgressEvent) => void;
}

interface UseIndexingSocketReturn {
  status: ConnectionStatus;
  connect: () => void;
  disconnect: () => void;
}

export function useIndexingSocket({
  token,
  onProgress,
  onComplete,
  onFailed,
}: UseIndexingSocketOptions): UseIndexingSocketReturn {
  const [status, setStatus] = useState<ConnectionStatus>('disconnected');
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const shouldReconnectRef = useRef(true);

  // Clean up timeout on unmount
  const clearReconnectTimeout = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
  }, []);

  // Handle incoming messages
  const handleMessage = useCallback(
    (event: MessageEvent) => {
      try {
        const message: WebSocketMessage = JSON.parse(event.data);
        const data = message.data;

        switch (message.type) {
          case 'index_progress':
            onProgress?.(data);
            break;
          case 'index_complete':
            onComplete?.(data);
            break;
          case 'index_failed':
            onFailed?.(data);
            break;
          default:
            console.warn('[WebSocket] Unknown message type:', message.type);
        }
      } catch (error) {
        console.error('[WebSocket] Failed to parse message:', error);
      }
    },
    [onProgress, onComplete, onFailed]
  );

  // Connect to WebSocket
  const connect = useCallback(() => {
    if (!token) {
      console.warn('[WebSocket] No token available, cannot connect');
      return;
    }

    if (wsRef.current?.readyState === WebSocket.OPEN) {
      console.log('[WebSocket] Already connected');
      return;
    }

    // Close existing connection if any
    if (wsRef.current) {
      wsRef.current.close();
    }

    setStatus('connecting');
    shouldReconnectRef.current = true;

    const url = `${WS_BASE_URL}${WS_ENDPOINT}?token=${encodeURIComponent(token)}`;
    console.log('[WebSocket] Connecting to:', WS_ENDPOINT);

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log('[WebSocket] Connected');
      setStatus('connected');
      reconnectAttemptsRef.current = 0;
    };

    ws.onclose = (event) => {
      console.log('[WebSocket] Disconnected:', event.code, event.reason);
      setStatus('disconnected');

      // Attempt to reconnect if not intentionally closed
      if (
        shouldReconnectRef.current &&
        reconnectAttemptsRef.current < MAX_RECONNECT_ATTEMPTS
      ) {
        reconnectAttemptsRef.current++;
        console.log(
          `[WebSocket] Reconnecting in ${RECONNECT_DELAY}ms (attempt ${reconnectAttemptsRef.current}/${MAX_RECONNECT_ATTEMPTS})`
        );
        reconnectTimeoutRef.current = setTimeout(() => {
          connect();
        }, RECONNECT_DELAY);
      }
    };

    ws.onerror = (error) => {
      console.error('[WebSocket] Error:', error);
      setStatus('error');
    };

    ws.onmessage = handleMessage;
  }, [token, handleMessage]);

  // Disconnect from WebSocket
  const disconnect = useCallback(() => {
    shouldReconnectRef.current = false;
    clearReconnectTimeout();
    
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    
    setStatus('disconnected');
  }, [clearReconnectTimeout]);

  // Auto-connect when token is available
  useEffect(() => {
    if (token) {
      connect();
    } else {
      disconnect();
    }

    return () => {
      disconnect();
    };
  }, [token, connect, disconnect]);

  return {
    status,
    connect,
    disconnect,
  };
}
