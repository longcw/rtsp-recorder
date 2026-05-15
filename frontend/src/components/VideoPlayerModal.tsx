import { useEffect, useRef, useState } from "react";
import { Download, Loader2, Scissors, X } from "lucide-react";
import type { RecordingFile } from "../types";
import { api } from "../api";
import { useToast } from "./Toast";

interface Props {
  streamName: string;
  file: RecordingFile;
  live: boolean;
  onClose: () => void;
}

export function VideoPlayerModal({ streamName, file, live, onClose }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const toast = useToast();
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState<number | null>(null);
  const [clipStart, setClipStart] = useState<number | null>(null);
  const [clipEnd, setClipEnd] = useState<number | null>(null);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const url = api.fileUrl(streamName, file.name);
  const subtitle = formatSubtitle(file);

  function setStartHere() {
    const v = videoRef.current;
    if (!v) return;
    const t = v.currentTime;
    setClipStart(t);
    if (clipEnd !== null && clipEnd <= t) setClipEnd(null);
  }

  function setEndHere() {
    const v = videoRef.current;
    if (!v) return;
    const t = v.currentTime;
    if (clipStart !== null && t <= clipStart) {
      toast("error", "End must be after start.");
      return;
    }
    setClipEnd(t);
  }

  function clearMarks() {
    setClipStart(null);
    setClipEnd(null);
  }

  function seekTo(seconds: number) {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = seconds;
  }

  async function exportClip() {
    if (clipStart === null || clipEnd === null) return;
    setExporting(true);
    try {
      const { blob, filename } = await api.clipFile(
        streamName,
        file.name,
        clipStart,
        clipEnd,
      );
      const objUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Revoke after a tick so the download has started.
      setTimeout(() => URL.revokeObjectURL(objUrl), 1000);
      toast("success", "Clip exported.");
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  }

  const canExport =
    clipStart !== null && clipEnd !== null && clipEnd > clipStart && !exporting;
  const clipDuration =
    clipStart !== null && clipEnd !== null ? clipEnd - clipStart : null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal
      aria-label={`Playing ${file.name}`}
    >
      <div
        className="absolute inset-0 bg-ink-950/80 backdrop-blur-sm"
        onClick={onClose}
      />

      <div className="relative w-full max-w-4xl card overflow-hidden flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between gap-4 px-4 py-2.5 border-b border-white/[0.06]">
          <div className="min-w-0 flex items-center gap-2.5">
            {live && (
              <span className="inline-flex items-center gap-1 px-1.5 h-5 rounded-md bg-emerald-500/15 border border-emerald-500/30 text-emerald-300 text-[10px] font-semibold uppercase tracking-wider shrink-0">
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse-dot" />
                REC
              </span>
            )}
            <div className="min-w-0">
              <div className="text-sm font-semibold truncate">
                {streamName}
                {subtitle && (
                  <span className="text-ink-400 font-normal">
                    {" "}
                    · {subtitle}
                  </span>
                )}
              </div>
              <div className="font-mono text-[11px] text-ink-500 truncate">
                {file.name}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <a
              href={url}
              download
              className="inline-flex items-center justify-center h-7 w-7 rounded-md hover:bg-white/[0.06] text-ink-300 hover:text-ink-100"
              title="Download"
            >
              <Download size={14} />
            </a>
            <button
              className="inline-flex items-center justify-center h-7 w-7 rounded-md hover:bg-white/[0.06] text-ink-300 hover:text-ink-100"
              onClick={onClose}
              aria-label="Close"
              title="Close (Esc)"
            >
              <X size={15} />
            </button>
          </div>
        </div>

        <div className="bg-black flex items-center justify-center">
          <video
            ref={videoRef}
            src={url}
            controls
            autoPlay
            playsInline
            onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
            onLoadedMetadata={(e) => {
              const d = e.currentTarget.duration;
              setDuration(Number.isFinite(d) ? d : null);
            }}
            className="w-full max-h-[70vh]"
          />
        </div>

        <TrimBar
          currentTime={currentTime}
          duration={duration}
          clipStart={clipStart}
          clipEnd={clipEnd}
          exporting={exporting}
          canExport={canExport}
          clipDuration={clipDuration}
          onSetStart={setStartHere}
          onSetEnd={setEndHere}
          onSeek={seekTo}
          onClear={clearMarks}
          onExport={exportClip}
        />
      </div>
    </div>
  );
}

