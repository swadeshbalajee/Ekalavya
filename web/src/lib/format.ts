export function clock(seconds: number | null | undefined): string {
  const value = Math.max(0, Math.floor(seconds ?? 0));
  const hours = Math.floor(value / 3600);
  const mins = Math.floor((value % 3600) / 60);
  const rest = String(value % 60).padStart(2, "0");
  return hours ? `${hours}:${String(mins).padStart(2, "0")}:${rest}` : `${mins}:${rest}`;
}

export function minutes(seconds: number | null | undefined): string {
  const value = seconds ?? 0;
  if (value < 60) return `${Math.max(1, Math.round(value))} sec`;
  return `${Math.round(value / 60)} min`;
}

export function compact(value: number): string {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(
    value,
  );
}

export function ago(value: string | null | undefined): string {
  if (!value) return "";
  const seconds = Math.round((Date.now() - new Date(value).getTime()) / 1000);
  const units: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, "second"],
    [60, "minute"],
    [24, "hour"],
    [7, "day"],
    [4.35, "week"],
    [12, "month"],
    [Infinity, "year"],
  ];
  let amount = seconds;
  for (const [size, unit] of units) {
    if (Math.abs(amount) < size) {
      return new Intl.RelativeTimeFormat("en", { numeric: "auto" }).format(
        -Math.round(amount),
        unit,
      );
    }
    amount /= size;
  }
  return "";
}

export function money(value: number | null | undefined): string {
  const amount = value ?? 0;
  return amount < 0.01 && amount > 0 ? `$${amount.toFixed(4)}` : `$${amount.toFixed(2)}`;
}

export function count(value: number | null | undefined): string {
  return compact(value ?? 0);
}

export function duration(seconds: number | null | undefined): string {
  const value = seconds ?? 0;
  if (!value) return "—";
  if (value < 60) return `${Math.round(value)}s`;
  if (value < 3600) return `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`;
  return `${Math.floor(value / 3600)}h ${Math.round((value % 3600) / 60)}m`;
}

export function isoDate(date: Date): string {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

export function parseDate(value: string): Date {
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  return new Date(year, month - 1, day);
}

export function dayLabel(value: string): string {
  return parseDate(value).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export const LANGUAGES = [
  "English",
  "Hindi",
  "Bengali",
  "Tamil",
  "Telugu",
  "Kannada",
  "Malayalam",
  "Marathi",
  "Gujarati",
  "Punjabi",
  "Odia",
  "Urdu",
  "Spanish",
  "French",
  "German",
  "Arabic",
  "Japanese",
  "Chinese",
];

export const LANGUAGE_CODES: Record<string, string> = {
  english: "en",
  hindi: "hi",
  bengali: "bn",
  tamil: "ta",
  telugu: "te",
  kannada: "kn",
  malayalam: "ml",
  marathi: "mr",
  gujarati: "gu",
  punjabi: "pa",
  odia: "or",
  urdu: "ur",
  spanish: "es",
  french: "fr",
  german: "de",
  arabic: "ar",
  japanese: "ja",
  chinese: "zh",
};

export const BUSY = new Set(["GENERATING", "EDITING", "LOCALIZING", "CANCELLING"]);
