export type StreamState =
  | "stopped"
  | "starting"
  | "recording"
  | "reconnecting"
  | "error";

export interface Stream {
  name: string;
  url: string;
  enabled: boolean;
}

export interface StreamStatus {
  name: string;
  url: string;
  enabled: boolean;
  state: StreamState;
  started_at: string | null;
  last_error: string | null;
  restart_count: number;
  current_file: string | null;
}

export interface ServiceStatus {
  running: boolean;
  retention_days: number;
  segment_seconds: number;
  streams: StreamStatus[];
}

export interface RecordingFile {
  name: string;
  size: number;
  modified_at: string;
}

export interface Config {
  streams: Stream[];
  retention_days: number;
  segment_seconds: number;
  running: boolean;
}
