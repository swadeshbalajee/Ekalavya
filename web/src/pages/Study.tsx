import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  Archive,
  CalendarCheck,
  ChevronLeft,
  ChevronRight,
  Plus,
  RefreshCw,
  Sparkles,
  Target,
  Trash2,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Bar, Button, Empty, Field, Menu, Modal, ProgressRing, Spinner, Tabs } from "../components/common";
import { TASK_KIND, TaskRow } from "../components/TaskRow";
import { api, errorText } from "../lib/api";
import { useAuth } from "../lib/auth";
import { dayLabel, isoDate, LANGUAGES, parseDate } from "../lib/format";
import type { StudyPlan, StudyTask } from "../lib/types";
import { useToast } from "../lib/ui";

type Tab = "overview" | "calendar" | "plans";

function weekBounds(today: Date): [Date, Date] {
  const start = new Date(today);
  start.setDate(today.getDate() - ((today.getDay() + 6) % 7)); // Monday
  const end = new Date(start);
  end.setDate(start.getDate() + 6);
  return [start, end];
}

export default function StudyPage() {
  const [tab, setTab] = useState<Tab>("overview");
  const [creating, setCreating] = useState(false);
  const plans = useQuery({
    queryKey: ["study", "plans"],
    queryFn: () => api<StudyPlan[]>("/v1/study-plans"),
  });

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:py-10">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="font-display text-2xl font-semibold sm:text-3xl">My Study Plan</h1>
          <p className="mt-1 text-sm text-ink-2">Stay consistent and reach your goals.</p>
        </div>
        <button className="btn-primary" onClick={() => setCreating(true)}>
          <Plus size={16} /> New plan
        </button>
      </div>
      <Tabs
        value={tab}
        onChange={setTab}
        className="mt-6 w-fit"
        items={[
          { value: "overview", label: "Overview" },
          { value: "calendar", label: "Calendar" },
          { value: "plans", label: "Goals", count: plans.data?.length },
        ]}
      />
      <div className="mt-6">
        {plans.isLoading ? (
          <div className="skeleton h-64" />
        ) : !plans.data?.length && tab !== "plans" ? (
          <Empty
            icon={<Target size={22} />}
            title="No study plan yet"
            action={
              <button className="btn-primary btn-sm" onClick={() => setCreating(true)}>
                <Sparkles size={14} /> Make my plan
              </button>
            }
          >
            Tell Ekalavya what you want to learn and by when. It schedules videos to watch, quizzes,
            reading and review — and replans when life happens.
          </Empty>
        ) : tab === "overview" ? (
          <Overview />
        ) : tab === "calendar" ? (
          <Calendar />
        ) : (
          <Plans plans={plans.data ?? []} onCreate={() => setCreating(true)} />
        )}
      </div>
      {creating && <NewPlanModal onClose={() => setCreating(false)} />}
    </div>
  );
}

function useTasks(start: Date, end: Date) {
  return useQuery({
    queryKey: ["study", "tasks", isoDate(start), isoDate(end)],
    queryFn: () =>
      api<StudyTask[]>("/v1/study-plans/tasks", { params: { start: isoDate(start), end: isoDate(end) } }),
  });
}

