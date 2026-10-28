import { useMutation, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { BookOpenText, Clapperboard, GraduationCap, Play, RotateCcw, Sparkles } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api, errorText } from "../lib/api";
import { dayLabel, isoDate } from "../lib/format";
import type { Quiz, StudyTask, TaskKind } from "../lib/types";
import { useToast } from "../lib/ui";
import { Spinner } from "./common";

export const TASK_KIND: Record<TaskKind, { label: string; icon: typeof Play; tone: string }> = {
  watch: { label: "Watch", icon: Play, tone: "text-primary bg-primary/12" },
  create: { label: "Create", icon: Clapperboard, tone: "text-teal bg-teal/12" },
  practice: { label: "Practice", icon: GraduationCap, tone: "text-amber bg-amber/12" },
  read: { label: "Read", icon: BookOpenText, tone: "text-success bg-success/12" },
  review: { label: "Review", icon: RotateCcw, tone: "text-ink-2 bg-raised" },
};

export function TaskRow({ task, showDate }: { task: StudyTask; showDate?: boolean }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const kind = TASK_KIND[task.kind] ?? TASK_KIND.review;
  const Icon = kind.icon;
  const overdue = !task.done && task.due_date < isoDate(new Date());

  const toggle = useMutation({
    mutationFn: (done: boolean) =>
      api<StudyTask>(`/v1/study-plans/tasks/${task.id}`, { method: "PATCH", body: { done } }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["home"] });
      void client.invalidateQueries({ queryKey: ["study"] });
    },
    onError: (error) => toast(errorText(error), "error"),
  });

  const practice = useMutation({
    mutationFn: () =>
      api<Quiz>("/v1/quizzes", {
        body: task.video_id ? { video_id: task.video_id } : { topic: task.topic || task.title },
      }),
    onSuccess: (quiz) => navigate(`/quiz/${quiz.id}?task=${task.id}`),
    onError: (error) => toast(errorText(error), "error"),
  });

  const act = () => {
    if (task.video_id && (task.kind === "watch" || task.kind === "review" || task.kind === "read")) {
      navigate(`/watch/${task.video_id}?task=${task.id}`);
    } else if (task.kind === "practice") {
      practice.mutate();
    } else if (task.kind === "create" || task.topic) {
      navigate(`/chat?prompt=${encodeURIComponent(`Make me a video explaining ${task.topic || task.title}`)}`);
    }
  };
  const actionLabel =
    task.video_id && task.kind !== "practice"
      ? "Open"
      : task.kind === "practice"
        ? "Start quiz"
        : task.kind === "create" || task.topic
          ? "Create video"
          : null;

  return (
    <div className="flex items-start gap-3 px-4 py-3.5">
      <button
        role="checkbox"
        aria-checked={task.done}
        aria-label={`Mark "${task.title}" ${task.done ? "not done" : "done"}`}
        onClick={() => toggle.mutate(!task.done)}
        className={clsx(
          "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition",
          task.done ? "border-primary bg-primary text-white" : "border-line hover:border-primary",
        )}
      >
        {toggle.isPending ? (
          <Spinner size={12} />
        ) : (
          task.done && (
            <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="m3.5 8.5 3 3 6-7" />
            </svg>
          )
        )}
      </button>
      <span className={clsx("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg", kind.tone)}>
        <Icon size={15} />
      </span>
      <div className="min-w-0 flex-1">
        <p className={clsx("text-sm font-medium", task.done && "text-muted line-through")}>{task.title}</p>
        {task.detail && <p className="mt-0.5 line-clamp-2 text-xs text-ink-2">{task.detail}</p>}
        <p className="mt-1 flex flex-wrap gap-x-2 text-[11px] text-muted">
          <span>{kind.label}</span>
          <span>· {task.minutes} min</span>
          {(showDate || overdue) && (
            <span className={overdue ? "text-warning" : ""}>
              · {overdue ? "Overdue, " : ""}
              {dayLabel(task.due_date)}
            </span>
          )}
          {task.plan_title && <span className="truncate">· {task.plan_title}</span>}
        </p>
      </div>
      {actionLabel && !task.done && (
        <button className="btn-secondary btn-sm shrink-0" onClick={act} disabled={practice.isPending}>
          {practice.isPending ? <Spinner size={14} /> : task.kind === "create" ? <Sparkles size={14} /> : null}
          {actionLabel}
        </button>
      )}
    </div>
  );
}
