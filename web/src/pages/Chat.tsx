import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  BookOpen,
  CalendarCheck,
  Check,
  ChevronLeft,
  ChevronRight,
  Clapperboard,
  Compass,
  Copy,
  ExternalLink,
  Globe2,
  GraduationCap,
  Home,
  Lightbulb,
  Menu as MenuIcon,
  Pencil,
  Quote,
  RefreshCw,
  Sparkles,
  X,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ThemeButton } from "../components/AppShell";
import { CatalogModal } from "../components/CatalogModal";
import { BuildProgress } from "../components/chat/BuildProgress";
import { ChatActions } from "../components/chat/ChatActions";
import { Composer, type Effort, type StagedPaper } from "../components/chat/Composer";
import { ConversationList } from "../components/chat/ConversationList";
import { LogoMark, Spinner } from "../components/common";
import { Markdown } from "../components/Markdown";
import { VideoPlayer } from "../components/VideoPlayer";
import { api, errorText } from "../lib/api";
import { useAuth } from "../lib/auth";
import { BUSY, clock } from "../lib/format";
import type { Chapter, ChatMessage, Folder, Project, VideoDetail } from "../lib/types";
import { copyText, useToast } from "../lib/ui";

type Outgoing = { text: string; paper: StagedPaper | null; effort: Effort };
/** A message typed on the new-chat screen, waiting for its chat to open. */
const outbox = new Map<string, Outgoing>();

const VIDEO_ACTIONS = new Set(["generate_video", "edit_range", "localize", "localize_scene", "verification_complete"]);
const SETTLED_TROUBLE = new Set(["CANCELLED", "FAILED"]);

type BranchControl = { key: string; selected: number; options: number[] };

/** The active conversation, or an older branch where the learner picked one with ‹ ›. */
function displayed(messages: ChatMessage[], view: Record<string, number>) {
  const shown: ChatMessage[] = [];
  const controls: Record<string, BranchControl> = {};
  for (const message of messages) {
    const branches = message.branches ?? [];
    if (message.role === "user" && branches.length) {
      const active = message.branch_index || branches.length + 1;
      const options = [...new Set([...branches.map((branch) => branch.index), active])].sort((a, b) => a - b);
      const selected = view[message.id] ?? active;
      if (selected !== active) {
        const older = (branches.find((branch) => branch.index === selected)?.messages ?? []).map((item) => ({
          ...item,
          readonly: true,
        }));
        if (older.length) controls[older[0].id] = { key: message.id, selected, options };
        return { shown: [...shown, ...older], controls };
      }
      controls[message.id] = { key: message.id, selected, options };
    }
    shown.push(message);
  }
  return { shown, controls };
}

/** Each video-producing turn owns the newest version made before the next such turn. */
function videosByMessage(messages: ChatMessage[], kept: { version: number; created_at: string }[]) {
  const paired: Record<string, { version: number; latest: boolean }> = {};
  if (!kept.length) return paired;
  const latest = Math.max(...kept.map((item) => item.version));
  const ordered = messages
    .filter((message) => message.role === "assistant" && VIDEO_ACTIONS.has(message.action ?? ""))
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
  ordered.forEach((message, index) => {
    const start = new Date(message.created_at).getTime();
    const end = index + 1 < ordered.length ? new Date(ordered[index + 1].created_at).getTime() : Infinity;
    const made = kept
      .filter((item) => {
        const at = new Date(item.created_at).getTime();
        return at >= start && at < end;
      })
      .map((item) => item.version);
    if (made.length) paired[message.id] = { version: Math.max(...made), latest: Math.max(...made) === latest };
  });
  return paired;
}

