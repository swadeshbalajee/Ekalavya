# Windows installation and QA report — 24 October 2026

This report records a new run on this machine. Historical checkmarks in
`functional-test-plan.md` are not evidence for this run. A successful HTTP request
or completed workflow is not, by itself, a learner-facing quality pass.

**Outcome:** the full local stack is installed and running. Automated checks pass,
but real lesson generation/localization and several input/error paths fail
acceptance. Failed test projects and evidence have been retained for reproduction.

**Workspace cleanup, 25 October:** unrelated research/benchmark files were moved
outside the project to `../out-unrelated-2026-10-25/`. Six research test cases were
removed with them; the Ekalavya-only suite now passes **218 tests** and lint
passes without research exclusions. QA helpers were grouped under `scripts/qa/`,
and the API/UI health checks pass. Counts of 224 below describe the historical
24 October mixed-archive run. Cleanup evidence is under `.local/qa/` in
`cleanup-2026-10-25.json` and `pytest-cleanup-2026-10-25.log`.

## Installation and access

- Conda environment: `scholarmotion`, Python 3.11.17, installed from `environment.yml`.
- Application dependencies installed successfully; `python -m pip check` found no broken requirements.
- Native local PostgreSQL 18.6, Qdrant 1.20.0, Temporal CLI 1.9.1/server 1.32.0,
  Manim 0.22.0, FFmpeg 9.0.2, and a local TinyTeX installation with Manim's TeX dependencies.
- PostgreSQL and every web service bind to loopback. Application and administrator API
  keys are generated in the ignored `.env`. Provider credentials are also kept there.
- Main acceptance projects belong to the requested `admin` account. Separate disposable
  accounts were used for ownership and registration tests.

| Component | Address |
|---|---|
| Learner UI | http://127.0.0.1:8501 |
| API documentation | http://127.0.0.1:8000/docs |
| Temporal UI | http://127.0.0.1:8233 |
| Qdrant | http://127.0.0.1:6333 |

Start the installed stack from this directory:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1
```

Stop the tracked local processes and PostgreSQL:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/stop-local.ps1
```

These scripts use the binaries/state already installed under `.local`; they do not
download or initialize a new machine. No Docker Desktop daemon was needed.

## Provider configuration exercised

Code generation uses `anthropic/claude-sonnet-4.6` through OpenRouter with medium
reasoning. Planning, translation, and visual review use `google/gemini-2.5-flash-lite`.
`openai/gpt-4.1-mini` was added as a planner/translation/vision fallback after real
upstream 429 responses. Video review retains its Gemini fallback. Speech synthesis
uses `fish-audio/s2.1-pro-free:free`. Initial transcription used
`openai/whisper-large-v3-turbo`; standard `openai/whisper-large-v3` was selected after
it returned Tamil script with closer wording on the same recording. Local sentence-transformer embeddings were downloaded
and exercised against real Qdrant collections.

The initial code configuration had a 6,000-token output ceiling and all four scene
responses in the first electromagnetism build ended incomplete. A configurable
16,000-token ceiling allowed a separate real Sonnet sample to return Python and
render successfully without a deterministic fallback. Full lesson generation still
encountered 180-second code-provider timeouts; the isolated sample does not establish
full-pipeline reliability.

