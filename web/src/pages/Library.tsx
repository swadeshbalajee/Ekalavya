import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  ArrowLeft,
  BookOpen,
  Download,
  ExternalLink,
  FileText,
  LayoutGrid,
  List,
  ListVideo,
  MessageSquare,
  NotebookPen,
  Pencil,
  Play,
  Plus,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Empty, Menu, Modal, Tabs } from "../components/common";
import { GridSkeleton, Thumbnail, VideoCard, VideoGrid } from "../components/VideoCard";
import { api, errorText, mediaUrl } from "../lib/api";
import { ago, clock } from "../lib/format";
import type { Note, Playlist, Source, VideoCard as Card } from "../lib/types";
import { useDebounced, useToast } from "../lib/ui";

type Tab = "videos" | "documents" | "notes" | "playlists";

export default function LibraryPage() {
  const { playlistId } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "videos";
  const [text, setText] = useState("");
  const q = useDebounced(text.trim(), 300);
  const navigate = useNavigate();
  if (playlistId) return <PlaylistView id={playlistId} />;

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:py-10">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="font-display text-2xl font-semibold sm:text-3xl">Your Library</h1>
          <p className="mt-1 text-sm text-ink-2">Your videos, saved explainers, documents, notes and playlists.</p>
        </div>
        <button className="btn-primary" onClick={() => navigate("/chat?attach=1")}>
          <Upload size={16} /> Upload a paper
        </button>
      </div>
      <div className="mt-6 flex flex-col gap-3 md:flex-row md:items-center">
        <Tabs
          value={tab}
          onChange={(value) => setParams({ tab: value }, { replace: true })}
          items={[
            { value: "videos", label: "Videos" },
            { value: "documents", label: "Documents" },
            { value: "notes", label: "Notes" },
            { value: "playlists", label: "Playlists" },
          ]}
        />
        {tab !== "playlists" && (
          <div className="relative md:ml-auto md:w-72">
            <Search size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted" />
            <input
              className="input pl-9"
              placeholder={`Search your ${tab}`}
              value={text}
              onChange={(event) => setText(event.target.value)}
            />
          </div>
        )}
      </div>
      <div className="mt-6">
        {tab === "videos" && <VideosTab q={q} />}
        {tab === "documents" && <DocumentsTab q={q} />}
        {tab === "notes" && <NotesTab q={q} />}
        {tab === "playlists" && <PlaylistsTab />}
      </div>
    </div>
  );
}

function VideosTab({ q }: { q: string }) {
  const [filter, setFilter] = useState<"all" | "mine" | "saved" | "public">("all");
  const [layout, setLayout] = useState<"grid" | "list">("grid");
  const videos = useQuery({
    queryKey: ["library", "videos", filter, q],
    queryFn: () => api<Card[]>("/v1/library/videos", { params: { filter, q } }),
  });
  return (
    <>
      <div className="mb-5 flex flex-wrap items-center gap-2">
        {(
          [
            ["all", "All"],
            ["mine", "Made by me"],
            ["public", "Published"],
            ["saved", "Saved"],
          ] as const
        ).map(([value, label]) => (
          <button key={value} className={clsx("chip", filter === value && "chip-active")} onClick={() => setFilter(value)}>
            {label}
          </button>
        ))}
        <div className="ml-auto flex rounded-xl border border-line bg-surface p-0.5">
          <button
            className={clsx("icon-btn h-8 w-8", layout === "grid" && "bg-raised text-ink")}
            onClick={() => setLayout("grid")}
            aria-label="Grid view"
          >
            <LayoutGrid size={15} />
          </button>
          <button
            className={clsx("icon-btn h-8 w-8", layout === "list" && "bg-raised text-ink")}
            onClick={() => setLayout("list")}
            aria-label="List view"
          >
            <List size={15} />
          </button>
        </div>
      </div>
      {videos.isLoading ? (
        <GridSkeleton count={8} />
      ) : !videos.data?.length ? (
        <Empty
          icon={<BookOpen size={22} />}
          title={q ? "Nothing matches" : "No videos yet"}
          action={
            <Link to="/chat" className="btn-primary btn-sm">
              Create a video
            </Link>
          }
        >
          {q ? "Try another word." : "Videos you make, and public ones you save, collect here."}
        </Empty>
      ) : layout === "grid" ? (
        <VideoGrid videos={videos.data} />
      ) : (
        <div className="space-y-2">
          {videos.data.map((video) => (
            <VideoCard key={video.id} video={video} compactLayout />
          ))}
        </div>
      )}
    </>
  );
}

