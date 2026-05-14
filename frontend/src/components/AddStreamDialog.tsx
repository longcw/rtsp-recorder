import { useState } from "react";
import { Modal } from "./Modal";

const NAME_RE = /^[A-Za-z0-9_-]{1,64}$/;

export function AddStreamDialog({
  open,
  onClose,
  onSubmit,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (data: {
    name: string;
    url: string;
    enabled: boolean;
  }) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const validName = NAME_RE.test(name);
  const valid = validName && url.trim().length > 0;

  function reset() {
    setName("");
    setUrl("");
    setEnabled(true);
    setError(null);
    setSubmitting(false);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit({ name: name.trim(), url: url.trim(), enabled });
      reset();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => {
        if (!submitting) {
          reset();
          onClose();
        }
      }}
      title="Add stream"
      footer={
        <>
          <button
            type="button"
            className="btn-ghost"
            disabled={submitting}
            onClick={() => {
              reset();
              onClose();
            }}
          >
            Cancel
          </button>
          <button
            type="submit"
            form="add-stream-form"
            className="btn-primary"
            disabled={!valid || submitting}
          >
            {submitting ? "Adding…" : "Add stream"}
          </button>
        </>
      }
    >
      <form id="add-stream-form" onSubmit={submit} className="space-y-4">
        <div>
          <label className="label" htmlFor="stream-name">
            Name
          </label>
          <input
            id="stream-name"
            className="input"
            placeholder="front-door"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
          <p className="text-xs text-ink-400 mt-1.5">
            Used as the folder name. Letters, digits, underscore, hyphen.
          </p>
          {name && !validName && (
            <p className="text-xs text-rose-300 mt-1">Invalid name.</p>
          )}
        </div>
        <div>
          <label className="label" htmlFor="stream-url">
            RTSP URL
          </label>
          <input
            id="stream-url"
            className="input font-mono"
            placeholder="rtsp://user:pass@host:554/stream"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </div>
        <label className="flex items-center gap-2.5 text-sm text-ink-200 cursor-pointer">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="h-4 w-4 rounded border-white/20 bg-ink-900 accent-accent"
          />
          Start recording immediately
        </label>
        {error && (
          <div className="text-sm text-rose-300 bg-rose-500/10 border border-rose-500/20 rounded-lg px-3 py-2">
            {error}
          </div>
        )}
      </form>
    </Modal>
  );
}
