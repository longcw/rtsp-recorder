import { Power, Video } from "lucide-react";

interface Props {
  running: boolean;
  busy: boolean;
  onToggle: () => void;
}

export function Header({ running, busy, onToggle }: Props) {
  return (
    <header className="flex items-center justify-between px-6 h-16 border-b border-white/[0.06]">
      <div className="flex items-center gap-3">
        <div className="h-9 w-9 rounded-xl bg-gradient-to-br from-accent/30 to-accent/5 border border-accent/30 flex items-center justify-center">
          <Video size={18} className="text-accent" />
        </div>
        <div>
          <div className="font-semibold text-ink-50 leading-none">
            rtsp-recorder
          </div>
          <div className="text-xs text-ink-400 mt-1">
            continuous RTSP capture
          </div>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <div
          className={`hidden sm:flex items-center gap-2 px-3 h-9 rounded-lg text-xs font-medium border ${
            running
              ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
              : "bg-ink-800/70 border-white/[0.06] text-ink-400"
          }`}
        >
          <span
            className={`inline-block h-1.5 w-1.5 rounded-full ${
              running ? "bg-emerald-400 animate-pulse-dot" : "bg-ink-500"
            }`}
          />
          {running ? "Daemon running" : "Daemon stopped"}
        </div>
        <button
          onClick={onToggle}
          disabled={busy}
          className={running ? "btn-danger" : "btn-primary"}
        >
          <Power size={15} />
          {running ? "Stop" : "Start"}
        </button>
      </div>
    </header>
  );
}
