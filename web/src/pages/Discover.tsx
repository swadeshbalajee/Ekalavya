import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { Compass, Search, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Empty } from "../components/common";
import { GridSkeleton, VideoGrid } from "../components/VideoCard";
import { api } from "../lib/api";
import type { VideoCard } from "../lib/types";
import { useDebounced } from "../lib/ui";

type Results = { total: number; match: "all" | "keyword" | "related"; items: VideoCard[] };

export default function DiscoverPage() {
  const [params, setParams] = useSearchParams();
  const [text, setText] = useState(params.get("q") ?? "");
  const q = useDebounced(text.trim(), 350);
  const subject = params.get("subject") ?? "";
  const sort = params.get("sort") === "recent" ? "recent" : "popular";

  useEffect(() => setText(params.get("q") ?? ""), [params]);
  useEffect(() => {
    const next = new URLSearchParams(params);
    if (q) next.set("q", q);
    else next.delete("q");
    if (next.toString() !== params.toString()) setParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const subjects = useQuery({
    queryKey: ["subjects"],
    queryFn: () => api<{ subject: string; count: number }[]>("/v1/videos/subjects"),
  });
  const results = useQuery({
    queryKey: ["videos", q, subject, sort],
    queryFn: () => api<Results>("/v1/videos", { params: { q, subject, sort, limit: 60 } }),
    placeholderData: (previous) => previous,
  });
  const data = results.data;

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:py-10">
      <h1 className="font-display text-2xl font-semibold sm:text-3xl">Discover</h1>
      <p className="mt-1 text-sm text-ink-2">
        Explainer videos published by Ekalavya learners. Search by any keyword or concept.
      </p>
      <div className="relative mt-6">
        <Search size={18} className="absolute left-4 top-1/2 -translate-y-1/2 text-muted" />
        <input
          className="input h-12 rounded-2xl pl-11 text-base"
          placeholder="Search for topics, concepts, keywords…"
          value={text}
          onChange={(event) => setText(event.target.value)}
          aria-label="Search public videos"
          autoFocus={!text}
        />
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button className={clsx("chip", !subject && "chip-active")} onClick={() => update("subject", "")}>
          All
        </button>
        {subjects.data?.map((item) => (
          <button
            key={item.subject}
            className={clsx("chip", subject === item.subject && "chip-active")}
            onClick={() => update("subject", subject === item.subject ? "" : item.subject)}
          >
            {item.subject}
            <span className="font-mono text-[10px] text-muted">{item.count}</span>
          </button>
        ))}
        <div className="ml-auto flex rounded-xl border border-line bg-surface p-0.5 text-xs">
          {(["popular", "recent"] as const).map((value) => (
            <button
              key={value}
              onClick={() => update("sort", value === "popular" ? "" : value)}
              className={clsx(
                "rounded-lg px-3 py-1.5 font-medium capitalize transition",
                sort === value ? "bg-raised text-ink" : "text-muted hover:text-ink",
              )}
              disabled={!!q}
            >
              {value}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-8">
        {results.isLoading ? (
          <GridSkeleton count={8} />
        ) : !data?.items.length ? (
          <Empty icon={<Compass size={22} />} title={q ? "No videos match" : "No public videos yet"}>
            {q
              ? "Try fewer or different keywords — or ask Ekalavya to make a video on it."
              : "When learners publish their explainers, they appear here."}
          </Empty>
        ) : (
          <>
            <p className="mb-4 flex items-center gap-2 text-sm text-ink-2">
              {data.match === "related" ? (
                <>
                  <Sparkles size={15} className="text-teal" />
                  No video has every word of “{q}”. These are the closest in meaning.
                </>
              ) : q ? (
                `${data.total} video${data.total === 1 ? "" : "s"} matching “${q}”`
              ) : (
                `${sort === "recent" ? "Recently published" : "Popular"}${subject ? ` in ${subject}` : ""}`
              )}
            </p>
            <VideoGrid videos={data.items} />
          </>
        )}
      </div>
    </div>
  );
}
