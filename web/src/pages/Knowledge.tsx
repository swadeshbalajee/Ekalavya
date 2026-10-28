import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  ArrowRight,
  Clapperboard,
  MessageSquare,
  MoreHorizontal,
  Network,
  RefreshCw,
  RotateCcw,
  Search,
  Send,
  Sparkles,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, { type ForceGraphMethods } from "react-force-graph-2d";
import { Link } from "react-router-dom";
import { Button, Empty, Menu, Spinner, Tabs } from "../components/common";
import { Markdown } from "../components/Markdown";
import { api, errorText } from "../lib/api";
import { useDebounced, useTheme, useToast } from "../lib/ui";

type GraphNode = {
  id: string;
  name: string;
  kind: string;
  description: string | null;
  mentions: number;
  degree: number;
  x?: number;
  y?: number;
};
type GraphEdge = { source: string | GraphNode; target: string | GraphNode; relation: string; count: number };
type Graph = { nodes: GraphNode[]; edges: GraphEdge[]; pending: number; chats_mapped: number };
type Chat = { id: string; title: string; has_video: boolean };
type NodeDetail = {
  id: string;
  name: string;
  kind: string;
  description: string | null;
  relations: { direction: "in" | "out"; relation: string; node: { id: string; name: string }; detail: string | null }[];
  chats: Chat[];
};
type Answer = { answer: string; nodes: string[]; chats: Chat[] };

/* Categorical hues in fixed order (most common kind first); the rest fold into "other". */
const DARK = ["#8B6CFF", "#22D3EE", "#F6B26B", "#4ADE80", "#F472B6", "#60A5FA"];
const LIGHT = ["#6346E6", "#0E8FA8", "#C2700F", "#16A34A", "#DB2777", "#2563EB"];
const OTHER = { dark: "#7C8599", light: "#7B8597" };

const idOf = (end: string | GraphNode) => (typeof end === "string" ? end : end.id);

function useSize(ref: React.RefObject<HTMLDivElement>) {
  const [size, setSize] = useState({ width: 600, height: 500 });
  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([entry]) =>
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height }),
    );
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, [ref]);
  return size;
}

