import { useEffect, useMemo, useRef, useState } from "react";
import {
  AudioLines,
  Download,
  Gauge,
  Loader2,
  Scissors,
  VolumeX,
  X,
} from "lucide-react";
import type { ClipJob, RecordingFile, Waveform } from "../types";
import { api } from "../api";
import { useToast } from "./Toast";
import { decodePeaks, WaveformStrip } from "./Waveform";

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
  const [exportJob, setExportJob] = useState<ClipJob | null>(null);
  const [exportSpeed, setExportSpeed] = useState(1);
  const [rate, setRate] = useState(1);
  // True when autoplay was only allowed because we muted the element. Drives
  // the unmute prompt; recordings carry audio whenever the camera sends it.
  const [autoMuted, setAutoMuted] = useState(false);
  // Mirrors autoMuted for applyRate, which runs from event handlers that would
  // otherwise close over a stale value.
  const policyMuted = useRef(false);
  // Timer that makes up the speed above the browser's native playbackRate cap
  // (~16x) by advancing currentTime. See applyRate.
  const fastTimer = useRef<number | undefined>(undefined);

  // Browsers refuse unmuted autoplay without a user gesture, so a recording
  // with sound would otherwise sit frozen on its first frame. Try with sound,
  // and on rejection fall back to muted playback plus a one-tap unmute.
  async function startPlayback() {
    const v = videoRef.current;
    if (!v) return;
    try {
      await v.play();
    } catch {
      v.muted = true;
      policyMuted.current = true;
      setAutoMuted(true);
      try {
        await v.play();
      } catch {
        // Leave it paused; the native controls can still start it.
      }
    }
  }

  function unmute() {
    const v = videoRef.current;
    if (!v) return;
    v.muted = false;
    policyMuted.current = false;
    setAutoMuted(false);
    void v.play().catch(() => {});
  }

  // Apply a logical rate to the element. Up to the native cap we just set
  // playbackRate; beyond it the browser clamps, so we cover the shortfall by
  // stepping currentTime forward on a timer (cheap on faststart/keyframe
  // files). Re-engaged on play, torn down on pause/unmount.
  function applyRate(r: number) {
    const v = videoRef.current;
    if (!v) return;
    if (fastTimer.current !== undefined) {
      window.clearInterval(fastTimer.current);
      fastTimer.current = undefined;
    }
    // Browsers reject playbackRate outside their supported range (Chrome
    // throws above 16x rather than clamping), so cap what we hand the element
    // and make up the rest with the timer below.
    let native = Math.min(r, NATIVE_MAX_RATE);
    try {
      v.playbackRate = native;
    } catch {
      // This browser's cap is even lower (e.g. older Safari) — fall back to
      // 1x and let the timer carry the whole speed-up.
      native = 1;
      try {
        v.playbackRate = 1;
      } catch {
        /* leave whatever the element had */
      }
    }
    const extra = Math.max(0, r - v.playbackRate);
    // Above the native cap we advance currentTime by hand, which turns audio
    // into stutter rather than speech, so silence it until the rate drops back.
    v.muted = policyMuted.current || extra > 0.5;
    if (extra > 0.5 && !v.paused) {
      const stepMs = 250;
      fastTimer.current = window.setInterval(() => {
        const el = videoRef.current;
        if (!el) return;
        const next = el.currentTime + (extra * stepMs) / 1000;
        if (el.duration && next >= el.duration) {
          el.currentTime = el.duration;
          if (fastTimer.current !== undefined) {
            window.clearInterval(fastTimer.current);
            fastTimer.current = undefined;
          }
        } else {
          el.currentTime = next;
        }
      }, stepMs);
    }
  }

  function changeRate(r: number) {
    setRate(r);
    applyRate(r);
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Tear down the fast-forward timer when the player closes.
  useEffect(() => {
    return () => {
      if (fastTimer.current !== undefined) {
        window.clearInterval(fastTimer.current);
      }
    };
  }, []);

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
    // The server runs ffmpeg as a job; poll it so a multi-minute re-encode
    // shows progress instead of a frozen spinner, then hand the finished file
    // to the browser's own download manager rather than buffering it here.
    let job: ClipJob;
    try {
      job = await api.startClip(
        streamName,
        file.name,
        clipStart,
        clipEnd,
        exportSpeed,
      );
      setExportJob(job);
      while (job.state === "running") {
        await new Promise((r) => setTimeout(r, 500));
        job = await api.clipJob(job.id);
        setExportJob(job);
      }
    } catch (e) {
      setExportJob(null);
      toast("error", e instanceof Error ? e.message : String(e));
      return;
    }
    setExportJob(null);
    if (job.state === "error") {
      toast("error", job.error ?? "Export failed.");
    } else if (job.state === "done") {
      const a = document.createElement("a");
      a.href = api.clipDownloadUrl(job.id);
      a.download = job.download_name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      toast("success", "Clip exported.");
    }
  }

  function cancelExport() {
    if (!exportJob) return;
    // the poll loop sees the cancelled state and stops on its own
    api.cancelClip(exportJob.id).catch(() => {});
  }

  const exporting = exportJob !== null;
  const canExport =
    clipStart !== null && clipEnd !== null && clipEnd > clipStart && !exporting;
  const clipDuration =
    clipStart !== null && clipEnd !== null ? clipEnd - clipStart : null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-0 sm:p-4"
      role="dialog"
      aria-modal
      aria-label={`Playing ${file.name}`}
    >
      <div
        className="absolute inset-0 bg-ink-950/80 backdrop-blur-sm"
        onClick={onClose}
      />

      <div className="relative w-full h-full sm:h-auto max-w-4xl card rounded-none sm:rounded-xl overflow-hidden flex flex-col max-h-[100dvh] sm:max-h-[90vh]">
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
              className="inline-flex items-center justify-center h-9 w-9 sm:h-7 sm:w-7 rounded-md hover:bg-white/[0.06] text-ink-300 hover:text-ink-100"
              title="Download"
            >
              <Download size={16} />
            </a>
            <button
              className="inline-flex items-center justify-center h-9 w-9 sm:h-7 sm:w-7 rounded-md hover:bg-white/[0.06] text-ink-300 hover:text-ink-100"
              onClick={onClose}
              aria-label="Close"
              title="Close (Esc)"
            >
              <X size={17} />
            </button>
          </div>
        </div>

        <div className="relative flex-1 min-h-0 sm:flex-none bg-black flex items-center justify-center">
          {autoMuted && (
            <button
              onClick={unmute}
              className="absolute top-3 left-3 z-10 inline-flex items-center gap-1.5 rounded-md bg-black/70 px-2.5 py-1.5 text-xs font-medium text-ink-100 ring-1 ring-white/20 hover:bg-black/85"
            >
              <VolumeX size={14} />
              Tap for sound
            </button>
          )}
          <video
            ref={videoRef}
            src={url}
            controls
            playsInline
            onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
            // Re-engage the fast-forward timer on resume; drop it on pause so
            // a paused video doesn't keep seeking.
            onPlay={() => applyRate(rate)}
            onPause={() => {
              if (fastTimer.current !== undefined) {
                window.clearInterval(fastTimer.current);
                fastTimer.current = undefined;
              }
            }}
            onLoadedMetadata={(e) => {
              const d = e.currentTarget.duration;
              setDuration(Number.isFinite(d) ? d : null);
              // Browsers reset playbackRate when a new source loads; re-apply
              // the user's choice so it survives autoplay/metadata load.
              applyRate(rate);
              void startPlayback();
            }}
            className="w-full h-full object-contain sm:h-auto sm:max-h-[70vh]"
          />
        </div>

        <AudioTimeline
          streamName={streamName}
          file={file.name}
          live={live}
          duration={duration}
          currentTime={currentTime}
          onSeek={seekTo}
        />

        <SpeedBar rate={rate} onChange={changeRate} />

        <TrimBar
          currentTime={currentTime}
          duration={duration}
          clipStart={clipStart}
          clipEnd={clipEnd}
          exportProgress={exportJob?.progress ?? null}
          canExport={canExport}
          clipDuration={clipDuration}
          exportSpeed={exportSpeed}
          onExportSpeedChange={setExportSpeed}
          onSetStart={setStartHere}
          onSetEnd={setEndHere}
          onSeek={seekTo}
          onClear={clearMarks}
          onExport={exportClip}
          onCancel={cancelExport}
        />
      </div>
    </div>
  );
}

