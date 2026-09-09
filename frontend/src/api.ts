import type {
  ClipJob,
  Config,
  RecordingFile,
  ServiceStatus,
  Stream,
  Waveform,
} from "./types";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    // Read the body as text once, then optionally parse as JSON. The previous
    // version called res.json() and res.text() in sequence which throws
    // "body stream already read" because each fetch Response body can only
    // be consumed once.
    const text = await res.text().catch(() => "");
    let detail: string = text;
    try {
      const body = JSON.parse(text);
      detail = body?.detail ?? text;
    } catch {
      // Not JSON — keep the raw text.
    }
    throw new Error(detail || `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  status: () => fetch("/api/status").then(json<ServiceStatus>),
  start: () =>
    fetch("/api/start", { method: "POST" }).then(json<ServiceStatus>),
  stop: () =>
    fetch("/api/stop", { method: "POST" }).then(json<ServiceStatus>),

  addStream: (s: Stream) =>
    fetch("/api/streams", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(s),
    }).then(json<Config>),

  patchStream: (name: string, updates: Partial<Pick<Stream, "url" | "enabled">>) =>
    fetch(`/api/streams/${encodeURIComponent(name)}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(updates),
    }).then(json<Config>),

  removeStream: (name: string) =>
    fetch(`/api/streams/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }).then(json<Config>),

  listFiles: (name: string) =>
    fetch(`/api/streams/${encodeURIComponent(name)}/files`).then(
      json<RecordingFile[]>,
    ),

  fileUrl: (stream: string, file: string) =>
    `/api/streams/${encodeURIComponent(stream)}/files/${encodeURIComponent(file)}`,

  waveform: (stream: string, file: string) =>
    fetch(
      `/api/streams/${encodeURIComponent(stream)}/files/${encodeURIComponent(file)}/waveform`,
    ).then(json<Waveform>),

  // Thumbnail-resolution peaks for every analyzed file in the stream, keyed by
  // filename. Deliberately not part of listFiles, which is polled every few
  // seconds and already returns every recording.
  waveforms: (stream: string) =>
    fetch(`/api/streams/${encodeURIComponent(stream)}/waveforms`).then(
      json<Record<string, string | null>>,
    ),

  startClip: (
    stream: string,
    file: string,
    startSeconds: number,
    endSeconds: number,
    speed: number = 1,
  ) =>
    fetch(
      `/api/streams/${encodeURIComponent(stream)}/files/${encodeURIComponent(file)}/clip` +
        `?start=${encodeURIComponent(startSeconds.toFixed(3))}` +
        `&end=${encodeURIComponent(endSeconds.toFixed(3))}` +
        (speed > 1 ? `&speed=${encodeURIComponent(speed)}` : ""),
      { method: "POST" },
    ).then(json<ClipJob>),
  clipJob: (id: string) =>
    fetch(`/api/clips/${encodeURIComponent(id)}`).then(json<ClipJob>),
  cancelClip: (id: string) =>
    fetch(`/api/clips/${encodeURIComponent(id)}`, { method: "DELETE" }).then(
      json<{ ok: boolean }>,
    ),
  clipDownloadUrl: (id: string) =>
    `/api/clips/${encodeURIComponent(id)}/download`,

  setRetention: (retention_days: number) =>
    fetch("/api/config/retention", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ retention_days }),
    }).then(json<Config>),

  setIdleRetention: (idle_retention_days: number) =>
    fetch("/api/config/idle-retention", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ idle_retention_days }),
    }).then(json<Config>),

  setMotionThreshold: (motion_threshold: number) =>
    fetch("/api/config/motion-threshold", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ motion_threshold }),
    }).then(json<Config>),

  setFileIdle: (stream: string, file: string, idle: boolean) =>
    fetch(
      `/api/streams/${encodeURIComponent(stream)}/files/${encodeURIComponent(file)}`,
      {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ idle }),
      },
    ).then(json<{ name: string; idle: boolean }>),

  reanalyzeIdle: (stream: string) =>
    fetch(`/api/streams/${encodeURIComponent(stream)}/reanalyze-idle`, {
      method: "POST",
    }).then(json<{ dropped: number }>),

  reanalyzeFile: (stream: string, file: string) =>
    fetch(
      `/api/streams/${encodeURIComponent(stream)}/files/${encodeURIComponent(file)}/reanalyze`,
      { method: "POST" },
    ).then(json<{ name: string }>),

  deleteFile: (stream: string, file: string) =>
    fetch(
      `/api/streams/${encodeURIComponent(stream)}/files/${encodeURIComponent(file)}`,
      { method: "DELETE" },
    ).then(json<{ name: string; deleted: boolean }>),

  setSegmentSeconds: (segment_seconds: number) =>
    fetch("/api/config/segment-seconds", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ segment_seconds }),
    }).then(json<Config>),

  setTimezone: (timezone: string) =>
    fetch("/api/config/timezone", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ timezone }),
    }).then(json<Config>),
};
