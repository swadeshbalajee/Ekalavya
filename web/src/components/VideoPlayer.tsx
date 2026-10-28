import clsx from "clsx";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { mediaUrl } from "../lib/api";
import { clock, LANGUAGE_CODES } from "../lib/format";
import type { Chapter } from "../lib/types";

export type PlayerHandle = {
  seek: (seconds: number, play?: boolean) => void;
  time: () => number;
  element: () => HTMLVideoElement | null;
};

type Props = {
  videoId: string;
  version?: number | null;
  chapters: Chapter[];
  subtitleLanguage?: string;
  startAt?: number;
  onTime?: (seconds: number, duration: number) => void;
  onPause?: (seconds: number, duration: number) => void;
  showChapterStrip?: boolean;
  className?: string;
};

/** The lesson video with its subtitle track and a clickable scene strip underneath. */
export const VideoPlayer = forwardRef<PlayerHandle, Props>(function VideoPlayer(
  {
    videoId,
    version,
    chapters,
    subtitleLanguage = "English",
    startAt,
    onTime,
    onPause,
    showChapterStrip = true,
    className,
  },
  ref,
) {
  const video = useRef<HTMLVideoElement>(null);
  const [now, setNow] = useState(0);
  const pin = version ? { version } : {};
  useImperativeHandle(ref, () => ({
    seek: (seconds, play = true) => {
      if (!video.current) return;
      video.current.currentTime = seconds;
      if (play) void video.current.play().catch(() => {});
    },
    time: () => video.current?.currentTime ?? 0,
    element: () => video.current,
  }));

  useEffect(() => {
    const element = video.current;
    if (!element || !startAt) return;
    const seek = () => {
      element.currentTime = startAt;
    };
    if (element.readyState >= 1) seek();
    else element.addEventListener("loadedmetadata", seek, { once: true });
  }, [startAt, videoId, version]);

  const total = Math.max(...chapters.map((chapter) => chapter.end || 0), 0);
  const active = chapters.findIndex((chapter) => now >= chapter.start && now < chapter.end);

  return (
    <div className={className}>
      <video
        ref={video}
        key={`${videoId}-${version ?? "current"}`}
        controls
        playsInline
        preload="metadata"
        crossOrigin="anonymous"
        className="aspect-video w-full rounded-2xl bg-black shadow-card"
        onTimeUpdate={(event) => {
          const element = event.currentTarget;
          setNow(element.currentTime);
          onTime?.(element.currentTime, element.duration || total);
        }}
        onPause={(event) =>
          onPause?.(event.currentTarget.currentTime, event.currentTarget.duration || total)
        }
        onEnded={(event) =>
          onPause?.(event.currentTarget.duration, event.currentTarget.duration || total)
        }
      >
        <source src={mediaUrl(`/v1/videos/${videoId}/video`, pin)} type="video/mp4" />
        <track kind="chapters" src={mediaUrl(`/v1/videos/${videoId}/chapters.vtt`, pin)} default />
        <track
          kind="subtitles"
          label="Subtitles"
          srcLang={LANGUAGE_CODES[subtitleLanguage.toLowerCase()] ?? "en"}
          src={mediaUrl(`/v1/videos/${videoId}/subtitles.vtt`, pin)}
          default
        />
      </video>
      {showChapterStrip && total > 0 && chapters.length > 1 && (
        <div className="mt-3 flex h-2 gap-[3px]" aria-label="Scenes">
          {chapters.map((chapter, index) => (
            <button
              key={chapter.scene_id + index}
              title={`${index + 1}. ${chapter.title} · ${clock(chapter.start)}`}
              aria-label={`Scene ${index + 1}: ${chapter.title}, at ${clock(chapter.start)}`}
              onClick={() => {
                if (video.current) {
                  video.current.currentTime = chapter.start;
                  void video.current.play().catch(() => {});
                }
              }}
              className={clsx(
                "h-full rounded-full transition hover:opacity-100",
                index === active
                  ? "bg-gradient-to-r from-primary to-teal"
                  : index < active
                    ? "bg-primary/60"
                    : "bg-raised hover:bg-primary/40",
              )}
              style={{ flexGrow: Math.max(chapter.end - chapter.start, 1) }}
            />
          ))}
        </div>
      )}
    </div>
  );
});
