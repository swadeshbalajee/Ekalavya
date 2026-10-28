import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  ArrowUp,
  CalendarCheck,
  Clapperboard,
  Compass,
  FileText,
  GraduationCap,
  Lightbulb,
  MessageSquare,
} from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { SectionHeader } from "../components/common";
import { TaskRow } from "../components/TaskRow";
import { GridSkeleton, VideoRow } from "../components/VideoCard";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ago } from "../lib/format";
import type { Home } from "../lib/types";

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 5) return "Burning the midnight oil";
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

const ACTIONS = [
  {
    label: "Explain a topic",
    icon: Lightbulb,
    tone: "text-teal bg-teal/10 ring-teal/30",
    prefix: "Explain ",
  },
  {
    label: "Generate a video",
    icon: Clapperboard,
    tone: "text-primary bg-primary/10 ring-primary/30",
    prefix: "Make me a video explaining ",
  },
  {
    label: "Summarize a paper",
    icon: FileText,
    tone: "text-success bg-success/10 ring-success/30",
    prefix: "Explain this paper as a video lecture",
    attach: true,
  },
  {
    label: "Quiz me",
    icon: GraduationCap,
    tone: "text-danger bg-danger/10 ring-danger/30",
    to: "/quiz",
  },
  {
    label: "Explore videos",
    icon: Compass,
    tone: "text-amber bg-amber/10 ring-amber/30",
    to: "/discover",
  },
];

export default function HomePage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [prompt, setPrompt] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["home"],
    queryFn: () => api<Home>("/v1/home"),
  });

  const start = (text: string, attach = false) => {
    const params = new URLSearchParams();
    if (text.trim()) params.set("prompt", text.trim());
    if (attach) params.set("attach", "1");
    navigate(`/chat?${params}`);
  };

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:py-10">
      <section className="text-center">
        <h1 className="font-display text-3xl font-semibold sm:text-4xl">
          {greeting()}, {user?.display_name.split(" ")[0]} <span aria-hidden>👋</span>
        </h1>
        <p className="mt-2 text-ink-2">What would you like to learn today?</p>
        <form
          className="mx-auto mt-6 flex max-w-2xl items-center gap-2 rounded-2xl border border-line bg-surface p-2 pl-4 shadow-card focus-within:border-primary/60"
          onSubmit={(event) => {
            event.preventDefault();
            if (prompt.trim()) start(prompt, false);
          }}
        >
          <MessageSquare size={18} className="shrink-0 text-muted" />
          <input
            className="min-w-0 flex-1 bg-transparent py-2 text-sm outline-none placeholder:text-muted"
            placeholder="Ask anything, create a video, or explore a topic…"
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            aria-label="Ask Ekalavya"
          />
          <button className="btn-primary h-10 w-10 shrink-0 p-0" aria-label="Start" disabled={!prompt.trim()}>
            <ArrowUp size={18} />
          </button>
        </form>
        <div className="mx-auto mt-5 grid max-w-3xl grid-cols-2 gap-3 sm:grid-cols-5">
          {ACTIONS.map(({ label, icon: Icon, tone, prefix, to, attach }) => (
            <button
              key={label}
              onClick={() => (to ? navigate(to) : prefix && start(prompt || prefix, attach))}
              className="card flex flex-col items-center gap-2 px-3 py-4 text-xs font-medium text-ink-2 transition hover:-translate-y-0.5 hover:border-primary/40 hover:text-ink"
            >
              <span className={`flex h-10 w-10 items-center justify-center rounded-xl ring-1 ${tone}`}>
                <Icon size={18} />
              </span>
              {label}
            </button>
          ))}
        </div>
      </section>

      {!!data?.today.length && (
        <section className="mt-12">
          <SectionHeader
            title="Today's study tasks"
            action={
              <Link to="/study" className="btn-ghost btn-sm">
                Study plan <ArrowRight size={14} />
              </Link>
            }
          />
          <div className="card divide-y divide-line">
            {data.today.map((task) => (
              <TaskRow key={task.id} task={task} />
            ))}
          </div>
        </section>
      )}

      <section className="mt-12">
        <SectionHeader
          title="Continue learning"
          action={
            <Link to="/library" className="btn-ghost btn-sm">
              Library <ArrowRight size={14} />
            </Link>
          }
        />
        {isLoading ? (
          <GridSkeleton count={4} />
        ) : data?.continue.length ? (
          <VideoRow videos={data.continue} />
        ) : (
          <div className="card flex flex-col items-start gap-3 p-6 sm:flex-row sm:items-center">
            <p className="flex-1 text-sm text-ink-2">
              Videos you start watching appear here so you can pick up where you left off.
            </p>
            <Link to="/discover" className="btn-secondary btn-sm">
              Find a video
            </Link>
          </div>
        )}
      </section>

      <section className="mt-12">
        <SectionHeader
          title="Recommended for you"
          sub="Public explainers close to what you've been studying"
          action={
            <Link to="/discover" className="btn-ghost btn-sm">
              See all <ArrowRight size={14} />
            </Link>
          }
        />
        {isLoading ? (
          <GridSkeleton count={4} />
        ) : data?.recommended.length ? (
          <VideoRow videos={data.recommended} />
        ) : (
          <p className="text-sm text-ink-2">No public videos yet — publish one of yours to start the catalog.</p>
        )}
      </section>

      {!!data?.recent_chats.length && (
        <section className="mt-12">
          <SectionHeader title="Recent chats" />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {data.recent_chats.map((chat) => (
              <Link
                key={chat.id}
                to={`/chat/${chat.id}`}
                className="card flex items-center gap-3 p-4 transition hover:border-primary/40"
              >
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-raised text-ink-2">
                  {chat.has_video ? <Clapperboard size={16} /> : <MessageSquare size={16} />}
                </span>
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{chat.title}</p>
                  <p className="text-xs text-muted">{ago(chat.updated_at)}</p>
                </div>
              </Link>
            ))}
          </div>
        </section>
      )}

      {!data?.today.length && !isLoading && (
        <section className="mt-12">
          <Link
            to="/study"
            className="card flex items-center gap-4 p-5 transition hover:border-primary/40"
          >
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary/12 text-primary ring-1 ring-primary/30">
              <CalendarCheck size={20} />
            </span>
            <div className="flex-1">
              <p className="font-display font-semibold">Make a study plan</p>
              <p className="text-sm text-ink-2">
                Tell Ekalavya your goal and deadline; it schedules videos, practice and review.
              </p>
            </div>
            <ArrowRight size={18} className="text-muted" />
          </Link>
        </section>
      )}
    </div>
  );
}