interface TrimBarProps {
  currentTime: number;
  duration: number | null;
  clipStart: number | null;
  clipEnd: number | null;
  exporting: boolean;
  canExport: boolean;
  clipDuration: number | null;
  onSetStart: () => void;
  onSetEnd: () => void;
  onSeek: (s: number) => void;
  onClear: () => void;
  onExport: () => void;
}

function TrimBar({
  currentTime,
  duration,
  clipStart,
  clipEnd,
  exporting,
  canExport,
  clipDuration,
  onSetStart,
  onSetEnd,
  onSeek,
  onClear,
  onExport,
}: TrimBarProps) {
  const hasMarks = clipStart !== null || clipEnd !== null;
  return (
    <div className="border-t border-white/[0.06] px-4 py-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
      <div className="flex items-center gap-2 text-ink-400">
        <Scissors size={14} className="text-ink-300" />
        <span className="font-mono text-xs text-ink-300">
          {fmt(currentTime)}
          {duration !== null && (
            <span className="text-ink-500"> / {fmt(duration)}</span>
          )}
        </span>
      </div>

      <Marker
        label="Start"
        value={clipStart}
        onSetHere={onSetStart}
        onSeek={() => clipStart !== null && onSeek(clipStart)}
      />
      <Marker
        label="End"
        value={clipEnd}
        onSetHere={onSetEnd}
        onSeek={() => clipEnd !== null && onSeek(clipEnd)}
      />

      <div className="text-xs text-ink-400 font-mono">
        {clipDuration !== null ? (
          <>
            Clip: <span className="text-ink-100">{fmt(clipDuration)}</span>
          </>
        ) : (
          <span className="text-ink-500">Set start &amp; end to export</span>
        )}
      </div>

      <div className="ml-auto flex items-center gap-2">
        {hasMarks && (
          <button
            className="text-xs text-ink-300 hover:text-ink-100 underline-offset-2 hover:underline"
            onClick={onClear}
            disabled={exporting}
          >
            Reset
          </button>
        )}
        <button
          className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-xs disabled:opacity-50 disabled:cursor-not-allowed"
          onClick={onExport}
          disabled={!canExport}
          title={canExport ? "Export clip" : "Set both start and end first"}
        >
          {exporting ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <Download size={13} />
          )}
          {exporting ? "Exporting…" : "Export clip"}
        </button>
      </div>
    </div>
  );
}

function Marker({
  label,
  value,
  onSetHere,
  onSeek,
}: {
  label: string;
  value: number | null;
  onSetHere: () => void;
  onSeek: () => void;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[11px] uppercase tracking-wider text-ink-500">
        {label}
      </span>
      <button
        className="font-mono text-xs text-ink-100 bg-white/[0.04] border border-white/[0.06] rounded px-1.5 py-0.5 hover:bg-white/[0.08] disabled:hover:bg-white/[0.04] disabled:text-ink-500"
        onClick={onSeek}
        disabled={value === null}
        title={value === null ? "Not set" : "Jump to this position"}
      >
        {value === null ? "—:—" : fmt(value)}
      </button>
      <button
        className="text-[11px] text-ink-300 hover:text-ink-100 px-1.5 py-0.5 rounded hover:bg-white/[0.06]"
        onClick={onSetHere}
        title={`Set ${label.toLowerCase()} to current time`}
      >
        Set here
      </button>
    </div>
  );
}

function fmt(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00.0";
  const total = Math.max(0, seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const sStr = s.toFixed(1).padStart(4, "0");
  if (h > 0) {
    return `${h}:${String(m).padStart(2, "0")}:${sStr.padStart(4, "0")}`;
  }
  return `${m}:${sStr}`;
}

function formatSubtitle(f: RecordingFile): string | null {
  if (!f.started_at) return null;
  const m = f.started_at.match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/,
  );
  if (!m) return null;
  return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}`;
}
