import type { Config, RecordingFile, ServiceStatus, Stream } from "./types";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail: string;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      detail = await res.text();
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

  setSegmentSeconds: (segment_seconds: number) =>
    fetch("/api/config/segment-seconds", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ segment_seconds }),
    }).then(json<Config>),
};
