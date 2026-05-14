import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";
import { AlertTriangle, CheckCircle2 } from "lucide-react";

type ToastKind = "success" | "error";
interface Toast {
  id: number;
  kind: ToastKind;
  text: string;
}

const Ctx = createContext<{
  push: (kind: ToastKind, text: string) => void;
} | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((kind: ToastKind, text: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, kind, text }]);
    setTimeout(() => {
      setToasts((t) => t.filter((x) => x.id !== id));
    }, 4000);
  }, []);
  const value = useMemo(() => ({ push }), [push]);
  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`card px-3.5 py-2.5 flex items-start gap-2.5 ${
              t.kind === "error"
                ? "border-rose-500/30"
                : "border-emerald-500/30"
            }`}
          >
            {t.kind === "error" ? (
              <AlertTriangle
                size={16}
                className="mt-0.5 text-rose-300 shrink-0"
              />
            ) : (
              <CheckCircle2
                size={16}
                className="mt-0.5 text-emerald-300 shrink-0"
              />
            )}
            <div className="text-sm leading-snug">{t.text}</div>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useToast() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useToast outside ToastProvider");
  return ctx.push;
}
