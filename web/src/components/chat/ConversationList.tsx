import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  ChevronRight,
  Clapperboard,
  Folder as FolderIcon,
  FolderPlus,
  MessageSquare,
  Pencil,
  Pin,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, errorText } from "../../lib/api";
import { BUSY } from "../../lib/format";
import type { Folder, Project } from "../../lib/types";
import { useDebounced, useToast } from "../../lib/ui";
import { Button, Menu, Modal, Spinner } from "../common";
import { ChatActions } from "./ChatActions";

function Row({ project, active, folders }: { project: Project; active: boolean; folders: Folder[] }) {
  return (
    <div
      className={clsx(
        "group flex items-center gap-1 rounded-xl pr-1 transition",
        active ? "bg-primary/15 ring-1 ring-primary/35" : "hover:bg-raised",
      )}
    >
      <Link to={`/chat/${project.id}`} className="flex min-w-0 flex-1 items-center gap-2.5 px-3 py-2 text-sm">
        {BUSY.has(project.status) ? (
          <Spinner size={14} className="shrink-0 text-primary" />
        ) : project.video_path ? (
          <Clapperboard size={14} className="shrink-0 text-muted" />
        ) : (
          <MessageSquare size={14} className="shrink-0 text-muted" />
        )}
        <span className={clsx("truncate", active ? "text-ink" : "text-ink-2 group-hover:text-ink")}>{project.title}</span>
        {project.pinned && <Pin size={11} className="shrink-0 text-muted" />}
      </Link>
      <div className="opacity-100 transition lg:opacity-0 lg:group-hover:opacity-100 lg:group-focus-within:opacity-100">
        <ChatActions project={project} folders={folders} />
      </div>
    </div>
  );
}

