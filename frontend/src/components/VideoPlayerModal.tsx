import { useEffect, useRef } from "react";
import { Download, X } from "lucide-react";
import type { RecordingFile } from "../types";
import { api } from "../api";

interface Props {
  streamName: string;
  file: RecordingFile;
  live: boolean;
  onClose: () => void;
}

export function VideoPlayerModal({ streamName, file, live, onClose }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const url = api.fileUrl(streamName, file.name);
  const subtitle = formatSubtitle(file);

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
            className="w-full max-h-[80vh]"
          />
        </div>
      </div>
    </div>
  );
}

function formatSubtitle(f: RecordingFile): string | null {
  if (!f.started_at) return null;
  const m = f.started_at.match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/,
  );
  if (!m) return null;
  return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}`;
}
