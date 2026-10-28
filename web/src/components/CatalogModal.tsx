import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Globe2, Lock, Sparkles } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "../lib/api";
import type { VideoCard } from "../lib/types";
import { useToast } from "../lib/ui";
import { Button, Field, Modal, Spinner } from "./common";

/** Publish or unpublish a video, and edit what the public catalog shows about it. */
export function CatalogModal({ video, onClose }: { video: VideoCard; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState({
    visibility: video.visibility,
    title: video.title,
    description: video.description ?? "",
    subject: video.subject ?? "",
    tags: video.tags.join(", "),
    level: video.level ?? "",
  });
  const set = (changes: Partial<typeof form>) => setForm((value) => ({ ...value, ...changes }));
  const refresh = () => {
    for (const key of [["video", video.id], ["library"], ["videos"], ["subjects"], ["home"], ["projects"]]) {
      void client.invalidateQueries({ queryKey: key });
    }
  };

  const describe = useMutation({
    mutationFn: () => api<VideoCard>(`/v1/videos/${video.id}/describe`, { method: "POST" }),
    onSuccess: (card) => {
      set({
        title: card.title,
        description: card.description ?? "",
        subject: card.subject ?? "",
        tags: card.tags.join(", "),
        level: card.level ?? "",
      });
      refresh();
    },
    onError: (error) => toast(errorText(error), "error"),
  });

  const save = useMutation({
    mutationFn: () =>
      api<VideoCard>(`/v1/videos/${video.id}/catalog`, {
        method: "PATCH",
        body: {
          visibility: form.visibility,
          title: form.title.trim() || video.title,
          description: form.description,
          subject: form.subject,
          level: form.level,
          tags: form.tags.split(",").map((tag) => tag.trim()).filter(Boolean),
        },
      }),
    onSuccess: (card) => {
      toast(card.visibility === "public" ? "Published — anyone can find it in Discover" : "Saved");
      refresh();
      onClose();
    },
    onError: (error) => toast(errorText(error), "error"),
  });

  return (
    <Modal
      open
      onClose={onClose}
      title={video.visibility === "public" ? "Video details" : "Publish video"}
      footer={
        <>
          <button className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <Button busy={save.isPending} onClick={() => save.mutate()}>
            {form.visibility === "public" && video.visibility !== "public" ? "Publish" : "Save"}
          </Button>
        </>
      }
    >
      <div className="grid grid-cols-2 gap-2">
        {(
          [
            ["public", Globe2, "Public", "Anyone can watch and find it in search"],
            ["private", Lock, "Private", "Only you can open it"],
          ] as const
        ).map(([value, Icon, label, text]) => (
          <button
            key={value}
            onClick={() => set({ visibility: value })}
            className={`rounded-xl border p-3 text-left transition ${
              form.visibility === value ? "border-primary bg-primary/10" : "border-line hover:border-primary/40"
            }`}
          >
            <Icon size={18} className={form.visibility === value ? "text-primary" : "text-muted"} />
            <p className="mt-2 text-sm font-medium">{label}</p>
            <p className="text-xs text-ink-2">{text}</p>
          </button>
        ))}
      </div>
      <div className="mt-5 flex items-center justify-between">
        <p className="text-sm text-ink-2">How learners will find it</p>
        <button className="btn-ghost btn-sm" onClick={() => describe.mutate()} disabled={describe.isPending}>
          {describe.isPending ? <Spinner size={14} /> : <Sparkles size={14} className="text-primary" />}
          Write with AI
        </button>
      </div>
      <div className="mt-3 space-y-4">
        <Field label="Title">
          <input className="input" value={form.title} onChange={(event) => set({ title: event.target.value })} />
        </Field>
        <Field label="Description">
          <textarea
            className="input min-h-[90px]"
            value={form.description}
            placeholder="Leave empty and Ekalavya writes it when you publish"
            onChange={(event) => set({ description: event.target.value })}
          />
        </Field>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Subject">
            <input
              className="input"
              value={form.subject}
              placeholder="e.g. Physics"
              onChange={(event) => set({ subject: event.target.value })}
            />
          </Field>
          <Field label="Level">
            <input
              className="input"
              value={form.level}
              placeholder="e.g. Class 11"
              onChange={(event) => set({ level: event.target.value })}
            />
          </Field>
        </div>
        <Field label="Keywords (comma separated)">
          <input
            className="input"
            value={form.tags}
            placeholder="wavelength, frequency, optics"
            onChange={(event) => set({ tags: event.target.value })}
          />
        </Field>
      </div>
    </Modal>
  );
}
