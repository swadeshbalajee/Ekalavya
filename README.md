# Ekalavya

**An AI STEM tutor that answers in chat or turns a question into a narrated, animated video lesson, in English or an Indian language.**

Ask Ekalavya something like *"Explain Fourier series visually, in Tamil"*. Ekalavya then:

1. plans a short lesson;
2. writes and reviews the script;
3. animates every scene with [Manim](https://www.manim.community/);
4. narrates it with TTS;
5. checks each rendered scene, and repairs the ones that fail;
6. delivers an MP4 with chapters and captions.

You can then talk to the video. For example, *"2:10 to 2:30, use a simpler example"* rebuilds only the scenes in that time range.

Named after Ekalavya, the self-taught archer of the Mahabharata who learned by watching.

---

## Key features

**Tutor chat**
- **Chat or video is the model's decision.** A planner agent reads the conversation and decides whether a request needs a text answer or a video lesson. No magic keywords are needed.
- **Edit and branch.** Edit any earlier message and resend it. The old turn is kept as a branch you can step through with ‹ ›.
- **Voice input**, quoted replies, copy, light/dark themes, chats grouped into folders, and temporary (unsaved) chats.

**Lessons from your own sources**
- **Your material.** Attach a PDF or paste a link (PDF or web page/blog) in chat.
- **Source choice.** Ekalavya asks whether to use *only your source* or *also add outside web sources*, then follows that choice.
- **Unreadable sources.** If a link can't be read, it says so instead of quietly substituting other material.

**Multilingual by design**
- **Languages.** Lessons and chat in English, Hindi, Tamil, Telugu, Bengali, Marathi, plus Arabic, French, Japanese and Spanish.
- **Separate text and voice languages.** For example, Tamil captions with English narration.
- **English canonical copy.** Planning, scripts and scene specs stay in English. Only learner-visible text and narration are translated, right before rendering and TTS. Equations are locked, so translation never corrupts notation.
- **Language changes.** Asking for the same video in another language builds a new version. Earlier versions stay available in the chat.

**Quality and self-correction**
- **Whole-lesson script review.** The full script is checked for correctness, flow, and a proper ending.
- **Checks on every rendered scene:**
  - render errors;
  - raw-LaTeX text shown on screen;
  - layout overlap and out-of-frame objects;
  - required equations;
  - pedagogy;
  - audio/visual sync, reviewed by a vision model.
- **Bounded repair.** Failing scenes enter a bounded repair loop. A final whole-lesson review can rebuild up to 3 weak scenes before assembly.
- **Speech timing.** Forced alignment of the narration audio times each visual to the spoken word.

**Robust and safe**
- **Sandboxed renders.** Generated Manim code is checked statically (an AST allow-list) before rendering. It then renders in a sandbox with CPU, memory and file limits.
- **Safe link fetching.** Links must be public HTTPS (SSRF-safe, size-capped). PDF text is sanitized before storage.
- **Durable jobs.** Builds run as Temporal workflows. A failed rebuild restores the previous working video.
- **Model fallbacks.** Every model capability has a fallback model, and usage and cost are tracked per project.

---

## Architecture

```mermaid
flowchart LR
    UI["Streamlit UI<br/>chat · player · scene edits"] --> API["FastAPI<br/>auth · chats · sources · progress"]
    API --> PG[("PostgreSQL<br/>source of truth")]
    API --> QD[("Qdrant<br/>retrieval index")]
    API --> TW["Temporal workflow"]
    TW --> W["Worker: lesson pipeline"]
    W --> P1["1 · Prepare<br/>intent, language, sources"]
    P1 --> P2["2 · Script + review"]
    P2 --> P3["3 · Per scene, in parallel:<br/>storyboard → TTS → Manim code → render → verify → repair"]
    P3 --> P4["4 · Whole-lesson review<br/>+ targeted scene repair"]
    P4 --> P5["5 · FFmpeg assembly<br/>MP4 + captions + chapters"]
    P5 --> ST[("Artifact store<br/>projects/")]
    ST --> UI
```

**Main components:**
- **PostgreSQL** holds the authoritative records: accounts, chats, projects, and every versioned artifact (script, spec, code, audio, render, video).
- **Qdrant** holds only embeddings that can be rebuilt.
- **Temporal** makes long builds durable and cancellable.

More detail is in [docs/system-design.md](docs/system-design.md).

### The lesson pipeline

| Stage | What happens | Code |
|---|---|---|
| Prepare | Planner LLM reads the chat: chat vs. video, languages, duration, source links and scope | `agents/lesson_preparation.py`, `api/tutor.py` |
| Ground | Retrieves from attached PDFs/links (and the web, if allowed) | `retrieval/`, `api/service.py` |
| Script | Drafts the whole lesson in English, then self-reviews and revises | `agents/script_writer.py` |
| Storyboard | Plans visual beats per scene to follow the narration | `agents/storyboard_agent.py` |
| Produce | TTS → forced alignment → Manim code → sandboxed render, scenes in parallel | `services/orchestration.py`, `agents/code_generator.py`, `manim_runtime/` |
| Verify / repair | Layout, math, pedagogy, raw-TeX and A/V checks; bounded repair loop | `verification/`, `agents/manager.py` |
| Review | Whole-lesson coherence review; rebuilds ≤3 weak scenes | `services/orchestration.py` |
| Assemble | FFmpeg concatenation, SRT/VTT captions, chapters | `media/ffmpeg.py`, `media/subtitles.py` |

---

## Quick start (Windows)

Development and testing were done on Windows 10/11 x64. The two scripts below set up and run everything locally under `.local/`. Docker is not needed.

**Prerequisites**
- [Miniconda or Miniforge](https://docs.conda.io/projects/miniconda/en/latest/), with `conda` on your PATH.
- An [OpenRouter](https://openrouter.ai/) API key, or any OpenAI-compatible provider. See [Configuration](#configuration).
- About 6 GB of disk space for the Conda env, the local embedding model and the aligner model.

**1. One-time setup.** This creates the Conda env, writes `.env` with a random client key, downloads Qdrant and Temporal, and initializes PostgreSQL:

```powershell
git clone <this-repo> Ekalavya
cd Ekalavya
powershell -ExecutionPolicy Bypass -File scripts\setup-local.ps1
```

**2. Add your model key.** Open `.env` and fill in the provider settings, as in the example under [Configuration](#configuration).

**3. Start everything:**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1
```

This starts PostgreSQL, Qdrant, Temporal, the API, the worker and the UI, and runs database migrations. Services that are already running are skipped. When it finishes, open:

| | URL |
|---|---|
| **App** | http://127.0.0.1:8501 |
| API docs (OpenAPI) | http://127.0.0.1:8000/docs |
| Temporal UI | http://127.0.0.1:8233 |

**4. Use it.** Click **Create account**, sign in, and try:
- `What is the derivative of sin x?` gets a chat answer.
- `Explain the Pythagorean theorem visually in Hindi` starts a video lesson.
- `Make a video from this blog: https://…` makes Ekalavya ask whether to use only that source.
- After a video finishes: `0:20 to 0:40, slow down and add a worked example`.

**Stop everything:**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop-local.ps1
```

Logs are written to `.local/logs/`. Your chats, accounts and videos persist across restarts: they live in `.local/postgres` and `projects/`.

**Optional: typeset equations.** Install [TinyTeX](https://yihui.org/tinytex/) into `.local\bin\TinyTeX`. Without it, Ekalavya detects the missing LaTeX and renders equations as Unicode text.

### Linux / macOS

There are no scripts for Linux or macOS yet; run the same services manually.

```bash
conda env create -f environment.yml && conda activate scholarmotion
cp .env.example .env                       # then add provider keys
initdb -D .local/postgres -U scholarmotion -A trust
pg_ctl -D .local/postgres -l .local/postgres.log start
createdb -U scholarmotion scholarmotion
./qdrant &                                 # native Qdrant binary, port 6333
temporal server start-dev &                # port 7233
alembic upgrade head
python -m scholarmotion.workflows.worker &
uvicorn scholarmotion.api.main:app --port 8000 &
SCHOLARMOTION_API_KEY=<APP_API_KEY from .env> PYTHONPATH=. streamlit run frontend/app.py
```

For an alternative Windows setup using Docker for Qdrant and a system PostgreSQL service, see [docs/windows.md](docs/windows.md).

---

## Configuration

All settings live in `.env`. [.env.example](.env.example) documents every variable. Each capability (main LLM, planner, code, translation, vision, video review, TTS, STT, embeddings) is configured independently, with an optional fallback model. Leave a capability blank to use deterministic mocks, which are useful for offline tests.

The reference setup uses a single OpenRouter key:

```dotenv
APP_API_KEY=<random; created by setup-local.ps1>
OPENROUTER_API_KEY=<your key>

MAIN_LLM_PROVIDER=openai_compatible
MAIN_LLM_BASE_URL=https://openrouter.ai/api/v1
MAIN_LLM_MODEL=anthropic/claude-sonnet-5.5        # script writing and review
MAIN_LLM_FALLBACK_MODELS=google/gemini-3.8-flash
PLANNER_LLM_MODEL=anthropic/claude-haiku-5.5      # chat/video intent, storyboard, lesson review

CODE_PROVIDER=openai_compatible
CODE_BASE_URL=https://openrouter.ai/api/v1
CODE_MODEL=anthropic/claude-sonnet-5.5            # Manim code

TRANSLATION_PROVIDER=openai_compatible
TRANSLATION_BASE_URL=https://openrouter.ai/api/v1
TRANSLATION_MODEL=google/gemini-2.5-flash-lite

VISUAL_LLM_PROVIDER=openai_compatible
VISUAL_LLM_BASE_URL=https://openrouter.ai/api/v1
VISUAL_LLM_MODEL=google/gemini-3.8-flash          # keyframe review

TTS_PROVIDER=openai_compatible
TTS_BASE_URL=https://openrouter.ai/api/v1
TTS_MODEL=google/gemini-3.8-flash-lite-tts
TTS_VOICE=Kore
TTS_RESPONSE_FORMAT=pcm

STT_PROVIDER=openai_compatible
STT_BASE_URL=https://openrouter.ai/api/v1
STT_MODEL=openai/whisper-large-v3                 # voice input

EMBEDDING_PROVIDER=local                          # sentence-transformers, runs on CPU
```

**Other providers.** Capabilities whose base URL is OpenRouter use `OPENROUTER_API_KEY`; `gemini` providers fall back to `GEMINI_API_KEY`; others use their own `*_API_KEY`. Other backends are also supported:
- Gemini directly (`*_PROVIDER=gemini`);
- Anthropic (`anthropic`);
- ElevenLabs or a local Kokoro server for TTS;
- local vLLM endpoints (`openai_compatible` with a local `*_BASE_URL`).

**Cost.** A 1–2 minute lesson typically costs about $0.20–0.30 with the setup above. Usage and cost are recorded for each project.

---

## Project structure

```
Ekalavya/
├── frontend/                 Streamlit UI
│   ├── app.py                  chat, sidebar/folders, player, edit & branch UI, settings
│   ├── assets/                 logo (ழ)
│   └── components/             caption track, auth-cookie bridge
├── src/scholarmotion/        backend package (internal name kept from the project's working title)
│   ├── api/                    FastAPI app
│   │   ├── main.py               routes: auth, chats, messages, sources, builds, edits, media
│   │   ├── tutor.py              chat turn routing (answer / start video / edit / ask about sources)
│   │   ├── service.py            build, edit and localization services; video versions
│   │   └── capabilities.py       per-capability endpoints (/v1/capabilities/*)
│   ├── agents/                 LLM agents: lesson preparation, curriculum, script writer,
│   │                           storyboard, Manim code generator, repair manager
│   ├── services/orchestration.py  the lesson pipeline (scenes in parallel, review, repair, assembly)
│   ├── manim_runtime/          sandbox renderer, AST validator, layout components, templates
│   ├── verification/           render, layout, math, pedagogy and grounding checks
│   ├── media/                  TTS orchestration, forced alignment, FFmpeg, subtitles
│   ├── retrieval/              PDF + web-page parsing, safe link fetching, Qdrant retrieval
│   ├── translation/            localization of learner-visible text (canonical English inside)
│   ├── providers/              LLM / TTS / STT / embedding adapters + factory with fallbacks
│   ├── persistence/            SQLAlchemy models, database, object storage
│   ├── workflows/              Temporal workflows, activities, worker
│   ├── skills/                 versioned agent skills stored in PostgreSQL
│   ├── observability/          JSON logs, Prometheus metrics, usage/cost tracking
│   ├── config/                 typed settings from .env
│   └── schemas/                shared Pydantic models
├── alembic/                  database migrations
├── tests/                    pytest suite (unit + mocked end-to-end)
├── scripts/
│   ├── setup-local.ps1         one-time Windows setup
│   ├── start-local.ps1         start all services
│   ├── stop-local.ps1          stop all services
│   ├── ingest_ncert.py         bulk-index NCERT textbook PDFs
│   ├── kokoro_server.py        optional local Kokoro TTS server
│   └── qa/                     live QA harnesses (browser, scenarios, multilingual, edits)
└── docs/                     system design, test plan and reports
```

---

## Tests

```powershell
conda activate scholarmotion
pytest -q        # ~330 tests, all offline: providers, DB and media are mocked or in-memory
ruff check src tests frontend
```

The suite covers:
- lesson preparation and chat routing;
- the source-scope policy (only the given source vs. given + web);
- video versioning and restore;
- message edit and branching;
- forced alignment and timing;
- layout and LaTeX validation;
- FFmpeg assembly;
- auth;
- provider fallbacks;
- a mocked end-to-end build.

The `scripts/qa/` harnesses drive the real running app, through its API and through a headless browser, for live checks.

---

## API

The full API is documented at `/docs`. Every route except `/health`, `/docs` and `/openapi.json` needs either a signed-in session or the `X-API-Key` client key. Main groups:

- `/v1/auth/*`: register, login, logout, account settings.
- `/v1/projects/*`: chats/projects, messages, sources (`/sources`, `/sources/link`), progress, videos, scene edits.
- `/v1/capabilities/*`: each model capability on its own (code, vision, TTS, STT, translation).
- `/v1/skills/*`: versioned agent skills with progressive disclosure.
- `/metrics`: Prometheus counters for latency, provider fallbacks and workflow outcomes.

---

## Known limitations

- **Platform:** Windows has scripts; Linux and macOS are documented but not scripted.
- **Sandboxing:** the render sandbox uses resource limits. Full network isolation (`SANDBOX_ISOLATION=required`, bubblewrap) is available only on Linux.
- **Lesson length:** set by the planner, up to 30 minutes. Very long lessons cost more and take longer to build.
- **Web sources:** web-page extraction is readability-style. Pages that need JavaScript to render must be attached as a PDF instead.
