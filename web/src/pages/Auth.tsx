import { ArrowLeft } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { Button, Field, LogoMark } from "../components/common";
import { ThemeButton } from "../components/AppShell";
import { errorText } from "../lib/api";
import { useAuth } from "../lib/auth";

export function AuthPage({ mode }: { mode: "login" | "signup" }) {
  const { user, login, register } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = params.get("next") || "/home";
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  if (user) return <Navigate to={next} replace />;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (mode === "login") await login(username, password);
      else await register(username, password, name);
      navigate(next, { replace: true });
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative flex min-h-[100dvh] items-center justify-center overflow-hidden px-4 py-10">
      <div className="pointer-events-none absolute inset-0" style={{ background: "var(--hero-glow)" }} />
      <div className="absolute left-4 right-4 top-4 flex justify-between">
        <Link to="/" className="btn-ghost btn-sm">
          <ArrowLeft size={16} /> Home
        </Link>
        <ThemeButton />
      </div>
      <div className="relative w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center text-center">
          <LogoMark size={56} />
          <h1 className="mt-4 font-display text-2xl font-semibold">
            {mode === "login" ? "Welcome back" : "Create your account"}
          </h1>
          <p className="mt-1 text-sm text-ink-2">
            {mode === "login"
              ? "Sign in to continue learning with Ekalavya."
              : "Ask, watch and understand — in your language."}
          </p>
        </div>
        <form onSubmit={submit} className="card space-y-4 p-6 shadow-card">
          {mode === "signup" && (
            <Field label="Your name">
              <input
                className="input"
                value={name}
                onChange={(event) => setName(event.target.value)}
                autoComplete="name"
              />
            </Field>
          )}
          <Field label="Username">
            <input
              className="input"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              required
            />
          </Field>
          <Field label="Password">
            <input
              className="input"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              minLength={mode === "signup" ? 8 : undefined}
              required
            />
          </Field>
          {error && (
            <p role="alert" className="rounded-lg bg-danger/10 px-3 py-2 text-sm text-danger">
              {error}
            </p>
          )}
          <Button busy={busy} className="btn-primary w-full" type="submit">
            {mode === "login" ? "Sign in" : "Create account"}
          </Button>
        </form>
        <p className="mt-5 text-center text-sm text-ink-2">
          {mode === "login" ? "New to Ekalavya? " : "Already have an account? "}
          <Link
            to={`${mode === "login" ? "/signup" : "/login"}${params.toString() ? `?${params}` : ""}`}
            className="font-medium text-primary hover:underline"
          >
            {mode === "login" ? "Create an account" : "Sign in"}
          </Link>
        </p>
      </div>
    </div>
  );
}