// How long to wait before asking again for a waveform the analyzer has not
// produced yet. It runs on a 30 s cadence, so this catches up within a tick.
const WAVEFORM_RETRY_MS = 10000;

function AudioTimeline({
  streamName,
  file,
  live,
  duration,
  currentTime,
  onSeek,
}: {
  streamName: string;
  file: string;
  live: boolean;
  duration: number | null;
  currentTime: number;
  onSeek: (seconds: number) => void;
}) {
  const [wave, setWave] = useState<Waveform | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    // The live segment is still growing, so any waveform we drew would be
    // both partial and wrong about where the end is.
    if (live) return;
    let active = true;
    let timer: number | undefined;
    async function load() {
      try {
        const w = await api.waveform(streamName, file);
        if (!active) return;
        setWave(w);
        if (w.pending) timer = window.setTimeout(load, WAVEFORM_RETRY_MS);
      } catch {
        if (active) setFailed(true);
      }
    }
    void load();
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [streamName, file, live]);

  const peaks = useMemo(
    () => (wave?.peaks ? decodePeaks(wave.peaks) : null),
    [wave?.peaks],
  );

  // Lay the peaks out against whichever length is longer, so a segment whose
  // video runs past its last audio packet still ends where the video ends.
  const span = Math.max(duration ?? 0, wave?.duration ?? 0);

  let body;
  if (live) {
    body = <Note>Available once this segment finishes recording.</Note>;
  } else if (failed) {
    body = <Note>Waveform unavailable.</Note>;
  } else if (wave === null) {
    body = <Note>Loading…</Note>;
  } else if (wave.pending) {
    body = <Note>Waveform is still being generated…</Note>;
  } else if (peaks === null || span <= 0) {
    body = <Note>This recording has no audio.</Note>;
  } else {
    body = (
      <WaveformStrip
        peaks={peaks}
        peaksDuration={wave.duration ?? span}
        duration={span}
        currentTime={currentTime}
        onSeek={onSeek}
      />
    );
  }

  return (
    <div className="border-t border-white/[0.06] px-4 py-2">
      <div className="flex items-center gap-2 mb-1">
        <AudioLines size={14} className="text-ink-300 shrink-0" />
        <span className="text-[11px] uppercase tracking-wider text-ink-500">
          Audio
        </span>
      </div>
      {body}
    </div>
  );
}

