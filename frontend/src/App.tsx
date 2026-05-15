import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Video } from "lucide-react";
import type { ServiceStatus } from "./types";
import { api } from "./api";
import { Header } from "./components/Header";
import { StreamList } from "./components/StreamList";
import { StreamDetail } from "./components/StreamDetail";
import { AddStreamDialog } from "./components/AddStreamDialog";
import { RetentionCard } from "./components/RetentionCard";
import { SegmentLengthCard } from "./components/SegmentLengthCard";
import { TimezoneCard } from "./components/TimezoneCard";
import { ToastProvider, useToast } from "./components/Toast";

const STATUS_POLL_MS = 3000;

function Dashboard() {
  const toast = useToast();
  const [status, setStatus] = useState<ServiceStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [toggling, setToggling] = useState(false);
  const pollTimer = useRef<number | undefined>(undefined);

  const refresh = useCallback(async () => {
    try {
      const s = await api.status();
      setStatus(s);
      setError(null);
      // Auto-select first stream once data arrives.
      setSelected((prev) => {
        if (prev && s.streams.some((x) => x.name === prev)) return prev;
        return s.streams[0]?.name ?? null;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
    function schedule() {
      pollTimer.current = window.setTimeout(async () => {
        await refresh();
        schedule();
      }, STATUS_POLL_MS);
    }
    schedule();
    return () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current);
    };
  }, [refresh]);

  async function toggleRunning() {
    if (!status) return;
    setToggling(true);
    try {
      const next = status.running ? await api.stop() : await api.start();
      setStatus(next);
      toast(
        "success",
        next.running ? "Daemon started." : "Daemon stopped.",
      );
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setToggling(false);
    }
  }

  async function handleAddStream(data: {
    name: string;
    url: string;
    enabled: boolean;
  }) {
    await api.addStream(data);
    toast("success", `Added ${data.name}`);
    setSelected(data.name);
    await refresh();
  }

  const selectedStream =
    status?.streams.find((s) => s.name === selected) ?? null;

  // If we have never received a status, but have an error, the backend isn't
  // reachable. Show a dedicated screen so the user gets actionable info
  // instead of an empty dashboard that pretends nothing's wrong.
  if (status === null && error) {
    return <BackendUnreachable error={error} onRetry={refresh} />;
  }

  return (
    <div className="h-full flex flex-col">
      <Header
        running={status?.running ?? false}
        busy={toggling}
        onToggle={toggleRunning}
      />

      {error && (
        <div className="bg-rose-500/10 border-b border-rose-500/30 text-rose-200 px-6 py-2 text-sm flex items-center gap-2">
          <AlertTriangle size={14} />
          <span>Lost connection to backend: {error}</span>
        </div>
      )}

      <main className="flex-1 min-h-0 grid grid-cols-[320px_1fr]">
        <aside className="border-r border-white/[0.06] flex flex-col min-h-0">
          <div className="flex-1 min-h-0">
            <StreamList
              streams={status?.streams ?? []}
              selected={selected}
              onSelect={setSelected}
              onAdd={() => setAddOpen(true)}
            />
          </div>
          {status && (
            <>
              <SegmentLengthCard
                segmentSeconds={status.segment_seconds}
                onUpdate={refresh}
              />
              <TimezoneCard
                timezone={status.timezone}
                onUpdate={refresh}
              />
              <RetentionCard
                retentionDays={status.retention_days}
                onUpdate={refresh}
              />
            </>
          )}
        </aside>
        <section className="min-h-0 overflow-hidden">
          {selectedStream ? (
            <StreamDetail
              key={selectedStream.name}
              stream={selectedStream}
              onChanged={refresh}
              onRemoved={() => {
                setSelected(null);
                refresh();
              }}
            />
          ) : (
            <EmptyState onAdd={() => setAddOpen(true)} />
          )}
        </section>
      </main>

      <AddStreamDialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onSubmit={handleAddStream}
      />
    </div>
  );
}

function BackendUnreachable({
  error,
  onRetry,
}: {
  error: string;
  onRetry: () => void;
}) {
  return (
    <div className="h-full flex items-center justify-center p-6">
      <div className="max-w-md w-full card p-6">
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 rounded-xl bg-rose-500/15 border border-rose-500/30 flex items-center justify-center">
            <AlertTriangle size={18} className="text-rose-300" />
          </div>
          <h2 className="text-lg font-semibold tracking-tight">
            Backend not reachable
          </h2>
        </div>
        <p className="text-sm text-ink-300 mt-3 leading-relaxed">
          The UI couldn&apos;t reach the recorder service. In development the
          Vite dev server proxies <code className="font-mono text-ink-100">/api/*</code>{" "}
          to <code className="font-mono text-ink-100">http://127.0.0.1:8765</code>{" "}
          — make sure the backend is running:
        </p>
        <pre className="mt-3 bg-ink-950 border border-white/[0.06] rounded-lg px-3 py-2.5 text-xs font-mono text-ink-200 overflow-x-auto">
          uv run rtsp-recorder
        </pre>
        <p className="text-xs text-ink-400 mt-3 leading-relaxed">
          The dashboard polls automatically — it will recover as soon as the
          backend is up. Last error:
        </p>
        <div className="mt-2 font-mono text-xs text-rose-300 bg-rose-500/10 border border-rose-500/20 rounded-md px-3 py-2 break-words">
          {error}
        </div>
        <button className="btn-primary mt-5 w-full" onClick={onRetry}>
          Retry now
        </button>
      </div>
    </div>
  );
}

function EmptyState({ onAdd }: { onAdd: () => void }) {
  return (
    <div className="h-full flex items-center justify-center">
      <div className="text-center max-w-sm px-6">
        <div className="mx-auto h-14 w-14 rounded-2xl bg-accent/10 border border-accent/30 flex items-center justify-center mb-4">
          <Video size={22} className="text-accent" />
        </div>
        <h2 className="text-lg font-semibold tracking-tight">
          No stream selected
        </h2>
        <p className="text-sm text-ink-400 mt-1.5 leading-relaxed">
          Add an RTSP source to start recording in 1-minute segments. Files
          land in their own folder, ready for download.
        </p>
        <button className="btn-primary mt-5" onClick={onAdd}>
          Add stream
        </button>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <Dashboard />
    </ToastProvider>
  );
}
