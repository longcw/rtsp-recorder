import { useEffect, useState } from "react";
import { Check, Pencil } from "lucide-react";
import { api } from "../api";
import { useToast } from "./Toast";

const PRESETS: { label: string; seconds: number }[] = [
  { label: "30 seconds", seconds: 30 },
  { label: "1 minute", seconds: 60 },
  { label: "2 minutes", seconds: 120 },
  { label: "5 minutes", seconds: 300 },
  { label: "10 minutes", seconds: 600 },
  { label: "15 minutes", seconds: 900 },
  { label: "30 minutes", seconds: 1800 },
  { label: "1 hour", seconds: 3600 },
];

function formatSeconds(s: number): string {
  const preset = PRESETS.find((p) => p.seconds === s);
  if (preset) return preset.label;
  if (s % 60 === 0) {
    const m = s / 60;
    return `${m} minute${m === 1 ? "" : "s"}`;
  }
  return `${s} seconds`;
}

export function SegmentLengthCard({
  segmentSeconds,
  onUpdate,
}: {
  segmentSeconds: number;
  onUpdate: () => void;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(String(segmentSeconds));
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!editing) setValue(String(segmentSeconds));
  }, [segmentSeconds, editing]);

  async function save(seconds: number) {
    setBusy(true);
    try {
      await api.setSegmentSeconds(seconds);
      toast("success", `Segment length: ${formatSeconds(seconds)}.`);
      setEditing(false);
      onUpdate();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="px-5 py-4 border-t border-white/[0.06]">
      <div className="text-[11px] font-medium uppercase tracking-wider text-ink-400">
        Segment length
      </div>
      {editing ? (
        <form
          className="mt-2 flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const n = Number(value);
            if (!Number.isFinite(n) || n < 10 || n > 3600) {
              toast("error", "Must be between 10 and 3600 seconds.");
              return;
            }
            save(Math.floor(n));
          }}
        >
          <select
            value={
              PRESETS.some((p) => String(p.seconds) === value) ? value : ""
            }
            onChange={(e) => setValue(e.target.value)}
            className="input h-8 text-sm flex-1 pr-8"
            disabled={busy}
          >
            {PRESETS.map((p) => (
              <option key={p.seconds} value={p.seconds}>
                {p.label}
              </option>
            ))}
          </select>
          <button
            type="submit"
            className="btn-primary h-8 px-2.5"
            disabled={busy}
            aria-label="Save"
          >
            <Check size={14} />
          </button>
        </form>
      ) : (
        <div className="mt-1 flex items-center justify-between">
          <div className="text-sm text-ink-100">
            {formatSeconds(segmentSeconds)}
          </div>
          <button
            className="btn-ghost h-7 px-2 text-xs"
            onClick={() => setEditing(true)}
          >
            <Pencil size={12} />
            Edit
          </button>
        </div>
      )}
      <p className="text-xs text-ink-400 mt-1.5 leading-snug">
        Each recording covers this much time. Changes restart active streams.
      </p>
    </div>
  );
}
