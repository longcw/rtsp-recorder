import { useCallback, useEffect, useState } from "react";
import {
  Download,
  ExternalLink,
  Film,
  PlayCircle,
  Trash2,
  X,
} from "lucide-react";
import type { ClipJob, SavedClip } from "../types";
import { api } from "../api";
import { useToast } from "./Toast";
import { Modal } from "./Modal";
import { formatBytes, formatTimeRange } from "./StreamDetail";
import { fmt, SPEEDS } from "./VideoPlayerModal";

interface Props {
  // Open the recording a clip was cut from, with the clip's span marked.
  onOpenSource: (clip: SavedClip) => void;
}

export function SavedClips({ onOpenSource }: Props) {
  const toast = useToast();
  const [clips, setClips] = useState<SavedClip[] | null>(null);
  const [playing, setPlaying] = useState<{ clip: SavedClip; speed: number } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<SavedClip | null>(null);
  // running re-exports, keyed by clip id
  const [jobs, setJobs] = useState<Record<string, ClipJob>>({});

  const refresh = useCallback(async () => {
    try {
      setClips(await api.savedClips());
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    }
  }, [toast]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function reexport(clip: SavedClip, speed: number) {
    let job: ClipJob;
    try {
      job = await api.reexportClip(clip.id, speed);
      setJobs((j) => ({ ...j, [clip.id]: job }));
      while (job.state === "running") {
        await new Promise((r) => setTimeout(r, 500));
        job = await api.clipJob(job.id);
        setJobs((j) => ({ ...j, [clip.id]: job }));
      }
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
      job = { state: "error" } as ClipJob;
    }
    setJobs(({ [clip.id]: _, ...rest }) => rest);
    if (job.state === "error" && job.error) toast("error", job.error);
    if (job.state === "done") toast("success", `Exported at ${speed}×.`);
    await refresh();
  }

  async function removeRendition(clip: SavedClip, speed: number) {
    try {
      await api.deleteRendition(clip.id, speed);
      await refresh();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    }
  }

  async function removeClip(clip: SavedClip) {
    setConfirmDelete(null);
    try {
      await api.deleteSavedClip(clip.id);
      toast("success", "Clip deleted.");
      await refresh();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    }
  }

  const totalSize = (clips ?? []).reduce(
    (n, c) => n + c.renditions.reduce((m, r) => m + r.size, 0),
    0,
  );

  return (
    <div className="h-full overflow-y-auto">
      <div className="px-4 sm:px-6 py-5 border-b border-white/[0.06]">
        <div className="flex items-center gap-2.5">
          <Film size={18} className="text-accent" />
          <h1 className="text-lg font-semibold tracking-tight">Saved clips</h1>
        </div>
        <p className="text-sm text-ink-400 mt-1">
          {clips === null
            ? "Loading…"
            : `${clips.length} ${clips.length === 1 ? "clip" : "clips"} · ${formatBytes(totalSize)}. Kept until you delete them, whatever the retention.`}
        </p>
      </div>

      {clips !== null && clips.length === 0 && (
        <div className="px-6 py-16 text-center text-sm text-ink-400">
          No saved clips yet. Mark a span in any recording and export it.
        </div>
      )}

      <ul className="divide-y divide-white/[0.06]">
        {(clips ?? []).map((clip) => {
          const length = clip.end - clip.start;
          const range = formatTimeRange(
            { name: clip.source, started_at: clip.started_at, duration_seconds: length },
            false,
          );
          const job = jobs[clip.id];
          const have = new Set(clip.renditions.map((r) => r.speed));
          return (
            <li key={clip.id} className="px-4 sm:px-6 py-3.5">
              <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium tabular-nums">
                    {range.primary}
                    <span className="text-ink-400 font-normal"> · {fmt(length)}</span>
                  </div>
                  <div className="text-xs text-ink-400 mt-0.5">
                    <span className="text-ink-200">{clip.stream}</span>
                    {range.dateLabel && <> · {range.dateLabel}</>}
                  </div>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    className="btn-ghost h-7 px-2 text-xs"
                    onClick={() => onOpenSource(clip)}
                    disabled={!clip.source_exists}
                    title={
                      clip.source_exists
                        ? "Open the recording this clip came from"
                        : "The source recording has been pruned"
                    }
                  >
                    <ExternalLink size={13} />
                    {clip.source_exists ? "Source" : "Source pruned"}
                  </button>
                  <button
                    className="inline-flex items-center justify-center h-7 w-7 rounded-md hover:bg-white/[0.06] text-ink-400 hover:text-rose-300"
                    onClick={() => setConfirmDelete(clip)}
                    title="Delete clip"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>

              <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                {clip.renditions.map((r) => (
                  <div
                    key={r.speed}
                    className="inline-flex items-center rounded-md border border-white/[0.08] bg-white/[0.02] text-xs"
                  >
                    <button
                      className="inline-flex items-center gap-1.5 h-7 pl-2 pr-1.5 hover:bg-white/[0.06] rounded-l-md"
                      onClick={() => setPlaying({ clip, speed: r.speed })}
                      title={r.speed === 1 ? "Play (full quality, with audio)" : `Play ${r.speed}× copy`}
                    >
                      <PlayCircle size={13} className="text-accent" />
                      <span className="font-mono">{r.speed}×</span>
                      <span className="text-ink-400">{formatBytes(r.size)}</span>
                    </button>
                    <a
                      href={api.savedClipUrl(clip.id, r.speed, true)}
                      download
                      className="inline-flex items-center justify-center h-7 w-7 hover:bg-white/[0.06] text-ink-400 hover:text-ink-100"
                      title="Download"
                    >
                      <Download size={13} />
                    </a>
                    {r.speed !== 1 && (
                      <button
                        className="inline-flex items-center justify-center h-7 w-7 hover:bg-white/[0.06] text-ink-400 hover:text-rose-300 rounded-r-md"
                        onClick={() => removeRendition(clip, r.speed)}
                        title={`Delete the ${r.speed}× copy`}
                      >
                        <X size={13} />
                      </button>
                    )}
                  </div>
                ))}

                {job ? (
                  <div className="inline-flex items-center gap-2 text-xs text-ink-300 h-7 px-2">
                    <div className="w-24 h-1.5 rounded-full bg-white/[0.08] overflow-hidden">
                      <div
                        className="h-full bg-accent transition-[width]"
                        style={{ width: `${Math.round(job.progress * 100)}%` }}
                      />
                    </div>
                    Exporting {job.speed}×
                    <button
                      className="underline-offset-2 hover:underline hover:text-ink-100"
                      onClick={() => api.cancelClip(job.id).catch(() => {})}
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <select
                    className="font-mono text-xs h-7 rounded-md px-1.5 bg-white/[0.02] border border-white/[0.06] text-ink-300 hover:bg-white/[0.06] hover:text-ink-100 focus:outline-none"
                    value=""
                    onChange={(e) => {
                      if (e.target.value) void reexport(clip, Number(e.target.value));
                    }}
                    title="Export another speed (re-encoded from the full-quality copy, no audio)"
                    aria-label="Export at another speed"
                  >
                    <option value="">Export at…</option>
                    {SPEEDS.filter((s) => !have.has(s)).map((s) => (
                      <option key={s} value={s}>
                        {s}×
                      </option>
                    ))}
                  </select>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      {playing && (
        <ClipPlayer
          clip={playing.clip}
          speed={playing.speed}
          onClose={() => setPlaying(null)}
        />
      )}

      <Modal
        open={confirmDelete !== null}
        onClose={() => setConfirmDelete(null)}
        title="Delete clip?"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setConfirmDelete(null)}>
              Cancel
            </button>
            <button
              className="btn-danger"
              onClick={() => confirmDelete && removeClip(confirmDelete)}
            >
              Delete
            </button>
          </>
        }
      >
        <p className="text-sm text-ink-300">
          This deletes every copy of the clip.
          {confirmDelete && !confirmDelete.source_exists &&
            " Its source recording is already pruned, so it cannot be cut again."}
        </p>
      </Modal>
    </div>
  );
}

function ClipPlayer({
  clip,
  speed,
  onClose,
}: {
  clip: SavedClip;
  speed: number;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-0 sm:p-4"
      role="dialog"
      aria-modal
      aria-label="Playing saved clip"
    >
      <div className="absolute inset-0 bg-ink-950/80 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full h-full sm:h-auto max-w-4xl card rounded-none sm:rounded-xl overflow-hidden flex flex-col">
        <div className="flex items-center justify-between gap-4 px-4 py-2.5 border-b border-white/[0.06]">
          <div className="min-w-0 text-sm font-semibold truncate">
            {clip.stream}
            <span className="text-ink-400 font-normal"> · {speed}× · {clip.source}</span>
          </div>
          <button
            className="inline-flex items-center justify-center h-9 w-9 sm:h-7 sm:w-7 rounded-md hover:bg-white/[0.06] text-ink-300 hover:text-ink-100"
            onClick={onClose}
            aria-label="Close"
            title="Close (Esc)"
          >
            <X size={17} />
          </button>
        </div>
        <div className="flex-1 min-h-0 bg-black flex items-center justify-center">
          <video
            src={api.savedClipUrl(clip.id, speed)}
            controls
            autoPlay
            playsInline
            className="w-full h-full object-contain sm:h-auto sm:max-h-[75vh]"
          />
        </div>
      </div>
    </div>
  );
}
