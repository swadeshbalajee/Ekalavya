# Ekalavya

**An AI STEM tutor that answers in chat or turns a question into a narrated, animated video lesson, in English or an Indian language.**

Ask Ekalavya something like *"Explain Fourier series visually, in Tamil"*. A team of agents then:

1. plans a short lesson from your question, your own sources, the library and the web;
2. writes the whole script and reviews it;
3. plans the visuals for every scene;
4. records the narration as one continuous performance and times every word;
5. writes [Manim](https://www.manim.community/) code for each scene and renders it in a sandbox;
6. checks every rendered scene and repairs the ones that fail;
7. delivers an MP4 with chapters and captions.

You can then talk to the video. For example, *"2:10 to 2:30, use a simpler example"* rebuilds only the scenes in that time range.

Named after Ekalavya, the self-taught archer of the Mahabharata who learned by watching.

**Contents:** [Features](#features) · [Architecture](#architecture) · [Running Ekalavya](#running-ekalavya) · [Configuration](#configuration) · [Project structure](#project-structure) · [Tests](#tests) · [API](#api) · [Known limitations](#known-limitations)

---

## Features

### Tutor chat
- **Chat or video is the model's decision.** A planner agent reads the conversation and decides whether a message needs a text answer, a new video, an edit to a video, or a translated version. No magic keywords are needed.
- **Checked answers.** Worked examples in a reply are recomputed, and every numeric equation must hold. Citations such as `[paper, p. 3]` must point to a retrieved page that contains the cited numbers. A failing reply is regenerated once with the errors named; anything still unverified is stated in a note.
- **Sources only when used.** Web links are listed under a reply only when the reply actually cites them.
- **Edit and branch.** Edit any earlier message and resend it. The old turn is kept as a branch you can step through with ‹ ›.
- **Voice input**, quoted replies, copy, light and dark themes, chats grouped into folders, and temporary (unsaved) chats.
- **Chat titles** are written by the model from your first message, in the language you wrote in. Renaming a chat keeps your title.

### Safety screen
- Every message is checked by a small, cheap open-weight safety model (`gpt-oss-safeguard-20b` by default) **while** the planner runs, so it adds almost no wait. It costs about a hundredth of a cent per message.
- Politics, hate, dangerous, sexual, harassment, self-harm and cheating requests get a gentle decline. The educational side of a topic is allowed, such as the history of a constitution or how a virus spreads. When unsure, the screen allows.
- Self-harm messages get a caring reply with helpline numbers.
- If the safety model itself fails, `GUARDRAIL_ALLOW_ON_ERROR` decides. The default lets the message through, so learners are never blocked by an outage.

### Personalisation that stays invisible
- Every answered quiz question is embedded and stored in Qdrant as soon as a quiz is submitted.
- Before each chat turn, lesson, quiz or study plan, Ekalavya builds **private learner notes**:
  - each quiz's latest score, and the learner's stronger and weaker topics;
  - the quiz answers closest in meaning to the current topic;
  - the active study plan's progress;
  - related concepts from the learner's knowledge map.
- The planner turns the notes into **teaching notes** for the lesson writers. For example: "start with a short refresher on basis and dimension before using them, and go faster through matrix multiplication".
- Lessons, edits, rebuilds, quizzes and study plans all use the notes, but never mention them. If you ask "can you see my quiz results?", the tutor answers honestly.
- Nothing is scheduled: the notes are built fresh when they're needed.

### Lessons from your own sources
- **Your material.** Attach a PDF or paste a link (a PDF, web page or blog) in chat.
- **Source choice.** Ekalavya asks whether to use *only your source* or *also add outside web sources*, and then follows that choice.
- **Whole-paper lectures.** A paper-outline agent follows the paper chapter by chapter, keeping its equations and table numbers. The paper's own figures are cropped from the PDF by their captions and shown in the video, with a check that highlight boxes land on the right part of the figure.
- **Unreadable sources.** If a link can't be read, Ekalavya says so instead of quietly substituting other material.

### Narration and audio-video alignment
- **One narrator, one performance.** The whole lesson is spoken as long takes, at most 4,500 characters each, split at chapter boundaries. Up to 3 takes are recorded in parallel. Speaking scenes separately would let the voice drift in pitch, pace and energy between scenes.
- **Forced alignment.** A multilingual acoustic model (Meta's MMS aligner, run locally on CPU) finds every word in the recording. Non-Latin scripts are romanised first. Each take is then cut into per-scene tracks at the real pauses between scenes.
- **The picture follows the voice.** Generated code animates through a `CueClock` built from the measured length of each sentence. Each visual starts with the sentence that describes it, and a scene ends exactly when its narration does. The audio is never time-stretched.
- **Deliberate pauses.** The script can ask for silence, such as `[pause 3s]` before revealing an answer. It becomes real silence in the audio, and every later timing shifts with it.
- **Same voice, always.** If a take fails, its scenes are spoken one by one in the same pinned voice. Assembly refuses a lesson with two different narrator identities.

### Editing a video by talking to it
- Edit by time range (*"0:20 to 0:40, slow down"*) or by scene. Operations are change, insert, delete, split, merge and move.
- A feedback classifier decides where the change starts:
  - visual or style → code;
  - math → spec;
  - audio or timing → narration;
  - content → script.
- Everything downstream of that point is rebuilt, **for the affected scenes only**. Unchanged scenes are reused.
- Every edit makes a new video version. Earlier versions stay in the chat and can be restored.

### Multilingual by design
- **Languages.** Lessons and chat in English, Hindi, Tamil, Telugu, Bengali and Marathi, plus Arabic, French, Japanese and Spanish.
- **Separate languages.** Chat language, on-screen text language and narration language are set separately. For example, you can chat in Tamil and get Tamil captions with English narration.
- **English canonical copy.** Planning, scripts and scene specs stay in English. Only learner-visible text and narration are translated, right before rendering and speech. Equations are locked, so translation never corrupts notation.
- **Checked translation:**
  - numbers, units, years, equations and each lesson's key STEM terms become placeholders before translation;
  - names are found with spaCy's English NER model;
  - every result is checked: placeholders intact, no invented digits, names present, nothing cut off or left untranslated;
  - a failed line gets one corrected retry, and otherwise the screen text stays in English.
- **STEM terms.** On screen a term reads `photoelectric effect (ஒளிமின் விளைவு)`, while the narration says only the English term.
- **Numbers spoken properly.** The narration says numbers as words in its own language and units by name, while the screen keeps digits.
- **Bodhan AI.** With a Bodhan key, translation and narration for Indian languages use Bodhan's Indic models, with one consistent voice per language. Rate limits are waited out rather than switching to another voice.
- **New versions.** Asking for the same video in another language builds a new version from the English original.

### Quality and self-correction
- **Whole-lesson script review.** The full script is checked for correctness, flow, unexplained symbols and a proper ending, and revised before any scene is drawn.
- **Nine checks on every rendered scene:** render errors, raw LaTeX shown as text, pedagogy, required equations, source citations, cue timing, audio-video duration, layout geometry, and a video review in which a video model watches the scene with its narration.
- **Bounded repair.** A failing scene climbs a repair ladder: patch the code, regenerate it, revise the spec, simplify. After two failed attempts, a stronger "escalation" model takes over the code.
- **Lesson review.** A final review reads the whole lesson in order and can rebuild up to 3 weak scenes before assembly.
- **Always a video.** When repairs run out, a scene falls back, in order, to:
  1. its attempt with only cosmetic issues;
  2. a plain title-and-equation scene;
  3. its best-scoring rendered attempt;
  4. the narration over a still frame.

  The lesson ships and says which scenes were degraded.

### The learning loop: corrections and skills
- **Corrections** are lessons from past failures, kept as plain Markdown files in `correction_folder/`, one folder per agent (code, script, storyboard, review):
  - when a repair fixes a problem, the problem and the fix that worked are recorded as a *candidate*;
  - once the same kind of problem has been fixed in 3 different scenes, the candidate is promoted to an *entry*;
  - every prompt includes the `CORRECTIONS.md` index, plus the full text of up to 4 entries whose triggers match the scene;
  - an entry can include a small Python check that runs on every generated code;
  - people can read, edit, add or delete any of these files.
- **Skills** follow the SKILL.md format and are versioned in PostgreSQL, with progressive disclosure:
  - every prompt sees only the catalog of names and descriptions;
  - the full SKILL.md loads only for skills that declare the capability being run;
  - references, assets and scripts load on demand.

  New versions come from a skill-authoring agent or the API. An admin activates them, and every activation is audited.

### Context management
- **Long chats.** The last 7 learner turns stay in context word for word. Older turns are folded into a running summary in the background, and only once they pass 3,000 characters, so short chats never pay for it. The summary keeps preferences, decisions, every video made, and open confusion. Editing a summarised message rebuilds the summary.
- **Large sources.** Only the planning agent (the lesson curator, or the paper outliner) reads all the material:
  - material over the one-pass limit is split into groups read in parallel, with central ideas marked "core" so the merge keeps them;
  - a cheaper model checks the plan against the material, and the curator fixes any gaps once;
  - detail that doesn't fit becomes a *reference*, and the writers look references up only when they need them;
  - the scene code writer never sees the source at all.
- **Small prompts elsewhere:** skills and corrections load progressively, learner notes keep only the closest quiz answers, and the shared code prompt prefix is prompt-cached across scenes.
- **Research depth** (*Settings → Research depth*) lets each learner set web searches, results per search, pages read in full, library queries and passages, reading limits, lookups per writing step and chat turns kept. The page shows the expected extra cost and time of each change, measured from your own usage. The server clamps every value to a safe range.

### Learning beyond the chat
- **Landing page** for visitors, with public explainer videos and sign-up.
- **Home:** quick actions (explain, make a video, summarise a paper, quiz me), *Continue learning*, today's study tasks, and recommendations ranked by embedding similarity.
- **Discover:** a public catalog. A catalog agent writes each published video's title, summary, subject and keywords. Search needs every keyword, and falls back to the closest videos by meaning.
- **Video page:** chapters, notes pinned to moments, a searchable transcript, resources (the sources used), practice, save, playlists, share, download and related videos.
- **Library:** your videos, saved videos, attached documents, notes and playlists.
- **Study plan:** state a goal, a date and minutes per day. The study planner agent schedules watching, practice, reading, review and new videos for gaps. There's a weekly progress ring, a calendar, and *Replan* from today.
- **Quiz me:** questions written from a video's narration or on a topic. Written answers are graded by the model, and each wrong answer links to the scene that explains it.
- **Knowledge map:** a graph of every concept from your chats and lessons and how they relate, stored in PostgreSQL with concept vectors in Qdrant. Explore it as a force graph, search it by meaning, or *Ask your map*: the answer uses only the closest concepts and their neighbours, and cites the chats they came from.

### Usage and activity dashboard
- **Totals:** spend, tokens, videos built, average build time, success rate and retries, for 7, 30 or 90 days, or all time.
- **Live runs:** a graph of the lesson stages, with a lane per scene showing what is done, running and next.
- **Breakdowns:** spend per day and per module (script, code, code repair, video review, translation, narration and more).
- **Per chat:** spend by module and by scene, a stage timeline, and every model call with its cost, time and outcome.
- **Admin view:** usernames listed in `ADMIN_USERNAMES` can see every user's usage.

### Robust and safe
- **Sandboxed renders.** Generated Manim code passes an AST allow-list first: no network, process or file access. It then renders with CPU, memory and file limits.
- **Safe link fetching.** Links must be public HTTPS (SSRF-safe, size-capped), and PDF text is sanitised before storage.
- **Durable jobs.** Builds, edits and translations run as Temporal workflows. A cancelled or failed rebuild restores the previous working video. Every artifact is saved per version, so a retried build resumes instead of paying again.
- **Model fallbacks.** Every model capability has its own model and a fallback.

---

## Architecture

![Ekalavya architecture](docs/diagrams/architecture.svg)

- **web** (nginx) serves the React app and forwards `/api` to the API.
- **api** (FastAPI) is the control plane. It never renders.
- **temporal** and the **worker** run the long jobs: build, edit, localise, and an hourly Qdrant reconciliation.
- **PostgreSQL** is the source of truth for accounts, chats, projects and every versioned artifact record.
- **Qdrant** holds only vectors that can be rebuilt: source chunks, skills, knowledge concepts and learners' quiz answers.
- **The artifact store** (`./projects`) holds every versioned file per chat and scene: plan, script, audio, code, renders and the final video.

More detail is in [docs/system-design.md](docs/system-design.md), with diagrams of:
- [one video build](docs/diagrams/lesson-pipeline.svg);
- [one scene](docs/diagrams/scene-production.svg);
- [correction memory and skills](docs/diagrams/learning-loop.svg);
- [chat turns and edits](docs/diagrams/chat-and-edits.svg).

### The lesson pipeline

| Stage | What happens | Code |
|---|---|---|
| Prepare | The safety screen and the planner run together. The planner reads the chat summary, recent turns and learner notes, then decides between chat and video and sets the languages, length, source links and scope | `api/tutor.py`, `agents/guardrail.py`, `agents/lesson_preparation.py`, `services/learner_record.py` |
| Collect | The planner splits the brief into focused queries; your sources and the library are searched with all of them at once, and web searches run in parallel where outside material is allowed | `retrieval/collection.py`, `retrieval/qdrant_retrieval.py` |
| Curate | One lesson plan: sections, key points, worked examples with their numbers, equations, visual ideas, and references to detail left out. A cheaper model checks it against the material. Long material is read in parallel groups; papers use the paper outline | `agents/lesson_curator.py`, `agents/paper_outline.py` |
| Script | The whole lesson in English from the plan, in a first-person teacher's voice, looking up references when needed, then reviewed and revised | `agents/script_writer.py`, `agents/reference_lookup.py` |
| Storyboard | Visual beats for the whole lesson in batches of 8 scenes; each beat quotes the narration phrase it shows | `agents/storyboard_agent.py`, `agents/scene_compiler.py` |
| Narration | Takes of up to 4,500 characters recorded in parallel, force-aligned word by word, and cut into scenes at the real pauses | `services/orchestration.py`, `media/alignment.py`, `media/narration.py` |
| Produce | Manim code (a prompt-cached shared prefix, a matched template and the relevant corrections), then a sandboxed render; 4 scenes at a time in a work-conserving scheduler | `agents/code_generator.py`, `manim_runtime/` |
| Verify and repair | Nine verification layers; the repair ladder, escalation model and fallbacks | `verification/`, `agents/manager.py` |
| Review | A whole-lesson coherence review that rebuilds up to 3 weak scenes | `services/orchestration.py` |
| Assemble | FFmpeg joins the scenes; SRT/VTT captions and chapters are written | `media/ffmpeg.py`, `media/subtitles.py` |

---

## Running Ekalavya

Ekalavya runs with Docker on Windows, macOS and Linux, with the same commands on each.

**Prerequisites**
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) on Windows or macOS, or Docker Engine with the Compose plugin on Linux.
- An [OpenRouter](https://openrouter.ai/) API key. It's the only key the reference setup needs; see [Configuration](#configuration) for other providers.
- About 8 GB of free disk space for the image and the models it downloads on first use.

**1. Get the code and create `.env`:**

```bash
git clone https://github.com/swadeshbalajee/Ekalavya.git
cd Ekalavya
cp .env.example .env          # Windows PowerShell: Copy-Item .env.example .env
```

**2. Edit `.env`:**
- set `APP_API_KEY` to any random string;
- set `OPENROUTER_API_KEY`;
- optionally set `BODHAN_API_KEY` for Indian-language translation and narration.

**3. Build and start everything:**

```bash
docker compose up --build
```

The first build takes about 10–15 minutes. It installs Manim, LaTeX, FFmpeg, CPU PyTorch and Noto fonts for Indic scripts. Later starts take seconds.

Compose starts these services:

| Service | Role |
|---|---|
| `postgres` | the database (source of truth) |
| `qdrant` | the vector index |
| `temporal` | durable workflows, with a UI on port 8233 |
| `migrate` | applies the Alembic database migrations, then exits; "Exited (0)" is normal |
| `api` | the FastAPI control plane, on port 8000 |
| `worker` | runs the lesson pipeline |
| `web` | the React app, on port 8080 |
| `kokoro` (optional) | local English TTS, started with `--profile kokoro` |

### Open the app

| | URL |
|---|---|
| **App** | http://127.0.0.1:8080 |
| API docs (OpenAPI) | http://127.0.0.1:8000/docs |
| Temporal UI | http://127.0.0.1:8233 |

Click **Create account**, sign in, and try:
- `What is the derivative of sin x?` gets a checked chat answer.
- `Make a one-minute video showing how a convex lens forms an image.` starts a video lesson. Open *Usage → Live runs* to watch it being built.
- `Explain the Pythagorean theorem visually in Tamil` makes a Tamil lesson.
- `Make a video from this blog: https://…` makes Ekalavya ask whether to use only that source.
- After a video finishes: `0:20 to 0:40, slow down and add a worked example`.
- Take a quiz on a topic, then ask for a video on something that builds on it. The lesson adapts to your weaker areas without saying so.

### Day-to-day commands

| Task | Command |
|---|---|
| Stop the stack (data is kept) | `docker compose down` |
| Start it again | `docker compose up -d` |
| Follow the lesson pipeline | `docker compose logs -f worker` |
| Deploy a code change | `docker compose build migrate && docker compose up -d api worker` |
| Smaller image without LaTeX | `docker compose build --build-arg WITH_LATEX=0` (equations then render as Unicode text) |
| **Delete all chats and accounts** | `docker compose down -v` |

**Where data lives:**
- Chats and accounts are in Docker volumes.
- Videos are in `./projects`, or `PROJECTS_DIR`, with one folder per user: `<owner_id>/<project_id>/video/vN/final.mp4`.
- Correction notes are in `./correction_folder`, or `CORRECTION_DIR`.

All three survive restarts; only `down -v` deletes the volumes.

<details>
<summary>Developing without Docker (Windows)</summary>

The project was developed natively on Windows. `scripts/setup-local.ps1` does the one-time setup:
- creates a Conda env;
- writes `.env`;
- downloads Qdrant and Temporal into `.local/`;
- initialises PostgreSQL.

`scripts/start-local.ps1` and `scripts/stop-local.ps1` start and stop all the services. Logs go to `.local/logs/`.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-local.ps1
powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1
```

For the React app with hot reload, run `npm install` and then `npm run dev` in `web/`, and open http://127.0.0.1:5173. The dev server forwards `/api` to the API at `EKALAVYA_API_URL` (default `http://127.0.0.1:8000`) and adds `APP_API_KEY` from `.env`.
</details>

---

## Configuration

All settings live in `.env`, and [.env.example](.env.example) documents every variable. Each capability is configured on its own, with its own model and an optional fallback. Set a capability's provider to `mock` (or leave it blank) for deterministic offline models, which is what the tests use.

The reference setup in `.env.example`, all through OpenRouter:

| Capability | Model | Used for |
|---|---|---|
| Main LLM | `z-ai/glm-5.3` (fallback `anthropic/claude-sonnet-5.5`) | script writing and review, paper outlines, lesson plans |
| Planner | `z-ai/glm-5.3-flash` | chat or video decisions, storyboards, plan checks, chat summaries |
| Code | `z-ai/glm-5.3-flash` (fallback `anthropic/claude-haiku-5.5`) | Manim code and repairs |
| Code escalation | `anthropic/claude-sonnet-5.5` | takes over a scene's code after 2 failed attempts |
| Safety screen | `openai/gpt-oss-safeguard-20b` | checks every chat message |
| Video review | `qwen/qwen3.8-omni-flash` | watches each scene with its narration |
| Vision | `z-ai/glm-5.3-flash` | keyframe review, paper-figure boxes |
| Translation | `google/gemini-2.5-flash-lite` | on-screen text, narration and chat replies |
| Speech | `google/gemini-3.8-flash-lite-tts`, voice `Kore` | narration |
| Speech-to-text | `openai/whisper-large-v3` | voice input |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2`, local CPU | search, recommendations, knowledge map, learner notes |
| Forced alignment | `MahmoudAshraf/mms-300m-1130-forced-aligner`, local CPU | word timings |

**Safety screen settings:**
- `GUARDRAIL_MODEL` sets the model; empty turns the screen off.
- `GUARDRAIL_TIMEOUT_SECONDS` limits how long it may take.
- `GUARDRAIL_ALLOW_ON_ERROR` (default `true`) lets messages through if the screen fails.

**Bodhan AI (optional).** With `BODHAN_API_KEY` set, translation and narration for Bengali, English, Hindi, Marathi, Tamil and Telugu use Bodhan. `BODHAN_TTS_VOICE` sets the voice (default `Kavya`), and `BODHAN_TTS_VOICES=ta:Name,...` sets one per language.

**Other providers.** Gemini directly (`*_PROVIDER=gemini`), Anthropic (`anthropic`), ElevenLabs or a local Kokoro server for TTS, and local vLLM endpoints (`openai_compatible` with a local `*_BASE_URL`).

**Cost.** With the reference setup, a 1–3 minute lesson typically costs about $0.25–0.45, and a 12-minute lesson about $1–1.50. Every model call is recorded, so the Usage page shows the exact cost of each chat, stage and scene.

---

## Project structure

```
Ekalavya/
├── web/                      React app (Vite, TypeScript, Tailwind), served by nginx
│   ├── src/pages/              Landing, Home, Chat, Discover, Watch, Library, Study, Quiz, Usage, Knowledge
│   ├── src/components/         app shell, video player, cards, chat composer and build progress
│   └── nginx.conf.template     serves the app; proxies /api to FastAPI and adds the API key
├── frontend/                 earlier Streamlit UI
├── src/scholarmotion/        backend package (named after the project's working title)
│   ├── api/                    FastAPI app
│   │   ├── main.py               routes: auth, chats, messages, sources, builds, edits, media
│   │   ├── tutor.py              each chat turn: safety screen, planner, answer / video / edit / localise
│   │   ├── service.py            build, edit and localisation services; video versions
│   │   ├── learning.py           catalog, library, notes, playlists, home, study plans, quizzes
│   │   ├── knowledge.py          knowledge map: concept graph, semantic search, graph questions
│   │   ├── capabilities.py       admin capabilities (/v1/capabilities/*), skills
│   │   └── usage.py              usage dashboard API (/v1/usage/*)
│   ├── agents/                 one file per agent: guardrail, lesson preparation, lesson curator,
│   │                           paper outline, reference lookup, script writer, storyboard, scene
│   │                           compiler, Manim code generator, repair manager, feedback classifier,
│   │                           video catalog, study planner, quiz writer, concept graph, chat titles
│   ├── services/               orchestration.py (the lesson pipeline), chat_memory.py (compaction),
│   │                           learner_record.py (learner notes), research_settings.py, edit plans
│   ├── workflows/              Temporal workflows, activities, client and worker
│   ├── manim_runtime/          sandbox renderer, AST validator, CueClock and layout components, templates
│   ├── verification/           render, layout, math, pedagogy, semantic, video and chat-reply checks
│   ├── media/                  speech, forced alignment, narration takes and cuts, pauses, FFmpeg, subtitles
│   ├── retrieval/              PDF and web-page parsing, safe link fetching, paper figures, Qdrant search
│   ├── translation/            checked localisation: masking (spaCy NER), checks, spoken numbers
│   ├── memory/                 the correction folder (Markdown lessons learned) and its seed notes
│   ├── skills/                 versioned agent skills with progressive disclosure
│   ├── providers/              LLM / TTS / STT / embedding adapters, Bodhan, a factory with fallbacks
│   ├── persistence/            SQLAlchemy models, database, Qdrant, artifact storage
│   ├── observability/          JSON logs, Prometheus metrics, usage and cost tracking
│   ├── config/                 typed settings from .env
│   └── schemas/                shared Pydantic models
├── alembic/                  database migrations, applied by the `migrate` service
├── docs/                     system design, diagrams, test reports
├── scripts/                  local setup, NCERT ingestion, Kokoro server, QA harnesses (scripts/qa/)
├── tests/                    pytest suite (offline)
└── Dockerfile, docker-compose.yml
```

---

## Tests

```bash
conda activate scholarmotion
pytest -q        # about 520 tests, all offline
ruff check src tests frontend
cd web && npm ci && npm run build    # type-checks and builds the React app
```

All tests run offline: providers, the database and media are mocked or in memory, and the safety screen is switched off by a test fixture so no real API is called. The suite covers:
- chat routing, the safety screen, learner notes and compaction;
- source scope;
- video versions, edits and branching;
- forced alignment, narration cuts and pauses;
- layout and LaTeX validation;
- translation checks;
- FFmpeg assembly;
- provider fallbacks and Bodhan retries;
- a mocked end-to-end build.

The `scripts/qa/` harnesses drive the real running app through its API and a headless browser. For example, `scripts/qa/qa_context_management.py` checks that curation and chat compaction keep the core of a lesson. See [docs/context-management-test-2026-10-28.md](docs/context-management-test-2026-10-28.md).

---

## API

The full API is documented at `/docs`. Every route except `/health`, `/docs` and `/openapi.json` needs either a signed-in session or the `X-API-Key` header. The main groups:

| Route | Covers |
|---|---|
| `/v1/auth/*` | register, login, logout, account settings and research depth |
| `/v1/projects/*` | chats, messages, sources (`/sources`, `/sources/link`), progress, videos, scene edits |
| `/v1/capabilities/*` | each model capability on its own (code, vision, TTS, STT, translation) |
| `/v1/skills/*` | versioned agent skills |
| `/v1/usage/*` | the usage dashboard's data |
| `/v1/videos/*` | the public catalog and search, a video's media, transcript, notes, progress, saves, sources and publishing |
| `/v1/library/*`, `/v1/home`, `/v1/study-plans/*`, `/v1/quizzes/*` | library, playlists, the home feed, study plans and quizzes |
| `/v1/knowledge/*` | the knowledge map (`sync`, `graph`, `nodes/{id}`, `search`, `query`) |
| `/metrics` | Prometheus counters for latency, provider fallbacks and workflow outcomes |

---

## Known limitations

- **Speed:** rendering and speech alignment run on the CPU, so a lesson takes several minutes on a laptop.
- **Resolution:** videos render at 854×480 to keep builds fast.
- **Code changes rebuild slowly:** the Docker image copies the source before installing dependencies, so any code change reinstalls them (7–11 minutes).
- **Sandboxing:** the render sandbox uses resource limits. Full network isolation (`SANDBOX_ISOLATION=required`, bubblewrap) is available only on Linux.
- **Bodhan:** Bodhan speech takes short inputs, so narration is sent in sentence-sized pieces. Busy periods are waited out, which can slow Indian-language builds.
- **Web sources:** web-page extraction is readability-style. Pages that need JavaScript to render must be attached as a PDF instead.
