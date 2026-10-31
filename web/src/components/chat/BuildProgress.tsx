import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ChevronDown, CircleStop, Clapperboard, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, errorText } from "../../lib/api";
import { BUSY } from "../../lib/format";
import type { Progress, ProgressEvent } from "../../lib/types";
import { useToast } from "../../lib/ui";
import { Spinner } from "../common";

const LABELS: Record<string, string> = {
  "project.started": "Starting your lesson",
  "preparation.started": "Understanding your request and preferences",
  "preparation.complete": "Choosing languages and finding learning sources",
  "retrieval.complete": "Source material is ready",
  "paper.reading": "Reading the whole paper",
  "paper.outlined": "Lecture plan for the paper is ready",
  "curation.started": "Reading the material and writing the lesson plan",
  "curriculum.complete": "Lesson plan is ready",
  "script.complete": "Narration is ready",
  "storyboard.complete": "Scenes are planned",
  "scenes.created": "Building the planned scenes",
  "scene.started": "Preparing scene",
  "scene.code_started": "Drawing the scene",
  "scene.audio_started": "Recording narration",
  "scene.render_started": "Rendering scene",
  "scene.verifying": "Checking scene layout and accuracy",
  "scene.repairing": "Fixing a scene issue and checking again",
  "scene.complete": "Scene passed verification",
  "scene.degraded": "Scene rendered with quality issues",
  "scene.blocked": "A scene could not be completed",
  "video.assembling": "Putting scenes, narration, and subtitles together",
  "video.complete": "Your video is ready",
  "localization.started": "Preparing the translated lesson",
  "localization.complete": "The translated lesson is ready",
  "edit.started": "Preparing your changes",
};

const SCENE_STAGE: Record<string, number> = {
  "scene.started": 0.05,
  "scene.code_started": 0.15,
  "scene.audio_started": 0.15,
  "scene.render_started": 0.45,
  "scene.timing_repaired": 0.5,
  "scene.verifying": 0.7,
  "scene.rejected": 0.35,
  "scene.repairing": 0.35,
  "scene.complete": 1,
  "scene.degraded": 1,
  "scene.blocked": 1,
};

const MILESTONES: Record<string, number> = {
  "project.started": 0.03,
  "preparation.started": 0.04,
  "preparation.complete": 0.1,
  "retrieval.complete": 0.12,
  "curation.started": 0.14,
  "curriculum.complete": 0.22,
  "script.complete": 0.32,
  "storyboard.complete": 0.4,
  "scenes.created": 0.43,
  "video.assembling": 0.94,
  "video.complete": 1,
  "localization.started": 0.05,
  "localization.complete": 1,
  "edit.started": 0.05,
};

const REPAIR: Record<string, string> = {
  patch_code: "patching the animation code",
  regenerate_code: "regenerating the animation code",
  revise_spec: "revising the scene plan",
  simplify: "simplifying the scene",
  block: "no repairs left",
};

