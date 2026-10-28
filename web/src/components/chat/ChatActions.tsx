import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  ArchiveRestore,
  Copy,
  FolderInput,
  Languages,
  Link2,
  MoreHorizontal,
  Pencil,
  Pin,
  PinOff,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, errorText } from "../../lib/api";
import { LANGUAGES } from "../../lib/format";
import type { Folder, Project } from "../../lib/types";
import { copyText, useToast } from "../../lib/ui";
import { Button, Field, Menu, Modal } from "../common";

type Dialog = "rename" | "move" | "delete" | "settings" | null;

/** The ⋯ menu for a chat, with its rename, move, settings and delete dialogs. */
export function ChatActions({ project, folders }: { project: Project; folders: Folder[] }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const [dialog, setDialog] = useState<Dialog>(null);
  const [title, setTitle] = useState(project.title);
  const [target, setTarget] = useState<string>(project.folder_id ?? "");
  const [settings, setSettings] = useState({
    target_duration_minutes: project.target_duration_minutes,
    text_language: project.text_language,
    voice_language: project.voice_language,
  });
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["projects"] });
    void client.invalidateQueries({ queryKey: ["folders"] });
    void client.invalidateQueries({ queryKey: ["project", project.id] });
  };
  const patch = useMutation({
    mutationFn: (changes: Partial<Project>) => api(`/v1/projects/${project.id}`, { method: "PATCH", body: changes }),
    onSuccess: () => {
      setDialog(null);
      refresh();
    },
    onError: (error) => toast(errorText(error), "error"),
  });
  const move = useMutation({
    mutationFn: () => api(`/v1/projects/${project.id}/move`, { body: { folder_id: target || null } }),
    onSuccess: () => {
      setDialog(null);
      refresh();
    },
    onError: (error) => toast(errorText(error), "error"),
  });
  const copy = useMutation({
    mutationFn: () => api<Project>(`/v1/projects/${project.id}/copy`, { body: { folder_id: project.folder_id } }),
    onSuccess: (copied) => {
      refresh();
      navigate(`/chat/${copied.id}`);
    },
    onError: (error) => toast(errorText(error), "error"),
  });
  const remove = useMutation({
    mutationFn: () => api(`/v1/projects/${project.id}`, { method: "DELETE" }),
    onSuccess: () => {
      setDialog(null);
      refresh();
      navigate("/chat");
    },
    onError: (error) => toast(errorText(error), "error"),
  });
  const saveSettings = useMutation({
    mutationFn: () =>
      api<{ workflow_id: string | null }>(`/v1/projects/${project.id}/settings`, { method: "PATCH", body: settings }),
    onSuccess: (result) => {
      setDialog(null);
      toast(result.workflow_id ? "Translating the video into the new languages" : "Chat settings saved");
      refresh();
    },
    onError: (error) => toast(errorText(error), "error"),
  });

  return (
    <>
      <Menu
        trigger={<MoreHorizontal size={16} />}
        label={`Actions for ${project.title}`}
        items={[
          { label: "Rename", icon: <Pencil size={15} />, onSelect: () => setDialog("rename") },
          {
            label: project.pinned ? "Unpin" : "Pin",
            icon: project.pinned ? <PinOff size={15} /> : <Pin size={15} />,
            onSelect: () => patch.mutate({ pinned: !project.pinned }),
          },
          { label: "Move to project", icon: <FolderInput size={15} />, onSelect: () => setDialog("move") },
          { label: "Languages & length", icon: <Languages size={15} />, onSelect: () => setDialog("settings") },
          { label: "Make a copy", icon: <Copy size={15} />, onSelect: () => copy.mutate() },
          {
            label: "Copy link",
            icon: <Link2 size={15} />,
            onSelect: () =>
              void copyText(`${window.location.origin}/chat/${project.id}`).then(() =>
                toast("Link copied — it opens for your account only"),
              ),
          },
          {
            label: project.archived ? "Unarchive" : "Archive",
            icon: project.archived ? <ArchiveRestore size={15} /> : <Archive size={15} />,
            onSelect: () => patch.mutate({ archived: !project.archived }),
          },
          { label: "Delete", icon: <Trash2 size={15} />, danger: true, onSelect: () => setDialog("delete") },
        ]}
      />
      <Modal
        open={dialog === "rename"}
        onClose={() => setDialog(null)}
        title="Rename chat"
        footer={
          <Button busy={patch.isPending} disabled={!title.trim()} onClick={() => patch.mutate({ title: title.trim() })}>
            Save
          </Button>
        }
      >
        <input
          className="input"
          autoFocus
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && title.trim() && patch.mutate({ title: title.trim() })}
        />
      </Modal>
      <Modal
        open={dialog === "move"}
        onClose={() => setDialog(null)}
        title="Move to project"
        footer={
          <Button busy={move.isPending} onClick={() => move.mutate()}>
            Move
          </Button>
        }
      >
        <select className="input" value={target} onChange={(event) => setTarget(event.target.value)}>
          <option value="">No project</option>
          {folders.map((folder) => (
            <option key={folder.id} value={folder.id}>
              {folder.name}
            </option>
          ))}
        </select>
      </Modal>
      <Modal
        open={dialog === "settings"}
        onClose={() => setDialog(null)}
        title="Languages & length"
        footer={
          <Button busy={saveSettings.isPending} onClick={() => saveSettings.mutate()}>
            Save
          </Button>
        }
      >
        <div className="space-y-4">
          <p className="text-sm text-ink-2">
            Changing a language on a finished video translates and re-voices it; the earlier version is kept.
          </p>
          <Field label="Target length (minutes)">
            <input
              className="input"
              type="number"
              min={0.5}
              max={30}
              step={0.5}
              value={settings.target_duration_minutes}
              onChange={(event) => setSettings({ ...settings, target_duration_minutes: Number(event.target.value) })}
            />
          </Field>
          <Field label="Text in video">
            <select
              className="input"
              value={settings.text_language}
              onChange={(event) => setSettings({ ...settings, text_language: event.target.value })}
            >
              {[...new Set([settings.text_language, ...LANGUAGES])].map((language) => (
                <option key={language}>{language}</option>
              ))}
            </select>
          </Field>
          <Field label="Narration voice">
            <select
              className="input"
              value={settings.voice_language}
              onChange={(event) => setSettings({ ...settings, voice_language: event.target.value })}
            >
              {[...new Set([settings.voice_language, ...LANGUAGES])].map((language) => (
                <option key={language}>{language}</option>
              ))}
            </select>
          </Field>
        </div>
      </Modal>
      <Modal
        open={dialog === "delete"}
        onClose={() => setDialog(null)}
        title="Delete chat?"
        footer={
          <>
            <button className="btn-secondary" onClick={() => setDialog(null)}>
              Cancel
            </button>
            <Button className="btn-danger" busy={remove.isPending} onClick={() => remove.mutate()}>
              Delete
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink-2">
          “{project.title}” and its videos, notes and sources will be permanently deleted
          {project.visibility === "public" ? ", and it will disappear from Discover" : ""}.
        </p>
      </Modal>
    </>
  );
}
