# Narration and visual-quality retest — 2026-10-25

This retest addresses the seven reported quality problems. The earlier installation
and broad acceptance results remain in [the original report](test-report-2026-10-24.md).
Historical videos are not evidence that the new generation path works.

## Changes

| Reported problem | Change |
| --- | --- |
| Unnatural narration | Gemini Flash Lite TTS with the named Kore voice and conversational STEM-tutor instructions; tempo correction limited to ten percent. |
| Symbols read as punctuation | Script prompts require spoken mathematics; the speech boundary verbalizes multiplication, transpose, roots, indices, fractions, and common STEM notation. |
| Narration and animation disagree | Animation receives the exact spoken narration after TTS, with sentence cues. Real scripts no longer get overwritten by a fixed river-bank example. Generic placeholder diagrams are blocked. |
| Keyword-dependent response mode | The preparation model chooses chat or video semantically on the latest turn and context, including visual paraphrases and ordinary questions. |
| Different voices between scenes | Credential-free project narrator profiles pin provider, model, voice, format, and speaking instructions across scenes and edits. Assembly rejects conflicting recorded narrator identities. |
| White boxes in equations | Canonical notation is converted to TeX through `STEMEquation`; transpose and subscripts do not depend on the text font's glyph coverage. |
| Abrupt or incoherent ending | The model writes and reviews the entire lesson, assigns scene-specific objectives, checks transitions and a closing takeaway, and tags the final scene for ending verification. |

Planning and script writing use `anthropic/claude-sonnet-4.6`. Code generation uses
Sonnet 4.6 with medium reasoning and Sonnet 4.5 as its configured model fallback.
The code adapter uses the native OpenRouter chat endpoint: the Responses
compatibility route returned incomplete code during the first retest.

The speech configuration is `google/gemini-3.8-flash-lite-tts`, voice `Kore`,
24 kHz mono PCM wrapped in WAV. Provider details follow the official
[TTS documentation](https://openrouter.ai/docs/guides/overview/multimodal/tts).

## Completed focused checks

- Six real-model intent cases passed: arithmetic and symbol clarifications stayed
  in chat; “watch the future positions disappear,” “explain visually,” and an explicit
  video explanation requested video; a short diagonal clarification stayed in chat.
- A real Manim equation render displayed `QKᵀ` and `dₖ` correctly, without missing
  glyph boxes. Evidence: `.local/qa/improvements/equation-probe.png` and its render metadata.
- Two independently synthesized Kore samples were joined for an audio review by
  `google/gemini-2.5-flash`. It transcribed “Q times K transpose” and “square root
  of d sub k,” reported consistent narration, rated naturalness 4/5, and found no
  pronunciation or ending problems. This is an automated listening assessment,
  not a human preference test. Evidence: `.local/qa/improvements/voice-review.json`.
- Gemini 3.8 Flash did not complete the earlier audio-review response; its partial
  response was not counted as a pass. The successful assessment used Gemini 2.5 Flash.
- A real Sonnet 4.6 code request with medium reasoning rendered successfully through
  the new chat route, without a deterministic template. Evidence:
  `.local/qa/sonnet16k/result.json`.
- The full offline regression suite passed 236 tests with Conda/FFmpeg/TeX available
  before the additional in-progress-chat cases. The final count is recorded below.

## Full lesson acceptance

The first QA project, `0836a113-deb2-4cc5-86a8-6028c4b407ba`, was cancelled after
verification rejected generic diagrams substituted for incomplete provider code.
It is a failed candidate, not a delivered lesson.

The current admin-account retest is `ed1187a2-91ef-42e8-bd08-4d0ce26b9e4d`.
Final media and verification results are pending. Do not count this build as a pass
until the scene reports, frames, audio, and delivered player have been checked.

## Limits

Sentence offsets are estimated from narration length and measured WAV duration;
they are not forced-alignment timestamps. Audio/video review must listen to the
actual media before concluding that speech and visuals are synchronized.
The named voice prevents random voice selection but cannot guarantee identical
prosody on every provider response. Earlier rendered videos need regeneration to
use the new script, voice, and math-rendering path.
