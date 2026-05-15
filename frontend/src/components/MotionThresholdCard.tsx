import { useEffect, useState } from "react";
import { Activity, Check, Pencil } from "lucide-react";
import { api } from "../api";
import { useToast } from "./Toast";

export function MotionThresholdCard({
  motionThreshold,
  onUpdate,
}: {
  motionThreshold: number;
  onUpdate: () => void;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(motionThreshold.toString());
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!editing) setValue(motionThreshold.toString());
  }, [motionThreshold, editing]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const n = Number(value);
    if (!Number.isFinite(n) || n < 0.5 || n > 50) {
      toast("error", "Motion threshold must be between 0.5 and 50.");
      return;
    }
    setBusy(true);
    try {
      await api.setMotionThreshold(n);
      toast(
        "success",
        `Motion threshold set to ${n}. Re-scan to relabel existing recordings.`,
      );
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
      <div className="text-[11px] font-medium uppercase tracking-wider text-ink-400 flex items-center gap-1.5">
        <Activity size={12} />
        Motion threshold
      </div>
      {editing ? (
        <form onSubmit={save} className="mt-2 flex items-center gap-2">
          <input
            type="number"
            min={0.5}
            max={50}
            step={0.5}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="input h-8 text-sm w-20 tabular-nums"
            autoFocus
          />
          <button
            type="submit"
            className="btn-primary h-8 px-2.5 ml-auto"
            disabled={busy}
          >
            <Check size={14} />
          </button>
        </form>
      ) : (
        <div className="mt-1 flex items-center justify-between">
          <div className="text-sm text-ink-100 tabular-nums">
            {motionThreshold}
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
        Lower = more sensitive (fewer idle). Higher = more permissive (more
        idle). Range <span className="text-ink-200 tabular-nums">0.5–50</span>,
        default <span className="text-ink-200 tabular-nums">5</span> — for
        reference, sensor noise sits{" "}
        <span className="text-ink-200 tabular-nums">0.5–2</span>, walking
        motion <span className="text-ink-200 tabular-nums">6+</span>.
      </p>
      <p className="text-xs text-ink-500 mt-1 leading-snug">
        Changes apply to future analyses only — use Re-scan idle to relabel
        existing recordings.
      </p>
    </div>
  );
}