/** One pipeline event as a sentence a student can follow; null hides noisy events. */
export function describe(event: ProgressEvent): string | null {
  const name = event.event;
  const detail = event.detail || {};
  const scene = detail.scene_key || detail.scene_id;
  const reasons: string[] = detail.reasons || [];
  const reasonText = reasons.slice(0, 3).join("; ");
  switch (name) {
    case "preparation.complete": {
      const sources: string[] = detail.sources || [];
      const kind = detail.source_type;
      const where =
        kind === "given"
          ? "only your sources"
          : kind === "given+web"
            ? "your sources plus web sources"
            : kind === "web"
              ? "web sources"
              : sources.length
                ? "saved/library sources"
                : "no sources";
      return (
        `Understood the request (${detail.rationale || "lesson"}). Text ${detail.text_language || "English"}, ` +
        `voice ${detail.voice_language || "English"}. Grounding: ${where}` +
        (sources.length ? ` — ${sources.slice(0, 4).join(", ")}` : "")
      );
    }
    case "paper.reading":
      return `Reading the paper '${detail.title || "attached paper"}'${detail.pages ? ` (${detail.pages} pages)` : ""}`;
    case "paper.outlined":
      return `Planned a ${detail.minutes ?? "?"}-minute lecture on '${detail.title || "the paper"}': ${(detail.chapters || []).length} chapters, ${detail.equations || 0} equations`;
    case "retrieval.web_started":
      return (
        (detail.reason || "Searching the web for reliable sources") +
        (detail.searches > 1 ? ` (${detail.searches} searches in parallel)` : "")
      );
    case "curation.started":
      return `Reading ${detail.passages ?? "the"} collected passages and writing the lesson plan`;
    case "curriculum.complete":
      return detail.plan === "curated"
        ? `Lesson plan ready: ${detail.sections} sections` +
            (detail.references ? `, ${detail.references} references kept for lookup` : "")
        : "Lesson outline is ready";
    case "script.complete":
      return `Script written for ${detail.segments ?? "?"} scenes and reviewed for ${(detail.checks || ["coherence"]).join(", ")}${detail.revisions ? ` after ${detail.revisions} revision(s)` : ""}`;
    case "script.review": {
      const problems: string[] = detail.problems || [];
      if (detail.approved || !problems.length) return `Script review round ${detail.round}: approved`;
      return `Script review round ${detail.round}: ${detail.blocking ? "blocking problems" : "revising"} — ${problems.slice(0, 3).join("; ")}`;
    }
    case "project.failed":
      return "Build stopped: " + String(detail.error || "unknown error").slice(0, 500);
    case "scenes.created":
      return "Planned scenes: " + (detail.scenes || []).join("; ");
    case "usage.summary":
      return `Model cost for this ${detail.operation || "run"}: $${(detail.cost_usd || 0).toFixed(2)} over ${detail.calls || 0} calls`;
    case "narration.started":
      return (detail.takes || 1) > 1
        ? `Recording the lecture in ${detail.takes} voice takes (${detail.scenes} scenes)`
        : `Recording the whole lesson in one voice take (${detail.scenes} scenes)`;
    case "narration.complete":
      return `Narration recorded: ${detail.seconds} s, voice ${detail.voice}`;
    case "narration.per_scene":
      return detail.reason || "Recording narration scene by scene";
    case "scene.rejected":
      return `${scene} rejected (attempt ${detail.attempt} of ${detail.max_attempts}): ${reasonText || "quality check failed"} → ${REPAIR[detail.next_action] || "repairing"}`;
    case "scene.complete":
      return `${scene} passed on attempt ${detail.attempt || 1}${detail.checks?.length ? ` (${detail.checks.join(", ")})` : ""}`;
    case "scene.degraded":
      return `${scene} kept with minor layout issues: ${reasonText}`;
    case "scene.blocked":
      return `${scene} could not be completed: ${reasonText}`;
    case "scene.code_failed":
      return `${scene}: animation model unavailable — ${reasonText}`;
    case "scene.timing_repaired":
      return `${scene}: retimed the animation to match the narration`;
    case "scene.review_repair":
      return `${scene}: rebuilding after the whole-lesson review — ${reasonText}`;
    case "video.ending":
      return "Added a settled ending (held final frame and fade-out)";
    case "lesson.reviewed": {
      const problems: string[] = detail.problems || [];
      return problems.length
        ? "Whole-lesson review notes: " + problems.slice(0, 3).join("; ")
        : "Whole-lesson review: scenes flow coherently and the ending resolves";
    }
    case "scene.started":
    case "scene.render_started":
    case "scene.verifying":
    case "scene.repairing":
      return null;
  }
  const label = LABELS[name];
  return label && scene ? `${label}: ${scene}` : (label ?? null);
}

function measure(progress: Progress) {
  const events = progress.events;
  const starts = new Set(["project.started", "preparation.started", "localization.started", "edit.started"]);
  let startIndex = 0;
  events.forEach((event, index) => {
    if (starts.has(event.event)) startIndex = index;
  });
  const current = events.slice(startIndex);
  const latest = current[current.length - 1];
  const name = latest?.event ?? "project.started";
  const scene = latest?.detail?.scene_key;
  let label = LABELS[name] ?? name.replace(/[._]/g, " ");
  if (scene) label = `${label}: ${scene}`;
  if (name === "scene.rejected" && latest) label = describe(latest) ?? label;
  let value = MILESTONES[name];
  if (value === undefined && (name.startsWith("scene.") || name.startsWith("narration."))) {
    // An edit rebuilds only its own scenes, not the whole video.
    const planned =
      [...current].reverse().find((event) => event.event === "scenes.created")?.detail?.scene_count ??
      current.find((event) => event.event === "edit.started")?.detail?.scenes?.length;
    const count = Math.max(Number(planned || progress.scene_count || 0), 1);
    const stages: Record<string, number> = {};
    for (const event of current) {
      const key = event.detail?.scene_key;
      if (key && SCENE_STAGE[event.event] !== undefined) stages[key] = SCENE_STAGE[event.event];
    }
    const sum = Object.values(stages).reduce((total, item) => total + item, 0);
    value = 0.45 + 0.47 * Math.min(sum / count, 1);
  }
  if (value === undefined) value = progress.status === "EDITING" ? 0.05 : 0.08;
  const log = current.map(describe).filter(Boolean) as string[];
  return { label, value, log };
}

