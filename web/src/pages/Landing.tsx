import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  AudioLines,
  CalendarCheck,
  Check,
  Clapperboard,
  FileText,
  GraduationCap,
  Languages,
  MessageSquareText,
  Play,
  Sparkles,
} from "lucide-react";
import { Link } from "react-router-dom";
import { ThemeButton } from "../components/AppShell";
import { Wordmark } from "../components/common";
import { GridSkeleton, VideoGrid } from "../components/VideoCard";
import { api } from "../lib/api";
import type { VideoCard } from "../lib/types";

const FEATURES = [
  {
    icon: MessageSquareText,
    title: "Ask like you'd ask a teacher",
    text: "Chat about any topic. Ekalavya explains step by step and remembers the conversation.",
  },
  {
    icon: Clapperboard,
    title: "Explanations that move",
    text: "Turn a question, topic or research paper into a narrated, animated explainer video.",
  },
  {
    icon: Languages,
    title: "In your language",
    text: "Narration and on-screen text in Indian and world languages, with STEM terms kept clear.",
  },
  {
    icon: CalendarCheck,
    title: "A plan that adapts",
    text: "Give a goal and a date. A study plan schedules videos, practice and review for you.",
  },
  {
    icon: GraduationCap,
    title: "Quiz me",
    text: "Practice questions written from each video, with feedback that sends you to the right scene.",
  },
  {
    icon: FileText,
    title: "Papers, made watchable",
    text: "Attach a PDF or arXiv link and get a lecture built from its equations, figures and results.",
  },
];

const STEPS = [
  ["Ask or upload", "Type a topic, ask a question, or attach a paper."],
  ["Watch it explained", "Ekalavya plans the scenes, writes the narration and animates them."],
  ["Practise and plan", "Quiz yourself, take notes, and follow a study plan to your goal."],
];

function HeroVisual() {
  return (
    <div className="relative mx-auto w-full max-w-xl">
      <div className="absolute -inset-6 rounded-[40px] bg-gradient-to-tr from-primary/30 via-transparent to-teal/20 blur-2xl" />
      <div className="relative rounded-3xl border border-line bg-surface/90 p-4 shadow-card backdrop-blur">
        <div className="flex items-center gap-2 border-b border-line pb-3 text-xs text-muted">
          <span className="h-2.5 w-2.5 rounded-full bg-danger/70" />
          <span className="h-2.5 w-2.5 rounded-full bg-warning/70" />
          <span className="h-2.5 w-2.5 rounded-full bg-success/70" />
          <span className="ml-2 font-mono">AI workspace</span>
        </div>
        <div className="mt-4 flex gap-3">
          <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/20 text-primary">
            <Sparkles size={14} />
          </span>
          <div className="min-w-0 flex-1 text-sm">
            <p className="text-ink-2">
              I&apos;ve planned a 6-scene lesson on <b className="text-ink">how wavelength sets colour</b>.
            </p>
            <ul className="mt-3 space-y-1.5 text-xs text-ink-2">
              {["Visual equations & diagrams", "Narration in Tamil · text in English", "Scene checks for layout and timing"].map(
                (item) => (
                  <li key={item} className="flex items-center gap-2">
                    <Check size={13} className="text-success" /> {item}
                  </li>
                ),
              )}
            </ul>
          </div>
        </div>
        <div className="relative mt-4 aspect-video overflow-hidden rounded-2xl border border-line bg-[#0b0d16]">
          <svg viewBox="0 0 400 225" className="absolute inset-0 h-full w-full">
            <defs>
              <linearGradient id="wave" x1="0" x2="1">
                <stop offset="0" stopColor="#7C5CFF" />
                <stop offset="1" stopColor="#22D3EE" />
              </linearGradient>
            </defs>
            <path
              d="M20 120 C 60 40, 100 40, 140 120 S 220 200, 260 120 S 340 40, 380 120"
              fill="none"
              stroke="url(#wave)"
              strokeWidth="4"
              strokeLinecap="round"
            />
            <line x1="80" y1="58" x2="320" y2="58" stroke="#F6B26B" strokeDasharray="5 5" strokeWidth="2" />
            <text x="200" y="48" fill="#F6B26B" fontSize="14" textAnchor="middle" fontFamily="JetBrains Mono">
              λ = 2 crests apart
            </text>
            <text x="200" y="208" fill="#A1A8B7" fontSize="12" textAnchor="middle" fontFamily="Inter">
              அலைநீளம் · wavelength
            </text>
          </svg>
          <span className="absolute left-1/2 top-1/2 flex h-12 w-12 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-white/15 text-white backdrop-blur">
            <Play size={20} fill="currentColor" className="ml-0.5" />
          </span>
        </div>
        <div className="mt-3 flex h-1.5 gap-1">
          {[3, 4, 2, 5, 3, 3].map((grow, index) => (
            <span
              key={index}
              className={index < 2 ? "rounded-full bg-gradient-to-r from-primary to-teal" : "rounded-full bg-raised"}
              style={{ flexGrow: grow }}
            />
          ))}
        </div>
      </div>
      <div className="absolute -bottom-6 -left-4 hidden rounded-2xl border border-line bg-surface/95 px-4 py-3 text-xs shadow-card backdrop-blur sm:block">
        <p className="flex items-center gap-2 text-ink">
          <AudioLines size={14} className="text-teal" /> Narrating scene 3 of 6
        </p>
        <div className="mt-2 h-1.5 w-40 overflow-hidden rounded-full bg-raised">
          <div className="h-full w-2/3 rounded-full bg-gradient-to-r from-primary to-teal" />
        </div>
      </div>
    </div>
  );
}

