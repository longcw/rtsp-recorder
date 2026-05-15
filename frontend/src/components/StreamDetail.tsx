import { useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Download,
  Moon,
  Pause,
  Play,
  PlayCircle,
  RotateCw,
  ScanSearch,
  Trash2,
  X,
} from "lucide-react";
import type { RecordingFile, StreamStatus } from "../types";
import { api } from "../api";
import { StatusDot, StateLabel } from "./StatusDot";
import { useToast } from "./Toast";
import { VideoPlayerModal } from "./VideoPlayerModal";

interface Props {
  stream: StreamStatus;
  onChanged: () => void;
  onRemoved: () => void;
}

const FILE_POLL_MS = 5000;

export function StreamDetail({ stream, onChanged, onRemoved }: Props) {
  const toast = useToast();
  const [files, setFiles] = useState<RecordingFile[] | null>(null);
  const [loadingFiles, setLoadingFiles] = useState(true);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [playing, setPlaying] = useState<RecordingFile | null>(null);
  const [confirmDeleteFile, setConfirmDeleteFile] =
    useState<RecordingFile | null>(null);
  const [deletingFile, setDeletingFile] = useState(false);

  useEffect(() => {
    let active = true;
    let timer: number | undefined;

    async function tick() {
      try {
        const list = await api.listFiles(stream.name);
        if (!active) return;
        setFiles(list);
      } catch (e) {
        if (!active) return;
        toast("error", e instanceof Error ? e.message : String(e));
      } finally {
        if (active) {
          setLoadingFiles(false);
          timer = window.setTimeout(tick, FILE_POLL_MS);
        }
      }
    }
    setLoadingFiles(true);
    setFiles(null);
    tick();
    return () => {
      active = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [stream.name, toast]);

  async function toggleEnabled() {
    setBusy(true);
    try {
      await api.patchStream(stream.name, { enabled: !stream.enabled });
      onChanged();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function deleteFile(file: RecordingFile) {
    setDeletingFile(true);
    // Optimistic: remove from the list immediately so the modal can close
    // without a poll round-trip first.
    const prev = files;
    setFiles((cur) => (cur ? cur.filter((x) => x.name !== file.name) : cur));
    try {
      await api.deleteFile(stream.name, file.name);
      toast("success", `Deleted ${file.name}`);
      setConfirmDeleteFile(null);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
      setFiles(prev);
    } finally {
      setDeletingFile(false);
    }
  }

  async function rescanFile(filename: string) {
    // Optimistic: clear the chip so the user sees something happen
    // immediately. Polling will refill with the new label.
    setFiles((cur) =>
      cur ? cur.map((x) => (x.name === filename ? { ...x, idle: null } : x)) : cur,
    );
    try {
      await api.reanalyzeFile(stream.name, filename);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
      try {
        const list = await api.listFiles(stream.name);
        setFiles(list);
      } catch {
        /* leave optimistic state */
      }
    }
  }

  async function rescanIdle() {
    // Optimistically clear all idle flags so chips disappear immediately;
    // the analyzer will re-populate them within a few seconds.
    setFiles((cur) => (cur ? cur.map((x) => ({ ...x, idle: null })) : cur));
    try {
      const res = await api.reanalyzeIdle(stream.name);
      toast("success", `Re-scanning ${res.dropped} recording(s) for idle…`);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
      try {
        const list = await api.listFiles(stream.name);
        setFiles(list);
      } catch {
        /* leave optimistic state */
      }
    }
  }

  async function setIdle(filename: string, idle: boolean) {
    // Optimistic: update locally so the chip flips immediately.
    setFiles((cur) =>
      cur ? cur.map((x) => (x.name === filename ? { ...x, idle } : x)) : cur,
    );
    try {
      await api.setFileIdle(stream.name, filename, idle);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
      try {
        const list = await api.listFiles(stream.name);
        setFiles(list);
      } catch {
        /* leave optimistic state */
      }
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.removeStream(stream.name);
      toast("success", `Removed ${stream.name}`);
      onRemoved();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  const totalSize = (files ?? []).reduce((a, b) => a + b.size, 0);

  return (
    <div className="h-full flex flex-col">
      <div className="px-4 sm:px-6 py-5 border-b border-white/[0.06]">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2.5">
              <StatusDot state={stream.state} />
              <h1 className="text-xl font-semibold tracking-tight truncate">
                {stream.name}
              </h1>
              <StateLabel state={stream.state} />
            </div>
            <div className="font-mono text-xs text-ink-400 mt-2 truncate">
              {stream.url}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              className="btn-ghost"
              onClick={toggleEnabled}
              disabled={busy}
              title={stream.enabled ? "Disable" : "Enable"}
            >
              {stream.enabled ? <Pause size={15} /> : <Play size={15} />}
              <span className="hidden sm:inline">
                {stream.enabled ? "Disable" : "Enable"}
              </span>
            </button>
            <button
              className="btn-danger"
              onClick={() => setConfirmDelete(true)}
              disabled={busy}
            >
              <Trash2 size={15} />
              <span className="hidden sm:inline">Remove</span>
            </button>
          </div>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-5">
          <Stat label="Current file" value={stream.current_file ?? "—"} mono />
          <Stat
            label="Started"
            value={stream.started_at ? timeAgo(stream.started_at) : "—"}
          />
          <Stat label="Restarts" value={String(stream.restart_count)} />
          <Stat
            label="Recordings"
            value={files ? `${files.length} (${formatBytes(totalSize)})` : "…"}
          />
        </div>

        {stream.last_error && (
          <div className="mt-4 flex items-start gap-2.5 rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-2.5 text-sm text-rose-200">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" />
            <div className="font-mono text-xs leading-snug">
              {stream.last_error}
            </div>
          </div>
        )}
      </div>

      <div className="flex-1 min-h-0 px-4 sm:px-6 py-5 flex flex-col">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-ink-200">Recordings</h2>
          <div className="flex items-center gap-1.5">
            <button
              className="btn-ghost h-7 px-2 text-xs"
              onClick={rescanIdle}
              title="Drop idle labels and re-run the detector on every recording in this stream"
            >
              <ScanSearch size={13} />
              Re-scan idle
            </button>
            <button
              className="btn-ghost h-7 px-2 text-xs"
              onClick={() => {
                setLoadingFiles(true);
                api
                  .listFiles(stream.name)
                  .then(setFiles)
                  .catch((e) => toast("error", String(e)))
                  .finally(() => setLoadingFiles(false));
              }}
            >
              <RotateCw size={13} />
              Refresh
            </button>
          </div>
        </div>
        <div className="card flex-1 min-h-0 overflow-y-auto">
          {loadingFiles && files === null ? (
            <div className="p-6 text-sm text-ink-400">Loading…</div>
          ) : files && files.length === 0 ? (
            <div className="p-6 text-sm text-ink-400">
              No recordings yet. They will appear here once ffmpeg finalizes
              the first segment (≈1 min).
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-ink-900/95 backdrop-blur border-b border-white/[0.06]">
                <tr className="text-left">
                  <th className="px-4 py-2.5 text-xs font-medium text-ink-400 uppercase tracking-wider">
                    Recording
                  </th>
                  <th className="px-4 py-2.5 text-xs font-medium text-ink-400 uppercase tracking-wider w-24">
                    Duration
                  </th>
                  <th className="px-4 py-2.5 text-xs font-medium text-ink-400 uppercase tracking-wider w-24 hidden sm:table-cell">
                    Size
                  </th>
                  <th className="px-2 py-2.5 w-44" />
                </tr>
              </thead>
              <tbody>
                {(files ?? []).map((f) => {
                  const live = f.name === stream.current_file;
                  const range = formatTimeRange(f, live);
                  return (
                    <tr
                      key={f.name}
                      onClick={() => setPlaying(f)}
                      className={`border-b border-white/[0.04] last:border-0 cursor-pointer ${
                        live
                          ? "bg-emerald-500/[0.04] hover:bg-emerald-500/[0.07]"
                          : "hover:bg-white/[0.02]"
                      }`}
                    >
                      <td className="px-4 py-2.5 max-w-0">
                        <div className="flex items-center gap-2 min-w-0">
                          {live && (
                            <span className="inline-flex items-center gap-1 px-1.5 h-5 rounded-md bg-emerald-500/15 border border-emerald-500/30 text-emerald-300 text-[10px] font-semibold uppercase tracking-wider shrink-0">
                              <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse-dot" />
                              REC
                            </span>
                          )}
                          {!live && f.idle === true && (
                            <span
                              className="inline-flex items-center gap-1 pl-1.5 pr-0.5 h-5 rounded-md bg-indigo-500/10 border border-indigo-400/25 text-indigo-200 text-[10px] font-semibold uppercase tracking-wider shrink-0"
                              title="No motion detected. Will be pruned on the idle retention schedule. Click X if this is wrong."
                            >
                              <Moon size={10} />
                              Idle
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setIdle(f.name, false);
                                }}
                                className="ml-0.5 mr-0.5 inline-flex items-center justify-center h-3.5 w-3.5 rounded hover:bg-indigo-500/25 text-indigo-300 hover:text-indigo-100"
                                aria-label="Mark as not idle"
                                title="Mark as not idle"
                              >
                                <X size={10} />
                              </button>
                            </span>
                          )}
                          {!live && f.idle === false && (
                            <span
                              className="inline-flex items-center gap-1 px-1.5 h-5 rounded-md bg-amber-400/8 border border-amber-400/20 text-amber-200/90 text-[10px] font-semibold uppercase tracking-wider shrink-0"
                              title="Motion detected. Kept on the regular retention schedule."
                            >
                              <Activity size={10} />
                              Action
                            </span>
                          )}
                          <div className="min-w-0">
                            <div className="text-ink-100 tabular-nums truncate">
                              {range.primary}
                            </div>
                            <div className="text-[11px] text-ink-400 truncate flex items-center gap-1.5">
                              {range.dateLabel && (
                                <>
                                  <span>{range.dateLabel}</span>
                                  <span className="text-ink-500">·</span>
                                </>
                              )}
                              <span
                                className="font-mono text-ink-500 truncate"
                                title={f.name}
                              >
                                {f.name}
                              </span>
                            </div>
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-2.5 text-ink-300 tabular-nums">
                        {live ? (
                          <span className="text-emerald-300">
                            {formatDuration(f.duration_seconds)}
                          </span>
                        ) : (
                          formatDuration(f.duration_seconds)
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-ink-300 tabular-nums hidden sm:table-cell">
                        {formatBytes(f.size)}
                      </td>
                      <td className="px-2 py-1.5 text-right">
                        <div className="inline-flex items-center gap-0.5">
                          {!live && (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                rescanFile(f.name);
                              }}
                              className="inline-flex items-center justify-center h-7 w-7 rounded-md hover:bg-white/[0.06] text-ink-400 hover:text-ink-100"
                              title="Re-scan this recording for motion"
                            >
                              <RotateCw size={13} />
                            </button>
                          )}
                          {!live && f.idle !== true && (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                setIdle(f.name, true);
                              }}
                              className="inline-flex items-center justify-center h-7 w-7 rounded-md hover:bg-white/[0.06] text-ink-400 hover:text-indigo-200"
                              title="Mark as idle"
                            >
                              <Moon size={14} />
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              setPlaying(f);
                            }}
                            className="inline-flex items-center justify-center h-7 w-7 rounded-md hover:bg-white/[0.06] text-ink-300 hover:text-ink-100"
                            title={live ? "Play (in progress)" : "Play"}
                          >
                            <PlayCircle size={15} />
                          </button>
                          <a
                            href={api.fileUrl(stream.name, f.name)}
                            download
                            onClick={(e) => e.stopPropagation()}
                            className="inline-flex items-center justify-center h-7 w-7 rounded-md hover:bg-white/[0.06] text-ink-300 hover:text-ink-100"
                            title={live ? "Download in-progress copy" : "Download"}
                          >
                            <Download size={14} />
                          </a>
                          {!live && (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                setConfirmDeleteFile(f);
                              }}
                              className="inline-flex items-center justify-center h-7 w-7 rounded-md hover:bg-rose-500/15 text-ink-400 hover:text-rose-300"
                              title="Delete recording"
                            >
                              <Trash2 size={13} />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {confirmDeleteFile && (
        <div
          className="fixed inset-0 z-40 flex items-center justify-center p-4"
          role="dialog"
        >
          <div
            className="absolute inset-0 bg-ink-950/70 backdrop-blur-sm"
            onClick={() => !deletingFile && setConfirmDeleteFile(null)}
          />
          <div className="relative card p-5 max-w-sm w-full">
            <h3 className="text-base font-semibold">Delete recording?</h3>
            <p className="text-sm text-ink-300 mt-2 break-words">
              <span className="font-mono text-xs">{confirmDeleteFile.name}</span>
              <span className="block text-ink-400 mt-1.5">
                This permanently removes the file from disk.
              </span>
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                className="btn-ghost"
                onClick={() => setConfirmDeleteFile(null)}
                disabled={deletingFile}
              >
                Cancel
              </button>
              <button
                className="btn-danger"
                onClick={() => deleteFile(confirmDeleteFile)}
                disabled={deletingFile}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      {playing && (
        <VideoPlayerModal
          streamName={stream.name}
          file={playing}
          live={playing.name === stream.current_file}
          onClose={() => setPlaying(null)}
        />
      )}

      {confirmDelete && (
        <div
          className="fixed inset-0 z-40 flex items-center justify-center p-4"
          role="dialog"
        >
          <div
            className="absolute inset-0 bg-ink-950/70 backdrop-blur-sm"
            onClick={() => !busy && setConfirmDelete(false)}
          />
          <div className="relative card p-5 max-w-sm w-full">
            <h3 className="text-base font-semibold">Remove stream?</h3>
            <p className="text-sm text-ink-300 mt-2">
              <span className="font-mono">{stream.name}</span> will stop
              recording. Existing files on disk are <strong>not</strong>{" "}
              deleted.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                className="btn-ghost"
                onClick={() => setConfirmDelete(false)}
                disabled={busy}
              >
                Cancel
              </button>
              <button className="btn-danger" onClick={remove} disabled={busy}>
                Remove
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="card px-3.5 py-2.5">
      <div className="text-[10px] uppercase tracking-wider text-ink-400 font-medium">
        {label}
      </div>
      <div
        className={`mt-1 text-sm text-ink-100 truncate ${mono ? "font-mono text-xs" : ""}`}
        title={value}
      >
        {value}
      </div>
    </div>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}

function timeAgo(iso: string): string {
  const t = new Date(iso).getTime();
  const diff = Math.max(0, Date.now() - t);
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return rs === 0 ? `${m}m` : `${m}m ${rs}s`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm === 0 ? `${h}h` : `${h}h ${rm}m`;
}

// `started_at` is a naive ISO string ("YYYY-MM-DDTHH:MM:SS", no offset)
// representing wall-clock time in the recorder's configured timezone. We
// parse the components directly so the browser's local-time interpretation
// of new Date(naive_iso) doesn't shift them.
function parseNaiveIso(iso: string): { date: string; time: string } | null {
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/);
  if (!m) return null;
  return {
    date: `${m[1]}-${m[2]}-${m[3]}`,
    time: `${m[4]}:${m[5]}:${m[6]}`,
  };
}

function addSecondsToHms(time: string, seconds: number): string {
  const [hh, mm, ss] = time.split(":").map(Number);
  const total = (hh * 3600 + mm * 60 + ss + Math.round(seconds)) % 86400;
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function formatTimeRange(
  f: { name: string; started_at: string | null; duration_seconds: number | null },
  live: boolean,
): { primary: string; dateLabel: string | null } {
  if (!f.started_at) {
    // Filename didn't match our pattern — fall back to showing it as the
    // primary and let the filename sub-row repeat it (cheap, harmless).
    return { primary: f.name, dateLabel: null };
  }
  const parsed = parseNaiveIso(f.started_at);
  if (!parsed) return { primary: f.name, dateLabel: null };

  const dur = f.duration_seconds ?? 0;
  const endTime = live ? null : addSecondsToHms(parsed.time, dur);
  const primary = endTime
    ? `${parsed.time} → ${endTime}`
    : `${parsed.time} → …`;

  // Friendly date for the sub-row. Falls back to the ISO date for older
  // recordings so users can always tell which day a file came from.
  const today = isoDateToday();
  const yesterday = isoDateOffset(-1);
  let dateLabel: string = parsed.date;
  if (parsed.date === today) dateLabel = "Today";
  else if (parsed.date === yesterday) dateLabel = "Yesterday";

  return { primary, dateLabel };
}

function isoDateToday(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function isoDateOffset(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