export default function ChatPage() {
  const { chatId } = useParams();
  const [params] = useSearchParams();
  const [drawer, setDrawer] = useState(false);
  useEffect(() => setDrawer(false), [chatId]);
  const folderId = params.get("folder");

  return (
    <div className="flex h-[100dvh] min-w-0">
      <aside className="hidden w-72 shrink-0 flex-col border-r border-line bg-surface/40 p-3 xl:flex">
        <ConversationList activeId={chatId} folderId={folderId} />
      </aside>
      {drawer && (
        <div className="fixed inset-0 z-[60] xl:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setDrawer(false)} />
          <aside className="absolute inset-y-0 left-0 flex w-80 max-w-[88vw] animate-fade-in flex-col border-r border-line bg-surface p-3">
            <div className="mb-3 flex items-center justify-between">
              <nav className="flex gap-1 lg:hidden">
                {[
                  ["/home", Home, "Home"],
                  ["/discover", Compass, "Discover"],
                  ["/library", BookOpen, "Library"],
                  ["/study", CalendarCheck, "Study plan"],
                  ["/quiz", GraduationCap, "Quiz me"],
                ].map(([to, Icon, label]) => {
                  const Glyph = Icon as typeof Home;
                  return (
                    <NavLink key={to as string} to={to as string} className="icon-btn" aria-label={label as string} title={label as string}>
                      <Glyph size={18} />
                    </NavLink>
                  );
                })}
              </nav>
              <button className="icon-btn ml-auto" onClick={() => setDrawer(false)} aria-label="Close chats">
                <X size={18} />
              </button>
            </div>
            <ConversationList activeId={chatId} folderId={folderId} />
          </aside>
        </div>
      )}
      {chatId ? (
        <Thread key={chatId} chatId={chatId} openDrawer={() => setDrawer(true)} />
      ) : (
        <NewChat openDrawer={() => setDrawer(true)} folderId={folderId} />
      )}
    </div>
  );
}

function Header({ openDrawer, children }: { openDrawer: () => void; children?: React.ReactNode }) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b border-line bg-bg/80 px-3 backdrop-blur-xl sm:px-4">
      <button className="icon-btn xl:hidden" onClick={openDrawer} aria-label="Show chats">
        <MenuIcon size={19} />
      </button>
      {children}
      <div className="ml-auto flex items-center">
        <ThemeButton />
      </div>
    </header>
  );
}

const SUGGESTIONS = [
  { icon: Clapperboard, title: "Pythagoras, made visual", text: "A gentle proof with a narrated triangle lesson.", prompt: "Explain Pythagoras' theorem and make me a video." },
  { icon: Lightbulb, title: "Untangle a concept", text: "A clear explanation, one step at a time.", prompt: "Explain a difficult concept step by step with a clear example: " },
  { icon: Sparkles, title: "Explore a source", text: "Turn a paper, link or idea into a visual lesson.", prompt: "Make a video lesson from this source: " },
];