function DocumentsTab({ q }: { q: string }) {
  const documents = useQuery({
    queryKey: ["library", "documents", q],
    queryFn: () => api<Source[]>("/v1/library/documents", { params: { q } }),
  });
  if (documents.isLoading) return <div className="skeleton h-40" />;
  if (!documents.data?.length) {
    return (
      <Empty
        icon={<FileText size={22} />}
        title="No documents"
        action={
          <Link to="/chat?attach=1" className="btn-primary btn-sm">
            Attach a paper
          </Link>
        }
      >
        Papers and links you attach to a chat appear here.
      </Empty>
    );
  }
  return (
    <div className="card divide-y divide-line">
      {documents.data.map((document) => (
        <div key={document.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-success/12 text-success">
            <FileText size={18} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium">{document.title}</p>
            <p className="truncate text-xs text-muted">
              In “{document.chat_title}” · {ago(document.created_at)}
            </p>
          </div>
          <div className="flex gap-1">
            <Link to={`/chat/${document.chat_id}`} className="btn-ghost btn-sm">
              <MessageSquare size={14} /> Chat
            </Link>
            {document.has_video && (
              <Link to={`/watch/${document.chat_id}`} className="btn-ghost btn-sm">
                <Play size={14} /> Video
              </Link>
            )}
            {document.url && (
              <a className="btn-ghost btn-sm" href={document.url} target="_blank" rel="noreferrer">
                <ExternalLink size={14} /> Open
              </a>
            )}
            {document.downloadable && (
              <a className="btn-ghost btn-sm" href={mediaUrl(`/v1/videos/${document.chat_id}/sources/${document.id}/file`)}>
                <Download size={14} /> File
              </a>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function NotesTab({ q }: { q: string }) {
  const notes = useQuery({
    queryKey: ["library", "notes", q],
    queryFn: () => api<Note[]>("/v1/library/notes", { params: { q } }),
  });
  if (notes.isLoading) return <div className="skeleton h-40" />;
  if (!notes.data?.length) {
    return (
      <Empty icon={<NotebookPen size={22} />} title="No notes yet">
        Add notes while watching a video; each is pinned to its moment.
      </Empty>
    );
  }
  const groups = new Map<string, Note[]>();
  for (const note of notes.data) groups.set(note.video_id, [...(groups.get(note.video_id) ?? []), note]);
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      {[...groups.entries()].map(([videoId, items]) => (
        <div key={videoId} className="card overflow-hidden">
          <Link to={`/watch/${videoId}`} className="flex items-center gap-3 border-b border-line p-3 hover:bg-raised/50">
            <Thumbnail id={videoId} title={items[0].video_title ?? ""} className="w-24 shrink-0 rounded-lg" />
            <p className="line-clamp-2 text-sm font-medium">{items[0].video_title}</p>
          </Link>
          <div className="divide-y divide-line">
            {items.map((note) => (
              <Link
                key={note.id}
                to={`/watch/${videoId}?t=${Math.floor(note.at_seconds)}`}
                className="flex gap-3 px-4 py-2.5 text-sm hover:bg-raised/50"
              >
                <span className="font-mono text-xs text-teal">{clock(note.at_seconds)}</span>
                <span className="line-clamp-3 text-ink-2">{note.text}</span>
              </Link>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function PlaylistsTab() {
  const client = useQueryClient();
  const toast = useToast();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const playlists = useQuery({
    queryKey: ["library", "playlists"],
    queryFn: () => api<Playlist[]>("/v1/library/playlists"),
  });
  const create = useMutation({
    mutationFn: () => api<Playlist>("/v1/library/playlists", { body: { name: name.trim() } }),
    onSuccess: () => {
      setCreating(false);
      setName("");
      void client.invalidateQueries({ queryKey: ["library", "playlists"] });
    },
    onError: (error) => toast(errorText(error), "error"),
  });
  return (
    <>
      <div className="mb-5 flex justify-end">
        <button className="btn-secondary btn-sm" onClick={() => setCreating(true)}>
          <Plus size={14} /> New playlist
        </button>
      </div>
      {playlists.isLoading ? (
        <GridSkeleton count={4} />
      ) : !playlists.data?.length ? (
        <Empty icon={<ListVideo size={22} />} title="No playlists yet">
          Group videos into playlists from any video page, or create one here.
        </Empty>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
          {playlists.data.map((playlist) => (
            <Link
              key={playlist.id}
              to={`/library/playlists/${playlist.id}`}
              className="group overflow-hidden rounded-2xl border border-line bg-surface transition hover:border-primary/50"
            >
              {playlist.cover_id ? (
                <Thumbnail id={playlist.cover_id} title={playlist.name}>
                  <span className="absolute inset-y-0 right-0 flex w-1/3 flex-col items-center justify-center bg-black/65 text-white backdrop-blur">
                    <ListVideo size={20} />
                    <span className="mt-1 font-mono text-sm">{playlist.count}</span>
                  </span>
                </Thumbnail>
              ) : (
                <div className="flex aspect-video items-center justify-center bg-raised text-muted">
                  <ListVideo size={28} />
                </div>
              )}
              <div className="p-3.5">
                <p className="font-medium">{playlist.name}</p>
                <p className="text-xs text-muted">
                  {playlist.count} videos · updated {ago(playlist.updated_at)}
                </p>
              </div>
            </Link>
          ))}
        </div>
      )}
      <Modal
        open={creating}
        onClose={() => setCreating(false)}
        title="New playlist"
        footer={
          <button className="btn-primary" disabled={!name.trim() || create.isPending} onClick={() => create.mutate()}>
            Create
          </button>
        }
      >
        <input
          className="input"
          autoFocus
          placeholder="e.g. Board exam physics"
          value={name}
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && name.trim() && create.mutate()}
        />
      </Modal>
    </>
  );
}

function PlaylistView({ id }: { id: string }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const [renaming, setRenaming] = useState<string | null>(null);
  const playlist = useQuery({
    queryKey: ["library", "playlist", id],
    queryFn: () => api<Playlist>(`/v1/library/playlists/${id}`),
  });
  const refresh = () => void client.invalidateQueries({ queryKey: ["library"] });
  const remove = useMutation({
    mutationFn: (videoId: string) => api(`/v1/library/playlists/${id}/items/${videoId}`, { method: "DELETE" }),
    onSuccess: refresh,
  });
  const rename = useMutation({
    mutationFn: (name: string) => api(`/v1/library/playlists/${id}`, { method: "PATCH", body: { name } }),
    onSuccess: () => {
      setRenaming(null);
      refresh();
    },
    onError: (error) => toast(errorText(error), "error"),
  });
  const destroy = useMutation({
    mutationFn: () => api(`/v1/library/playlists/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      refresh();
      navigate("/library?tab=playlists");
    },
  });
  const data = playlist.data;
  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
      <Link to="/library?tab=playlists" className="btn-ghost btn-sm -ml-3">
        <ArrowLeft size={14} /> Playlists
      </Link>
      <div className="mt-3 flex items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-2xl font-semibold">{data?.name ?? "…"}</h1>
          <p className="text-sm text-ink-2">{data?.count ?? 0} videos</p>
        </div>
        <div className="flex gap-2">
          {data?.items?.length ? (
            <Link to={`/watch/${data.items[0].id}`} className="btn-primary btn-sm">
              <Play size={14} /> Play
            </Link>
          ) : null}
          <Menu
            items={[
              { label: "Rename", icon: <Pencil size={14} />, onSelect: () => setRenaming(data?.name ?? "") },
              { label: "Delete playlist", icon: <Trash2 size={14} />, danger: true, onSelect: () => destroy.mutate() },
            ]}
            trigger={<Pencil size={16} />}
          />
        </div>
      </div>
      <div className="mt-6 space-y-2">
        {data?.items?.map((video, index) => (
          <div key={video.id} className="flex items-center gap-2">
            <span className="w-6 text-center font-mono text-xs text-muted">{index + 1}</span>
            <div className="flex-1">
              <VideoCard video={video} compactLayout />
            </div>
            <button className="icon-btn" aria-label="Remove from playlist" onClick={() => remove.mutate(video.id)}>
              <Trash2 size={15} />
            </button>
          </div>
        ))}
        {data && !data.items?.length && (
          <Empty icon={<ListVideo size={22} />} title="This playlist is empty">
            Open any video and choose “Playlist” to add it here.
          </Empty>
        )}
      </div>
      <Modal
        open={renaming !== null}
        onClose={() => setRenaming(null)}
        title="Rename playlist"
        footer={
          <button className="btn-primary" onClick={() => renaming?.trim() && rename.mutate(renaming.trim())}>
            Save
          </button>
        }
      >
        <input className="input" autoFocus value={renaming ?? ""} onChange={(event) => setRenaming(event.target.value)} />
      </Modal>
    </div>
  );
}