function Overview() {
  const today = new Date();
  const [weekStart, weekEnd] = weekBounds(today);
  const upcomingEnd = new Date(today);
  upcomingEnd.setDate(today.getDate() + 14);
  const back = new Date(today);
  back.setDate(today.getDate() - 30);
  const week = useTasks(weekStart, weekEnd);
  const span = useTasks(back, upcomingEnd);
  const todayKey = isoDate(today);
  const done = week.data?.filter((task) => task.done).length ?? 0;
  const total = week.data?.length ?? 0;
  const due = (span.data ?? []).filter((task) => task.due_date <= todayKey && (!task.done || task.due_date === todayKey));
  const upcoming = (span.data ?? []).filter((task) => task.due_date > todayKey && !task.done).slice(0, 8);
  const minutes = (week.data ?? []).filter((task) => task.done).reduce((sum, task) => sum + task.minutes, 0);

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[300px_minmax(0,1fr)_minmax(0,0.9fr)]">
      <div className="card flex flex-col items-center p-6">
        <h2 className="self-start font-display text-sm font-semibold">Weekly progress</h2>
        <div className="mt-6">
          <ProgressRing value={total ? done / total : 0} size={150}>
            <span className="font-display text-3xl font-semibold">{total ? Math.round((done / total) * 100) : 0}%</span>
            <span className="text-xs text-muted">this week</span>
          </ProgressRing>
        </div>
        <p className="mt-5 text-sm text-ink-2">
          {done} of {total} tasks completed
        </p>
        <p className="mt-1 text-xs text-muted">{minutes} minutes studied</p>
        <div className="mt-5 flex w-full justify-between gap-1">
          {Array.from({ length: 7 }, (_, index) => {
            const day = new Date(weekStart);
            day.setDate(weekStart.getDate() + index);
            const key = isoDate(day);
            const items = (week.data ?? []).filter((task) => task.due_date === key);
            const complete = items.length > 0 && items.every((task) => task.done);
            return (
              <div key={key} className="flex flex-1 flex-col items-center gap-1.5">
                <span className="text-[10px] text-muted">{day.toLocaleDateString(undefined, { weekday: "narrow" })}</span>
                <span
                  className={clsx(
                    "h-7 w-full rounded-md",
                    complete ? "bg-gradient-to-b from-primary to-teal" : items.length ? "bg-primary/20" : "bg-raised",
                    key === todayKey && "ring-2 ring-primary/60",
                  )}
                  title={`${items.filter((task) => task.done).length}/${items.length} done`}
                />
              </div>
            );
          })}
        </div>
      </div>

      <div className="card overflow-hidden">
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2 className="font-display text-sm font-semibold">Today&apos;s tasks</h2>
          <span className="text-xs text-muted">{today.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" })}</span>
        </div>
        {span.isLoading ? (
          <div className="p-6">
            <Spinner />
          </div>
        ) : due.length ? (
          <div className="divide-y divide-line">
            {due.map((task) => (
              <TaskRow key={task.id} task={task} />
            ))}
          </div>
        ) : (
          <p className="p-6 text-sm text-ink-2">Nothing due today. Take a breath — or get ahead on tomorrow.</p>
        )}
      </div>

      <div className="card overflow-hidden">
        <div className="border-b border-line px-4 py-3">
          <h2 className="font-display text-sm font-semibold">Upcoming</h2>
        </div>
        {upcoming.length ? (
          <ul className="divide-y divide-line">
            {upcoming.map((task) => {
              const kind = TASK_KIND[task.kind] ?? TASK_KIND.review;
              return (
                <li key={task.id} className="flex items-center gap-3 px-4 py-3">
                  <span className="w-14 shrink-0 rounded-lg bg-raised px-2 py-1 text-center font-mono text-[11px] text-ink-2">
                    {dayLabel(task.due_date)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm">{task.title}</span>
                    <span className="text-[11px] text-muted">
                      {kind.label} · {task.minutes} min
                    </span>
                  </span>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="p-6 text-sm text-ink-2">No upcoming tasks.</p>
        )}
      </div>
    </div>
  );
}

function Calendar() {
  const [month, setMonth] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });
  const [selected, setSelected] = useState(isoDate(new Date()));
  const gridStart = new Date(month);
  gridStart.setDate(1 - ((month.getDay() + 6) % 7));
  const gridEnd = new Date(gridStart);
  gridEnd.setDate(gridStart.getDate() + 41);
  const tasks = useTasks(gridStart, gridEnd);
  const byDay = useMemo(() => {
    const map = new Map<string, StudyTask[]>();
    for (const task of tasks.data ?? []) map.set(task.due_date, [...(map.get(task.due_date) ?? []), task]);
    return map;
  }, [tasks.data]);
  const todayKey = isoDate(new Date());
  const shift = (amount: number) => setMonth(new Date(month.getFullYear(), month.getMonth() + amount, 1));

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
      <div className="card p-4">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-display font-semibold">
            {month.toLocaleDateString(undefined, { month: "long", year: "numeric" })}
          </h2>
          <div className="flex gap-1">
            <button className="icon-btn" onClick={() => shift(-1)} aria-label="Previous month">
              <ChevronLeft size={18} />
            </button>
            <button className="icon-btn" onClick={() => shift(1)} aria-label="Next month">
              <ChevronRight size={18} />
            </button>
          </div>
        </div>
        <div className="grid grid-cols-7 gap-1 text-center text-[11px] text-muted">
          {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day) => (
            <span key={day} className="py-1">
              {day}
            </span>
          ))}
        </div>
        <div className="grid grid-cols-7 gap-1">
          {Array.from({ length: 42 }, (_, index) => {
            const day = new Date(gridStart);
            day.setDate(gridStart.getDate() + index);
            const key = isoDate(day);
            const items = byDay.get(key) ?? [];
            const inMonth = day.getMonth() === month.getMonth();
            return (
              <button
                key={key}
                onClick={() => setSelected(key)}
                className={clsx(
                  "flex aspect-square flex-col items-center justify-start gap-1 rounded-xl p-1 text-sm transition sm:aspect-[1.2]",
                  selected === key ? "bg-primary/15 ring-1 ring-primary/50" : "hover:bg-raised",
                  !inMonth && "opacity-40",
                )}
              >
                <span className={clsx("mt-1 text-xs", key === todayKey && "rounded-full bg-primary px-1.5 text-white")}>
                  {day.getDate()}
                </span>
                <span className="flex flex-wrap justify-center gap-0.5">
                  {items.slice(0, 4).map((task) => (
                    <span
                      key={task.id}
                      className={clsx("h-1.5 w-1.5 rounded-full", task.done ? "bg-success" : "bg-primary")}
                    />
                  ))}
                </span>
              </button>
            );
          })}
        </div>
      </div>
      <div className="card overflow-hidden">
        <div className="border-b border-line px-4 py-3">
          <h2 className="font-display text-sm font-semibold">
            {parseDate(selected).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}
          </h2>
        </div>
        {(byDay.get(selected) ?? []).length ? (
          <div className="divide-y divide-line">
            {(byDay.get(selected) ?? []).map((task) => (
              <TaskRow key={task.id} task={task} />
            ))}
          </div>
        ) : (
          <p className="p-6 text-sm text-ink-2">No tasks on this day.</p>
        )}
      </div>
    </div>
  );
}

