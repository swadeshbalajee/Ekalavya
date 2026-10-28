import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowLeft, CheckCircle2, GraduationCap, PlayCircle, RotateCcw, Sparkles, Trash2, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Button, Empty, ProgressRing, Spinner, Tabs } from "../components/common";
import { Thumbnail } from "../components/VideoCard";
import { api, errorText } from "../lib/api";
import { ago } from "../lib/format";
import type { Quiz, QuizResult, VideoCard } from "../lib/types";
import { useToast } from "../lib/ui";

export default function QuizPage() {
  const { quizId } = useParams();
  return quizId ? <TakeQuiz id={quizId} /> : <QuizHome />;
}

function QuizHome() {
  const navigate = useNavigate();
  const toast = useToast();
  const [params] = useSearchParams();
  const [source, setSource] = useState<"video" | "topic">(params.get("topic") ? "topic" : "video");
  const [topic, setTopic] = useState(params.get("topic") ?? "");
  const [instruction, setInstruction] = useState("");
  const [video, setVideo] = useState<string | null>(params.get("video"));
  const videos = useQuery({
    queryKey: ["library", "videos", "all", ""],
    queryFn: () => api<VideoCard[]>("/v1/library/videos"),
  });
  const quizzes = useQuery({ queryKey: ["quizzes"], queryFn: () => api<Quiz[]>("/v1/quizzes") });
  const create = useMutation({
    mutationFn: () =>
      api<Quiz>("/v1/quizzes", {
        body: source === "video" ? { video_id: video, instruction } : { topic: topic.trim(), instruction },
      }),
    onSuccess: (quiz) => navigate(`/quiz/${quiz.id}`),
    onError: (error) => toast(errorText(error), "error"),
  });
  const ready = source === "video" ? !!video : topic.trim().length > 2;

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6 lg:py-10">
      <h1 className="font-display text-2xl font-semibold sm:text-3xl">Quiz me</h1>
      <p className="mt-1 text-sm text-ink-2">
        Practice questions written for you. Wrong answers point to the moment in the video that explains it.
      </p>
      <div className="card mt-6 p-5">
        <Tabs
          value={source}
          onChange={setSource}
          items={[
            { value: "video", label: "From a video" },
            { value: "topic", label: "On a topic" },
          ]}
          className="w-fit"
        />
        <div className="mt-4">
          {source === "video" ? (
            videos.isLoading ? (
              <Spinner />
            ) : videos.data?.length ? (
              <div className="grid max-h-[340px] gap-2 overflow-y-auto pr-1 sm:grid-cols-2">
                {videos.data.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => setVideo(item.id)}
                    className={clsx(
                      "flex items-center gap-3 rounded-xl border p-2 text-left transition",
                      video === item.id ? "border-primary bg-primary/10" : "border-line hover:border-primary/40",
                    )}
                  >
                    <Thumbnail id={item.id} title={item.title} className="w-24 shrink-0 rounded-lg" />
                    <span className="line-clamp-2 text-sm">{item.title}</span>
                  </button>
                ))}
              </div>
            ) : (
              <p className="text-sm text-ink-2">
                No videos in your library yet. <Link to="/discover" className="text-primary">Find one</Link> or quiz
                yourself on a topic.
              </p>
            )
          ) : (
            <input
              className="input"
              placeholder="e.g. Photosynthesis — light and dark reactions"
              value={topic}
              onChange={(event) => setTopic(event.target.value)}
            />
          )}
          <input
            className="input mt-3"
            placeholder="Optional: what should it focus on? (harder, numericals, exam style…)"
            value={instruction}
            onChange={(event) => setInstruction(event.target.value)}
          />
          <div className="mt-4 flex justify-end">
            <Button busy={create.isPending} disabled={!ready} onClick={() => create.mutate()}>
              <Sparkles size={16} /> {create.isPending ? "Writing your questions…" : "Create quiz"}
            </Button>
          </div>
        </div>
      </div>

      <h2 className="mt-10 font-display text-lg font-semibold">Your quizzes</h2>
      <div className="mt-4">
        {quizzes.isLoading ? (
          <div className="skeleton h-32" />
        ) : !quizzes.data?.length ? (
          <Empty icon={<GraduationCap size={22} />} title="No quizzes yet">
            Your quizzes and scores will be kept here so you can retake them.
          </Empty>
        ) : (
          <div className="card divide-y divide-line">
            {quizzes.data.map((quiz) => (
              <Link key={quiz.id} to={`/quiz/${quiz.id}`} className="flex items-center gap-3 px-4 py-3 hover:bg-raised/40">
                <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-amber/12 text-amber">
                  <GraduationCap size={16} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{quiz.title}</span>
                  <span className="text-xs text-muted">
                    {quiz.questions.length} questions · {ago(quiz.created_at)}
                  </span>
                </span>
                <span
                  className={clsx(
                    "rounded-lg px-2 py-1 font-mono text-xs",
                    quiz.best_score === null
                      ? "bg-raised text-muted"
                      : quiz.best_score >= 0.7
                        ? "bg-success/12 text-success"
                        : "bg-warning/12 text-warning",
                  )}
                >
                  {quiz.best_score === null ? "new" : `${Math.round(quiz.best_score * 100)}%`}
                </span>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function TakeQuiz({ id }: { id: string }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const [params] = useSearchParams();
  const taskId = params.get("task");
  const quiz = useQuery({ queryKey: ["quiz", id], queryFn: () => api<Quiz>(`/v1/quizzes/${id}`) });
  const [answers, setAnswers] = useState<(number | string | null)[]>([]);
  const [results, setResults] = useState<{ score: number; results: QuizResult[] } | null>(null);

  useEffect(() => {
    if (quiz.data) setAnswers(quiz.data.questions.map(() => null));
  }, [quiz.data?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = useMutation({
    mutationFn: () => api<{ score: number; results: QuizResult[] }>(`/v1/quizzes/${id}/attempts`, { body: { answers } }),
    onSuccess: (graded) => {
      setResults(graded);
      window.scrollTo({ top: 0, behavior: "smooth" });
      void client.invalidateQueries({ queryKey: ["quizzes"] });
      if (taskId) {
        void api(`/v1/study-plans/tasks/${taskId}`, { method: "PATCH", body: { done: true } }).then(() => {
          void client.invalidateQueries({ queryKey: ["study"] });
          void client.invalidateQueries({ queryKey: ["home"] });
        });
      }
    },
    onError: (error) => toast(errorText(error), "error"),
  });
  const remove = useMutation({
    mutationFn: () => api(`/v1/quizzes/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["quizzes"] });
      navigate("/quiz");
    },
  });

  if (quiz.isLoading || !quiz.data) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <Spinner />
      </div>
    );
  }
  const data = quiz.data;
  const answered = answers.filter((answer) => answer !== null && answer !== "").length;

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
      <div className="flex items-center justify-between">
        <Link to="/quiz" className="btn-ghost btn-sm -ml-3">
          <ArrowLeft size={14} /> Quizzes
        </Link>
        <button className="icon-btn" aria-label="Delete quiz" onClick={() => remove.mutate()}>
          <Trash2 size={16} />
        </button>
      </div>
      <h1 className="mt-2 font-display text-2xl font-semibold">{data.title}</h1>
      <p className="mt-1 text-sm text-ink-2">
        {data.questions.length} questions
        {data.video_id && (
          <>
            {" · "}
            <Link to={`/watch/${data.video_id}`} className="text-primary hover:underline">
              watch the video
            </Link>
          </>
        )}
      </p>

      {results && (
        <div className="card mt-6 flex flex-col items-center gap-5 p-6 sm:flex-row">
          <ProgressRing value={results.score} size={110} stroke={10}>
            <span className="font-display text-2xl font-semibold">{Math.round(results.score * 100)}%</span>
          </ProgressRing>
          <div className="flex-1 text-center sm:text-left">
            <p className="font-display text-lg font-semibold">
              {results.score >= 0.8 ? "Excellent work!" : results.score >= 0.5 ? "Good effort — nearly there." : "Let's review this together."}
            </p>
            <p className="mt-1 text-sm text-ink-2">
              {results.results.filter((item) => item.correct).length} of {results.results.length} correct. Wrong answers show
              where the video explains it.
            </p>
          </div>
          <button
            className="btn-secondary"
            onClick={() => {
              setResults(null);
              setAnswers(data.questions.map(() => null));
            }}
          >
            <RotateCcw size={16} /> Retake
          </button>
        </div>
      )}

      <ol className="mt-6 space-y-4">
        {data.questions.map((question, index) => {
          const result = results?.results[index];
          return (
            <li
              key={index}
              className={clsx(
                "card p-5",
                result && (result.correct ? "border-success/50" : "border-danger/50"),
              )}
            >
              <div className="flex gap-3">
                <span className="font-mono text-sm text-muted">{index + 1}.</span>
                <p className="flex-1 font-medium">{question.prompt}</p>
                {result &&
                  (result.correct ? (
                    <CheckCircle2 size={20} className="shrink-0 text-success" />
                  ) : (
                    <XCircle size={20} className="shrink-0 text-danger" />
                  ))}
              </div>
              <div className="mt-4 pl-6">
                {question.kind === "choice" ? (
                  <div className="space-y-2">
                    {question.options.map((option, choice) => {
                      const picked = answers[index] === choice;
                      const right = result && result.answer_index === choice;
                      return (
                        <button
                          key={choice}
                          disabled={!!results}
                          onClick={() => setAnswers((items) => items.map((item, at) => (at === index ? choice : item)))}
                          className={clsx(
                            "flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left text-sm transition",
                            right
                              ? "border-success bg-success/10"
                              : result && picked
                                ? "border-danger bg-danger/10"
                                : picked
                                  ? "border-primary bg-primary/10"
                                  : "border-line hover:border-primary/40",
                          )}
                        >
                          <span
                            className={clsx(
                              "flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px]",
                              picked ? "border-primary bg-primary text-white" : "border-line",
                            )}
                          >
                            {String.fromCharCode(65 + choice)}
                          </span>
                          {option}
                        </button>
                      );
                    })}
                  </div>
                ) : (
                  <textarea
                    className="input min-h-[90px]"
                    placeholder="Write your answer in your own words"
                    disabled={!!results}
                    value={(answers[index] as string) ?? ""}
                    onChange={(event) =>
                      setAnswers((items) => items.map((item, at) => (at === index ? event.target.value : item)))
                    }
                  />
                )}
                {result && (
                  <div className="mt-3 space-y-2 rounded-xl bg-raised/60 p-3 text-sm">
                    {result.feedback && <p className="text-ink">{result.feedback}</p>}
                    {!result.correct && question.kind === "short" && result.answer && (
                      <p className="text-ink-2">
                        <span className="font-medium text-ink">Model answer: </span>
                        {result.answer}
                      </p>
                    )}
                    {result.explanation && <p className="text-ink-2">{result.explanation}</p>}
                    {!result.correct && data.video_id && result.at_seconds !== null && (
                      <Link
                        to={`/watch/${data.video_id}?t=${Math.floor(result.at_seconds)}`}
                        className="inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:underline"
                      >
                        <PlayCircle size={15} /> Rewatch this part
                      </Link>
                    )}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {!results && (
        <div className="sticky bottom-20 mt-6 flex items-center justify-between gap-3 rounded-2xl border border-line bg-surface/95 p-3 backdrop-blur lg:bottom-4">
          <span className="pl-2 text-sm text-ink-2">
            {answered}/{data.questions.length} answered
          </span>
          <Button busy={submit.isPending} disabled={!answered} onClick={() => submit.mutate()}>
            {submit.isPending ? "Checking…" : "Submit answers"}
          </Button>
        </div>
      )}
    </div>
  );
}