export function ConversationList({ activeId, folderId }: { activeId?: string; folderId?: string | null }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const [search, setSearch] = useState("");
  const query = useDebounced(search.trim(), 300);
  const [open, setOpen] = useState<Set<string>>(new Set(folderId ? [folderId] : []));
  const [showArchived, setShowArchived] = useState(false);
  const [folderDialog, setFolderDialog] = useState<{ id?: string; name: string } | null>(null);

  const projects = useQuery({
    queryKey: ["projects", query],
    queryFn: () => api<Project[]>("/v1/projects", { params: { query } }),
    refetchInterval: 15000,
  });
  const archived = useQuery({
    queryKey: ["projects", "archived"],
    queryFn: () => api<Project[]>("/v1/projects", { params: { archived: true } }),
    enabled: showArchived,
  });
  const folders = useQuery({ queryKey: ["folders"], queryFn: () => api<Folder[]>("/v1/project-folders") });
  const saveFolder = useMutation({
    mutationFn: (value: { id?: string; name: string }) =>
      value.id
        ? api(`/v1/project-folders/${value.id}`, { method: "PATCH", body: { name: value.name } })
        : api<Folder>("/v1/project-folders", { body: { name: value.name } }),
    onSuccess: () => {
      setFolderDialog(null);
      void client.invalidateQueries({ queryKey: ["folders"] });
    },
    onError: (error) => toast(errorText(error), "error"),
  });
  const deleteFolder = useMutation({
    mutationFn: (id: string) => api(`/v1/project-folders/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["folders"] });
      void client.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  const list = projects.data ?? [];
  const folderList = folders.data ?? [];
  const loose = list.filter((project) => !project.folder_id || query);

  return (
    <div className="flex h-full flex-col">
      <button className="btn-primary w-full" onClick={() => navigate("/chat")}>
        <Plus size={17} /> New chat
      </button>
      <div className="relative mt-3">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
        <input
          className="input py-2 pl-8 text-xs"
          placeholder="Search chats"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>
      <div className="-mx-1 mt-4 flex-1 space-y-5 overflow-y-auto px-1 pb-4">
        {!query && (
          <section>
            <div className="mb-1 flex items-center justify-between px-2">
              <span className="eyebrow">Projects</span>
              <button className="icon-btn h-7 w-7" onClick={() => setFolderDialog({ name: "" })} aria-label="New project">
                <FolderPlus size={14} />
              </button>
            </div>
            {folderList.length === 0 && <p className="px-2 text-xs text-muted">Group related chats into projects.</p>}
            {folderList.map((folder) => {
              const expanded = open.has(folder.id);
              const chats = list.filter((project) => project.folder_id === folder.id);
              return (
                <div key={folder.id}>
                  <div className="group flex items-center rounded-xl pr-1 hover:bg-raised">
                    <button
                      className="flex min-w-0 flex-1 items-center gap-2 px-2 py-2 text-sm text-ink-2"
                      onClick={() =>
                        setOpen((value) => {
                          const next = new Set(value);
                          if (next.has(folder.id)) next.delete(folder.id);
                          else next.add(folder.id);
                          return next;
                        })
                      }
                    >
                      <ChevronRight size={14} className={clsx("shrink-0 transition", expanded && "rotate-90")} />
                      <FolderIcon size={14} className="shrink-0" />
                      <span className="truncate">{folder.name}</span>
                      <span className="ml-auto font-mono text-[10px] text-muted">{folder.chat_count}</span>
                    </button>
                    <Menu
                      trigger={<Pencil size={13} />}
                      label={`Actions for project ${folder.name}`}
                      items={[
                        {
                          label: "New chat here",
                          icon: <Plus size={14} />,
                          onSelect: () => navigate(`/chat?folder=${folder.id}`),
                        },
                        { label: "Rename", icon: <Pencil size={14} />, onSelect: () => setFolderDialog({ id: folder.id, name: folder.name }) },
                        { label: "Delete project", icon: <Trash2 size={14} />, danger: true, onSelect: () => deleteFolder.mutate(folder.id) },
                      ]}
                    />
                  </div>
                  {expanded && (
                    <div className="ml-4 space-y-0.5 border-l border-line pl-2">
                      <button
                        className="flex w-full items-center gap-2 rounded-xl px-3 py-1.5 text-xs text-muted hover:bg-raised hover:text-ink"
                        onClick={() => navigate(`/chat?folder=${folder.id}`)}
                      >
                        <Plus size={12} /> New chat in {folder.name}
                      </button>
                      {chats.map((project) => (
                        <Row key={project.id} project={project} active={project.id === activeId} folders={folderList} />
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </section>
        )}
        <section>
          <p className="eyebrow mb-1 px-2">{query ? "Results" : "Chats"}</p>
          {projects.isLoading ? (
            <div className="space-y-2 px-1">
              {[0, 1, 2, 3].map((index) => (
                <div key={index} className="skeleton h-8" />
              ))}
            </div>
          ) : loose.length ? (
            <div className="space-y-0.5">
              {loose.map((project) => (
                <Row key={project.id} project={project} active={project.id === activeId} folders={folderList} />
              ))}
            </div>
          ) : (
            <p className="px-2 text-xs text-muted">{query ? "No chats match." : "Your conversations will appear here."}</p>
          )}
        </section>
        {!query && (
          <section>
            <button className="eyebrow mb-1 flex items-center gap-1 px-2 hover:text-ink" onClick={() => setShowArchived(!showArchived)}>
              <ChevronRight size={12} className={clsx("transition", showArchived && "rotate-90")} /> Archived
            </button>
            {showArchived && (
              <div className="space-y-0.5">
                {(archived.data ?? []).map((project) => (
                  <Row key={project.id} project={project} active={project.id === activeId} folders={folderList} />
                ))}
                {archived.data?.length === 0 && <p className="px-2 text-xs text-muted">Nothing archived.</p>}
              </div>
            )}
          </section>
        )}
      </div>
      <Modal
        open={!!folderDialog}
        onClose={() => setFolderDialog(null)}
        title={folderDialog?.id ? "Rename project" : "New project"}
        footer={
          <Button
            busy={saveFolder.isPending}
            disabled={!folderDialog?.name.trim()}
            onClick={() => folderDialog && saveFolder.mutate({ ...folderDialog, name: folderDialog.name.trim() })}
          >
            {folderDialog?.id ? "Save" : "Create project"}
          </Button>
        }
      >
        <p className="mb-3 text-sm text-ink-2">A project holds related chats, sources and lesson videos.</p>
        <input
          className="input"
          autoFocus
          placeholder="e.g. Linear algebra course"
          value={folderDialog?.name ?? ""}
          onChange={(event) => setFolderDialog((value) => value && { ...value, name: event.target.value })}
        />
      </Modal>
    </div>
  );
}
