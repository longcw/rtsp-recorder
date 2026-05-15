import { useEffect, useMemo, useState } from "react";
import { Check, Pencil } from "lucide-react";
import { api } from "../api";
import { useToast } from "./Toast";

// Curated fallback if Intl.supportedValuesOf("timeZone") isn't available
// (older browsers). Covers the most common cases for IP camera deployments.
const FALLBACK_ZONES = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Sao_Paulo",
  "Europe/London",
  "Europe/Berlin",
  "Europe/Paris",
  "Europe/Moscow",
  "Asia/Dubai",
  "Asia/Kolkata",
  "Asia/Shanghai",
  "Asia/Tokyo",
  "Asia/Singapore",
  "Asia/Seoul",
  "Australia/Sydney",
  "Pacific/Auckland",
];

function allZones(): string[] {
  const intl = Intl as unknown as {
    supportedValuesOf?: (key: string) => string[];
  };
  if (typeof intl.supportedValuesOf === "function") {
    try {
      return intl.supportedValuesOf("timeZone");
    } catch {
      /* fall through */
    }
  }
  return FALLBACK_ZONES;
}

function formatOffset(tz: string): string {
  // "GMT+08:00" style, derived from the browser's Intl formatter.
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      timeZoneName: "shortOffset",
    }).formatToParts(new Date());
    return parts.find((p) => p.type === "timeZoneName")?.value ?? "";
  } catch {
    return "";
  }
}

export function TimezoneCard({
  timezone,
  onUpdate,
}: {
  timezone: string;
  onUpdate: () => void;
}) {
  const toast = useToast();
  const zones = useMemo(allZones, []);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(timezone);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!editing) setValue(timezone);
  }, [timezone, editing]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.setTimezone(value);
      toast("success", `Timezone set to ${value}.`);
      setEditing(false);
      onUpdate();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const offset = formatOffset(timezone);

  return (
    <div className="px-5 py-4 border-t border-white/[0.06]">
      <div className="text-[11px] font-medium uppercase tracking-wider text-ink-400">
        Timezone
      </div>
      {editing ? (
        <form onSubmit={save} className="mt-2 flex items-center gap-2">
          <input
            list="tz-options"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="input h-8 text-sm flex-1 font-mono"
            placeholder="UTC"
            autoFocus
            disabled={busy}
            spellCheck={false}
          />
          <datalist id="tz-options">
            {zones.map((tz) => (
              <option key={tz} value={tz} />
            ))}
          </datalist>
          <button
            type="submit"
            className="btn-primary h-8 px-2.5"
            disabled={busy || !value.trim()}
            aria-label="Save"
          >
            <Check size={14} />
          </button>
        </form>
      ) : (
        <div className="mt-1 flex items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="text-sm text-ink-100 font-mono truncate">
              {timezone}
            </div>
            {offset && (
              <div className="text-[11px] text-ink-400 tabular-nums">
                {offset}
              </div>
            )}
          </div>
          <button
            className="btn-ghost h-7 px-2 text-xs shrink-0"
            onClick={() => setEditing(true)}
          >
            <Pencil size={12} />
            Edit
          </button>
        </div>
      )}
      <p className="text-xs text-ink-400 mt-1.5 leading-snug">
        Used for filenames and segment-boundary alignment. Changes restart
        active streams.
      </p>
    </div>
  );
}
