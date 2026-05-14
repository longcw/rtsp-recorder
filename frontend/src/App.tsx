import { useCallback, useEffect, useRef, useState } from "react";
import { Video } from "lucide-react";
import type { ServiceStatus } from "./types";
import { api } from "./api";
import { Header } from "./components/Header";
import { StreamList } from "./components/StreamList";
import { StreamDetail } from "./components/StreamDetail";
import { AddStreamDialog } from "./components/AddStreamDialog";
import { RetentionCard } from "./components/RetentionCard";
import { SegmentLengthCard } from "./components/SegmentLengthCard";
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

  return (
    <div className="h-full flex flex-col">
      <Header
        running={status?.running ?? false}
        busy={toggling}
        onToggle={toggleRunning}
      />

      {error && (
        <div className="bg-rose-500/10 border-b border-rose-500/30 text-rose-200 px-6 py-2 text-sm">
          {error}
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
