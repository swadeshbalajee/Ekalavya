import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { MessageSquare } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Tabs } from "../components/common";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ago, count, duration, money } from "../lib/format";

const PERIODS = { "7d": 7, "30d": 30, "90d": 90, All: 3650 } as const;
type Period = keyof typeof PERIODS;

type Summary = {
  totals: {
    cost_usd: number;
    calls: number;
    tokens: number;
    input_tokens: number;
    output_tokens: number;
    videos: number;
    chats: number;
    build_seconds_avg: number;
    build_seconds_total: number;
    success_rate: number | null;
    retries: number;
    failed_calls: number;
    fallback_calls: number;
  };
  daily: { date: string; cost_usd: number; calls: number }[];
  modules: { module: string; cost_usd: number; calls: number; tokens: number; avg_latency_ms: number }[];
  users: { user: string; username: string | null; cost_usd: number; chats: number; calls: number; tokens: number }[];
  chats: {
    project_id: string;
    title: string;
    owner: string | null;
    cost_usd: number;
    tokens: number;
    videos: number;
    time_seconds: number;
    retries: number;
    last_activity: string;
  }[];
};

type Step = { label: string; state: string };
type LiveRun = {
  title: string;
  status: string;
  owner?: string;
  elapsed_seconds: number;
  pipeline: {
    next: string | null;
    stages: Step[];
    scenes: { scene: string; state: string; attempt: number; steps: Step[] }[];
  };
};

type ChatDetail = {
  project: { title: string };
  totals: { cost_usd: number; tokens: number; calls: number; retries: number };
  modules: Summary["modules"];
  scenes: {
    scene: string;
    cost_usd: number;
    tokens: number;
    calls: number;
    retries: number;
    avg_latency_ms: number | null;
    modules: { module: string }[];
  }[];
  runs: {
    index: number;
    started_at: string;
    outcome: string;
    seconds: number;
    timeline: { stage: string; start_seconds: number; end_seconds: number }[];
  }[];
  calls: {
    time: string;
    operation: string;
    module: string;
    scene: string | null;
    model: string;
    status: string;
    tokens: number;
    cost_usd: number | null;
    latency_ms: number | null;
  }[];
};

const STATE_STYLE: Record<string, string> = {
  done: "border-success/50 bg-success/10 text-success",
  running: "border-primary/60 bg-primary/15 text-ink animate-pulse",
  queued: "border-line text-muted",
  failed: "border-danger/50 bg-danger/10 text-danger",
  degraded: "border-warning/50 bg-warning/10 text-warning",
  skipped: "border-line text-muted line-through",
};

function Flow({ steps }: { steps: Step[] }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {steps.map((step, index) => (
        <span key={step.label + index} className="flex items-center gap-1.5">
          {index > 0 && <span className={clsx("h-px w-3", steps[index - 1].state === "done" ? "bg-success/60" : "bg-line")} />}
          <span className={clsx("rounded-full border px-2.5 py-0.5 text-[11px] font-medium", STATE_STYLE[step.state] ?? STATE_STYLE.queued)}>
            {step.label}
          </span>
        </span>
      ))}
    </div>
  );
}

function ChartTooltip({ active, payload, label }: { active?: boolean; payload?: any[]; label?: string }) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  return (
    <div className="rounded-xl border border-line bg-surface px-3 py-2 text-xs shadow-card">
      <p className="font-medium text-ink">{label ?? row.module}</p>
      <p className="text-ink-2">Spend {money(row.cost_usd)}</p>
      <p className="text-ink-2">{row.calls} calls</p>
    </div>
  );
}

