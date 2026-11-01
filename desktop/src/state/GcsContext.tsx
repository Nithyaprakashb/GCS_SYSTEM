import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api, wsUrl } from "../lib/api";
import type { GcsEvent, GcsState } from "../lib/types";

type Theme = "dark" | "light";

interface Toast {
  id: number;
  kind: "ok" | "warn" | "error" | "info";
  title: string;
  detail?: string;
}

interface GcsContextValue {
  state: GcsState | null;
  events: GcsEvent[];
  connected: boolean;
  loading: boolean;
  error: string | null;
  theme: Theme;
  setTheme: (theme: Theme) => void;
  refresh: () => Promise<void>;
  run: <T>(label: string, action: () => Promise<T>, success?: string) => Promise<T | null>;
  notify: (kind: Toast["kind"], title: string, detail?: string) => void;
  busy: string | null;
  toasts: Toast[];
  dismissToast: (id: number) => void;
}

const GcsContext = createContext<GcsContextValue | null>(null);

function initialTheme(): Theme {
  const stored = localStorage.getItem("rs-theme");
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function GcsProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<GcsState | null>(null);
  const [events, setEvents] = useState<GcsEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [theme, setThemeState] = useState<Theme>(initialTheme);
  const [busy, setBusy] = useState<string | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const refreshTimer = useRef<number | null>(null);
  const toastId = useRef(0);

  const notify = useCallback((kind: Toast["kind"], title: string, detail?: string) => {
    const id = ++toastId.current;
    setToasts((current) => [...current.slice(-3), { id, kind, title, detail }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((toast) => toast.id !== id));
    }, 6200);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const snapshot = await api.state();
      setState(snapshot);
      setError(null);
      setConnected(true);
    } catch (err) {
      setConnected(false);
      setError(err instanceof Error ? err.message : "GCS backend unreachable");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("rs-theme", theme);
  }, [theme]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1400);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: number | null = null;
    let stopped = false;

    const connect = () => {
      try {
        socket = new WebSocket(wsUrl());
      } catch {
        retry = window.setTimeout(connect, 2500);
        return;
      }
      socket.onopen = () => {
        setConnected(true);
        setError(null);
      };
      socket.onmessage = (message) => {
        try {
          const data = JSON.parse(message.data) as { kind: string; event?: GcsEvent; data?: GcsState };
          if (data.kind === "snapshot" && data.data) {
            setState(data.data);
            setEvents(data.data.events);
            setLoading(false);
          }
          if (data.kind === "event" && data.event) {
            setEvents((current) => [...current.slice(-399), data.event as GcsEvent]);
            if (refreshTimer.current) window.clearTimeout(refreshTimer.current);
            refreshTimer.current = window.setTimeout(() => void refresh(), 350);
          }
        } catch {
          /* Ignore malformed frames; the polling path remains authoritative. */
        }
      };
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        if (!stopped) retry = window.setTimeout(connect, 2500);
      };
    };
    connect();
    return () => {
      stopped = true;
      if (retry) window.clearTimeout(retry);
      if (refreshTimer.current) window.clearTimeout(refreshTimer.current);
      socket?.close();
    };
  }, [refresh]);

  const run = useCallback(
    async <T,>(label: string, action: () => Promise<T>, success?: string): Promise<T | null> => {
      setBusy(label);
      try {
        const result = await action();
        await refresh();
        if (success) notify("ok", success);
        return result;
      } catch (err) {
        notify("error", label, err instanceof Error ? err.message : "Action failed");
        return null;
      } finally {
        setBusy(null);
      }
    },
    [notify, refresh],
  );

  const setTheme = useCallback((next: Theme) => setThemeState(next), []);
  const dismissToast = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const value = useMemo(
    () => ({
      state,
      events,
      connected,
      loading,
      error,
      theme,
      setTheme,
      refresh,
      run,
      notify,
      busy,
      toasts,
      dismissToast,
    }),
    [state, events, connected, loading, error, theme, setTheme, refresh, run, notify, busy, toasts, dismissToast],
  );

  return <GcsContext.Provider value={value}>{children}</GcsContext.Provider>;
}

export function useGcs(): GcsContextValue {
  const context = useContext(GcsContext);
  if (!context) throw new Error("useGcs must be used inside GcsProvider");
  return context;
}
