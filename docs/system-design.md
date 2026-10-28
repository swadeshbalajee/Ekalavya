# Ekalavya system design

![Ekalavya system design](diagrams/architecture.svg)

## Runtime boundaries

**Learner workspace** is the Streamlit interface. It owns the tutor chat, video player, and scene-edit controls. It never renders media itself.

**API / control plane** is FastAPI. It authenticates users, owns project/chat/progress APIs, reads and writes PostgreSQL, queries Qdrant, and starts durable Temporal workflows. It does not perform long-running lesson generation or rendering in the request process.

**Durable worker pipeline** is where lesson work actually runs. A Temporal workflow dispatches activities to a worker in this order:

1. Prepare the request and retrieve relevant sources.
2. Build the curriculum and canonical English script; translate learner-visible copies when needed.
3. Generate Manim scene code and narration assets.
4. Render the scene in the sandbox.
5. Verify layout, pedagogy, math, timing, and sampled visual output. Only a failed scene enters the bounded repair loop.
6. Assemble passed scenes, audio, and captions into the final MP4 with FFmpeg.

The lesson pipeline is a set of worker-invoked application modules, not separately deployed API services. This is why curriculum, script, translation, and scene-code generation appear once—inside the worker pipeline.

**Data and providers** are dependencies rather than pipeline stages. PostgreSQL is authoritative for projects, chat, and metadata. Qdrant is a rebuildable retrieval index. The artifact store retains MP4, audio, and SRT/VTT files. Model providers are configured per capability in `.env`; the reference setup uses OpenRouter (Claude Sonnet for scripts and Manim code, Claude Haiku for planning, Gemini Flash for translation, vision and TTS, Whisper for voice input), with a fallback model for each.