function Table<T>({ rows, columns }: { rows: T[]; columns: [string, string, (row: T) => React.ReactNode, boolean?][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs text-muted">
            {columns.map(([key, label, , right]) => (
              <th key={key} className={clsx("whitespace-nowrap px-3 py-2 font-medium", right && "text-right")}>
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((row, index) => (
            <tr key={index} className="hover:bg-raised/40">
              {columns.map(([key, , render, right]) => (
                <td key={key} className={clsx("whitespace-nowrap px-3 py-2", right && "text-right font-mono text-xs")}>
                  {render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function UsagePage() {
  const { user } = useAuth();
  const [period, setPeriod] = useState<Period>("30d");
  const [scope, setScope] = useState<"me" | "all">("me");
  const [chat, setChat] = useState<string | null>(null);
  const summary = useQuery({
    queryKey: ["usage", period, scope],
    queryFn: () => api<Summary>("/v1/usage/summary", { params: { days: PERIODS[period], scope } }),
  });
  const live = useQuery({
    queryKey: ["usage-live", scope],
    queryFn: () => api<{ runs: LiveRun[] }>("/v1/usage/live", { params: { scope } }),
    refetchInterval: 4000,
  });
  const data = summary.data;
  const daily = useMemo(() => {
    if (!data) return [];
    const days = Math.min(PERIODS[period], 90);
    const byDay = new Map(data.daily.map((row) => [row.date.slice(0, 10), row]));
    return Array.from({ length: days }, (_, index) => {
      const day = new Date();
      day.setDate(day.getDate() - (days - 1 - index));
      const key = day.toISOString().slice(0, 10);
      const row = byDay.get(key);
      return {
        day: day.toLocaleDateString(undefined, { day: "2-digit", month: "short" }),
        cost_usd: row?.cost_usd ?? 0,
        calls: row?.calls ?? 0,
      };
    });
  }, [data, period]);
  const totals = data?.totals;
  const selected = chat ?? data?.chats[0]?.project_id ?? null;

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:py-10">
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="font-display text-2xl font-semibold sm:text-3xl">Usage &amp; activity</h1>
          <p className="mt-1 text-sm text-ink-2">
            {scope === "all" ? "Everyone" : user?.display_name} · spend, tokens, time and retries
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Tabs
            value={period}
            onChange={setPeriod}
            items={(Object.keys(PERIODS) as Period[]).map((value) => ({ value, label: value }))}
          />
          {user?.is_admin && (
            <Tabs
              value={scope}
              onChange={setScope}
              items={[
                { value: "me", label: "Me" },
                { value: "all", label: "All users" },
              ]}
            />
          )}
        </div>
      </div>

      {totals && (
        <div className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          {[
            ["Spend", money(totals.cost_usd), `${totals.calls.toLocaleString()} model calls`],
            ["Tokens", count(totals.tokens), `${count(totals.input_tokens)} in · ${count(totals.output_tokens)} out`],
            ["Videos built", String(totals.videos), `across ${totals.chats} chats`],
            ["Avg build", duration(totals.build_seconds_avg), `${duration(totals.build_seconds_total)} in total`],
            ["Success", totals.success_rate === null ? "—" : `${Math.round(totals.success_rate * 100)}%`, "videos finished"],
            ["Retries", String(totals.retries), `${totals.failed_calls} failed · ${totals.fallback_calls} fallbacks`],
          ].map(([label, value, sub]) => (
            <div key={label} className="card p-4">
              <p className="text-xs text-muted">{label}</p>
              <p className="mt-1 font-display text-2xl font-semibold">{value}</p>
              <p className="mt-0.5 truncate text-[11px] text-ink-2">{sub}</p>
            </div>
          ))}
        </div>
      )}

      <section className="mt-8">
        <h2 className="font-display text-lg font-semibold">Live runs</h2>
        <div className="mt-3 space-y-3">
          {!live.data?.runs.length ? (
            <p className="card p-5 text-sm text-ink-2">
              No video is being built right now. Runs appear here, stage by stage, while they happen.
            </p>
          ) : (
            live.data.runs.map((run, index) => (
              <div key={index} className="card space-y-3 p-4">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="font-medium">{run.title}</p>
                  <p className="text-xs text-muted">
                    {run.status.toLowerCase()} {run.owner ? `· ${run.owner}` : ""} · {duration(run.elapsed_seconds)} elapsed
                  </p>
                </div>
                <p className="text-sm text-ink-2">{run.pipeline.next || "Starting…"}</p>
                <Flow steps={run.pipeline.stages} />
                {run.pipeline.scenes.length > 0 && (
                  <div className="space-y-1.5 border-t border-line pt-3">
                    {run.pipeline.scenes.map((lane) => (
                      <div key={lane.scene} className="flex items-center gap-3">
                        <span className="w-10 font-mono text-xs text-muted">{lane.scene}</span>
                        <Flow steps={lane.steps} />
                        {lane.attempt > 1 && <span className="text-[11px] text-warning">attempt {lane.attempt}</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </section>

      <div className="mt-8 grid grid-cols-1 gap-4 lg:grid-cols-[1.15fr_1fr]">
        <section className="card p-4">
          <h2 className="font-display text-sm font-semibold">Spend per day</h2>
          <div className="mt-4 h-60">
            <ResponsiveContainer>
              <BarChart data={daily} margin={{ left: -8, right: 8 }}>
                <CartesianGrid vertical={false} stroke="rgb(var(--c-line))" strokeDasharray="3 3" />
                <XAxis dataKey="day" tick={{ fill: "rgb(var(--c-muted))", fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={18} />
                <YAxis tickFormatter={(value) => `$${value}`} tick={{ fill: "rgb(var(--c-muted))", fontSize: 11 }} tickLine={false} axisLine={false} width={48} />
                <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgb(var(--c-raised))" }} />
                <Bar dataKey="cost_usd" fill="rgb(var(--c-primary))" radius={[4, 4, 0, 0]} maxBarSize={22} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>
        <section className="card p-4">
          <h2 className="font-display text-sm font-semibold">Spend by module</h2>
          <ModuleBars modules={data?.modules ?? []} />
        </section>
      </div>

      {scope === "all" && !!data?.users.length && (
        <section className="card mt-8 p-2">
          <h2 className="px-3 py-2 font-display text-sm font-semibold">Users</h2>
          <Table
            rows={data.users}
            columns={[
              ["user", "User", (row) => row.user],
              ["username", "Username", (row) => row.username ?? "—"],
              ["spend", "Spend", (row) => money(row.cost_usd), true],
              ["chats", "Chats", (row) => row.chats, true],
              ["calls", "Calls", (row) => row.calls.toLocaleString(), true],
              ["tokens", "Tokens", (row) => count(row.tokens), true],
            ]}
          />
        </section>
      )}

      <section className="card mt-8 p-2">
        <h2 className="px-3 py-2 font-display text-sm font-semibold">Chats</h2>
        {data?.chats.length ? (
          <Table
            rows={data.chats}
            columns={[
              [
                "chat",
                "Chat",
                (row) => (
                  <button className="max-w-[280px] truncate text-left hover:text-primary" onClick={() => setChat(row.project_id)}>
                    {row.title}
                  </button>
                ),
              ],
              ...(scope === "all" ? ([["owner", "Owner", (row) => row.owner ?? "—"]] as [string, string, (row: Summary["chats"][number]) => React.ReactNode][]) : []),
              ["spend", "Spend", (row) => money(row.cost_usd), true],
              ["tokens", "Tokens", (row) => row.tokens.toLocaleString(), true],
              ["videos", "Videos", (row) => row.videos, true],
              ["time", "Build time", (row) => duration(row.time_seconds), true],
              ["retries", "Retries", (row) => row.retries, true],
              ["last", "Last activity", (row) => ago(row.last_activity)],
            ]}
          />
        ) : (
          <p className="px-3 pb-3 text-sm text-ink-2">No chats with usage in this period yet.</p>
        )}
      </section>

      {selected && <ChatUsage id={selected} />}
    </div>
  );
}

function ModuleBars({ modules }: { modules: Summary["modules"] }) {
  if (!modules.length) return <p className="mt-4 text-sm text-ink-2">No model calls yet.</p>;
  const top = Math.max(...modules.map((row) => row.cost_usd), 0.0001);
  return (
    <ul className="mt-4 space-y-2.5">
      {[...modules]
        .sort((a, b) => b.cost_usd - a.cost_usd)
        .map((row) => (
          <li key={row.module} className="grid grid-cols-[120px_1fr_64px] items-center gap-3 text-xs" title={`${row.calls} calls · ${count(row.tokens)} tokens`}>
            <span className="truncate text-ink-2">{row.module}</span>
            <span className="h-3 overflow-hidden rounded-r bg-raised">
              <span className="block h-full rounded-r bg-primary" style={{ width: `${(row.cost_usd / top) * 100}%` }} />
            </span>
            <span className="text-right font-mono text-ink">{money(row.cost_usd)}</span>
          </li>
        ))}
    </ul>
  );
}

function ChatUsage({ id }: { id: string }) {
  const [tab, setTab] = useState<"modules" | "scenes" | "runs" | "calls">("modules");
  const [run, setRun] = useState<number | null>(null);
  const detail = useQuery({ queryKey: ["usage-chat", id], queryFn: () => api<ChatDetail>(`/v1/usage/chats/${id}`) });
  const data = detail.data;
  if (!data) return <div className="skeleton mt-8 h-60" />;
  const runs = data.runs;
  const picked = runs[run ?? Math.max(runs.findLastIndex((item) => item.seconds >= 30), runs.length - 1)];
  const span = picked ? Math.max(...picked.timeline.map((item) => item.end_seconds), 1) : 1;
  return (
    <section className="card mt-8 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-base font-semibold">{data.project.title}</h2>
          <p className="text-xs text-muted">
            {money(data.totals.cost_usd)} · {count(data.totals.tokens)} tokens · {data.totals.calls} calls · {data.totals.retries} retries
          </p>
        </div>
        <Link to={`/chat/${id}`} className="btn-secondary btn-sm">
          <MessageSquare size={14} /> Open chat
        </Link>
      </div>
      <Tabs
        value={tab}
        onChange={setTab}
        className="mt-4 w-fit"
        items={[
          { value: "modules", label: "By module" },
          { value: "scenes", label: "By scene" },
          { value: "runs", label: "Run timeline" },
          { value: "calls", label: "Every call" },
        ]}
      />
      <div className="mt-4">
        {tab === "modules" && <ModuleBars modules={data.modules} />}
        {tab === "scenes" &&
          (data.scenes.length ? (
            <Table
              rows={data.scenes}
              columns={[
                ["scene", "Scene", (row) => row.scene],
                ["spend", "Spend", (row) => money(row.cost_usd), true],
                ["tokens", "Tokens", (row) => row.tokens.toLocaleString(), true],
                ["calls", "Calls", (row) => row.calls, true],
                ["retries", "Retries", (row) => row.retries, true],
                ["latency", "Avg call", (row) => (row.avg_latency_ms ? duration(row.avg_latency_ms / 1000) : "—"), true],
                ["module", "Costliest module", (row) => row.modules[0]?.module ?? ""],
              ]}
            />
          ) : (
            <p className="text-sm text-ink-2">Per-scene usage is recorded for videos built from now on.</p>
          ))}
        {tab === "runs" &&
          (picked ? (
            <>
              <select className="input mb-4 max-w-md" value={runs.indexOf(picked)} onChange={(event) => setRun(Number(event.target.value))}>
                {runs.map((item, index) => (
                  <option key={index} value={index}>
                    Run {item.index} · {ago(item.started_at)} · {item.outcome} · {duration(item.seconds)}
                  </option>
                ))}
              </select>
              <ul className="space-y-2">
                {picked.timeline.map((stage) => (
                  <li key={stage.stage + stage.start_seconds} className="grid grid-cols-[130px_1fr_60px] items-center gap-3 text-xs">
                    <span className="truncate text-ink-2">{stage.stage}</span>
                    <span className="relative h-3 rounded bg-raised">
                      <span
                        className="absolute h-full rounded bg-primary"
                        style={{
                          left: `${(stage.start_seconds / span) * 100}%`,
                          width: `${Math.max(((stage.end_seconds - stage.start_seconds) / span) * 100, 0.8)}%`,
                        }}
                      />
                    </span>
                    <span className="text-right font-mono">{duration(stage.end_seconds - stage.start_seconds)}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="text-sm text-ink-2">No runs yet.</p>
          ))}
        {tab === "calls" && (
          <div className="max-h-[440px] overflow-y-auto">
            <Table
              rows={[...data.calls].reverse()}
              columns={[
                ["time", "Time", (row) => ago(row.time)],
                ["operation", "Run", (row) => row.operation],
                ["module", "Module", (row) => row.module],
                ["scene", "Scene", (row) => row.scene ?? "—"],
                ["model", "Model", (row) => row.model],
                ["status", "Outcome", (row) => ({ ok: "OK", fallback: "Fallback model", error: "Failed, retried" })[row.status] ?? row.status],
                ["tokens", "Tokens", (row) => (row.tokens ? row.tokens.toLocaleString() : "—"), true],
                ["cost", "Cost", (row) => (row.cost_usd === null ? "—" : money(row.cost_usd)), true],
                ["took", "Took", (row) => (row.latency_ms ? duration(row.latency_ms / 1000) : "—"), true],
              ]}
            />
          </div>
        )}
      </div>
    </section>
  );
}