function Plans({ plans, onCreate }: { plans: StudyPlan[]; onCreate: () => void }) {
  const [open, setOpen] = useState<string | null>(plans[0]?.id ?? null);
  if (!plans.length) {
    return (
      <Empty
        icon={<Target size={22} />}
        title="No active goals"
        action={
          <button className="btn-primary btn-sm" onClick={onCreate}>
            New plan
          </button>
        }
      />
    );
  }
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
      <div className="space-y-2">
        {plans.map((plan) => (
          <button
            key={plan.id}
            onClick={() => setOpen(plan.id)}
            className={clsx(
              "card w-full p-4 text-left transition",
              open === plan.id ? "border-primary/60 bg-primary/5" : "hover:border-primary/40",
            )}
          >
            <p className="font-medium">{plan.title}</p>
            <p className="mt-0.5 line-clamp-2 text-xs text-ink-2">{plan.goal}</p>
            <Bar value={plan.task_count ? plan.done_count / plan.task_count : 0} className="mt-3" />
            <p className="mt-1.5 text-[11px] text-muted">
              {plan.done_count}/{plan.task_count} tasks · {plan.end_date ? `until ${dayLabel(plan.end_date)}` : "open-ended"}
            </p>
          </button>
        ))}
      </div>
      {open && <PlanDetail id={open} onGone={() => setOpen(null)} />}
    </div>
  );
}