export function Landing() {
  const { data, isLoading } = useQuery({
    queryKey: ["videos", "landing"],
    queryFn: () => api<{ items: VideoCard[] }>("/v1/videos", { params: { limit: 8 } }),
  });
  const videos = data?.items ?? [];
  return (
    <div className="min-h-[100dvh] bg-bg">
      <header className="sticky top-0 z-40 border-b border-line/60 bg-bg/75 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-7xl items-center gap-6 px-4 sm:px-6">
          <Link to="/">
            <Wordmark />
          </Link>
          <nav className="hidden items-center gap-6 text-sm text-ink-2 md:flex">
            <a href="#features" className="hover:text-ink">Features</a>
            <a href="#how" className="hover:text-ink">How it works</a>
            <Link to="/discover" className="hover:text-ink">Explore videos</Link>
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <ThemeButton />
            <Link to="/login" className="btn-ghost btn-sm hidden sm:inline-flex">
              Log in
            </Link>
            <Link to="/signup" className="btn-primary btn-sm">
              Sign up
            </Link>
          </div>
        </div>
      </header>

      <section className="relative overflow-hidden">
        <div className="pointer-events-none absolute inset-0" style={{ background: "var(--hero-glow)" }} />
        <div className="relative mx-auto grid max-w-7xl items-center gap-14 px-4 pb-20 pt-14 sm:px-6 lg:grid-cols-2 lg:pt-24">
          <div>
            <span className="inline-flex items-center gap-2 rounded-full border border-primary/40 bg-primary/10 px-3 py-1 text-xs font-medium text-ink">
              <Sparkles size={13} className="text-primary" /> AI video tutor
            </span>
            <h1 className="mt-5 font-display text-4xl font-bold leading-[1.05] sm:text-5xl lg:text-6xl">
              Make complex ideas <span className="gradient-text">move.</span>
            </h1>
            <p className="mt-5 max-w-xl text-lg text-ink-2">
              Ekalavya turns a topic, a question or a research paper into a narrated, animated
              explainer — in your language. Chat, watch, practise and plan, all in one place.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link to="/signup" className="btn-primary px-6 py-3 text-base">
                Get started free <ArrowRight size={18} />
              </Link>
              <a href="#videos" className="btn-secondary px-6 py-3 text-base">
                <Play size={16} /> Watch examples
              </a>
            </div>
            <div className="mt-10 grid max-w-lg grid-cols-3 gap-4 text-sm">
              {[
                ["Chat", "explain anything"],
                ["Watch", "animated lessons"],
                ["Practise", "quizzes & plans"],
              ].map(([title, text]) => (
                <div key={title}>
                  <p className="font-display text-base font-semibold">{title}</p>
                  <p className="text-ink-2">{text}</p>
                </div>
              ))}
            </div>
          </div>
          <HeroVisual />
        </div>
      </section>

      <section id="features" className="mx-auto max-w-7xl px-4 py-16 sm:px-6">
        <p className="eyebrow">What you get</p>
        <h2 className="mt-2 font-display text-3xl font-semibold">Learn in motion</h2>
        <div className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map(({ icon: Icon, title, text }) => (
            <div key={title} className="card p-6 transition hover:border-primary/40">
              <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary/12 text-primary ring-1 ring-primary/30">
                <Icon size={20} />
              </span>
              <h3 className="mt-4 font-display text-base font-semibold">{title}</h3>
              <p className="mt-1.5 text-sm leading-6 text-ink-2">{text}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="videos" className="mx-auto max-w-7xl px-4 py-16 sm:px-6">
        <div className="mb-6 flex items-end justify-between gap-4">
          <div>
            <p className="eyebrow">Made by learners</p>
            <h2 className="mt-2 font-display text-3xl font-semibold">Public explainer videos</h2>
          </div>
          <Link to="/discover" className="btn-ghost btn-sm">
            See all <ArrowRight size={14} />
          </Link>
        </div>
        {isLoading ? (
          <GridSkeleton />
        ) : videos.length ? (
          <VideoGrid videos={videos} />
        ) : (
          <div className="card p-10 text-center text-sm text-ink-2">
            The first public videos will appear here once learners publish them.
          </div>
        )}
      </section>

      <section id="how" className="mx-auto max-w-7xl px-4 py-16 sm:px-6">
        <p className="eyebrow">How it works</p>
        <h2 className="mt-2 font-display text-3xl font-semibold">From question to understanding</h2>
        <div className="mt-8 grid grid-cols-1 gap-4 md:grid-cols-3">
          {STEPS.map(([title, text], index) => (
            <div key={title} className="card relative p-6">
              <span className="font-mono text-sm text-primary">0{index + 1}</span>
              <h3 className="mt-3 font-display text-lg font-semibold">{title}</h3>
              <p className="mt-1.5 text-sm text-ink-2">{text}</p>
            </div>
          ))}
        </div>
        <div className="relative mt-12 overflow-hidden rounded-3xl border border-primary/30 bg-surface p-8 text-center sm:p-12">
          <div className="pointer-events-none absolute inset-0" style={{ background: "var(--hero-glow)" }} />
          <h2 className="relative font-display text-2xl font-semibold sm:text-3xl">
            Ready to learn something new?
          </h2>
          <p className="relative mt-2 text-ink-2">Your first explainer is one question away.</p>
          <Link to="/signup" className="btn-primary relative mt-6 px-6 py-3">
            Start learning <ArrowRight size={18} />
          </Link>
        </div>
      </section>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-3 px-4 py-8 text-sm text-muted sm:flex-row sm:px-6">
          <Wordmark size={26} />
          <p>Learn · Watch · Explore — in motion.</p>
        </div>
      </footer>
    </div>
  );
}
