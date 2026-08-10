import { memo, useCallback, useEffect, useRef, useState } from "react";

// Peak levels arrive base64-encoded, one byte per bucket: 0 is silence and
// 255 is full scale on a fixed -60 dBFS floor, so levels are comparable
// between recordings.
export function decodePeaks(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

const PLAYED = "#7aa7ff"; // accent
const UNPLAYED = "#3c4456"; // ink-600
const BAR_WIDTH = 2;
const BAR_GAP = 1;
const STRIP_HEIGHT = 44;

interface StripProps {
  peaks: Uint8Array;
  // Seconds the peak array spans. Can fall short of the video's own duration,
  // because a segment cut lands on a keyframe and can run past the last audio.
  peaksDuration: number;
  duration: number;
  currentTime: number;
  onSeek: (seconds: number) => void;
}

export function WaveformStrip({
  peaks,
  peaksDuration,
  duration,
  currentTime,
  onSeek,
}: StripProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const ro = new ResizeObserver((entries) => {
      setWidth(entries[0].contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const el = canvasRef.current;
    if (!el || width === 0 || duration <= 0 || peaksDuration <= 0) return;
    const dpr = window.devicePixelRatio || 1;
    el.width = Math.round(width * dpr);
    el.height = Math.round(STRIP_HEIGHT * dpr);
    const ctx = el.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, STRIP_HEIGHT);

    const bars = Math.max(1, Math.floor(width / (BAR_WIDTH + BAR_GAP)));
    const played = (currentTime / duration) * bars;
    for (let i = 0; i < bars; i++) {
      // Each bar covers a slice of the timeline; draw the loudest peak in it,
      // so a short burst survives however wide the strip is.
      const from = Math.floor((i / bars) * (duration / peaksDuration) * peaks.length);
      const to = Math.max(
        from + 1,
        Math.ceil(((i + 1) / bars) * (duration / peaksDuration) * peaks.length),
      );
      let level = 0;
      for (let k = from; k < to && k < peaks.length; k++) {
        if (peaks[k] > level) level = peaks[k];
      }
      const h = Math.max(1, (level / 255) * (STRIP_HEIGHT - 4));
      ctx.fillStyle = i < played ? PLAYED : UNPLAYED;
      ctx.fillRect(i * (BAR_WIDTH + BAR_GAP), (STRIP_HEIGHT - h) / 2, BAR_WIDTH, h);
    }
  }, [peaks, peaksDuration, duration, currentTime, width]);

  const seekFrom = useCallback(
    (clientX: number, el: HTMLElement) => {
      const r = el.getBoundingClientRect();
      if (r.width <= 0) return;
      const pct = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
      onSeek(pct * duration);
    },
    [duration, onSeek],
  );

  const playedPct =
    duration > 0 ? Math.min(100, Math.max(0, (currentTime / duration) * 100)) : 0;

  return (
    <div
      className="relative cursor-pointer touch-none select-none"
      style={{ height: STRIP_HEIGHT }}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        seekFrom(e.clientX, e.currentTarget);
      }}
      onPointerMove={(e) => {
        if (e.currentTarget.hasPointerCapture(e.pointerId)) {
          seekFrom(e.clientX, e.currentTarget);
        }
      }}
    >
      <canvas
        ref={canvasRef}
        className="block w-full"
        style={{ height: STRIP_HEIGHT }}
      />
      <div
        className="pointer-events-none absolute inset-y-0 w-px bg-ink-100"
        style={{ left: `${playedPct}%` }}
      />
    </div>
  );
}

// One path element rather than one bar element, because the recordings list
// renders every file in the stream and re-renders it on every poll.
export const MiniWaveform = memo(function MiniWaveform({
  peaks,
  className,
}: {
  peaks: Uint8Array;
  className?: string;
}) {
  let d = "";
  for (let i = 0; i < peaks.length; i++) {
    const h = Math.max(0.5, (peaks[i] / 255) * 11);
    const y = (12 - h) / 2;
    d += `M${i + 0.5} ${y.toFixed(2)}V${(y + h).toFixed(2)}`;
  }
  return (
    <svg
      viewBox={`0 0 ${peaks.length} 12`}
      preserveAspectRatio="none"
      aria-hidden
      className={className}
      style={{ width: "100%", height: 14 }}
    >
      <path
        d={d}
        stroke="currentColor"
        strokeWidth={1}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
});
