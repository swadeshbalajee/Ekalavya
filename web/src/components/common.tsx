import clsx from "clsx";
import { Loader2, X } from "lucide-react";
import {
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
  type ButtonHTMLAttributes,
} from "react";
import { createPortal } from "react-dom";

/* ------------------------------------------------------------------ brand */

/** The ழ mark, in the violet-to-teal brand gradient. */
export function LogoMark({ size = 36, className }: { size?: number; className?: string }) {
  const id = useId().replace(/:/g, "");
  return (
    <svg
      viewBox="0 0 128 128"
      width={size}
      height={size}
      className={className}
      role="img"
      aria-label="Ekalavya"
    >
      <defs>
        <linearGradient id={`g${id}`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#A48BFF" />
          <stop offset="0.55" stopColor="#7C5CFF" />
          <stop offset="1" stopColor="#22D3EE" />
        </linearGradient>
      </defs>
      <rect width="128" height="128" rx="32" fill="rgb(var(--c-raised))" />
      <rect
        x="1.5"
        y="1.5"
        width="125"
        height="125"
        rx="30.5"
        fill="none"
        stroke={`url(#g${id})`}
        strokeOpacity="0.45"
        strokeWidth="3"
      />
      <text
        x="64"
        y="86"
        textAnchor="middle"
        fontFamily="'Noto Sans Tamil', 'Nirmala UI', sans-serif"
        fontSize="80"
        fontWeight="700"
        fill={`url(#g${id})`}
      >
        ழ
      </text>
    </svg>
  );
}

export function Wordmark({ size = 32 }: { size?: number }) {
  return (
    <span className="flex items-center gap-2.5">
      <LogoMark size={size} />
      <span className="font-display text-lg font-semibold tracking-tight">Ekalavya</span>
    </span>
  );
}

/* ------------------------------------------------------------------ basics */

export function Spinner({ size = 18, className }: { size?: number; className?: string }) {
  return <Loader2 size={size} className={clsx("animate-spin", className)} aria-hidden />;
}

export function Avatar({
  name,
  color,
  size = 32,
}: {
  name: string;
  color?: string;
  size?: number;
}) {
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center rounded-full font-semibold text-white"
      style={{
        width: size,
        height: size,
        fontSize: size * 0.42,
        background: color || "linear-gradient(135deg, #7C5CFF, #22D3EE)",
      }}
      aria-hidden
    >
      {(name || "?").trim().charAt(0).toUpperCase()}
    </span>
  );
}

