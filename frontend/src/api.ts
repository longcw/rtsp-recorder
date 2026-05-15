import type { Config, RecordingFile, ServiceStatus, Stream } from "./types";

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