function NewChat({ openDrawer, folderId }: { openDrawer: () => void; folderId: string | null }) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const client = useQueryClient();
  const [params] = useSearchParams();
  const [initial, setInitial] = useState(params.get("prompt") ?? "");
  const [temporary, setTemporary] = useState(false);
  const [sending, setSending] = useState<string | null>(null);
  const [scratch, setScratch] = useState<{ role: "user" | "assistant"; message: string }[]>([]);
  const folders = useQuery({ queryKey: ["folders"], queryFn: () => api<Folder[]>("/v1/project-folders") });
  const folder = folders.data?.find((item) => item.id === folderId);

  const send = async (text: string, paper: StagedPaper | null, effort: Effort) => {
    if (!user) return;
    if (temporary) {
      setScratch((items) => [...items, { role: "user", message: text }]);
      setSending(text);
      try {
        const reply = await api<{ message: string }>("/v1/temporary-chat", {
          body: {
            message: text,
            language: user.default_text_language,
            text_language: user.default_text_language,
            history: scratch.slice(-12).map((item) => `${item.role}: ${item.message}`),
            reasoning_effort: effort,
          },
        });
        setScratch((items) => [...items, { role: "assistant", message: reply.message }]);
      } catch (error) {
        toast(errorText(error), "error");
      } finally {
        setSending(null);
      }
      return;
    }
    setSending(text);
    try {
      const project = await api<Project>("/v1/projects", {
        body: {
          title: text.slice(0, 80),
          request: text,
          mode: "chat",
          folder_id: folderId,
          target_duration_minutes: user.default_duration_minutes,
          text_language: user.default_text_language,
          voice_language: user.default_voice_language,
        },
      });
      outbox.set(project.id, { text, paper, effort });
      void client.invalidateQueries({ queryKey: ["projects"] });
      navigate(`/chat/${project.id}`);
    } catch (error) {
      toast(errorText(error), "error");
      setSending(null);
    }
  };

  const first = (user?.display_name || user?.username || "").split(" ")[0];
  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <Header openDrawer={openDrawer}>
        <span className="truncate font-display text-sm font-semibold">{folder ? folder.name : "New chat"}</span>
        <button
          className={clsx("btn-sm ml-2", temporary ? "btn-accent" : "btn-ghost")}
          onClick={() => {
            setTemporary(!temporary);
            setScratch([]);
          }}
          title="Temporary chat: not saved, gone on refresh"
        >
          <Zap size={14} /> Temporary
        </button>
      </Header>
      <div className="flex-1 overflow-y-auto px-4">
        {temporary && scratch.length ? (
          <div className="mx-auto max-w-3xl space-y-6 py-8">
            {scratch.map((item, index) =>
              item.role === "user" ? (
                <div key={index} className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-md bg-primary/15 px-4 py-2.5 text-[15px] ring-1 ring-primary/25">
                  {item.message}
                </div>
              ) : (
                <div key={index} className="flex gap-3">
                  <LogoMark size={28} className="shrink-0" />
                  <Markdown content={item.message} />
                </div>
              ),
            )}
            {sending && <Thinking />}
          </div>
        ) : (
          <div className="mx-auto flex min-h-full max-w-3xl flex-col items-center justify-center py-10 text-center">
            <LogoMark size={56} />
            {temporary ? (
              <>
                <h1 className="mt-5 font-display text-2xl font-semibold">Temporary chat</h1>
                <p className="mt-2 text-ink-2">Nothing here is saved. It disappears when you leave.</p>
              </>
            ) : (
              <>
                <h1 className="mt-5 font-display text-2xl font-semibold sm:text-3xl">
                  {folder ? `What should we work on in ${folder.name}?` : `Hi ${first}! What would you like to learn?`}
                </h1>
                <p className="mt-2 max-w-lg text-ink-2">
                  Ask a question, explore an idea, or turn a topic or paper into a narrated video lesson.
                </p>
                {sending ? (
                  <div className="mt-8 w-full max-w-xl text-left">
                    <div className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-md bg-primary/15 px-4 py-2.5 ring-1 ring-primary/25">
                      {sending}
                    </div>
                    <div className="mt-4">
                      <Thinking />
                    </div>
                  </div>
                ) : (
                  <div className="mt-8 grid w-full grid-cols-1 gap-3 sm:grid-cols-3">
                    {SUGGESTIONS.map(({ icon: Icon, title, text, prompt }) => (
                      <button
                        key={title}
                        onClick={() => setInitial(prompt)}
                        className="card p-4 text-left transition hover:-translate-y-0.5 hover:border-primary/40"
                      >
                        <Icon size={18} className="text-primary" />
                        <p className="mt-3 text-sm font-medium">{title}</p>
                        <p className="mt-1 text-xs text-ink-2">{text}</p>
                      </button>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>
      <div className="shrink-0 px-4 pb-4 pt-2">
        <Composer
          onSend={send}
          busy={!!sending}
          placeholder={temporary ? "Message Ekalavya temporarily" : "Ask anything, or ask for a video…"}
          language={user?.default_text_language}
          initialText={initial}
          openAttach={params.get("attach") === "1"}
        />
      </div>
    </div>
  );
}

/** "bodhan" -> "Bodhan"; an LLM model id such as "google/gemini-2.5-flash-lite" as it is. */
function translatorName(value: string) {
  return value === "bodhan" ? "Bodhan" : value;
}

function Thinking() {
  return (
    <div className="flex items-center gap-3 text-sm text-ink-2">
      <LogoMark size={28} />
      <span className="flex gap-1">
        {[0, 1, 2].map((index) => (
          <span key={index} className="h-1.5 w-1.5 animate-bounce rounded-full bg-primary" style={{ animationDelay: `${index * 120}ms` }} />
        ))}
      </span>
      Thinking…
    </div>
  );
}

function Thread({ chatId, openDrawer }: { chatId: string; openDrawer: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const [pending, setPending] = useState<string | null>(null);
  const [quote, setQuote] = useState<string | null>(null);
  const [view, setView] = useState<Record<string, number>>({});
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [publishing, setPublishing] = useState<VideoDetail | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const project = useQuery({
    queryKey: ["project", chatId],
    queryFn: () => api<Project>(`/v1/projects/${chatId}`),
    refetchInterval: (query) => (BUSY.has(query.state.data?.status ?? "") ? 5000 : false),
  });
  const messages = useQuery({
    queryKey: ["chat", chatId],
    queryFn: () => api<ChatMessage[]>(`/v1/projects/${chatId}/chat`),
  });
  const data = project.data;
  const kept = useQuery({
    queryKey: ["videos-kept", chatId, data?.video_path],
    queryFn: () => api<{ version: number; created_at: string }[]>(`/v1/projects/${chatId}/videos`),
    enabled: !!data?.video_path,
  });
  const scenes = useQuery({
    queryKey: ["scenes", chatId, data?.video_path],
    queryFn: () => api<{ id: string; title: string }[]>(`/v1/projects/${chatId}/scenes`),
    enabled: !!data?.video_path,
  });
  const folders = useQuery({ queryKey: ["folders"], queryFn: () => api<Folder[]>("/v1/project-folders") });

  const refresh = useCallback(() => {
    for (const key of [["project", chatId], ["chat", chatId], ["videos-kept", chatId], ["scenes", chatId], ["projects"], ["timeline", chatId]]) {
      void client.invalidateQueries({ queryKey: key });
    }
  }, [client, chatId]);

  const send = useCallback(
    async (text: string, paper: StagedPaper | null, effort: Effort) => {
      const body = quote ? `> ${quote.replace(/\n/g, "\n> ")}\n\n${text}` : text;
      setQuote(null);
      setPending(body);
      try {
        if (paper) {
          if (paper.file) {
            const form = new FormData();
            form.append("file", paper.file, paper.file.name);
            await api(`/v1/projects/${chatId}/sources`, { form });
          } else if (paper.link) {
            await api(`/v1/projects/${chatId}/sources/link`, { body: { url: paper.link } });
          }
          toast(`Attached ${paper.name}`);
        }
        const current = client.getQueryData<Project>(["project", chatId]);
        await api(`/v1/projects/${chatId}/chat`, {
          body: { message: body, language: current?.text_language, reasoning_effort: effort },
        });
      } catch (error) {
        toast(errorText(error), "error");
      } finally {
        setPending(null);
        refresh();
      }
    },
    [chatId, client, quote, refresh, toast],
  );

  useEffect(() => {
    const queued = outbox.get(chatId);
    if (!queued) return;
    outbox.delete(chatId);
    void send(queued.text, queued.paper, queued.effort);
  }, [chatId, send]);

  const all = messages.data ?? [];
  const { shown, controls } = useMemo(() => displayed(all, view), [all, view]);
  const everything = useMemo(
    () => [...all, ...all.flatMap((message) => (message.branches ?? []).flatMap((branch) => branch.messages))],
    [all],
  );
  const videoFor = useMemo(() => videosByMessage(everything, kept.data ?? []), [everything, kept.data]);
  const latestUser = [...all].reverse().find((message) => message.role === "user")?.id;
  const busy = BUSY.has(data?.status ?? "");
  const showProgress = !!data && (busy || SETTLED_TROUBLE.has(data.status));

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [shown.length, pending]);

  const resend = async (id: string, text: string) => {
    setEditing(null);
    setPending(text);
    try {
      await api(`/v1/projects/${chatId}/chat/${id}`, { method: "PATCH", body: { message: text } });
      toast("Updated. The reply has been refreshed.");
    } catch (error) {
      toast(errorText(error), "error");
    } finally {
      setPending(null);
      refresh();
    }
  };

  const openPublish = async () => {
    try {
      setPublishing(await api<VideoDetail>(`/v1/videos/${chatId}`));
    } catch (error) {
      toast(errorText(error), "error");
    }
  };

  if (project.isError) {
    return (
      <div className="flex flex-1 flex-col">
        <Header openDrawer={openDrawer} />
        <div className="m-auto max-w-md p-6 text-center">
          <p className="font-display text-lg font-semibold">This conversation isn&apos;t available</p>
          <p className="mt-2 text-sm text-ink-2">It belongs to another account, or it was deleted.</p>
          <button className="btn-primary mt-5" onClick={() => navigate("/chat")}>
            Start a new chat
          </button>
        </div>
      </div>
    );
  }

  let lastUser: ChatMessage | null = null;
  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <Header openDrawer={openDrawer}>
        <div className="min-w-0">
          <p className="truncate font-display text-sm font-semibold">{data?.title ?? "…"}</p>
          {data && (
            <p className="flex items-center gap-1.5 truncate text-[11px] text-muted">
              {busy ? (
                <>
                  <Spinner size={10} className="text-primary" /> {data.status.toLowerCase()}
                </>
              ) : data.video_path ? (
                <>
                  {data.visibility === "public" ? <Globe2 size={11} className="text-teal" /> : null}
                  Video ready · {data.voice_language === data.text_language ? data.voice_language : `${data.voice_language} voice · ${data.text_language} text`}
                </>
              ) : (
                "Conversation"
              )}
            </p>
          )}
        </div>
        {data && (
          <div className="ml-2 flex items-center gap-1">
            {data.video_path && (
              <>
                <Link to={`/watch/${chatId}`} className="btn-ghost btn-sm hidden sm:inline-flex">
                  <ExternalLink size={14} /> Video page
                </Link>
                <button className="btn-secondary btn-sm hidden sm:inline-flex" onClick={openPublish}>
                  <Globe2 size={14} /> {data.visibility === "public" ? "Public" : "Publish"}
                </button>
              </>
            )}
            <ChatActions project={data} folders={folders.data ?? []} />
          </div>
        )}
      </Header>

      <div className="flex-1 overflow-y-auto px-4">
        <div className="mx-auto max-w-3xl space-y-6 py-8">
          {messages.isLoading && (
            <div className="space-y-4">
              <div className="skeleton ml-auto h-10 w-2/3" />
              <div className="skeleton h-24 w-full" />
            </div>
          )}
          {!latestUser && showProgress && data && <BuildProgress projectId={chatId} status={data.status} onSettled={refresh} />}
          {shown.map((message) => {
            const control = controls[message.id];
            const readonly = !!message.readonly;
            if (message.role === "user") {
              lastUser = message;
              const isEditing = editing?.id === message.id;
              return (
                <div key={message.id} className="group animate-fade-in">
                  {readonly && control && <p className="mb-2 text-right text-[11px] text-muted">Earlier version of this conversation · read-only</p>}
                  {isEditing ? (
                    <div className="ml-auto max-w-[85%] rounded-2xl border border-primary/50 bg-surface p-3">
                      <textarea
                        className="input min-h-[90px]"
                        autoFocus
                        value={editing.text}
                        onChange={(event) => setEditing({ id: message.id, text: event.target.value })}
                      />
                      <div className="mt-2 flex justify-end gap-2">
                        <button className="btn-ghost btn-sm" onClick={() => setEditing(null)}>
                          Cancel
                        </button>
                        <button className="btn-primary btn-sm" disabled={!editing.text.trim()} onClick={() => void resend(message.id, editing.text.trim())}>
                          Save &amp; resend
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="ml-auto w-fit max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-primary/15 px-4 py-2.5 text-[15px] ring-1 ring-primary/25">
                      {message.message}
                    </div>
                  )}
                  {!isEditing && (
                    <div className="mt-1 flex items-center justify-end gap-0.5 text-muted opacity-100 transition sm:opacity-0 sm:group-hover:opacity-100">
                      {control && (
                        <>
                          <button
                            className="icon-btn h-7 w-7"
                            aria-label="Earlier version"
                            disabled={control.options.indexOf(control.selected) === 0}
                            onClick={() => setView({ ...view, [control.key]: control.options[control.options.indexOf(control.selected) - 1] })}
                          >
                            <ChevronLeft size={14} />
                          </button>
                          <span className="font-mono text-[11px]">
                            {control.options.indexOf(control.selected) + 1}/{control.options.length}
                          </span>
                          <button
                            className="icon-btn h-7 w-7"
                            aria-label="Later version"
                            disabled={control.options.indexOf(control.selected) === control.options.length - 1}
                            onClick={() => setView({ ...view, [control.key]: control.options[control.options.indexOf(control.selected) + 1] })}
                          >
                            <ChevronRight size={14} />
                          </button>
                        </>
                      )}
                      <CopyButton text={message.message} label="Copy your message" />
                      {!readonly && (
                        <button
                          className="icon-btn h-7 w-7"
                          aria-label="Edit and retry from this message"
                          title="Edit and retry from this message"
                          disabled={busy || !!pending}
                          onClick={() => setEditing({ id: message.id, text: message.message })}
                        >
                          <Pencil size={14} />
                        </button>
                      )}
                    </div>
                  )}
                  {message.id === latestUser && showProgress && data && (
                    <div className="mt-6">
                      <BuildProgress projectId={chatId} status={data.status} onSettled={refresh} />
                    </div>
                  )}
                </div>
              );
            }
            const video = videoFor[message.id];
            const regenerateFrom = lastUser as ChatMessage | null;
            return (
              <div key={message.id} className="group flex animate-fade-in gap-3">
                <LogoMark size={28} className="mt-0.5 shrink-0" />
                <div className="min-w-0 flex-1">
                  <Markdown content={message.message} />
                  {(message.action === "edit_range" || message.action === "localize_scene") && message.metadata?.start !== undefined && (
                    <p className="mt-2 font-mono text-xs text-muted">
                      Changing {clock(message.metadata.start)} to {clock(message.metadata.end)}
                    </p>
                  )}
                  {message.action === "generate_video" && message.metadata?.source_note && (
                    <p className="mt-2 text-xs text-muted">{message.metadata.source_note}</p>
                  )}
                  {message.metadata?.translated_by && (
                    <p className="mt-2 text-xs text-muted">Translated by {translatorName(message.metadata.translated_by)}</p>
                  )}
                  {video && data && (
                    <ChatVideo
                      projectId={chatId}
                      version={video.version}
                      latest={video.latest}
                      language={data.voice_language}
                      sceneTitles={scenes.data ?? []}
                      visibility={data.visibility}
                      onPublish={openPublish}
                    />
                  )}
                  {!readonly && (
                    <div className="mt-2 flex items-center gap-0.5 text-muted opacity-100 transition sm:opacity-0 sm:group-hover:opacity-100">
                      <CopyButton text={message.message} label="Copy reply" />
                      <button className="icon-btn h-7 w-7" aria-label="Quote this reply" title="Quote this reply" onClick={() => setQuote(message.message.slice(0, 600))}>
                        <Quote size={14} />
                      </button>
                      {regenerateFrom && (
                        <button
                          className="icon-btn h-7 w-7"
                          aria-label="Regenerate this reply"
                          title="Regenerate this reply"
                          disabled={busy || !!pending}
                          onClick={() => void resend(regenerateFrom.id, regenerateFrom.message)}
                        >
                          <RefreshCw size={14} />
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
          {pending && (
            <>
              <div className="ml-auto w-fit max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-primary/15 px-4 py-2.5 text-[15px] ring-1 ring-primary/25">
                {pending}
              </div>
              <Thinking />
            </>
          )}
          <div ref={bottom} />
        </div>
      </div>
      <div className="shrink-0 px-4 pb-4 pt-2">
        <Composer
          onSend={(text, paper, effort) => void send(text, paper, effort)}
          busy={!!pending}
          placeholder="Ask a follow-up, or ask for a change to the video…"
          language={data?.text_language}
          quote={quote}
          onClearQuote={() => setQuote(null)}
        />
      </div>
      {publishing && (
        <CatalogModal
          video={publishing}
          onClose={() => {
            setPublishing(null);
            refresh();
          }}
        />
      )}
    </div>
  );
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="icon-btn h-7 w-7"
      aria-label={done ? "Copied" : label}
      title={done ? "Copied" : label}
      onClick={async () => {
        if (await copyText(text)) {
          setDone(true);
          setTimeout(() => setDone(false), 1200);
        }
      }}
    >
      {done ? <Check size={14} className="text-success" /> : <Copy size={14} />}
    </button>
  );
}

function ChatVideo({
  projectId,
  version,
  latest,
  language,
  sceneTitles,
  visibility,
  onPublish,
}: {
  projectId: string;
  version: number;
  latest: boolean;
  language: string;
  sceneTitles: { id: string; title: string }[];
  visibility: string;
  onPublish: () => void;
}) {
  const timeline = useQuery({
    queryKey: ["timeline", projectId, version],
    queryFn: () =>
      api<{ scenes: { scene_id: string; start: number; end: number }[] }>(`/v1/projects/${projectId}/timeline`, {
        params: { version },
      }),
  });
  const chapters: Chapter[] = (timeline.data?.scenes ?? []).map((scene, index) => ({
    scene_id: scene.scene_id,
    title: sceneTitles.find((item) => item.id === scene.scene_id)?.title ?? `Scene ${index + 1}`,
    start: scene.start,
    end: scene.end,
  }));
  return (
    <div className="mt-4 overflow-hidden rounded-2xl border border-line bg-surface p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 px-1">
        <p className="flex items-center gap-2 text-sm font-medium">
          <Clapperboard size={15} className="text-primary" />
          {latest ? "Your lesson video" : `Earlier version (v${version})`}
        </p>
        {latest && (
          <div className="flex gap-1">
            <Link to={`/watch/${projectId}`} className="btn-ghost btn-sm">
              <ExternalLink size={13} /> Open
            </Link>
            <Link to={`/quiz?video=${projectId}`} className="btn-ghost btn-sm">
              <GraduationCap size={13} /> Quiz me
            </Link>
            <button className="btn-ghost btn-sm" onClick={onPublish}>
              <Globe2 size={13} /> {visibility === "public" ? "Public" : "Publish"}
            </button>
          </div>
        )}
      </div>
      <VideoPlayer videoId={projectId} version={version} chapters={chapters} subtitleLanguage={language} />
      {chapters.length > 1 && (
        <div className="mt-3 flex flex-wrap gap-1.5 px-1">
          {chapters.map((chapter, index) => (
            <span key={chapter.scene_id + index} className="rounded-full bg-raised px-2.5 py-1 text-[11px] text-ink-2">
              {index + 1}. {chapter.title} · <span className="font-mono">{clock(chapter.start)}</span>
            </span>
          ))}
        </div>
      )}
      <p className="mt-2 px-1 text-[11px] text-muted">Click a scene in the strip to jump there, then ask for a change by scene or time.</p>
    </div>
  );
}
