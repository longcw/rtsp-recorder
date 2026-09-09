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
  // Whether the source carries usable audio, as of the last probe. Null when
  // the stream has never been probed successfully.
  has_audio: boolean | null;
  // Input audio codec, e.g. "pcm_alaw". Recorded as AAC unless MP4 can carry it.
  audio_codec: string | null;
  // When the next reconnect attempt is due, while the camera is unreachable.
  next_retry_at: string | null;
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
  // True while the analyzer is currently processing this file.
  analyzing: boolean;
  // Decode progress in [0,1] when analyzing; null otherwise (or when the
  // duration probe failed so we have no denominator).
  analyze_progress: number | null;
}

export interface Waveform {
  // Base64 peak levels, one byte per bucket. Null when the recording carries
  // no audio track.
  peaks: string | null;
  // Seconds the peaks span. Null when the duration probe failed.
  duration: number | null;
  // True while the analyzer has not reached this recording yet.
  pending: boolean;
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

export interface ClipJob {
  id: string;
  state: "running" | "done" | "error" | "cancelled";
  progress: number;
  error: string | null;
  download_name: string;
}
