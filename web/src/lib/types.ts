export type User = {
  id: string;
  username: string;
  display_name: string;
  email: string | null;
  avatar_color: string;
  default_duration_minutes: number;
  default_text_language: string;
  default_voice_language: string;
  is_admin: boolean;
};

export type Project = {
  id: string;
  folder_id: string | null;
  title: string;
  request: string;
  original_request: string | null;
  target_duration_minutes: number;
  mode: string;
  text_language: string;
  voice_language: string;
  status: string;
  pinned: boolean;
  archived: boolean;
  video_path: string | null;
  visibility: "public" | "private";
  subject: string | null;
  created_at: string;
  updated_at: string;
};

export type Folder = { id: string; name: string; chat_count: number };

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  message: string;
  canonical_message: string;
  language: string;
  action: string | null;
  metadata: Record<string, any> | null;
  created_at: string;
  branches?: { index: number; messages: ChatMessage[] }[];
  branch_index?: number;
  readonly?: boolean;
};

export type ProgressEvent = {
  event: string;
  scene_id: string | null;
  detail: Record<string, any>;
  created_at: string;
};

export type Progress = {
  status: string;
  failure_message: string | null;
  scene_count: number;
  events: ProgressEvent[];
};

export type VideoCard = {
  id: string;
  title: string;
  description: string | null;
  subject: string | null;
  tags: string[];
  level: string | null;
  visibility: "public" | "private";
  views: number;
  duration: number;
  author: string;
  is_owner: boolean;
  saved: boolean;
  status: string;
  text_language: string;
  voice_language: string;
  published_at: string | null;
  created_at: string;
  updated_at: string;
  progress: { position: number; duration: number; completed: boolean; updated_at: string } | null;
};

export type Chapter = { scene_id: string; title: string; start: number; end: number };
export type VideoDetail = VideoCard & {
  request: string;
  video_version: number | null;
  chapters: Chapter[];
};

export type Cue = { start: number; end: number; text: string };

export type Note = {
  id: string;
  video_id: string;
  at_seconds: number;
  text: string;
  created_at: string;
  updated_at: string;
  video_title?: string;
};

export type Playlist = {
  id: string;
  name: string;
  description: string | null;
  count: number;
  cover_id: string | null;
  updated_at: string;
  items?: VideoCard[];
};

export type Source = {
  id: string;
  title: string;
  kind: string;
  url: string | null;
  downloadable: boolean;
  created_at: string;
  chat_id?: string;
  chat_title?: string;
  has_video?: boolean;
};

export type TaskKind = "watch" | "create" | "practice" | "read" | "review";
export type StudyTask = {
  id: string;
  plan_id: string;
  plan_title: string | null;
  due_date: string;
  kind: TaskKind;
  title: string;
  detail: string | null;
  minutes: number;
  video_id: string | null;
  topic: string | null;
  done: boolean;
  done_at: string | null;
};

export type StudyPlan = {
  id: string;
  goal: string;
  title: string;
  summary: string | null;
  start_date: string;
  end_date: string | null;
  minutes_per_day: number;
  language: string;
  status: string;
  task_count: number;
  done_count: number;
  created_at: string;
  tasks?: StudyTask[];
};

export type QuizQuestion = {
  kind: "choice" | "short";
  prompt: string;
  options: string[];
  scene_key: string | null;
};

export type QuizResult = {
  correct: boolean;
  feedback: string;
  explanation: string;
  answer_index: number | null;
  answer: string;
  scene_key: string | null;
  at_seconds: number | null;
};

export type Quiz = {
  id: string;
  title: string;
  video_id: string | null;
  topic: string | null;
  language: string;
  questions: QuizQuestion[];
  attempts: number;
  best_score: number | null;
  latest: {
    answers: (number | string | null)[];
    results: QuizResult[];
    score: number;
    created_at: string;
  } | null;
  created_at: string;
};

export type Home = {
  continue: VideoCard[];
  recommended: VideoCard[];
  today: StudyTask[];
  recent_chats: {
    id: string;
    title: string;
    status: string;
    has_video: boolean;
    updated_at: string;
  }[];
};