/** Live status of a build, edit or translation, with cancel and retry. */
export function BuildProgress({ projectId, status, onSettled }: { projectId: string; status: string; onSettled: () => void }) {
  const toast = useToast();
  const client = useQueryClient();
  const [open, setOpen] = useState(true);
  const busy = BUSY.has(status);
  const progress = useQuery({
    queryKey: ["progress", projectId],
    queryFn: () => api<Progress>(`/v1/projects/${projectId}/progress`),
    refetchInterval: busy ? 4000 : false,
  });
  const previous = useRef(status);
  useEffect(() => {
    const now = progress.data?.status;
    if (now && BUSY.has(previous.current) && !BUSY.has(now)) onSettled();
    if (now) previous.current = now;
  }, [progress.data?.status, onSettled]);

  const cancel = useMutation({
    mutationFn: () => api(`/v1/projects/${projectId}/cancel`, { method: "POST" }),
    onSuccess: () => {
      toast("Cancellation requested", "info");
      void progress.refetch();
    },
    onError: (error) => toast(errorText(error), "error"),
  });
  const retry = useMutation({
    mutationFn: () => api(`/v1/projects/${projectId}/generate`, { method: "POST" }),
    onSuccess: () => {
      toast("Video generation restarted");
      void client.invalidateQueries({ queryKey: ["project", projectId] });
      void progress.refetch();
    },
    onError: (error) => toast(errorText(error), "error"),
  });

  const data = progress.data;
  if (!data) {
    return (
      <div className="flex items-center gap-2 rounded-2xl border border-line bg-surface p-4 text-sm text-ink-2">
        <Spinner size={16} /> Checking progress…
      </div>
    );
  }
  if (data.status === "CANCELLED") {
    return <p className="rounded-2xl border border-line bg-surface p-4 text-sm text-ink-2">Video generation was cancelled. You can keep chatting or ask for another video.</p>;
  }
  const { label, value, log } = measure(data);
  if (data.status === "FAILED") {
    return (
      <div className="rounded-2xl border border-danger/40 bg-danger/5 p-4">
        <p className="flex items-start gap-2 text-sm text-ink">
          <AlertTriangle size={18} className="mt-0.5 shrink-0 text-danger" />
          {data.failure_message || "The video build failed before saving a video."}
        </p>
        {log.length > 0 && (
          <details className="mt-3 text-sm">
            <summary className="cursor-pointer text-ink-2">What happened</summary>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-ink-2">
              {log.slice(-20).map((line, index) => (
                <li key={index}>{line}</li>
              ))}
            </ul>
          </details>
        )}
        <button className="btn-secondary btn-sm mt-3" onClick={() => retry.mutate()} disabled={retry.isPending}>
          <RotateCcw size={14} /> Try generating again
        </button>
      </div>
    );
  }
  if (!BUSY.has(data.status)) return null;
  const percent = Math.round(value * 100);
  return (
    <div className="overflow-hidden rounded-2xl border border-primary/30 bg-surface">
      <div className="flex items-center gap-3 p-4">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary/15 text-primary">
          <Clapperboard size={17} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium" role="status">
            {data.events.length ? label : "Your lesson is queued. Waiting for a worker to start."}
          </p>
          <div className="mt-2 flex items-center gap-3">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-raised">
              <div className="h-full rounded-full bg-gradient-to-r from-primary to-teal transition-all duration-700" style={{ width: `${percent}%` }} />
            </div>
            <span className="font-mono text-xs text-ink-2">{percent}%</span>
          </div>
        </div>
        {data.status === "CANCELLING" ? (
          <span className="text-xs text-muted">Stopping…</span>
        ) : (
          <button className="btn-ghost btn-sm" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
            <CircleStop size={14} /> Cancel
          </button>
        )}
      </div>
      {log.length > 0 && (
        <div className="border-t border-line">
          <button className="flex w-full items-center justify-between px-4 py-2 text-xs text-ink-2 hover:text-ink" onClick={() => setOpen(!open)}>
            What&apos;s happening ({log.length} steps)
            <ChevronDown size={14} className={open ? "rotate-180 transition" : "transition"} />
          </button>
          {open && (
            <ul className="max-h-56 space-y-1.5 overflow-y-auto px-4 pb-3 text-xs text-ink-2">
              {log.slice(-14).map((line, index) => (
                <li key={index} className="flex gap-2">
                  <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-primary" />
                  {line}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
