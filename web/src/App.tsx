import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { Spinner } from "./components/common";
import { useAuth } from "./lib/auth";
import { AuthPage } from "./pages/Auth";
import { Landing } from "./pages/Landing";

const HomePage = lazy(() => import("./pages/Home"));
const ChatPage = lazy(() => import("./pages/Chat"));
const DiscoverPage = lazy(() => import("./pages/Discover"));
const WatchPage = lazy(() => import("./pages/Watch"));
const LibraryPage = lazy(() => import("./pages/Library"));
const StudyPage = lazy(() => import("./pages/Study"));
const QuizPage = lazy(() => import("./pages/Quiz"));
const UsagePage = lazy(() => import("./pages/Usage"));
const KnowledgePage = lazy(() => import("./pages/Knowledge"));

function Loading() {
  return (
    <div className="flex min-h-[60vh] items-center justify-center text-muted">
      <Spinner size={22} />
    </div>
  );
}

function SignedIn({ children }: { children: ReactNode }) {
  const { user, ready } = useAuth();
  const location = useLocation();
  if (!ready) return <Loading />;
  if (!user) {
    return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  }
  return <>{children}</>;
}

function Shell({ children, auth = true }: { children: ReactNode; auth?: boolean }) {
  const page = (
    <AppShell>
      <Suspense fallback={<Loading />}>{children}</Suspense>
    </AppShell>
  );
  return auth ? <SignedIn>{page}</SignedIn> : page;
}

export function App() {
  const { user, ready } = useAuth();
  if (!ready) return <Loading />;
  return (
    <Routes>
      <Route path="/" element={user ? <Navigate to="/home" replace /> : <Landing />} />
      <Route path="/login" element={<AuthPage mode="login" />} />
      <Route path="/signup" element={<AuthPage mode="signup" />} />
      <Route path="/home" element={<Shell><HomePage /></Shell>} />
      <Route path="/chat" element={<Shell><ChatPage /></Shell>} />
      <Route path="/chat/:chatId" element={<Shell><ChatPage /></Shell>} />
      <Route path="/discover" element={<Shell auth={false}><DiscoverPage /></Shell>} />
      <Route path="/watch/:videoId" element={<Shell auth={false}><WatchPage /></Shell>} />
      <Route path="/library" element={<Shell><LibraryPage /></Shell>} />
      <Route path="/library/playlists/:playlistId" element={<Shell><LibraryPage /></Shell>} />
      <Route path="/study" element={<Shell><StudyPage /></Shell>} />
      <Route path="/quiz" element={<Shell><QuizPage /></Shell>} />
      <Route path="/quiz/:quizId" element={<Shell><QuizPage /></Shell>} />
      <Route path="/usage" element={<Shell><UsagePage /></Shell>} />
      <Route path="/knowledge" element={<Shell><KnowledgePage /></Shell>} />
      <Route path="*" element={<Navigate to={user ? "/home" : "/"} replace />} />
    </Routes>
  );
}