Model availability was checked against the provider's model API and official
[Sonnet model page](https://openrouter.ai/anthropic/claude-sonnet-4.6),
[speech synthesis documentation](https://openrouter.ai/docs/guides/overview/multimodal/tts),
and [transcription documentation](https://openrouter.ai/docs/guides/overview/multimodal/stt).

## Automated and live evidence

| Check | Result | Evidence under `.local/qa/` |
|---|---|---|
| Initial complete pytest suite | 215 passed | `pytest.log`, `pytest.xml`, `coverage.json` |
| Final expanded regression suite | 224 passed; latest run 25.69 seconds; 64.7% application statement coverage | `pytest-release.log`, `pytest-release.xml`, `coverage-release.json` |
| Broad live API run | 92/100 passed; eight expected-rejection cases failed | `live-results.json`, `live.log` |
| Additional race/upload/speech checks | 8/16 passed initially | `additional-results.json` |
| Visible browser testing | Account/settings/progress, text-only chat, refresh, mobile composer and overflow checked; duration failed before the parser fix | `ui-results.json`, `ui-checks-final.log`, `ui-*.png` |
| Native sandbox render | Passed after Windows fixes; geometry JSON and MP4 produced | `render-probe-before-fix.json`, `render-probe-result.json` |
| Sandbox/storage negative cases | 14/14 passed, including blocked code, path escape, immutable overwrite, and timeout | `sandbox-edge-results.json` |
| Real Sonnet sample | Real code returned and rendered after TeX setup | `sonnet16k/code.py`, `sonnet16k/render-tex-complete-result.json` |
| Cancellation | Reached CANCELLED in five seconds; duplicate generation rejected | `scenarios-cancel.log` |
| Qdrant outage | Detected outage; PDF remained authoritative; vector indexing pending | `qdrant-outage-results.json` |
| Qdrant recovery/reconciliation | 501 source chunks and eight skills/corrections indexed | `qdrant-recovery-results.json` |
| Speech/provider retest | English/Hindi/Tamil STT returned 200; Tamil/Hindi translation preserved equation | `provider-retest-results.json` |
| Alternative STT comparison | Standard Whisper Large V3 returned Tamil script; Turbo returned Devanagari for the same audio | `whisper-large-v3-comparison.json` |
| Tamil multi-turn text chat | Three Tamil replies, action `answer`, no video workflow | `tamil-chat-results.json` |
| Actual browser media | Playback, seek, two chapter cues and nine subtitle cues loaded after CORS fix | `browser-player-after-fix.json` |
| Version restoration after failure | New active video version, COMPLETE status, old artifact unchanged | `restore-results.json` |
| Canonical English preservation | Both persisted English scene specs match their original JSON artifacts; equations unchanged | `canonical-spec-results.json` |

The 24 October suite included the older `t2m` tests then present in the archive. Ruff's initial
whole-tests check found pre-existing lint/format problems in those research tests.
Application lint and the QA scripts pass after excluding three pre-existing
research test files. The formatter also flags three pre-existing application/test
files (`code_generator.py`, `test_code_generator.py`, and
`test_orchestration_quality_filters.py`); modified files are checked separately.
Do not claim the entire mixed archive is lint/format-clean.

`acceptance-snapshot.json` captures project status, workflow errors, scene versions,
chat, source metadata, subtitle timings, HTTP artifact delivery, and FFprobe output.
It can be regenerated with `python scripts/qa/qa_snapshot.py` in the environment.
Unlike the private scenario session file, it contains no login token.

## Completed builds and quality findings

| Scenario | Project | Result |
|---|---|---|
| Initial electromagnetism | `5d7ad842-8df1-4757-8ebe-5bcdb14ca312` | FAILED after bounded repairs; no partial video published. Every scene failed rendering/verification. The adjective duration bug retained three minutes. |
| Initial mechanics | `bb1bc879-740d-45df-be53-d05267ea970d` | FAILED after bounded repairs; no partial video published. Same duration and Sonnet output issues. |
| Paper-grounded lesson | `465ed67d-6bf8-431e-a11b-867d18117534` | Completed two scenes and assembled MP4/SRT/VTT. Runtime pass, visual quality failure: missing equation glyphs in sampled frames despite PASSED scene states. |
| Corrected one-minute Sonnet retest | `397e4271-da71-4131-a5fa-dfed18756735` | FAILED after bounded model timeouts/repairs; both scenes blocked, no partial video published. Duration resolved to one minute correctly. |
| Full Hindi localization of the paper lesson | `465ed67d-6bf8-431e-a11b-867d18117534` | FAILED after 825 seconds. S02 retained high-severity layout overlaps. The previous English video was preserved and restored. |
| Tamil localization of S01 only | Same paper project | FAILED after 413 seconds, including critical unreadable equation glyphs. All 167 checked files in S02 were unchanged; this is an isolation pass, not a localization-quality pass. |

The paper video contains 60.000 s of H.264 video and 60.002833 s of AAC audio at
854×480. Authenticated MP4, subtitles, and chapters return successfully; a 1,024-byte
range request returns 206 with the correct Content-Range. Six sampled frames are
in `paper-contact-english.png`. Missing superscript/subscript glyphs are visible in the
attention equation. The narration teaches future-position masking, whereas sampled
visuals show a generic river-bank attention example without illustrating the mask.
Sampling does not establish full-video correctness. The original English snapshot
is preserved in `acceptance-before-localization.json`.

The public Attention paper import and upload parity both extracted the title
`Attention Is All You Need`. Two follow-up chat answers stayed conversational;
the decoder answer cited page 5. No claim-by-claim audit or broad golden-suite
quality pass is asserted.

The later edit/review paths explicitly identified the missing equation glyphs and
the generic attention example's mismatch with the masking narration. Thus the
initial published lesson's PASSED state is not a reliable quality acceptance result.
Localization used a temporary 45-second code timeout to exercise bounded provider
failure/fallback; final service configuration has been restored to 180 seconds.

## Bugs fixed during setup/testing

1. **Fresh database migrations failed.** Revision 0001 creates current ORM metadata;
   0007 and 0008 then attempted to recreate existing columns/table/indexes. They now
   inspect existing schema, matching the earlier migrations. Regressions upgrade
   both empty and ORM-initialized databases twice. Fresh PostgreSQL upgrade to
   revision 0008 also completed.
2. **Windows renderer could not determine its home directory.** Its stripped child
   environment omitted Windows profile/temp/OS variables. The renderer now supplies
   scratch profile/temp locations and required OS variables.
3. **Windows instrumentation paths were inserted as unescaped Python strings.**
   Paths now use a proper Python literal. A real Windows rendering regression
   verifies MP4 output and captured geometry in a path containing spaces.
4. **Code reasoning was hard-coded low and output limited to 6,000 tokens.** Added
   validated settings for reasoning effort and output budget. Regressions verify
   medium/high effort and the configured budget reach the provider request.
5. **An adjective between duration and video defeated duration precedence.** The
   parser now recognizes requests such as “1-minute beginner video”; a regression
   confirms that it overrides a stored three-minute setting.
6. **STT saved WAV input with an `.audio` extension.** The API now saves the WAV
   contract with `.wav`. The same test audio then returned successfully through
   the API. This fixes request delivery, not Tamil recognition quality.
7. **API-started videos were hidden in the UI.** Completion-summary messages are
   now valid video anchors. A visible admin browser confirmed the completed paper
   player after the fix. Progress also renders when a build has no user chat turn.
8. **Queued edits still reported COMPLETE.** Range and scene edit routes now set
   EDITING when the workflow is queued and retain the previous active video.
   A regression verifies both behaviors. Live workflows are also checked directly
   against Temporal so an old artifact cannot produce a false test pass.
9. **Browser captions/chapters failed across the UI/API ports.** HTTP artifact checks
   passed, but actual browser tracks had error state 3 and zero cues. The player now
   requests anonymous CORS media and the API permits the two configured local UI
   origins. The browser loaded two chapter cues and nine subtitle cues afterward;
   playback and seeking were also verified. Tests cover allowed/disallowed origins
   and authentication remaining required. `MEDIA_CORS_ORIGINS` configures the list.
10. **Restoring a good video retained the prior FAILED state.** Successful active
    reassembly now marks the project COMPLETE. A live restore after the failed
    Hindi localization produced video v2 and preserved the previous file hash
    (`restore-results-hindi.json`). After the Tamil failure, the final recovery
    produced English video v3 with the old artifact again unchanged
    (`restore-results.json`).

## Confirmed remaining issues

| Priority | Reproduction / observed behavior | Impact |
|---|---|---|
| High | Six simultaneous registrations for one fresh username returned `[500,201,500,500,409,409]`. | Unique-constraint races escape as server errors. |
| High | Creating a project with a NUL character in its title returns 500. An oversized create title also bypasses schema length validation. | Malformed client input reaches database exceptions. |
| High | Upload filename `../../escape.txt` returns 500. The storage layer correctly prevents the escape. | Validation failure is an unhandled server error; no traversal write succeeded. |
| High | Real translation upstream 429s returned application 500s before fallback configuration. | Provider errors lack a stable capability-level error contract; configured fallback mitigates availability but does not fix the API boundary. |
| High | Both paper scenes were PASSED although the final equation has missing glyphs. | Verification can accept defective learner-visible notation. |
| High | Complex full-scene Sonnet requests time out and use deterministic templates even with the larger budget. | A rendered sample does not demonstrate reliable model-authored lesson generation. |
| Medium | Empty/whitespace project titles and requests are accepted; whitespace renames and folders also succeed. | Blank conversations/folders and inconsistent input validation. |
| Medium | Corrupt `.pdf` and `.txt` source uploads return 201; parsing failure is stored in metadata rather than surfaced in the response. | UI/client can mistake a failed source for a usable paper; duplicate filename then returns 409. |
| Medium | Turbo's Tamil speech round trip returns Devanagari even with a Tamil hint. Standard Large V3 mitigates this; Hindi/Tamil transcripts still have spelling errors. | Broad multilingual intelligibility/correctness is not accepted on these short samples alone. |
| Medium | Worker logs show workflow tasks exceeding five seconds during rendering. Renderer/FFmpeg calls are synchronous inside async activities. | Responsiveness and durable-workflow scheduling need further hardening. |
| Medium | Single-scene localization is exposed on the legacy route without the matching `/v1/projects/.../localize` alias. | Versioned-only clients cannot use that operation consistently. |
| Medium | The API `/metrics` has workflow/provider metric definitions but no worker terminal-outcome samples after real failed workflows. Counters live in separate process memory. | API scraping alone does not collect the worker metrics described in the README; an actual shared/exported worker telemetry path is needed. |

## Acceptance limits and reproduction

### Mapping to the documented product checklist

| Checklist scenario | This run |
|---|---|
| Account/project isolation | Cross-owner reads, mutations, edits, copies and media rejected; registration race still fails. |
| Multi-turn tutor | English, paper-grounded follow-ups, and three Tamil text turns exercised. |
| Async scene generation | Prompt 202/queued behavior and live worker execution verified; golden physics builds failed. |
| Cancellation/recovery | Queued cancellation reached CANCELLED in five seconds; failed-video restoration passed. The attempted active-edit cancellation arrived after the edit failed and returned 409, so it is not an active-cancel pass. |
| Verification and repair | Bounded repair/failure behavior observed; published paper notation/visual mismatch is a false quality pass. |
| Selective scene edit | Live edit failed verification and preserved the previous complete artifact. Successful selective regeneration is not accepted. |
| Translation/Tamil | Equation-preserving translation and Tamil tutor chat passed; full Hindi localization failed after 825 seconds, and one-scene Tamil failed after 413 seconds. Untouched-scene hashes stayed stable. |
| Step 0 preparation | Real request preparation, paper retrieval, duration and text-only routing exercised; account defaults have unit/live coverage. NCERT-present retrieval was not run because no NCERT corpus was configured. |
| Paper upload | Real PDF/upload parity, duplicate conflict and 100 MB rejection passed; corrupt/non-PDF response handling fails. |
| Paper URL import | Real arXiv import, private destination/credentials rejection, and public non-PDF rejection passed. |
| Paper-grounded lesson | Real MP4 delivered; visual/narration quality fails. Full claim-by-claim PDF audit remains open. |
| Audio/subtitles | Real speech, measured A/V duration, SRT/VTT, authenticated range fetch, playback/seek and browser cues exercised. Full accessibility/intelligibility review remains open. |
| Skills/corrections | Live catalog/draft/resource/activation/admin-audit checks passed. Correction promotion/disable has unit coverage, not a new live promoted candidate in this run. |
| Provider outage/retry | Real 429, incomplete-response and timeout paths exercised; fallback restored translation. Capability error normalization still has gaps. |
| Video quality gate | Fails acceptance: physics builds blocked and the published paper has visible defects. |
| Observability | API metrics exported; worker metric visibility gap confirmed. No external Prometheus/OTLP dashboard was configured. |

This is broad testing, not proof that every possible input is safe or every video
is correct. A complete mechanics/electromagnetism/paper golden-quality suite has
not passed. Full cue-by-cue review, a successful Tamil/Hindi lesson localization,
successful selective regeneration, crash recovery during active rendering, and
external OTLP dashboard verification require passing evidence before being marked
accepted. The failed localized workflow did preserve all 167 checked untouched-scene
files; that narrower property has evidence in `tamil-selectivity.json`.

## Final running state

- API, Streamlit, Qdrant and Temporal health checks succeed; one Temporal worker is running.
- No test workflows remain RUNNING or CANCELLING.
- The admin paper project has a restored English video v3 and COMPLETE status.
  Its known visual defects remain documented; restoration is not a quality fix.
- Code defaults are Sonnet 4.6, medium reasoning, 16,000 output tokens and a
  180-second timeout. STT is standard Whisper Large V3.
- Browser playback/seek and chapter/subtitle cues were rechecked against the restored video.
- `acceptance-failures.json` preserves failed-workflow state before recovery;
  `acceptance-snapshot.json` records the final running state. Canonical English specs
  still match their original artifacts.
- Installed environment/package manifests and download hashes are in
  `environment-installed.yml`, `packages.txt`, and `download-sha256.json` under `.local/qa/`.
- Two disposable invalid-title projects were archived after recording their failures;
  named QA projects remain available to the admin account.

### Commands for repeating the checks

Useful commands, after activating Conda:

```powershell
python scripts/qa/qa_pytest.py -q
python scripts/qa/qa_live.py
python scripts/qa/qa_additional.py
python scripts/qa/qa_provider_retest.py
python scripts/qa/qa_snapshot.py
python scripts/qa/qa_contact_sheets.py
```

Provider tests make real billable requests. `qa_live.py` creates disposable users;
the scenario and additional scripts use the private saved admin session. The
Playwright UI checks require the installed `playwright` package and the visible
Edge session started by `qa_browser.py`. Its password is supplied through the
process environment, not checked into a script. State, logs, media, private sessions,
and downloaded binaries stay under ignored `.local/` and `projects/` directories.
`qa_pytest.py` disables dotenv loading before collection, so regression tests use
their test settings rather than accidentally consuming the operator's configured
real provider credentials.
