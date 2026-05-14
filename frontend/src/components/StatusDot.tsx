import type { StreamState } from "../types";

const COLOR: Record<StreamState, string> = {
  recording: "bg-emerald-400",
  starting: "bg-amber-300",
  reconnecting: "bg-sky-300",
  error: "bg-rose-400",
  stopped: "bg-ink-400",
};

const RING: Record<StreamState, string> = {
  recording: "ring-emerald-400/30",
  starting: "ring-amber-300/30",
  reconnecting: "ring-sky-300/30",
  error: "ring-rose-400/30",
  stopped: "ring-ink-400/20",
};

const LABEL: Record<StreamState, string> = {
  recording: "Recording",
  starting: "Starting",
  reconnecting: "Reconnecting",
  error: "Error",
  stopped: "Stopped",
};

const TEXT_COLOR: Record<StreamState, string> = {
  recording: "text-emerald-300",
  starting: "text-amber-200",
  reconnecting: "text-sky-300",
  error: "text-rose-300",
  stopped: "text-ink-400",
};

export function StatusDot({
  state,
  size = "md",
}: {
  state: StreamState;
  size?: "sm" | "md";
}) {
  const dim = size === "sm" ? "h-2 w-2" : "h-2.5 w-2.5";
  const pulse =
    state === "recording" || state === "reconnecting"
      ? "animate-pulse-dot"
      : "";
  return (
    <span
      aria-label={state}
      className={`inline-block rounded-full ring-4 ${dim} ${COLOR[state]} ${RING[state]} ${pulse}`}
    />
  );
}

export function StateLabel({ state }: { state: StreamState }) {
  return (
    <span className={`text-xs font-medium ${TEXT_COLOR[state]}`}>
      {LABEL[state]}
    </span>
  );
}
