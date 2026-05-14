import { Plus } from "lucide-react";
import type { StreamStatus } from "../types";
import { StatusDot, StateLabel } from "./StatusDot";

interface Props {
  streams: StreamStatus[];
  selected: string | null;
  onSelect: (name: string) => void;
  onAdd: () => void;
}

export function StreamList({ streams, selected, onSelect, onAdd }: Props) {
  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-5 py-4">
        <div>
          <div className="text-[11px] font-medium uppercase tracking-wider text-ink-400">
            Streams
          </div>
          <div className="text-sm text-ink-200">
            {streams.length} {streams.length === 1 ? "source" : "sources"}
          </div>
        </div>
        <button className="btn-ghost h-8 px-2.5" onClick={onAdd}>
          <Plus size={15} />
          Add
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-3 space-y-1">
        {streams.length === 0 && (
          <div className="px-3 py-12 text-center">
            <p className="text-sm text-ink-400">No streams yet.</p>
            <button
              className="btn-ghost mt-3 h-8 px-3 text-xs"
              onClick={onAdd}
            >
              <Plus size={14} />
              Add your first stream
            </button>
          </div>
        )}
        {streams.map((s) => {
          const active = selected === s.name;
          return (
            <button
              key={s.name}
              onClick={() => onSelect(s.name)}
              className={`w-full text-left px-3 py-2.5 rounded-lg transition-colors border ${
                active
                  ? "bg-accent/10 border-accent/30"
                  : "border-transparent hover:bg-white/[0.04]"
              }`}
            >
              <div className="flex items-center gap-2.5">
                <StatusDot state={s.state} />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium text-ink-50 truncate">
                    {s.name}
                  </div>
                  <div className="flex items-center gap-2 mt-0.5">
                    <StateLabel state={s.state} />
                    {!s.enabled && (
                      <span className="text-[10px] uppercase tracking-wider text-ink-400 bg-white/[0.04] rounded px-1.5 py-0.5">
                        Disabled
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
