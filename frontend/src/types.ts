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
  idle_retention_days: number;
  motion_threshold: number;
  segment_seconds: number;
  timezone: string;
  streams: StreamStatus[];
}

export interface RecordingFile {
  name: string;
  size: number;
  modified_at: string;
  // ISO datetime parsed from the filename (no offset suffix — wall-clock
  // in the recorder's configured timezone). Null if the filename didn't
  // match the segment pattern.
  started_at: string | null;
  // mtime - started_at, in seconds. Null when started_at is null.
  duration_seconds: number | null;
  // Backend idle classification. null = not yet analyzed.
  idle: boolean | null;
}

export interface Config {
  streams: Stream[];
  retention_days: number;
  idle_retention_days: number;
  motion_threshold: number;
  segment_seconds: number;
  timezone: string;
  running: boolean;
}
