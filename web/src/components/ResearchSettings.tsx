import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { api } from "../lib/api";
import { duration, money } from "../lib/format";
import type { ResearchKnob } from "../lib/types";
import { Spinner } from "./common";

type Values = Record<string, number>;

/** How much more (or less) a video is expected to cost and take with these values. */
function estimate(knobs: ResearchKnob[], values: Values) {
  // Chat memory is paid per chat turn, not per video.
  return knobs.filter((knob) => knob.key !== "chat_turns_kept").reduce(
    (total, knob) => {
      const steps = (values[knob.key] ?? knob.default) - knob.default;
      return {
        cost: total.cost + steps * knob.cost_per_step_usd,
        seconds: total.seconds + steps * knob.seconds_per_step,
      };
    },
    { cost: 0, seconds: 0 },
  );
}

function signed(text: string, value: number) {
  return `${value > 0 ? "+" : "−"}${text}`;
}

export function ResearchSettings({
  values,
  onChange,
}: {
  values: Values;
  onChange: (values: Values) => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["research-options"],
    queryFn: () => api<{ settings: ResearchKnob[] }>("/v1/research/options"),
    staleTime: 5 * 60_000,
  });
  if (isLoading || !data) {
    return (
      <div className="flex justify-center py-10 text-muted">
        <Spinner />
      </div>
    );
  }
  const knobs = data.settings;
  const change = estimate(knobs, values);
  const costly = change.cost > 0.0005 || change.seconds > 1;
  const cheaper = change.cost < -0.0005 || change.seconds < -1;

  return (
    <div className="space-y-5">
      <p className="text-sm text-ink-2">
        How much material each video collects and how much of a chat the assistant keeps in
        view. Higher values can make videos more thorough, but they cost more and take longer.
      </p>
      {costly && (
        <div className="flex gap-3 rounded-xl border border-amber/40 bg-amber/10 p-3 text-sm">
          <AlertTriangle size={18} className="mt-0.5 shrink-0 text-amber" />
          <p>
            With these values each video may cost about{" "}
            <strong>{money(Math.max(change.cost, 0))} more</strong> and take about{" "}
            <strong>{duration(Math.max(change.seconds, 0))} longer</strong> than with the
            defaults. Estimates come from your recent usage where available.
          </p>
        </div>
      )}
      {!costly && cheaper && (
        <p className="rounded-xl border border-line bg-sunken p-3 text-sm text-ink-2">
          Below the defaults: videos may be a little cheaper and faster, but use less material.
        </p>
      )}
      {knobs.map((knob) => {
        const value = values[knob.key] ?? knob.default;
        const steps = value - knob.default;
        return (
          <div key={knob.key}>
            <div className="mb-1 flex items-baseline justify-between gap-3">
              <label htmlFor={`research-${knob.key}`} className="label mb-0">
                {knob.label}
              </label>
              <span className="font-mono text-sm text-ink">
                {value}
                {value !== knob.default && (
                  <span className="ml-2 text-xs text-muted">default {knob.default}</span>
                )}
              </span>
            </div>
            <input
              id={`research-${knob.key}`}
              type="range"
              className="w-full accent-[rgb(var(--c-primary))]"
              min={knob.min}
              max={knob.max}
              step={1}
              value={value}
              onChange={(event) => onChange({ ...values, [knob.key]: Number(event.target.value) })}
            />
            <p className="mt-1 text-xs text-muted">
              {knob.help}
              {steps !== 0 && (
                <span className={steps > 0 ? "text-amber" : undefined}>
                  {" "}
                  ≈ {signed(money(Math.abs(steps * knob.cost_per_step_usd)), steps)},{" "}
                  {signed(duration(Math.abs(steps * knob.seconds_per_step)), steps)}
                  {knob.key === "chat_turns_kept" ? " per chat turn" : " per video"}.
                </span>
              )}
            </p>
          </div>
        );
      })}
      <button
        className="btn-ghost btn-sm"
        onClick={() =>
          onChange(Object.fromEntries(knobs.map((knob) => [knob.key, knob.default])))
        }
      >
        <RotateCcw size={14} /> Reset to defaults
      </button>
    </div>
  );
}