export default function KnowledgePage() {
  const client = useQueryClient();
  const toast = useToast();
  const { theme } = useTheme();
  const graphRef = useRef<ForceGraphMethods<GraphNode, GraphEdge>>();
  const box = useRef<HTMLDivElement>(null);
  const size = useSize(box);
  const [selected, setSelected] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<Set<string>>(new Set());
  const [hover, setHover] = useState<string | null>(null);
  const [panel, setPanel] = useState<"ask" | "concept">("ask");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [find, setFind] = useState("");
  const term = useDebounced(find.trim(), 300);

  const graph = useQuery({ queryKey: ["knowledge"], queryFn: () => api<Graph>("/v1/knowledge/graph") });
  const detail = useQuery({
    queryKey: ["knowledge", "node", selected],
    queryFn: () => api<NodeDetail>(`/v1/knowledge/nodes/${selected}`),
    enabled: !!selected,
  });
  const found = useQuery({
    queryKey: ["knowledge", "search", term],
    queryFn: () => api<{ id: string; name: string; kind: string | null; score: number }[]>("/v1/knowledge/search", { params: { q: term } }),
    enabled: term.length > 1,
  });

  const sync = useMutation({
    mutationFn: () => api<{ read: number; failed: number; pending: number }>("/v1/knowledge/sync", { method: "POST" }),
    onSuccess: (result) => {
      toast(
        result.read
          ? `Read ${result.read} chat${result.read === 1 ? "" : "s"} into your map${result.pending ? ` · ${result.pending} still to go` : ""}`
          : "Your map is up to date",
      );
      void client.invalidateQueries({ queryKey: ["knowledge"] });
    },
    onError: (error) => toast(errorText(error), "error"),
  });
  const reset = useMutation({
    mutationFn: () => api("/v1/knowledge", { method: "DELETE" }),
    onSuccess: () => {
      setSelected(null);
      setAnswer(null);
      setHighlight(new Set());
      void client.invalidateQueries({ queryKey: ["knowledge"] });
    },
  });
  const ask = useMutation({
    mutationFn: () => api<Answer>("/v1/knowledge/query", { body: { question: question.trim() } }),
    onSuccess: (result) => {
      setAnswer(result);
      setHighlight(new Set(result.nodes));
      if (result.nodes.length) {
        setTimeout(() => graphRef.current?.zoomToFit(600, 80, (node) => result.nodes.includes(node.id)), 50);
      }
    },
    onError: (error) => toast(errorText(error), "error"),
  });

  const data = graph.data;
  const palette = theme === "dark" ? DARK : LIGHT;
  const kinds = useMemo(() => {
    const counts = new Map<string, number>();
    for (const node of data?.nodes ?? []) counts.set(node.kind, (counts.get(node.kind) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([kind]) => kind);
  }, [data]);
  const colorOf = (kind: string) => {
    const index = kinds.indexOf(kind);
    return index >= 0 && index < palette.length ? palette[index] : OTHER[theme];
  };
  const graphData = useMemo(
    () => ({
      nodes: (data?.nodes ?? []).map((node) => ({ ...node })),
      links: (data?.edges ?? []).map((edge) => ({ ...edge })),
    }),
    [data],
  );
  const neighbours = useMemo(() => {
    const focus = hover ?? selected;
    if (!focus) return null;
    const near = new Set([focus]);
    for (const edge of data?.edges ?? []) {
      if (idOf(edge.source) === focus) near.add(idOf(edge.target));
      if (idOf(edge.target) === focus) near.add(idOf(edge.source));
    }
    return near;
  }, [hover, selected, data]);

  const focusNode = (id: string) => {
    setSelected(id);
    // A concept outside the answer's highlight would otherwise stay dimmed.
    if (!highlight.has(id)) setHighlight(new Set());
    setPanel("concept");
    const node = graphData.nodes.find((item) => item.id === id) as GraphNode | undefined;
    if (node?.x !== undefined && node.y !== undefined) {
      graphRef.current?.centerAt(node.x, node.y, 600);
      graphRef.current?.zoom(2.4, 600);
    }
  };

  const ink = theme === "dark" ? "#F8FAFC" : "#0F172A";
  const muted = theme === "dark" ? "rgba(161,168,183,0.55)" : "rgba(71,85,105,0.5)";
  const line = theme === "dark" ? "rgba(124,133,153,0.28)" : "rgba(100,116,139,0.28)";

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col px-4 py-6 sm:px-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="font-display text-2xl font-semibold sm:text-3xl">Knowledge map</h1>
          <p className="mt-1 text-sm text-ink-2">
            Every idea from your chats and lessons, and how they connect.
            {data && (
              <span className="text-muted">
                {" "}
                {data.nodes.length} concepts · {data.edges.length} links · {data.chats_mapped} chats mapped
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button busy={sync.isPending} className="btn-primary" onClick={() => sync.mutate()}>
            {!sync.isPending && <RefreshCw size={16} />}
            {sync.isPending ? "Reading your chats…" : "Update map"}
            {!!data?.pending && !sync.isPending && (
              <span className="rounded-md bg-white/20 px-1.5 font-mono text-[11px]">{data.pending} new</span>
            )}
          </Button>
          <Menu
            trigger={<MoreHorizontal size={18} />}
            items={[
              {
                label: "Rebuild from scratch",
                icon: <RotateCcw size={15} />,
                onSelect: () => reset.mutate(),
              },
            ]}
          />
        </div>
      </div>

      {graph.isLoading ? (
        <div className="skeleton mt-6 h-[70vh]" />
      ) : !data?.nodes.length ? (
        <div className="mt-6">
          <Empty
            icon={<Network size={22} />}
            title={data?.pending ? "Build your knowledge map" : "Nothing to map yet"}
            action={
              data?.pending ? (
                <Button busy={sync.isPending} onClick={() => sync.mutate()}>
                  <Sparkles size={16} /> Map my {data.pending} chats
                </Button>
              ) : (
                <Link to="/chat" className="btn-primary btn-sm">
                  Start a chat
                </Link>
              )
            }
          >
            Ekalavya reads each chat and lesson, picks out the concepts and how they relate, and links the same idea
            across chats. Then you can explore it and ask it questions.
          </Empty>
        </div>
      ) : (
        <div className="mt-6 grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
          <div className="card relative overflow-hidden">
            <div className="absolute left-3 right-3 top-3 z-10 flex flex-wrap items-start gap-2">
              <div className="relative w-full max-w-xs">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
                <input
                  className="input bg-surface/90 py-2 pl-8 text-xs backdrop-blur"
                  placeholder="Find a concept"
                  value={find}
                  onChange={(event) => setFind(event.target.value)}
                />
                {term.length > 1 && !!found.data?.length && (
                  <div className="absolute mt-1 w-full rounded-xl border border-line bg-surface p-1 shadow-card">
                    {found.data.slice(0, 8).map((item) => (
                      <button
                        key={item.id}
                        className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs hover:bg-raised"
                        onClick={() => {
                          setFind("");
                          focusNode(item.id);
                        }}
                      >
                        <span className="h-2 w-2 rounded-full" style={{ background: colorOf(item.kind ?? "concept") }} />
                        {item.name}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              {highlight.size > 0 && (
                <button className="chip bg-surface/90" onClick={() => setHighlight(new Set())}>
                  <X size={12} /> Clear highlight
                </button>
              )}
            </div>
            <div ref={box} className="h-[62vh] min-h-[420px] w-full lg:h-[calc(100dvh-210px)]">
              <ForceGraph2D<GraphNode, GraphEdge>
                ref={graphRef}
                width={size.width}
                height={size.height}
                graphData={graphData}
                backgroundColor="rgba(0,0,0,0)"
                nodeId="id"
                linkSource="source"
                linkTarget="target"
                cooldownTicks={120}
                onEngineStop={() => undefined}
                nodeRelSize={4}
                nodeVal={(node) => 1 + node.mentions * 1.5 + node.degree * 0.4}
                nodeLabel={(node) => `${node.name}${node.description ? ` — ${node.description}` : ""}`}
                linkLabel={(link) => link.relation}
                linkColor={(link) =>
                  neighbours && !(neighbours.has(idOf(link.source)) && neighbours.has(idOf(link.target)))
                    ? "rgba(124,133,153,0.08)"
                    : line
                }
                linkWidth={(link) => Math.min(1 + link.count, 4)}
                linkDirectionalArrowLength={4}
                linkDirectionalArrowRelPos={0.92}
                linkDirectionalParticles={(link) => (selected && (idOf(link.source) === selected || idOf(link.target) === selected) ? 2 : 0)}
                linkDirectionalParticleWidth={2}
                onNodeHover={(node) => setHover(node?.id ?? null)}
                onNodeClick={(node) => focusNode(node.id)}
                onBackgroundClick={() => setSelected(null)}
                nodeCanvasObjectMode={() => "replace"}
                nodeCanvasObject={(node, context, scale) => {
                  const radius = 4 * Math.sqrt(1 + node.mentions * 1.5 + node.degree * 0.4);
                  const dim =
                    (neighbours && !neighbours.has(node.id)) || (highlight.size > 0 && !highlight.has(node.id));
                  const color = colorOf(node.kind);
                  context.globalAlpha = dim ? 0.18 : 1;
                  if (highlight.has(node.id) || node.id === selected) {
                    context.beginPath();
                    context.arc(node.x!, node.y!, radius + 4, 0, 2 * Math.PI);
                    context.fillStyle = `${color}40`;
                    context.fill();
                  }
                  context.beginPath();
                  context.arc(node.x!, node.y!, radius, 0, 2 * Math.PI);
                  context.fillStyle = color;
                  context.fill();
                  context.lineWidth = 1.5 / scale;
                  context.strokeStyle = theme === "dark" ? "#0A0B10" : "#FFFFFF";
                  context.stroke();
                  const important = node.mentions > 1 || node.degree > 2;
                  if (scale > 1.4 || important || node.id === selected || highlight.has(node.id) || neighbours?.has(node.id)) {
                    const size = Math.max(11 / scale, 2.5);
                    context.font = `${node.id === selected ? 600 : 500} ${size}px Inter, sans-serif`;
                    context.textAlign = "center";
                    context.textBaseline = "top";
                    context.fillStyle = dim ? muted : ink;
                    context.fillText(node.name, node.x!, node.y! + radius + 2 / scale);
                  }
                  context.globalAlpha = 1;
                }}
                nodePointerAreaPaint={(node, color, context) => {
                  const radius = 4 * Math.sqrt(1 + node.mentions * 1.5 + node.degree * 0.4) + 3;
                  context.fillStyle = color;
                  context.beginPath();
                  context.arc(node.x!, node.y!, radius, 0, 2 * Math.PI);
                  context.fill();
                }}
              />
            </div>
            <div className="absolute bottom-3 left-3 flex flex-wrap gap-x-3 gap-y-1 rounded-xl bg-surface/90 px-3 py-2 text-[11px] text-ink-2 backdrop-blur">
              {kinds.slice(0, palette.length).map((kind) => (
                <span key={kind} className="inline-flex items-center gap-1.5 capitalize">
                  <span className="h-2 w-2 rounded-full" style={{ background: colorOf(kind) }} />
                  {kind}
                </span>
              ))}
              {kinds.length > palette.length && (
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full" style={{ background: OTHER[theme] }} />
                  other
                </span>
              )}
              <span className="text-muted">· size = how often it comes up</span>
            </div>
          </div>

          <aside className="card flex max-h-[calc(100dvh-140px)] flex-col overflow-hidden">
            <div className="border-b border-line p-3">
              <Tabs
                value={panel}
                onChange={setPanel}
                items={[
                  { value: "ask", label: "Ask your map" },
                  { value: "concept", label: "Concept" },
                ]}
              />
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              {panel === "ask" ? (
                <div>
                  <p className="text-sm text-ink-2">
                    Ask about anything you have studied. The answer comes only from your own map, and shows which
                    concepts and chats it used.
                  </p>
                  <form
                    className="mt-3 flex gap-2"
                    onSubmit={(event) => {
                      event.preventDefault();
                      if (question.trim().length > 1) ask.mutate();
                    }}
                  >
                    <input
                      className="input"
                      placeholder="e.g. How does wavelength relate to colour?"
                      value={question}
                      onChange={(event) => setQuestion(event.target.value)}
                    />
                    <button className="btn-primary px-3" disabled={question.trim().length < 2 || ask.isPending} aria-label="Ask">
                      {ask.isPending ? <Spinner size={16} /> : <Send size={16} />}
                    </button>
                  </form>
                  {answer && (
                    <div className="mt-4 animate-fade-in space-y-4">
                      <div className="rounded-xl bg-raised/60 p-3">
                        <Markdown content={answer.answer} />
                      </div>
                      {answer.nodes.length > 0 && (
                        <div>
                          <p className="eyebrow mb-2">Concepts used</p>
                          <div className="flex flex-wrap gap-1.5">
                            {answer.nodes.map((id) => {
                              const node = data.nodes.find((item) => item.id === id);
                              return node ? (
                                <button key={id} className="chip" onClick={() => focusNode(id)}>
                                  <span className="h-2 w-2 rounded-full" style={{ background: colorOf(node.kind) }} />
                                  {node.name}
                                </button>
                              ) : null;
                            })}
                          </div>
                        </div>
                      )}
                      {answer.chats.length > 0 && <ChatLinks chats={answer.chats} />}
                    </div>
                  )}
                </div>
              ) : !selected ? (
                <p className="text-sm text-ink-2">Click a concept in the map to see what it connects to.</p>
              ) : detail.isLoading || !detail.data ? (
                <Spinner />
              ) : (
                <div className="animate-fade-in">
                  <span className="inline-flex items-center gap-1.5 text-xs capitalize text-ink-2">
                    <span className="h-2 w-2 rounded-full" style={{ background: colorOf(detail.data.kind) }} />
                    {detail.data.kind}
                  </span>
                  <h2 className="mt-1 font-display text-xl font-semibold">{detail.data.name}</h2>
                  {detail.data.description && <p className="mt-2 text-sm text-ink-2">{detail.data.description}</p>}
                  {detail.data.relations.length > 0 && (
                    <div className="mt-5">
                      <p className="eyebrow mb-2">Connections</p>
                      <ul className="space-y-1">
                        {detail.data.relations.map((item, index) => (
                          <li key={index}>
                            <button
                              className="w-full rounded-lg px-2 py-1.5 text-left text-sm hover:bg-raised"
                              onClick={() => focusNode(item.node.id)}
                              title={item.detail ?? undefined}
                            >
                              {item.direction === "out" ? (
                                <>
                                  <span className="text-muted">{item.relation} </span>
                                  <span className="font-medium">{item.node.name}</span>
                                </>
                              ) : (
                                <>
                                  <span className="font-medium">{item.node.name}</span>
                                  <span className="text-muted"> {item.relation} this</span>
                                </>
                              )}
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <div className="mt-5">
                    <ChatLinks chats={detail.data.chats} />
                  </div>
                  <Link
                    to={`/chat?prompt=${encodeURIComponent(`Explain ${detail.data.name} with a video`)}`}
                    className={clsx("btn-secondary btn-sm mt-5 w-full")}
                  >
                    <Sparkles size={14} /> Learn more about {detail.data.name}
                  </Link>
                </div>
              )}
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}

function ChatLinks({ chats }: { chats: Chat[] }) {
  return (
    <div>
      <p className="eyebrow mb-2">From your chats</p>
      <div className="space-y-1">
        {chats.map((chat) => (
          <Link
            key={chat.id}
            to={chat.has_video ? `/watch/${chat.id}` : `/chat/${chat.id}`}
            className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-ink-2 hover:bg-raised hover:text-ink"
          >
            {chat.has_video ? <Clapperboard size={14} /> : <MessageSquare size={14} />}
            <span className="flex-1 truncate">{chat.title}</span>
            <ArrowRight size={13} className="text-muted" />
          </Link>
        ))}
      </div>
    </div>
  );
}
