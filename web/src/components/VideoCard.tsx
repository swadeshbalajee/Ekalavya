import clsx from "clsx";
import { Bookmark, Eye, Globe2, Lock, Play } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { mediaUrl } from "../lib/api";
import { ago, clock, compact } from "../lib/format";
import type { VideoCard as Video } from "../lib/types";
import { Bar } from "./common";

/** Deterministic gradient per video, shown until (or instead of) the frame thumbnail. */
function gradientFor(id: string): string {
  let hash = 0;
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) | 0;
  const hue = 230 + (Math.abs(hash) % 80);
  return `radial-gradient(120% 90% at 20% 10%, hsl(${hue} 85% 62% / .55), transparent 60%),
    radial-gradient(90% 80% at 90% 100%, hsl(${(hue + 120) % 360} 85% 55% / .35), transparent 60%),
    linear-gradient(135deg, #12141f, #1d2233)`;
}

export function Thumbnail({
  id,
  title,
  className,
  children,
}: {
  id: string;
  title: string;
  className?: string;
  children?: React.ReactNode;
}) {
  const [failed, setFailed] = useState(false);
  return (
    <div
      className={clsx("relative aspect-video overflow-hidden bg-sunken", className)}
      style={{ background: gradientFor(id) }}
    >
      {!failed && (
        <img
          src={mediaUrl(`/v1/videos/${id}/thumbnail.jpg`)}
          alt=""
          loading="lazy"
          onError={() => setFailed(true)}
          className="absolute inset-0 h-full w-full object-cover transition duration-500 group-hover:scale-[1.03]"
        />
      )}
      {failed && (
        <div className="absolute inset-0 flex items-center p-4">
          <span className="line-clamp-3 font-display text-base font-semibold text-white/90 drop-shadow">
            {title}
          </span>
        </div>
      )}
      {children}
    </div>
  );
}

export function VideoCard({
  video,
  showAuthor = true,
  compactLayout,
}: {
  video: Video;
  showAuthor?: boolean;
  compactLayout?: boolean;
}) {
  const progress =
    video.progress && video.progress.duration
      ? video.progress.position / video.progress.duration
      : 0;
  const href = `/watch/${video.id}${video.progress && !video.progress.completed ? `?t=${Math.floor(video.progress.position)}` : ""}`;
  return (
    <Link
      to={href}
      className={clsx(
        "group block overflow-hidden rounded-2xl border border-line bg-surface transition hover:-translate-y-0.5 hover:border-primary/50 hover:shadow-glow",
        compactLayout && "flex gap-3 rounded-xl p-2 hover:translate-y-0",
      )}
    >
      <Thumbnail
        id={video.id}
        title={video.title}
        className={clsx(compactLayout ? "w-40 shrink-0 rounded-lg" : "")}
      >
        <div className="absolute inset-0 flex items-center justify-center opacity-0 transition group-hover:opacity-100">
          <span className="flex h-12 w-12 items-center justify-center rounded-full bg-black/55 text-white backdrop-blur">
            <Play size={20} className="ml-0.5" fill="currentColor" />
          </span>
        </div>
        {video.duration > 0 && (
          <span className="absolute bottom-2 right-2 rounded-md bg-black/70 px-1.5 py-0.5 font-mono text-[11px] text-white">
            {clock(video.duration)}
          </span>
        )}
        {video.is_owner && (
          <span className="absolute left-2 top-2 inline-flex items-center gap-1 rounded-md bg-black/60 px-1.5 py-0.5 text-[10px] font-medium text-white backdrop-blur">
            {video.visibility === "public" ? <Globe2 size={11} /> : <Lock size={11} />}
            {video.visibility === "public" ? "Public" : "Private"}
          </span>
        )}
        {video.saved && !video.is_owner && (
          <span className="absolute left-2 top-2 rounded-md bg-black/60 p-1 text-white">
            <Bookmark size={12} fill="currentColor" />
          </span>
        )}
        {progress > 0 && (
          <div className="absolute inset-x-0 bottom-0 h-1 bg-black/40">
            <div
              className="h-full bg-gradient-to-r from-primary to-teal"
              style={{ width: `${Math.min(progress, 1) * 100}%` }}
            />
          </div>
        )}
      </Thumbnail>
      <div className={clsx(compactLayout ? "min-w-0 py-1" : "p-3.5")}>
        <h3 className="line-clamp-2 text-sm font-semibold leading-snug text-ink">{video.title}</h3>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
          {video.subject && <span className="text-teal">{video.subject}</span>}
          {showAuthor && !video.is_owner && <span>{video.author}</span>}
          {video.visibility === "public" && (
            <span className="inline-flex items-center gap-1">
              <Eye size={12} />
              {compact(video.views)}
            </span>
          )}
          {!compactLayout && <span>{ago(video.published_at || video.created_at)}</span>}
        </div>
        {progress > 0 && !compactLayout && (
          <p className="mt-2 text-xs text-ink-2">
            {video.progress?.completed
              ? "Watched"
              : `${clock(video.progress?.position)} of ${clock(video.progress?.duration)}`}
          </p>
        )}
      </div>
    </Link>
  );
}

export function VideoGrid({ videos }: { videos: Video[] }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
      {videos.map((video) => (
        <VideoCard key={video.id} video={video} />
      ))}
    </div>
  );
}

export function VideoRow({ videos }: { videos: Video[] }) {
  return (
    <div className="-mx-4 flex snap-x gap-4 overflow-x-auto px-4 pb-2 sm:mx-0 sm:grid sm:grid-cols-2 sm:overflow-visible sm:px-0 lg:grid-cols-3 2xl:grid-cols-4">
      {videos.map((video) => (
        <div key={video.id} className="w-[78%] shrink-0 snap-start sm:w-auto">
          <VideoCard video={video} />
        </div>
      ))}
    </div>
  );
}

export function GridSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
      {Array.from({ length: count }, (_, index) => (
        <div key={index} className="overflow-hidden rounded-2xl border border-line bg-surface">
          <div className="skeleton aspect-video rounded-none" />
          <div className="space-y-2 p-3.5">
            <div className="skeleton h-4 w-4/5" />
            <div className="skeleton h-3 w-2/5" />
          </div>
        </div>
      ))}
    </div>
  );
}

export function ContinueBar({ video }: { video: Video }) {
  const value = video.progress?.duration ? video.progress.position / video.progress.duration : 0;
  return <Bar value={value} />;
}