function Note({ children }: { children: React.ReactNode }) {
  return (
    <div className="h-11 flex items-center text-xs text-ink-500">{children}</div>
  );
}

const SPEEDS = [1, 2, 4, 8, 16, 32, 64];
// Most browsers cap HTMLMediaElement.playbackRate at 16x; above this we drive
// playback by stepping currentTime on a timer (see applyRate).
const NATIVE_MAX_RATE = 16;

function SpeedBar({
  rate,
  onChange,
}: {
  rate: number;
  onChange: (r: number) => void;
}) {
  return (
    <div className="border-t border-white/[0.06] px-4 py-2 flex items-center gap-2 flex-wrap">
      <Gauge size={14} className="text-ink-300 shrink-0" />
      <span className="text-[11px] uppercase tracking-wider text-ink-500 shrink-0">
        Speed
      </span>
      <div className="flex items-center gap-1 flex-wrap">
        {SPEEDS.map((s) => {
          const active = s === rate;
          return (
            <button
              key={s}
              onClick={() => onChange(s)}
              aria-pressed={active}
              className={
                "font-mono text-xs rounded px-3 py-1.5 sm:px-2 sm:py-0.5 border " +
                (active
                  ? "bg-white/[0.10] border-white/[0.14] text-ink-100"
                  : "bg-white/[0.02] border-white/[0.06] text-ink-300 hover:bg-white/[0.06] hover:text-ink-100")
              }
            >
              {s}×
            </button>
          );
        })}
      </div>
    </div>
  );
}

interface TrimBarProps {
  currentTime: number;
  duration: number | null;
  clipStart: number | null;
  clipEnd: number | null;
  // null when idle, otherwise 0..1 of the export done so far
  exportProgress: number | null;
  canExport: boolean;
  clipDuration: number | null;
  exportSpeed: number;
  onExportSpeedChange: (s: number) => void;
  onSetStart: () => void;
  onSetEnd: () => void;
  onSeek: (s: number) => void;
  onClear: () => void;
  onExport: () => void;
  onCancel: () => void;
}

function TrimBar({
  currentTime,
  duration,
  clipStart,
  clipEnd,
  exportProgress,
  canExport,
  clipDuration,
  exportSpeed,
  onExportSpeedChange,
  onSetStart,
  onSetEnd,
  onSeek,
  onClear,
  onExport,
  onCancel,
}: TrimBarProps) {
  const hasMarks = clipStart !== null || clipEnd !== null;
  const exporting = exportProgress !== null;
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
            {exportSpeed > 1 && (
              <span className="text-ink-500">
                {" "}
                → {fmt(clipDuration / exportSpeed)}
              </span>
            )}
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
        <select
          className="font-mono text-xs rounded px-1.5 py-1.5 sm:py-0.5 bg-white/[0.02] border border-white/[0.06] text-ink-300 hover:bg-white/[0.06] hover:text-ink-100 focus:outline-none"
          value={exportSpeed}
          onChange={(e) => onExportSpeedChange(Number(e.target.value))}
          disabled={exporting}
          title="Export speed (above 1× re-encodes and drops audio)"
          aria-label="Export speed"
        >
          {SPEEDS.map((s) => (
            <option key={s} value={s}>
              {s}×
            </option>
          ))}
        </select>
        {exporting && (
          <button
            className="text-xs text-ink-300 hover:text-ink-100 underline-offset-2 hover:underline"
            onClick={onCancel}
          >
            Cancel
          </button>
        )}
        <button
          className="btn-primary relative overflow-hidden inline-flex items-center gap-1.5 px-3 py-1.5 text-xs disabled:opacity-50 disabled:cursor-not-allowed"
          onClick={onExport}
          disabled={!canExport}
          title={canExport ? "Export clip" : "Set both start and end first"}
        >
          {exporting && (
            <span
              className="absolute inset-y-0 left-0 bg-white/20 transition-[width] duration-300"
              style={{ width: `${Math.round(exportProgress * 100)}%` }}
            />
          )}
          <span className="relative inline-flex items-center gap-1.5">
            {exporting ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <Download size={13} />
            )}
            {exporting
              ? `Exporting… ${Math.round(exportProgress * 100)}%`
              : "Export clip"}
          </span>
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
