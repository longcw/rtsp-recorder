import type { StreamState } from "../types";

const COLOR: Record<StreamState, string> = {
  recording: "bg-emerald-400",
  starting: "bg-amber-300",
  error: "bg-rose-400",
  stopped: "bg-ink-400",
};

const RING: Record<StreamState, string> = {
  recording: "ring-emerald-400/30",
  starting: "ring-amber-300/30",
  error: "ring-rose-400/30",
  stopped: "ring-ink-400/20",
};

export function StatusDot({
  state,
  size = "md",
}: {
  state: StreamState;
  size?: "sm" | "md";
}) {
  const dim = size === "sm" ? "h-2 w-2" : "h-2.5 w-2.5";
  const pulse = state === "recording" ? "animate-pulse-dot" : "";
  return (
    <span
      aria-label={state}
      className={`inline-block rounded-full ring-4 ${dim} ${COLOR[state]} ${RING[state]} ${pulse}`}
    />
  );
}

export function StateLabel({ state }: { state: StreamState }) {
  const text =
    state === "recording"
      ? "Recording"
      : state === "starting"
        ? "Starting"
        : state === "error"
          ? "Error"
          : "Stopped";
  const color =
    state === "recording"
      ? "text-emerald-300"
      : state === "starting"
        ? "text-amber-200"
        : state === "error"
          ? "text-rose-300"
          : "text-ink-400";
  return <span className={`text-xs font-medium ${color}`}>{text}</span>;
}