export function Empty({
  icon,
  title,
  children,
  action,
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-line px-6 py-12 text-center">
      {icon && (
        <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
          {icon}
        </div>
      )}
      <h3 className="font-display text-base font-semibold">{title}</h3>
      {children && <p className="mt-1 max-w-sm text-sm text-ink-2">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function SectionHeader({
  title,
  action,
  sub,
}: {
  title: string;
  action?: ReactNode;
  sub?: string;
}) {
  return (
    <div className="mb-4 flex items-end justify-between gap-4">
      <div>
        <h2 className="font-display text-lg font-semibold">{title}</h2>
        {sub && <p className="mt-0.5 text-sm text-ink-2">{sub}</p>}
      </div>
      {action}
    </div>
  );
}

export function Tabs<T extends string>({
  value,
  onChange,
  items,
  className,
}: {
  value: T;
  onChange: (value: T) => void;
  items: { value: T; label: string; count?: number }[];
  className?: string;
}) {
  return (
    <div
      role="tablist"
      className={clsx(
        "flex gap-1 overflow-x-auto rounded-xl border border-line bg-surface p-1",
        className,
      )}
    >
      {items.map((item) => (
        <button
          key={item.value}
          role="tab"
          aria-selected={value === item.value}
          onClick={() => onChange(item.value)}
          className={clsx(
            "flex shrink-0 items-center gap-1.5 rounded-lg px-3.5 py-1.5 text-sm font-medium transition",
            value === item.value
              ? "bg-primary/15 text-ink ring-1 ring-primary/40"
              : "text-ink-2 hover:text-ink",
          )}
        >
          {item.label}
          {item.count !== undefined && (
            <span className="rounded-md bg-raised px-1.5 font-mono text-[10px] text-muted">
              {item.count}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
}) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={clsx(
        "relative h-6 w-11 shrink-0 rounded-full transition",
        checked ? "bg-primary" : "bg-raised ring-1 ring-line",
      )}
    >
      <span
        className={clsx(
          "absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition",
          checked ? "left-[22px]" : "left-0.5",
        )}
      />
    </button>
  );
}

export function ProgressRing({
  value,
  size = 132,
  stroke = 12,
  children,
}: {
  value: number;
  size?: number;
  stroke?: number;
  children?: ReactNode;
}) {
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const id = useId().replace(/:/g, "");
  return (
    <div className="relative inline-flex" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <defs>
          <linearGradient id={`r${id}`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="rgb(var(--c-primary))" />
            <stop offset="1" stopColor="rgb(var(--c-teal))" />
          </linearGradient>
        </defs>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="rgb(var(--c-raised))"
          strokeWidth={stroke}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={`url(#r${id})`}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - Math.min(Math.max(value, 0), 1))}
          style={{ transition: "stroke-dashoffset .6s ease" }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">{children}</div>
    </div>
  );
}

export function Bar({ value, className }: { value: number; className?: string }) {
  return (
    <div className={clsx("h-1.5 overflow-hidden rounded-full bg-raised", className)}>
      <div
        className="h-full rounded-full bg-gradient-to-r from-primary to-teal transition-all"
        style={{ width: `${Math.round(Math.min(Math.max(value, 0), 1) * 100)}%` }}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ overlays */

export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  wide,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-[70] flex items-end justify-center sm:items-center sm:p-4">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={clsx(
          "relative flex max-h-[92vh] w-full animate-fade-in flex-col rounded-t-3xl border border-line bg-surface shadow-card sm:rounded-3xl",
          wide ? "sm:max-w-3xl" : "sm:max-w-lg",
        )}
      >
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <h2 className="font-display text-base font-semibold">{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="overflow-y-auto px-5 py-5">{children}</div>
        {footer && (
          <div className="flex justify-end gap-2 border-t border-line px-5 py-4">{footer}</div>
        )}
      </div>
    </div>,
    document.body,
  );
}

export type MenuItem = {
  label: string;
  icon?: ReactNode;
  onSelect: () => void;
  danger?: boolean;
  hidden?: boolean;
};

export function Menu({
  trigger,
  items,
  align = "right",
  label = "More actions",
}: {
  trigger: ReactNode;
  items: MenuItem[];
  align?: "left" | "right";
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div className="relative" ref={root}>
      <button
        className="icon-btn"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          setOpen((value) => !value);
        }}
      >
        {trigger}
      </button>
      {open && (
        <div
          role="menu"
          className={clsx(
            "absolute z-50 mt-1 min-w-[190px] animate-fade-in rounded-xl border border-line bg-surface p-1 shadow-card",
            align === "right" ? "right-0" : "left-0",
          )}
        >
          {items
            .filter((item) => !item.hidden)
            .map((item) => (
              <button
                key={item.label}
                role="menuitem"
                onClick={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  setOpen(false);
                  item.onSelect();
                }}
                className={clsx(
                  "flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm transition hover:bg-raised",
                  item.danger ? "text-danger" : "text-ink",
                )}
              >
                <span className="text-ink-2">{item.icon}</span>
                {item.label}
              </button>
            ))}
        </div>
      )}
    </div>
  );
}

export function Button({
  busy,
  children,
  className = "btn-primary",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { busy?: boolean }) {
  return (
    <button className={className} disabled={busy || props.disabled} {...props}>
      {busy && <Spinner size={16} />}
      {children}
    </button>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
    </label>
  );
}
