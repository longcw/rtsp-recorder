import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Film, Settings, Video, X } from "lucide-react";
import type { SavedClip, ServiceStatus } from "./types";
import { api } from "./api";
import { Header } from "./components/Header";
import { StreamList } from "./components/StreamList";
import { StreamDetail } from "./components/StreamDetail";
import { SavedClips } from "./components/SavedClips";
import { AddStreamDialog } from "./components/AddStreamDialog";
import { IdleRetentionCard } from "./components/IdleRetentionCard";
import { MotionThresholdCard } from "./components/MotionThresholdCard";
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
  const [drawerOpen, setDrawerOpen] = useState(false);
  // what the main pane shows; "stream" means the selected stream
  const [view, setView] = useState<"stream" | "clips" | "settings">("stream");
  const [openRequest, setOpenRequest] = useState<{
    file: string;
    start: number;
    end: number;
  } | null>(null);
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

  // Close the mobile drawer on Escape, mirroring the modal dismissals.
  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDrawerOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  const selectStream = useCallback((name: string) => {
    setSelected(name);
    setView("stream");
    // On mobile the list lives in an overlay drawer; picking a stream should
    // dismiss it so the detail is visible. No-op on desktop where it's static.
    setDrawerOpen(false);
  }, []);

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
        onMenu={() => setDrawerOpen(true)}
      />

      {error && (
        <div className="bg-rose-500/10 border-b border-rose-500/30 text-rose-200 px-6 py-2 text-sm flex items-center gap-2">
          <AlertTriangle size={14} />
          <span>Lost connection to backend: {error}</span>
        </div>
      )}

      <main className="flex-1 min-h-0 md:grid md:grid-cols-[280px_1fr] xl:grid-cols-[320px_1fr]">
        {/* Mobile-only scrim behind the drawer. */}
        {drawerOpen && (
          <div
            className="fixed inset-0 z-30 bg-ink-950/70 backdrop-blur-sm md:hidden"
            onClick={() => setDrawerOpen(false)}
            aria-hidden
          />
        )}
        <aside
          className={`border-white/[0.06] flex flex-col min-h-0 bg-ink-900
            fixed inset-y-0 left-0 z-40 w-[85%] max-w-xs border-r shadow-2xl
            transform transition-transform duration-200 ease-out
            ${drawerOpen ? "translate-x-0" : "-translate-x-full"}
            md:static md:z-auto md:w-auto md:max-w-none md:translate-x-0
            md:shadow-none md:bg-transparent md:transition-none`}
        >
          <div className="flex items-center justify-end px-2 h-12 border-b border-white/[0.06] md:hidden">
            <button
              onClick={() => setDrawerOpen(false)}
              className="inline-flex items-center justify-center h-10 w-10 rounded-lg text-ink-300 hover:text-ink-100 hover:bg-white/[0.06]"
              aria-label="Close menu"
            >
              <X size={18} />
            </button>
          </div>
          <nav className="px-2 pt-2 space-y-1">
            {(
              [
                ["clips", "Saved clips", Film],
                ["settings", "Settings", Settings],
              ] as const
            ).map(([key, label, Icon]) => (
              <button
                key={key}
                onClick={() => {
                  setView(key);
                  setDrawerOpen(false);
                }}
                className={`w-full flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-sm font-medium border transition-colors ${
                  view === key
                    ? "bg-accent/10 border-accent/30 text-ink-50"
                    : "border-transparent text-ink-200 hover:bg-white/[0.04]"
                }`}
              >
                <Icon size={15} className="text-accent" />
                {label}
              </button>
            ))}
          </nav>
          <div className="flex-1 min-h-0">
            <StreamList
              streams={status?.streams ?? []}
              selected={view === "stream" ? selected : null}
              onSelect={selectStream}
              onAdd={() => {
                setDrawerOpen(false);
                setAddOpen(true);
              }}
            />
          </div>
        </aside>
        <section className="min-h-0 overflow-hidden">
          {view === "settings" && status ? (
            <div className="h-full overflow-y-auto">
              <div className="px-4 sm:px-6 py-5 border-b border-white/[0.06]">
                <div className="flex items-center gap-2.5">
                  <Settings size={18} className="text-accent" />
                  <h1 className="text-lg font-semibold tracking-tight">Settings</h1>
                </div>
                <p className="text-sm text-ink-400 mt-1">
                  Apply to every stream.
                </p>
              </div>
              <div className="px-4 sm:px-6 py-5">
                {/* the cards draw their own top rule, which the first one doesn't need */}
                <div className="card max-w-2xl overflow-hidden [&>div:first-child]:border-t-0">
                  <SegmentLengthCard
                    segmentSeconds={status.segment_seconds}
                    onUpdate={refresh}
                  />
                  <TimezoneCard timezone={status.timezone} onUpdate={refresh} />
                  <RetentionCard
                    retentionDays={status.retention_days}
                    onUpdate={refresh}
                  />
                  <IdleRetentionCard
                    idleRetentionDays={status.idle_retention_days}
                    retentionDays={status.retention_days}
                    onUpdate={refresh}
                  />
                  <MotionThresholdCard
                    motionThreshold={status.motion_threshold}
                    onUpdate={refresh}
                  />
                </div>
              </div>
            </div>
          ) : view === "clips" ? (
            <SavedClips
              onOpenSource={(clip: SavedClip) => {
                selectStream(clip.stream);
                setOpenRequest({ file: clip.source, start: clip.start, end: clip.end });
              }}
            />
          ) : selectedStream ? (
            <StreamDetail
              key={selectedStream.name}
              stream={selectedStream}
              openRequest={openRequest}
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
