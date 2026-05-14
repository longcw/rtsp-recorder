import { useEffect, useState } from "react";
import { Check, Pencil } from "lucide-react";
import { api } from "../api";
import { useToast } from "./Toast";

export function RetentionCard({
  retentionDays,
  onUpdate,
}: {
  retentionDays: number;
  onUpdate: () => void;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(String(retentionDays));
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!editing) setValue(String(retentionDays));
  }, [retentionDays, editing]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const n = Number(value);
    if (!Number.isFinite(n) || n < 1 || n > 3650) {
      toast("error", "Retention must be between 1 and 3650 days.");
      return;
    }
    setBusy(true);
    try {
      await api.setRetention(Math.floor(n));
      toast("success", `Retention set to ${Math.floor(n)} day(s).`);
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
        Retention
      </div>
      {editing ? (
        <form onSubmit={save} className="mt-2 flex items-center gap-2">
          <input
            type="number"
            min={1}
            max={3650}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="input h-8 text-sm w-20 tabular-nums"
            autoFocus
          />
          <span className="text-xs text-ink-400">days</span>
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
          <div className="text-sm text-ink-100">
            <span className="tabular-nums">{retentionDays}</span>
            <span className="text-ink-400 ml-1">
              day{retentionDays === 1 ? "" : "s"}
            </span>
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
        Files older than this are pruned automatically.
      </p>
    </div>
  );
}
