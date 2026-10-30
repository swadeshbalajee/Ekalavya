import { CheckCircle2, Info, X, XCircle } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

/* ------------------------------------------------------------------ theme */

type Theme = "dark" | "light";
/** Accent colours, independent of light/dark: crimson (black and red) or the original violet. */
export type Palette = "crimson" | "violet";
export const PALETTES: { value: Palette; label: string; swatch: string[] }[] = [
  { value: "crimson", label: "Crimson", swatch: ["#070708", "#E5252E", "#FF6E5C"] },
  { value: "violet", label: "Violet", swatch: ["#0A0B10", "#7C5CFF", "#22D3EE"] },
];
const ThemeContext = createContext<{
  theme: Theme;
  toggle: () => void;
  setTheme: (theme: Theme) => void;
  palette: Palette;
  setPalette: (palette: Palette) => void;
}>({
  theme: "dark",
  toggle: () => {},
  setTheme: () => {},
  palette: "crimson",
  setPalette: () => {},
});

function remember(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* not remembered */
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(
    () => (document.documentElement.dataset.theme as Theme) || "dark",
  );
  const [palette, setPalette] = useState<Palette>(
    () => (document.documentElement.dataset.palette as Palette) || "crimson",
  );
  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = theme;
    root.dataset.palette = palette;
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute("content", `rgb(${getComputedStyle(root).getPropertyValue("--c-bg").trim()})`);
    remember("ekalavya-theme", theme);
    remember("ekalavya-palette", palette);
  }, [theme, palette]);
  const toggle = useCallback(() => setTheme((value) => (value === "dark" ? "light" : "dark")), []);
  return (
    <ThemeContext.Provider value={{ theme, toggle, setTheme, palette, setPalette }}>
      {children}
    </ThemeContext.Provider>
  );
}

export const useTheme = () => useContext(ThemeContext);

/* ------------------------------------------------------------------ toasts */

type Toast = { id: number; tone: "success" | "error" | "info"; text: string };
const ToastContext = createContext<(text: string, tone?: Toast["tone"]) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: Toast["tone"] = "success") => {
    const id = Date.now() + Math.random();
    setToasts((items) => [...items, { id, tone, text }]);
    setTimeout(() => setToasts((items) => items.filter((item) => item.id !== id)), 4200);
  }, []);
  const icon = { success: CheckCircle2, error: XCircle, info: Info };
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div
        className="pointer-events-none fixed inset-x-0 bottom-20 z-[80] flex flex-col items-center gap-2 px-4 md:bottom-6"
        role="status"
        aria-live="polite"
      >
        {toasts.map((toast) => {
          const Icon = icon[toast.tone];
          return (
            <div
              key={toast.id}
              className="pointer-events-auto flex max-w-md animate-fade-in items-center gap-3 rounded-xl border border-line bg-surface px-4 py-3 text-sm shadow-card"
            >
              <Icon
                size={18}
                className={
                  toast.tone === "error"
                    ? "text-danger"
                    : toast.tone === "info"
                      ? "text-teal"
                      : "text-success"
                }
              />
              <span className="text-ink">{toast.text}</span>
              <button
                className="ml-1 text-muted hover:text-ink"
                aria-label="Dismiss"
                onClick={() => setToasts((items) => items.filter((item) => item.id !== toast.id))}
              >
                <X size={14} />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);

/* ------------------------------------------------------------------ misc hooks */

export function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const field = document.createElement("textarea");
    field.value = text;
    field.style.position = "fixed";
    field.style.opacity = "0";
    document.body.appendChild(field);
    field.select();
    const copied = document.execCommand("copy");
    field.remove();
    return copied;
  }
}