function PlanDetail({ id, onGone }: { id: string; onGone: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [replanning, setReplanning] = useState(false);
  const [instruction, setInstruction] = useState("");
  const plan = useQuery({
    queryKey: ["study", "plan", id],
    queryFn: () => api<StudyPlan>(`/v1/study-plans/${id}`),
  });
  const refresh = () => void client.invalidateQueries({ queryKey: ["study"] });
  const replan = useMutation({
    mutationFn: () => api<StudyPlan>(`/v1/study-plans/${id}/replan`, { body: { instruction } }),
    onSuccess: () => {
      setReplanning(false);
      setInstruction("");
      toast("Plan updated from today");
      refresh();
    },
    onError: (error) => toast(errorText(error), "error"),
  });
  const archive = useMutation({
    mutationFn: () => api(`/v1/study-plans/${id}`, { method: "PATCH", body: { status: "archived" } }),
    onSuccess: () => {
      refresh();
      onGone();
    },
  });
  const remove = useMutation({
    mutationFn: () => api(`/v1/study-plans/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      refresh();
      onGone();
    },
  });
  const data = plan.data;
  if (!data) return <div className="skeleton h-80" />;
  const days = new Map<string, StudyTask[]>();
  for (const task of data.tasks ?? []) days.set(task.due_date, [...(days.get(task.due_date) ?? []), task]);
  return (
    <div className="card overflow-hidden">
      <div className="flex items-start gap-3 border-b border-line p-4">
        <div className="min-w-0 flex-1">
          <h2 className="font-display text-lg font-semibold">{data.title}</h2>
          {data.summary && <p className="mt-1 text-sm text-ink-2">{data.summary}</p>}
          <p className="mt-2 text-xs text-muted">
            {data.minutes_per_day} min/day · {dayLabel(data.start_date)} → {data.end_date ? dayLabel(data.end_date) : "open"} · {data.language}
          </p>
        </div>
        <button className="btn-secondary btn-sm" onClick={() => setReplanning(true)}>
          <RefreshCw size={14} /> Replan
        </button>
        <Menu
          trigger={<Archive size={16} />}
          items={[
            { label: "Archive goal", icon: <Archive size={14} />, onSelect: () => archive.mutate() },
            { label: "Delete goal", icon: <Trash2 size={14} />, danger: true, onSelect: () => remove.mutate() },
          ]}
        />
      </div>
      <div className="max-h-[640px] overflow-y-auto">
        {[...days.entries()].map(([day, tasks]) => (
          <div key={day}>
            <p className="sticky top-0 z-10 border-b border-line bg-surface/95 px-4 py-2 font-mono text-[11px] uppercase tracking-wider text-muted backdrop-blur">
              {parseDate(day).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}
            </p>
            <div className="divide-y divide-line">
              {tasks.map((task) => (
                <TaskRow key={task.id} task={task} />
              ))}
            </div>
          </div>
        ))}
      </div>
      <Modal
        open={replanning}
        onClose={() => setReplanning(false)}
        title="Replan from today"
        footer={
          <Button busy={replan.isPending} onClick={() => replan.mutate()}>
            {replan.isPending ? "Replanning…" : "Replan"}
          </Button>
        }
      >
        <p className="mb-3 text-sm text-ink-2">
          Finished tasks stay. Everything still to do is rescheduled from today, taking your progress into account.
        </p>
        <Field label="Anything to change? (optional)">
          <textarea
            className="input min-h-[90px]"
            placeholder="e.g. I missed three days, I have exams on Friday, more practice please…"
            value={instruction}
            onChange={(event) => setInstruction(event.target.value)}
          />
        </Field>
      </Modal>
    </div>
  );
}

function NewPlanModal({ onClose }: { onClose: () => void }) {
  const { user } = useAuth();
  const client = useQueryClient();
  const toast = useToast();
  const [goal, setGoal] = useState("");
  const [endDate, setEndDate] = useState("");
  const [minutesPerDay, setMinutesPerDay] = useState(30);
  const [language, setLanguage] = useState(user?.default_text_language ?? "English");
  const create = useMutation({
    mutationFn: () =>
      api<StudyPlan>("/v1/study-plans", {
        body: {
          goal: goal.trim(),
          end_date: endDate || null,
          minutes_per_day: minutesPerDay,
          language,
          start_date: isoDate(new Date()),
        },
      }),
    onSuccess: (plan) => {
      toast(`Planned ${plan.task_count} tasks`);
      void client.invalidateQueries({ queryKey: ["study"] });
      void client.invalidateQueries({ queryKey: ["home"] });
      onClose();
    },
    onError: (error) => toast(errorText(error), "error"),
  });
  return (
    <Modal
      open
      onClose={onClose}
      title="New study plan"
      footer={
        <Button busy={create.isPending} disabled={goal.trim().length < 3} onClick={() => create.mutate()}>
          <CalendarCheck size={16} /> {create.isPending ? "Planning your days…" : "Make my plan"}
        </Button>
      }
    >
      <div className="space-y-4">
        <Field label="What do you want to achieve?">
          <textarea
            className="input min-h-[96px]"
            autoFocus
            placeholder="e.g. Finish CBSE Class 12 Physics — Optics and Electrostatics — before my board exam"
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
          />
        </Field>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Field label="By (optional)">
            <input className="input" type="date" min={isoDate(new Date())} value={endDate} onChange={(event) => setEndDate(event.target.value)} />
          </Field>
          <Field label="Minutes per day">
            <input
              className="input"
              type="number"
              min={5}
              max={600}
              step={5}
              value={minutesPerDay}
              onChange={(event) => setMinutesPerDay(Number(event.target.value))}
            />
          </Field>
          <Field label="Language">
            <select className="input" value={language} onChange={(event) => setLanguage(event.target.value)}>
              {LANGUAGES.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </Field>
        </div>
        <p className="text-xs text-muted">
          The planner uses your videos, saved videos and the public catalog, and suggests new videos for gaps.
        </p>
      </div>
    </Modal>
  );
}
