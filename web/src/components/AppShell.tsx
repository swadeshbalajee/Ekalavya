import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import {
  BarChart3,
  BookOpen,
  Compass,
  GraduationCap,
  Home,
  LogIn,
  LogOut,
  Menu as MenuIcon,
  MessageSquare,
  Moon,
  Network,
  Plus,
  Search,
  Settings,
  Sparkles,
  Sun,
  CalendarCheck,
  X,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { money } from "../lib/format";
import { useTheme } from "../lib/ui";
import { Avatar, Menu, Wordmark } from "./common";
import { SettingsModal } from "./SettingsModal";

const NAV = [
  { to: "/home", label: "Home", icon: Home, auth: true },
  { to: "/chat", label: "Chat", icon: MessageSquare, auth: true },
  { to: "/discover", label: "Discover", icon: Compass, auth: false },
  { to: "/library", label: "Library", icon: BookOpen, auth: true },
  { to: "/study", label: "Study plan", icon: CalendarCheck, auth: true },
  { to: "/quiz", label: "Quiz me", icon: GraduationCap, auth: true },
];

function NavItems({ onNavigate }: { onNavigate?: () => void }) {
  const { user } = useAuth();
  return (
    <nav className="space-y-1">
      {NAV.filter((item) => user || !item.auth).map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          onClick={onNavigate}
          className={({ isActive }) =>
            clsx(
              "flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition",
              isActive
                ? "bg-primary/15 text-ink ring-1 ring-primary/35"
                : "text-ink-2 hover:bg-raised hover:text-ink",
            )
          }
        >
          <Icon size={18} strokeWidth={1.8} />
          {label}
        </NavLink>
      ))}
    </nav>
  );
}

function Credit() {
  const { data } = useQuery({
    queryKey: ["credits"],
    queryFn: () => api<{ available: boolean; remaining_usd?: number }>("/v1/credits"),
    staleTime: 60_000,
  });
  if (!data?.available || data.remaining_usd === undefined) return null;
  return (
    <div className="mb-3 flex items-center justify-between rounded-xl border border-line bg-sunken px-3 py-2 text-xs">
      <span className="text-muted">Credit left</span>
      <span className="font-mono text-ink">{money(data.remaining_usd)}</span>
    </div>
  );
}

