/**
 * WebSocket event types matching backend IndexProgressEvent
 */

export type EventType = 'index_progress' | 'index_complete' | 'index_failed';
export type ProgressStage = 'PARSE' | 'CHUNK' | 'EMBED' | 'STORE';

export interface IndexProgressEvent {
  type: EventType;
  document_id: string;
  job_id: string;
  user_id: string;
  stage: ProgressStage | null;
  progress: number;
  message: string | null;
  timestamp: string;
}

export interface WebSocketMessage {
  type: EventType;
  data: IndexProgressEvent;
}

export type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error';
