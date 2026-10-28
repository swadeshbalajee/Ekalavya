import { useState } from "react";
import { errorText } from "../lib/api";
import { useAuth } from "../lib/auth";
import { LANGUAGES } from "../lib/format";
import { useToast } from "../lib/ui";
import { Button, Field, Modal, Tabs } from "./common";

const COLORS = ["#7C5CFF", "#22D3EE", "#F6B26B", "#22C55E", "#EC4899", "#EF4444", "#64748B"];

export function SettingsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { user, update, logout } = useAuth();
  const toast = useToast();
  const [tab, setTab] = useState<"account" | "video">("account");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState(() => ({
    display_name: user?.display_name ?? "",
    email: user?.email ?? "",
    avatar_color: user?.avatar_color ?? COLORS[0],
    default_duration_minutes: user?.default_duration_minutes ?? 3,
    default_text_language: user?.default_text_language ?? "English",
    default_voice_language: user?.default_voice_language ?? "English",
  }));
  if (!user) return null;
  const set = (changes: Partial<typeof form>) => setForm((value) => ({ ...value, ...changes }));

  const save = async () => {
    setBusy(true);
    try {
      await update({ ...form, email: form.email || null });
      toast("Settings saved");
      onClose();
    } catch (error) {
      toast(errorText(error), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Settings"
      footer={
        <>
          <button className="btn-ghost mr-auto" onClick={() => void logout()}>
            Sign out
          </button>
          <button className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <Button busy={busy} onClick={save}>
            Save changes
          </Button>
        </>
      }
    >
      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          { value: "account", label: "Account" },
          { value: "video", label: "Video defaults" },
        ]}
        className="mb-5"
      />
      {tab === "account" ? (
        <div className="space-y-4">
          <Field label="Username">
            <input className="input opacity-70" value={user.username} disabled />
          </Field>
          <Field label="Display name">
            <input
              className="input"
              value={form.display_name}
              onChange={(event) => set({ display_name: event.target.value })}
            />
          </Field>
          <Field label="Email">
            <input
              className="input"
              type="email"
              value={form.email}
              onChange={(event) => set({ email: event.target.value })}
            />
          </Field>
          <div>
            <span className="label">Avatar colour</span>
            <div className="flex gap-2">
              {COLORS.map((color) => (
                <button
                  key={color}
                  aria-label={`Colour ${color}`}
                  onClick={() => set({ avatar_color: color })}
                  className="h-8 w-8 rounded-full ring-offset-2 ring-offset-surface transition"
                  style={{
                    background: color,
                    boxShadow: form.avatar_color === color ? `0 0 0 2px ${color}` : undefined,
                  }}
                />
              ))}
            </div>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <p className="text-sm text-ink-2">
            Used for new chats. Each chat can change its own languages from its menu.
          </p>
          <Field label="Target video length (minutes)">
            <input
              className="input"
              type="number"
              min={0.5}
              max={30}
              step={0.5}
              value={form.default_duration_minutes}
              onChange={(event) => set({ default_duration_minutes: Number(event.target.value) })}
            />
          </Field>
          <Field label="Text in video">
            <select
              className="input"
              value={form.default_text_language}
              onChange={(event) => set({ default_text_language: event.target.value })}
            >
              {LANGUAGES.map((language) => (
                <option key={language}>{language}</option>
              ))}
            </select>
          </Field>
          <Field label="Narration voice">
            <select
              className="input"
              value={form.default_voice_language}
              onChange={(event) => set({ default_voice_language: event.target.value })}
            >
              {LANGUAGES.map((language) => (
                <option key={language}>{language}</option>
              ))}
            </select>
          </Field>
        </div>
      )}
    </Modal>
  );
}