function SidebarBody({
  onNavigate,
  openSettings,
}: {
  onNavigate?: () => void;
  openSettings: () => void;
}) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  return (
    <div className="flex h-full flex-col">
      <Link to={user ? "/home" : "/"} className="mb-6 px-2" onClick={onNavigate}>
        <Wordmark />
      </Link>
      <NavItems onNavigate={onNavigate} />
      <div className="mt-auto pt-6">
        {user ? (
          <>
            <button
              className="btn-primary mb-4 w-full"
              onClick={() => {
                onNavigate?.();
                navigate("/chat");
              }}
            >
              <Plus size={18} /> Create
            </button>
            <Credit />
            <NavLink
              to="/usage"
              onClick={onNavigate}
              className={({ isActive }) =>
                clsx(
                  "mb-1 flex items-center gap-3 rounded-xl px-3 py-2 text-sm transition",
                  isActive ? "bg-raised text-ink" : "text-ink-2 hover:bg-raised hover:text-ink",
                )
              }
            >
              <BarChart3 size={18} strokeWidth={1.8} /> Usage
            </NavLink>
            <NavLink
              to="/knowledge"
              onClick={onNavigate}
              className={({ isActive }) =>
                clsx(
                  "mb-1 flex items-center gap-3 rounded-xl px-3 py-2 text-sm transition",
                  isActive ? "bg-raised text-ink" : "text-ink-2 hover:bg-raised hover:text-ink",
                )
              }
            >
              <Network size={18} strokeWidth={1.8} /> Knowledge map
            </NavLink>
            <button
              onClick={openSettings}
              className="mb-3 flex w-full items-center gap-3 rounded-xl px-3 py-2 text-sm text-ink-2 transition hover:bg-raised hover:text-ink"
            >
              <Settings size={18} strokeWidth={1.8} /> Settings
            </button>
            <div className="flex items-center gap-3 rounded-xl border border-line bg-surface px-3 py-2.5">
              <Avatar name={user.display_name} color={user.avatar_color} size={30} />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{user.display_name}</p>
                <p className="truncate text-xs text-muted">@{user.username}</p>
              </div>
              <button className="icon-btn" aria-label="Sign out" onClick={() => void logout()}>
                <LogOut size={16} />
              </button>
            </div>
          </>
        ) : (
          <div className="space-y-2">
            <Link to="/signup" className="btn-primary w-full" onClick={onNavigate}>
              <Sparkles size={16} /> Get started free
            </Link>
            <Link to="/login" className="btn-secondary w-full" onClick={onNavigate}>
              <LogIn size={16} /> Log in
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}

export function TopSearch({ className }: { className?: string }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [value, setValue] = useState(() => new URLSearchParams(location.search).get("q") ?? "");
  return (
    <form
      role="search"
      className={clsx("relative", className)}
      onSubmit={(event) => {
        event.preventDefault();
        navigate(`/discover${value.trim() ? `?q=${encodeURIComponent(value.trim())}` : ""}`);
      }}
    >
      <Search size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted" />
      <input
        className="input h-10 rounded-full pl-10"
        placeholder="Search videos, topics, keywords…"
        aria-label="Search videos"
        value={value}
        onChange={(event) => setValue(event.target.value)}
      />
    </form>
  );
}

export function ThemeButton() {
  const { theme, toggle } = useTheme();
  return (
    <button
      className="icon-btn"
      onClick={toggle}
      aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
    >
      {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
    </button>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [drawer, setDrawer] = useState(false);
  const [settings, setSettings] = useState(false);
  const chatRoute = location.pathname.startsWith("/chat");
  useEffect(() => setDrawer(false), [location.pathname]);

  return (
    <div className="flex min-h-[100dvh] bg-bg">
      <aside className="sticky top-0 hidden h-[100dvh] w-64 shrink-0 border-r border-line bg-surface/60 px-4 py-5 lg:block">
        <SidebarBody openSettings={() => setSettings(true)} />
      </aside>

      {drawer && (
        <div className="fixed inset-0 z-[60] lg:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setDrawer(false)} />
          <aside className="absolute inset-y-0 left-0 w-72 animate-fade-in border-r border-line bg-surface px-4 py-5">
            <button
              className="icon-btn absolute right-3 top-4"
              onClick={() => setDrawer(false)}
              aria-label="Close menu"
            >
              <X size={18} />
            </button>
            <SidebarBody
              onNavigate={() => setDrawer(false)}
              openSettings={() => {
                setDrawer(false);
                setSettings(true);
              }}
            />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        {!chatRoute && (
          <header className="sticky top-0 z-40 flex h-16 items-center gap-3 border-b border-line bg-bg/80 px-4 backdrop-blur-xl sm:px-6">
            <button
              className="icon-btn lg:hidden"
              onClick={() => setDrawer(true)}
              aria-label="Open menu"
            >
              <MenuIcon size={20} />
            </button>
            <Link to={user ? "/home" : "/"} className="lg:hidden">
              <Wordmark size={28} />
            </Link>
            <TopSearch className="ml-auto hidden w-full max-w-md md:block lg:ml-0" />
            <div className="ml-auto flex items-center gap-1">
              <button
                className="icon-btn md:hidden"
                aria-label="Search"
                onClick={() => navigate("/discover")}
              >
                <Search size={18} />
              </button>
              <ThemeButton />
              {user ? (
                <Menu
                  label="Account"
                  trigger={<Avatar name={user.display_name} color={user.avatar_color} size={30} />}
                  items={[
                    {
                      label: "Settings",
                      icon: <Settings size={16} />,
                      onSelect: () => setSettings(true),
                    },
                    {
                      label: "Usage",
                      icon: <BarChart3 size={16} />,
                      onSelect: () => navigate("/usage"),
                    },
                    {
                      label: "Sign out",
                      icon: <LogOut size={16} />,
                      onSelect: () => void logout(),
                    },
                  ]}
                />
              ) : (
                <Link to="/login" className="btn-primary btn-sm ml-1">
                  Log in
                </Link>
              )}
            </div>
          </header>
        )}
        <main className={clsx("min-w-0 flex-1", !chatRoute && "pb-24 lg:pb-10")}>{children}</main>
      </div>

      {!chatRoute && <MobileTabs />}
      {settings && <SettingsModal open={settings} onClose={() => setSettings(false)} />}
    </div>
  );
}

function MobileTabs() {
  const { user } = useAuth();
  const items = user
    ? [NAV[0], NAV[1], NAV[2], NAV[3], NAV[4]]
    : [{ to: "/", label: "Home", icon: Home, auth: false }, NAV[2]];
  return (
    <nav className="fixed inset-x-0 bottom-0 z-40 flex border-t border-line bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-xl lg:hidden">
      {items.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          end={to === "/"}
          className={({ isActive }) =>
            clsx(
              "flex flex-1 flex-col items-center gap-1 py-2.5 text-[11px] font-medium",
              isActive ? "text-primary" : "text-muted",
            )
          }
        >
          <Icon size={20} strokeWidth={1.8} />
          {label}
        </NavLink>
      ))}
    </nav>
  );
}
