import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  Bookmark,
  Download,
  ExternalLink,
  Eye,
  FileText,
  Globe2,
  GraduationCap,
  ListPlus,
  Lock,
  MessageSquare,
  Pencil,
  Plus,
  Search,
  Share2,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { CatalogModal } from "../components/CatalogModal";
import { Empty, Menu, Modal, Spinner, Tabs } from "../components/common";
import { PlaylistPicker } from "../components/PlaylistPicker";
import { VideoCard } from "../components/VideoCard";
import { VideoPlayer, type PlayerHandle } from "../components/VideoPlayer";
import { api, errorText, mediaUrl } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ago, clock, compact } from "../lib/format";
import type { Cue, Note, Quiz, Source, VideoCard as Card, VideoDetail } from "../lib/types";
import { copyText, useToast } from "../lib/ui";

type Tab = "notes" | "transcript" | "resources" | "practice";

export default function WatchPage() {
  const { videoId = "" } = useParams();
  const [params] = useSearchParams();
  const { user } = useAuth();
  const client = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const player = useRef<PlayerHandle>(null);
  const [now, setNow] = useState(0);
  const [tab, setTab] = useState<Tab>(user ? "notes" : "transcript");
  const [editing, setEditing] = useState(false);
  const [picking, setPicking] = useState(false);
  const lastSaved = useRef(0);
  const taskId = params.get("task");

  const detail = useQuery({
    queryKey: ["video", videoId],
    queryFn: () => api<VideoDetail>(`/v1/videos/${videoId}`),
  });
  const video = detail.data;
  const related = useQuery({
    queryKey: ["related", videoId],
    queryFn: () => api<Card[]>(`/v1/videos/${videoId}/related`),
    enabled: !!video,
  });

  const startAt = useMemo(() => {
    const fromUrl = Number(params.get("t"));
    if (fromUrl > 0) return fromUrl;
    const saved = video?.progress;
    return saved && !saved.completed && saved.position > 5 ? saved.position : undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [video?.id]);

  useEffect(() => {
    if (!video || video.is_owner) return;
    const key = `ekalavya-viewed-${video.id}`;
    try {
      if (sessionStorage.getItem(key)) return;
      sessionStorage.setItem(key, "1");
    } catch {
      /* count it anyway */
    }
    void api(`/v1/videos/${video.id}/view`, { method: "POST" }).catch(() => {});
  }, [video]);

  const saveProgress = (position: number, duration: number, force = false) => {
    if (!user || !video || !Number.isFinite(duration) || !duration) return;
    if (!force && Math.abs(position - lastSaved.current) < 10) return;
    lastSaved.current = position;
    void api<{ completed: boolean }>(`/v1/videos/${video.id}/progress`, {
      method: "PUT",
      body: { position_seconds: position, duration_seconds: duration },
    })
      .then((result) => {
        if (result.completed && taskId) {
          void api(`/v1/study-plans/tasks/${taskId}`, { method: "PATCH", body: { done: true } }).then(
            () => {
              toast("Study task marked done");
              void client.invalidateQueries({ queryKey: ["study"] });
            },
          );
        }
        void client.invalidateQueries({ queryKey: ["home"] });
      })
      .catch(() => {});
  };

  const save = useMutation({
    mutationFn: (saved: boolean) =>
      api(`/v1/videos/${videoId}/save`, { method: saved ? "PUT" : "DELETE" }),
    onSuccess: (_, saved) => {
      toast(saved ? "Saved to your library" : "Removed from your library");
      void client.invalidateQueries({ queryKey: ["video", videoId] });
      void client.invalidateQueries({ queryKey: ["library"] });
    },
    onError: (error) => toast(errorText(error), "error"),
  });

  if (detail.isLoading) {
    return (
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        <div className="skeleton aspect-video w-full lg:w-[70%]" />
      </div>
    );
  }
  if (!video) {
    return (
      <div className="mx-auto max-w-xl px-4 py-16">
        <Empty icon={<Lock size={22} />} title="This video isn't available">
          It may be private, or it was removed. {user ? "" : "Signing in may help if it's yours."}
        </Empty>
      </div>
    );
  }

  const active = video.chapters.findIndex((chapter) => now >= chapter.start && now < chapter.end);
  const share = async () => {
    const url = `${window.location.origin}/watch/${video.id}`;
    if (navigator.share) {
      try {
        await navigator.share({ title: video.title, url });
        return;
      } catch {
        /* fall back to copying */
      }
    }
    if (await copyText(url)) {
      toast(video.visibility === "public" ? "Link copied" : "Link copied — only you can open it until you publish");
    }
  };

  return (
    <div className="mx-auto max-w-[1500px] px-4 py-6 sm:px-6">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0">
          <VideoPlayer
            ref={player}
            videoId={video.id}
            chapters={video.chapters}
            subtitleLanguage={video.voice_language}
            startAt={startAt}
            onTime={(seconds, duration) => {
              setNow(seconds);
              saveProgress(seconds, duration);
            }}
            onPause={(seconds, duration) => saveProgress(seconds, duration, true)}
          />

          <div className="mt-5 flex flex-col gap-3">
            <div className="min-w-0">
              <h1 className="font-display text-xl font-semibold sm:text-2xl">{video.title}</h1>
              <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-2">
                <span>{video.is_owner ? "Your video" : video.author}</span>
                {video.visibility === "public" ? (
                  <span className="inline-flex items-center gap-1">
                    <Eye size={14} /> {compact(video.views)} views
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-amber">
                    <Lock size={14} /> Private
                  </span>
                )}
                <span>{ago(video.published_at || video.created_at)}</span>
                <span>
                  {video.voice_language === video.text_language
                    ? video.voice_language
                    : `${video.voice_language} voice · ${video.text_language} text`}
                </span>
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {video.is_owner ? (
                <>
                  <button className="btn-primary btn-sm" onClick={() => setEditing(true)}>
                    {video.visibility === "public" ? <Pencil size={14} /> : <Globe2 size={14} />}
                    {video.visibility === "public" ? "Edit details" : "Publish"}
                  </button>
                  <Link to={`/chat/${video.id}`} className="btn-secondary btn-sm">
                    <MessageSquare size={14} /> Open chat
                  </Link>
                </>
              ) : (
                user && (
                  <button
                    className={clsx("btn-sm", video.saved ? "btn-primary" : "btn-secondary")}
                    onClick={() => save.mutate(!video.saved)}
                    disabled={save.isPending}
                  >
                    <Bookmark size={14} fill={video.saved ? "currentColor" : "none"} />
                    {video.saved ? "Saved" : "Save"}
                  </button>
                )
              )}
              {user && (
                <button className="btn-secondary btn-sm" onClick={() => setPicking(true)}>
                  <ListPlus size={14} /> Playlist
                </button>
              )}
              <button className="btn-secondary btn-sm" onClick={share}>
                <Share2 size={14} /> Share
              </button>
              <a
                className="btn-secondary btn-sm"
                href={mediaUrl(`/v1/videos/${video.id}/video`)}
                download={`${video.title}.mp4`}
              >
                <Download size={14} /> Download
              </a>
            </div>
          </div>

          {(video.description || video.tags.length > 0) && (
            <div className="mt-4 rounded-2xl bg-surface p-4 ring-1 ring-line">
              {video.description && <p className="text-sm leading-6 text-ink-2">{video.description}</p>}
              {video.tags.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {video.subject && (
                    <Link to={`/discover?subject=${encodeURIComponent(video.subject)}`} className="chip border-teal/40 text-teal">
                      {video.subject}
                    </Link>
                  )}
                  {video.tags.slice(0, 12).map((tag) => (
                    <Link key={tag} to={`/discover?q=${encodeURIComponent(tag)}`} className="chip">
                      {tag}
                    </Link>
                  ))}
                </div>
              )}
            </div>
          )}

          <div className="mt-6">
            <Tabs
              value={tab}
              onChange={setTab}
              items={[
                ...(user ? [{ value: "notes" as Tab, label: "Notes" }] : []),
                { value: "transcript" as Tab, label: "Transcript" },
                { value: "resources" as Tab, label: "Resources" },
                ...(user ? [{ value: "practice" as Tab, label: "Practice" }] : []),
              ]}
              className="w-fit"
            />
            <div className="mt-4">
              {tab === "notes" && user && (
                <NotesPanel videoId={video.id} now={now} onSeek={(time) => player.current?.seek(time)} />
              )}
              {tab === "transcript" && (
                <TranscriptPanel videoId={video.id} now={now} onSeek={(time) => player.current?.seek(time)} />
              )}
              {tab === "resources" && <ResourcesPanel videoId={video.id} />}
              {tab === "practice" && user && (
                <PracticePanel videoId={video.id} onOpen={(id) => navigate(`/quiz/${id}`)} />
              )}
            </div>
          </div>
        </div>

        <aside className="space-y-6">
          {video.chapters.length > 0 && (
            <div className="card overflow-hidden">
              <div className="flex items-center justify-between border-b border-line px-4 py-3">
                <h2 className="font-display text-sm font-semibold">Chapters</h2>
                <span className="font-mono text-xs text-muted">{video.chapters.length}</span>
              </div>
              <ol className="max-h-[420px] overflow-y-auto p-1.5">
                {video.chapters.map((chapter, index) => (
                  <li key={chapter.scene_id + index}>
                    <button
                      onClick={() => player.current?.seek(chapter.start)}
                      className={clsx(
                        "flex w-full items-start gap-3 rounded-xl px-3 py-2.5 text-left text-sm transition",
                        index === active ? "bg-primary/15 text-ink ring-1 ring-primary/35" : "text-ink-2 hover:bg-raised",
                      )}
                    >
                      <span className="mt-px font-mono text-xs text-muted">{index + 1}.</span>
                      <span className="flex-1">{chapter.title}</span>
                      <span className="font-mono text-xs text-muted">{clock(chapter.start)}</span>
                    </button>
                  </li>
                ))}
              </ol>
            </div>
          )}
          <div>
            <h2 className="mb-3 font-display text-sm font-semibold">Related videos</h2>
            {related.isLoading ? (
              <div className="space-y-3">
                {[0, 1, 2].map((index) => (
                  <div key={index} className="skeleton h-24" />
                ))}
              </div>
            ) : related.data?.length ? (
              <div className="space-y-2">
                {related.data.map((item) => (
                  <VideoCard key={item.id} video={item} compactLayout />
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted">No related public videos yet.</p>
            )}
          </div>
        </aside>
      </div>
      {editing && <CatalogModal video={video} onClose={() => setEditing(false)} />}
      {picking && <PlaylistPicker videoId={video.id} onClose={() => setPicking(false)} />}
    </div>
  );
}

function NotesPanel({ videoId, now, onSeek }: { videoId: string; now: number; onSeek: (time: number) => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState("");
  const [editing, setEditing] = useState<Note | null>(null);
  const notes = useQuery({
    queryKey: ["notes", videoId],
    queryFn: () => api<Note[]>(`/v1/videos/${videoId}/notes`),
  });
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["notes", videoId] });
    void client.invalidateQueries({ queryKey: ["library", "notes"] });
  };
  const add = useMutation({
    mutationFn: () =>
      api(`/v1/videos/${videoId}/notes`, { body: { at_seconds: Math.floor(now), text: text.trim() } }),
    onSuccess: () => {
      setText("");
      refresh();
    },
    onError: (error) => toast(errorText(error), "error"),
  });
  const update = useMutation({
    mutationFn: (note: Note) => api(`/v1/notes/${note.id}`, { method: "PATCH", body: { text: note.text } }),
    onSuccess: () => {
      setEditing(null);
      refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/v1/notes/${id}`, { method: "DELETE" }),
    onSuccess: refresh,
  });

  return (
    <div>
      <form
        className="card flex items-start gap-2 p-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (text.trim()) add.mutate();
        }}
      >
        <span className="mt-2 rounded-md bg-primary/15 px-2 py-0.5 font-mono text-xs text-primary">{clock(now)}</span>
        <textarea
          className="min-h-[42px] flex-1 resize-none bg-transparent px-1 py-2 text-sm outline-none placeholder:text-muted"
          placeholder="Add a note at this moment…"
          rows={1}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              if (text.trim()) add.mutate();
            }
          }}
        />
        <button className="btn-primary btn-sm mt-1" disabled={!text.trim() || add.isPending}>
          {add.isPending ? <Spinner size={14} /> : <Plus size={14} />} Add
        </button>
      </form>
      <div className="mt-3 space-y-2">
        {notes.data?.length === 0 && (
          <p className="px-1 text-sm text-muted">Your notes are private. Each one remembers the moment you wrote it.</p>
        )}
        {notes.data?.map((note) => (
          <div key={note.id} className="group card flex items-start gap-3 p-3">
            <button
              onClick={() => onSeek(note.at_seconds)}
              className="rounded-md bg-raised px-2 py-0.5 font-mono text-xs text-teal hover:bg-primary/20"
            >
              {clock(note.at_seconds)}
            </button>
            {editing?.id === note.id ? (
              <form
                className="flex flex-1 gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  update.mutate(editing);
                }}
              >
                <input
                  className="input py-1.5"
                  value={editing.text}
                  autoFocus
                  onChange={(event) => setEditing({ ...editing, text: event.target.value })}
                />
                <button className="btn-primary btn-sm">Save</button>
              </form>
            ) : (
              <p className="flex-1 whitespace-pre-wrap text-sm">{note.text}</p>
            )}
            <Menu
              trigger={<Pencil size={14} />}
              label="Note actions"
              items={[
                { label: "Edit", icon: <Pencil size={14} />, onSelect: () => setEditing(note) },
                { label: "Delete", icon: <Trash2 size={14} />, danger: true, onSelect: () => remove.mutate(note.id) },
              ]}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

function TranscriptPanel({ videoId, now, onSeek }: { videoId: string; now: number; onSeek: (time: number) => void }) {
  const [filter, setFilter] = useState("");
  const transcript = useQuery({
    queryKey: ["transcript", videoId],
    queryFn: () => api<{ language: string; cues: Cue[] }>(`/v1/videos/${videoId}/transcript`),
  });
  const cues = (transcript.data?.cues ?? []).filter(
    (cue) => !filter || cue.text.toLowerCase().includes(filter.toLowerCase()),
  );
  if (transcript.isLoading) return <Spinner />;
  if (!transcript.data?.cues.length) return <p className="text-sm text-muted">No transcript for this video.</p>;
  return (
    <div className="card">
      <div className="relative border-b border-line p-2">
        <Search size={15} className="absolute left-5 top-1/2 -translate-y-1/2 text-muted" />
        <input
          className="input border-0 bg-transparent pl-9 focus:ring-0"
          placeholder="Search the transcript"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
      </div>
      <div className="max-h-[420px] overflow-y-auto p-2">
        {cues.map((cue, index) => {
          const current = now >= cue.start && now < cue.end;
          return (
            <button
              key={index}
              onClick={() => onSeek(cue.start)}
              className={clsx(
                "flex w-full gap-3 rounded-lg px-3 py-2 text-left text-sm transition",
                current ? "bg-primary/12 text-ink" : "text-ink-2 hover:bg-raised",
              )}
            >
              <span className="w-12 shrink-0 font-mono text-xs text-muted">{clock(cue.start)}</span>
              <span>{cue.text}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function ResourcesPanel({ videoId }: { videoId: string }) {
  const sources = useQuery({
    queryKey: ["sources", videoId],
    queryFn: () => api<Source[]>(`/v1/videos/${videoId}/sources`),
  });
  if (sources.isLoading) return <Spinner />;
  if (!sources.data?.length) {
    return (
      <p className="text-sm text-muted">
        This lesson was built from Ekalavya&apos;s library and web sources; no document was attached.
      </p>
    );
  }
  return (
    <div className="space-y-2">
      {sources.data.map((source) => (
        <div key={source.id} className="card flex items-center gap-3 p-3">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-success/12 text-success">
            <FileText size={16} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium">{source.title}</p>
            <p className="text-xs capitalize text-muted">{source.kind}</p>
          </div>
          {source.url && (
            <a href={source.url} target="_blank" rel="noreferrer" className="btn-ghost btn-sm">
              <ExternalLink size={14} /> Open
            </a>
          )}
          {source.downloadable && (
            <a href={mediaUrl(`/v1/videos/${videoId}/sources/${source.id}/file`)} className="btn-ghost btn-sm">
              <Download size={14} /> File
            </a>
          )}
        </div>
      ))}
    </div>
  );
}

function PracticePanel({ videoId, onOpen }: { videoId: string; onOpen: (id: string) => void }) {
  const toast = useToast();
  const [asking, setAsking] = useState(false);
  const [instruction, setInstruction] = useState("");
  const quizzes = useQuery({
    queryKey: ["quizzes", videoId],
    queryFn: () => api<Quiz[]>("/v1/quizzes", { params: { video_id: videoId } }),
  });
  const create = useMutation({
    mutationFn: () => api<Quiz>("/v1/quizzes", { body: { video_id: videoId, instruction } }),
    onSuccess: (quiz) => onOpen(quiz.id),
    onError: (error) => toast(errorText(error), "error"),
  });
  return (
    <div>
      <div className="card flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-amber/12 text-amber">
          <GraduationCap size={18} />
        </span>
        <div className="flex-1">
          <p className="font-medium">Quiz yourself on this video</p>
          <p className="text-sm text-ink-2">Questions are written from the narration; wrong answers link to the scene.</p>
        </div>
        <button className="btn-secondary btn-sm" onClick={() => setAsking(true)}>
          Customise
        </button>
        <button className="btn-primary btn-sm" onClick={() => create.mutate()} disabled={create.isPending}>
          {create.isPending && <Spinner size={14} />} {create.isPending ? "Writing questions…" : "Start quiz"}
        </button>
      </div>
      {!!quizzes.data?.length && (
        <div className="mt-3 space-y-2">
          {quizzes.data.map((quiz) => (
            <Link key={quiz.id} to={`/quiz/${quiz.id}`} className="card flex items-center justify-between p-3 text-sm hover:border-primary/40">
              <span>
                {quiz.title} · {quiz.questions.length} questions
              </span>
              <span className="font-mono text-xs text-muted">
                {quiz.best_score === null ? "not taken" : `best ${Math.round(quiz.best_score * 100)}%`}
              </span>
            </Link>
          ))}
        </div>
      )}
      <Modal
        open={asking}
        onClose={() => setAsking(false)}
        title="Customise the quiz"
        footer={
          <button
            className="btn-primary"
            disabled={create.isPending}
            onClick={() => {
              setAsking(false);
              create.mutate();
            }}
          >
            Write quiz
          </button>
        }
      >
        <label className="label" htmlFor="quiz-instruction">
          Anything the quiz should focus on?
        </label>
        <textarea
          id="quiz-instruction"
          className="input min-h-[96px]"
          placeholder="e.g. harder numerical questions, only the second half, exam style…"
          value={instruction}
          onChange={(event) => setInstruction(event.target.value)}
        />
      </Modal>
    </div>
  );
}
