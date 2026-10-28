import clsx from "clsx";
import { ArrowUp, FileText, Link2, Mic, Paperclip, Square, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, errorText } from "../../lib/api";
import { useToast } from "../../lib/ui";
import { Spinner } from "../common";

export type StagedPaper = { name: string; file?: File; link?: string };
export type Effort = "low" | "medium" | "high";

/** Mono 16-bit PCM WAV, which every speech-to-text provider accepts. */
async function toWav(blob: Blob): Promise<string> {
  const context = new AudioContext();
  const audio = await context.decodeAudioData(await blob.arrayBuffer());
  await context.close();
  const samples = audio.getChannelData(0);
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (offset: number, value: string) =>
    [...value].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, audio.sampleRate, true);
  view.setUint32(28, audio.sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, index) => {
    const clamped = Math.max(-1, Math.min(1, sample));
    view.setInt16(44 + index * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
  });
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let index = 0; index < bytes.length; index += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  }
  return btoa(binary);
}

export function Composer({
  onSend,
  busy,
  placeholder,
  language,
  initialText = "",
  openAttach = false,
  quote,
  onClearQuote,
}: {
  onSend: (text: string, paper: StagedPaper | null, effort: Effort) => void;
  busy?: boolean;
  placeholder: string;
  language?: string;
  initialText?: string;
  openAttach?: boolean;
  quote?: string | null;
  onClearQuote?: () => void;
}) {
  const toast = useToast();
  const [text, setText] = useState(initialText);
  const [paper, setPaper] = useState<StagedPaper | null>(null);
  const [attaching, setAttaching] = useState(openAttach);
  const [link, setLink] = useState("");
  const [effort, setEffort] = useState<Effort>("medium");
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const file = useRef<HTMLInputElement>(null);

  useEffect(() => setText(initialText), [initialText]);
  useEffect(() => {
    const element = field.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 220)}px`;
  }, [text]);

  const send = () => {
    if (!text.trim() || busy) return;
    onSend(text.trim(), paper, effort);
    setText("");
    setPaper(null);
  };

  const record = async () => {
    if (recording) {
      recorder.current?.stop();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks: Blob[] = [];
      const media = new MediaRecorder(stream);
      media.ondataavailable = (event) => chunks.push(event.data);
      media.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        setRecording(false);
        setTranscribing(true);
        try {
          const audio = await toWav(new Blob(chunks, { type: media.mimeType }));
          const result = await api<{ text: string }>("/v1/capabilities/stt/transcribe", {
            body: { audio_base64: audio, language },
          });
          const heard = (result.text || "").trim();
          if (heard) setText((value) => `${value} ${heard}`.trim());
          else toast("I couldn't hear any words in that recording.", "info");
        } catch (error) {
          toast(`Speech-to-text is unavailable: ${errorText(error)}`, "error");
        } finally {
          setTranscribing(false);
        }
      };
      recorder.current = media;
      media.start();
      setRecording(true);
    } catch {
      toast("Microphone access was blocked.", "error");
    }
  };

  return (
    <div className="mx-auto w-full max-w-3xl">
      {quote && (
        <div className="mb-2 flex items-start gap-2 rounded-xl border border-line bg-surface px-3 py-2 text-xs text-ink-2">
          <span className="line-clamp-2 flex-1 border-l-2 border-primary pl-2">{quote}</span>
          <button className="text-muted hover:text-ink" onClick={onClearQuote} aria-label="Remove quote">
            <X size={14} />
          </button>
        </div>
      )}
      {paper && (
        <div className="mb-2 inline-flex max-w-full items-center gap-2 rounded-xl border border-success/40 bg-success/10 px-3 py-1.5 text-xs text-ink">
          <FileText size={14} className="text-success" />
          <span className="truncate">{paper.name}</span>
          <span className="text-muted">· sent with your next message</span>
          <button onClick={() => setPaper(null)} aria-label="Remove attachment" className="text-muted hover:text-ink">
            <X size={13} />
          </button>
        </div>
      )}
      {attaching && (
        <div className="mb-2 animate-fade-in rounded-2xl border border-line bg-surface p-3">
          <div className="flex flex-col gap-2 sm:flex-row">
            <button className="btn-secondary btn-sm" onClick={() => file.current?.click()}>
              <FileText size={14} /> Upload a PDF
            </button>
            <form
              className="flex flex-1 gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                if (!link.trim()) return;
                setPaper({ name: link.trim(), link: link.trim() });
                setLink("");
                setAttaching(false);
              }}
            >
              <div className="relative flex-1">
                <Link2 size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
                <input
                  className="input py-1.5 pl-8 text-xs"
                  placeholder="…or paste a paper link (arXiv or a PDF URL)"
                  value={link}
                  onChange={(event) => setLink(event.target.value)}
                />
              </div>
              <button className="btn-secondary btn-sm" disabled={!link.trim()}>
                Attach
              </button>
            </form>
            <button className="icon-btn h-8 w-8" onClick={() => setAttaching(false)} aria-label="Close">
              <X size={15} />
            </button>
          </div>
          <p className="mt-2 text-[11px] text-muted">Then ask, for example: “explain this paper” for a full video lecture.</p>
          <input
            ref={file}
            type="file"
            accept="application/pdf,.pdf"
            hidden
            onChange={(event) => {
              const chosen = event.target.files?.[0];
              if (chosen) {
                setPaper({ name: chosen.name, file: chosen });
                setAttaching(false);
              }
              event.target.value = "";
            }}
          />
        </div>
      )}
      <div className="rounded-2xl border border-line bg-surface p-2 shadow-card transition focus-within:border-primary/60 focus-within:shadow-glow">
        <textarea
          ref={field}
          rows={1}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              send();
            }
          }}
          placeholder={placeholder}
          aria-label="Message"
          className="block max-h-[220px] w-full resize-none bg-transparent px-2.5 py-2 text-[15px] outline-none placeholder:text-muted"
        />
        <div className="flex items-center gap-1 pt-1">
          <button className="icon-btn" onClick={() => setAttaching(!attaching)} aria-label="Attach a paper" title="Attach a paper">
            <Paperclip size={18} />
          </button>
          <div className="ml-1 hidden rounded-lg border border-line p-0.5 text-[11px] sm:flex" role="radiogroup" aria-label="Thinking">
            {(
              [
                ["low", "Quick"],
                ["medium", "Balanced"],
                ["high", "Deep"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                role="radio"
                aria-checked={effort === value}
                onClick={() => setEffort(value)}
                className={clsx("rounded-md px-2 py-1 font-medium transition", effort === value ? "bg-primary/15 text-ink" : "text-muted hover:text-ink")}
                title={value === "high" ? "Reasons longer before answering" : value === "low" ? "Answers faster" : "Default"}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="ml-auto flex items-center gap-1">
            <button
              className={clsx("icon-btn", recording && "bg-danger/15 text-danger")}
              onClick={record}
              disabled={transcribing}
              aria-label={recording ? "Stop recording" : "Record a voice message"}
              title={recording ? "Stop recording" : "Speak your message"}
            >
              {transcribing ? <Spinner size={16} /> : recording ? <Square size={16} fill="currentColor" /> : <Mic size={18} />}
            </button>
            <button className="btn-primary h-9 w-9 p-0" onClick={send} disabled={!text.trim() || busy} aria-label="Send message">
              {busy ? <Spinner size={16} /> : <ArrowUp size={18} />}
            </button>
          </div>
        </div>
      </div>
      <p className="mt-2 text-center text-[11px] text-muted">Ekalavya can make mistakes. Verify important information.</p>
    </div>
  );
}
