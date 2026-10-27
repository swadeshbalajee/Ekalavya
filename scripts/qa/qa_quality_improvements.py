"""Focused real-provider checks for narration, semantic routing, and scene quality.

Private session state stays under .local/qa; reports never contain credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from scholarmotion.agents.lesson_preparation import prepare_lesson
from scholarmotion.config import get_settings
from scholarmotion.providers import create_llm_provider

ROOT = Path(".local/qa/improvements")


def save(name, data):
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / name).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


async def main(stage):
    if stage == "ui":
        from playwright.async_api import async_playwright

        project_id = json.loads((ROOT / "live-project.json").read_text(encoding="utf-8"))[
            "project_id"
        ]
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp("http://127.0.0.1:9222")
            page = browser.contexts[0].pages[0]
            await page.goto("http://127.0.0.1:8501/?chat=" + project_id)
            await page.wait_for_timeout(5000)
            await page.screenshot(path=str(ROOT / "admin-quality-retest.png"), full_page=True)
            (ROOT / "admin-quality-retest.txt").write_text(
                await page.locator("body").inner_text(), encoding="utf-8"
            )
            print("Visible admin UI: " + project_id)
        return
    if stage in {"voice", "lesson_voice"}:
        import base64

        from openai import AsyncOpenAI

        from scholarmotion.providers import create_tts_provider

        settings = get_settings()
        tts = create_tts_provider(settings)
        report_prefix = "lesson-voice" if stage == "lesson_voice" else "voice"
        samples = [
            "A query looks for relevant information. Q*K^T gives query-key similarity scores. Divide by sqrt(d_k) to keep those scores well scaled.",
            "Now add the causal mask before softmax. Future positions receive negative infinity and therefore zero weight. The current position remains available, along with earlier tokens. That is how causal attention uses only available context.",
        ]
        clips = []
        if stage == "lesson_voice":
            project_id = json.loads((ROOT / "live-project.json").read_text(encoding="utf-8"))["project_id"]
            samples = [block["text"] for block in json.loads(
                (Path("projects") / project_id / "artifacts/script/v1.json").read_text(encoding="utf-8")
            )]
        for i, narration in enumerate(samples):
            from scholarmotion.schemas import TTSResult

            if stage == "lesson_voice":
                from scholarmotion.media.audio import estimate_sentence_timings, wav_duration
                folder = Path("projects") / project_id / "scenes" / f"S{i+1:02d}" / "audio"
                path = folder / "v1.timed-v1.wav"
                if not path.exists():
                    path = folder / "v1.wav"
                duration = wav_duration(str(path))
                clips.append(TTSResult(audio_path=str(path), duration_seconds=duration,
                    sentence_timings=estimate_sentence_timings(narration, duration),
                    voice_id=tts.voice, provider=tts.name, model=tts.model))
                continue
            metadata = ROOT / f"voice-sample-{i}.json"
            if metadata.exists():
                result = TTSResult.model_validate_json(metadata.read_text(encoding="utf-8"))
            else:
                result = await tts.synthesize(narration, ROOT / f"voice-sample-{i}.wav")
                save(metadata.name, result.model_dump(mode="json"))
            clips.append(result)
        client = AsyncOpenAI(
            api_key=settings.openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
            timeout=120,
            max_retries=0,
        )
        import shutil
        import subprocess
        import wave

        joined_path = ROOT / f"{report_prefix}-samples.wav"
        with wave.open(str(joined_path), "wb") as joined:
            joined.setnchannels(1)
            joined.setsampwidth(2)
            joined.setframerate(24000)
            for index, clip in enumerate(clips):
                sample_path = Path(clip.audio_path)
                with wave.open(str(sample_path), "rb") as sample:
                    needs_resampling = sample.getframerate() != 24000 or sample.getnchannels() != 1 or sample.getsampwidth() != 2
                if needs_resampling:
                    sample_path = ROOT / f"{report_prefix}-normalized-{index}.wav"
                    await asyncio.to_thread(subprocess.run, [shutil.which("ffmpeg") or "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                                    "-i", clip.audio_path, "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(sample_path)], check=True)
                with wave.open(str(sample_path), "rb") as sample:
                    joined.writeframes(sample.readframes(sample.getnframes()))
                joined.writeframes(b"\x00\x00" * 18000)
        reviewer = "google/gemini-2.5-flash"
        response = await client.chat.completions.create(
            model=reviewer,
            temperature=0,
            max_tokens=4096,
            response_format={"type": "json_object"},
            reasoning_effort="low",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Listen to this synthetic tutoring narration: two samples joined with a pause. Assess only what you hear. Return compact JSON: transcripts (one per sample), same_voice (boolean), naturalness (1-5), stem_pronunciation_correct (boolean), problems (list). Check for notation read as punctuation, asterisk, caret or raw markup; a key dimension d_k should not be described as subtraction. Check whether the narrator sounds consistent across the two samples, with a conversational teacher's cadence, and that the last sentence resolves naturally without cutoff. This is an audio production quality check, not identification of a real person.",
                        },
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "format": "wav",
                                "data": base64.b64encode(joined_path.read_bytes()).decode(),
                            },
                        },
                    ],
                }
            ],
        )
        save(f"{report_prefix}-review-response.json", response.model_dump(mode="json"))
        report = json.loads(response.choices[0].message.content)
        save(
            f"{report_prefix}-review.json",
            {
                "tts_model": tts.model,
                "voice": tts.voice,
                "reviewer": reviewer,
                "clips": [clip.model_dump(mode="json") for clip in clips],
                "review": report,
            },
        )
        print(json.dumps(report, indent=2))
        return
    if stage == "math":
        from dataclasses import asdict

        from scholarmotion.agents.code_generator import normalize_manim_direction_constants
        from scholarmotion.manim_runtime.sandbox import SandboxRenderer

        ROOT.mkdir(parents=True, exist_ok=True)
        code = normalize_manim_direction_constants(
            "from manim import *\nclass EquationProbe(Scene):\n"
            " def construct(self):\n"
            '  equation = Text("Attention(Q,K,V) = softmax(QKᵀ / sqrt(dₖ) + M)V", font_size=32)\n'
            "  equation.scale_to_fit_width(12)\n"
            "  self.add(equation)\n  self.wait(2)\n",
            latex_enabled=True,
        )
        (ROOT / "equation-probe.py").write_text(code, encoding="utf-8")
        result = SandboxRenderer(mock_missing=False).render(
            code, "EquationProbe", ROOT / "equation-probe"
        )
        save("equation-probe.json", asdict(result))
        print(f"Equation render success={result.success}; video={result.video_path}")
        if not result.success:
            print(result.log[-3000:])
        return
    if stage == "configure":
        from dotenv import set_key

        for name, value in {
            "MAIN_LLM_MODEL": "anthropic/claude-sonnet-4.6",
            "MAIN_LLM_FALLBACK_MODELS": "google/gemini-3.8-flash",
            "CODE_MODEL": "anthropic/claude-sonnet-4.6",
            "CODE_FALLBACK_MODELS": "anthropic/claude-sonnet-4.5",
            "CODE_REASONING_EFFORT": "medium",
            "CODE_GENERATION_TIMEOUT_SECONDS": "240",
            "CODE_MAX_OUTPUT_TOKENS": "16000",
            "TTS_MODEL": "google/gemini-3.8-flash-lite-tts",
            "TTS_VOICE": "Kore",
            "TTS_RESPONSE_FORMAT": "pcm",
            "TTS_PCM_SAMPLE_RATE": "24000",
            "VISUAL_LLM_MODEL": "google/gemini-3.8-flash",
            "VIDEO_LLM_MODEL": "google/gemini-3.8-flash",
        }.items():
            set_key(".env", name, value)
        print("Configured Sonnet planning/code, Gemini review, and the fixed Kore narrator.")
        return
    settings = get_settings()
    if stage == "intent":
        provider = create_llm_provider(settings)
        cases = [
            ("What is two plus two?", "answer"),
            ("What does the subscript k stand for?", "answer"),
            (
                "I'd understand causal masking better if I could watch the future positions disappear, one at a time.",
                "generate_video",
            ),
            ("Give me a video explanation of causal attention.", "generate_video"),
            (
                "Explain visually why the softmax weights on masked positions become zero.",
                "generate_video",
            ),
            ("Why is the diagonal kept? A short answer is enough.", "answer"),
        ]
        results = []
        for message, expected in cases:
            result = await prepare_lesson(
                provider,
                message=message,
                project_request="Causal masking in scaled dot-product attention",
                mode="chat",
                conversation="USER: We were discussing the attention mask.\nASSISTANT: It blocks future tokens.",
                account_text_language="English",
                account_voice_language="English",
                project_text_language="English",
                project_voice_language="English",
            )
            results.append(
                {
                    "message": message,
                    "expected": expected,
                    "actual": result.action,
                    "rationale": result.rationale,
                    "passed": result.action == expected,
                }
            )
            save("intent.json", results)
            print(f"{result.action}: {message} ({result.action == expected})", flush=True)
        return
    state = json.loads(Path(".local/qa/scenario-session.json").read_text(encoding="utf-8"))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        timeout=240,
        headers={"X-API-Key": settings.app_api_key, "Authorization": "Bearer " + state["token"]},
    ) as client:

        async def request(method, path, **kwargs):
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        if stage in {"start", "retest"}:
            previous = ROOT / "live-project.json"
            if previous.exists():
                old = json.loads(previous.read_text(encoding="utf-8"))
                save("previous-project-" + old["project_id"] + ".json", old)
            prompt = (
                "Explain visually, in a one-minute beginner lesson with two coherent scenes, how causal "
                "masking works in scaled dot-product attention. Show three token positions and why the "
                "current token cannot attend to future positions. Explain Q times K transpose, division "
                "by the square root of the key dimension, adding the mask before softmax, and why masked "
                "weights become zero. Use consistent notation and one example across both scenes. "
                "Finish with a clear takeaway, not a teaser."
            )
            if stage == "retest":
                # Fixed narration isolates the repaired render path; the prior
                # free-form run already exercised whole-lesson script planning.
                prompt += (
                    " Keep the visuals simple: one matrix or row at a time, with a separate equation "
                    "band. No gradient plots or extra topics. Use this two-scene narration fixture, "
                    "with only light wording edits for natural speech:\n"
                    "Scene 1: Each token has a query describing what it seeks, and a key describing "
                    "what it offers. Multiply Q by K transpose to score their matches. Divide by "
                    "the square root of the key dimension. If that dimension is four, divide by two: "
                    "scores eight, six, four become four, three, two. These scores are ready for masking.\n"
                    "Scene 2: For the second token, the first and current positions are available, "
                    "but the third is in the future. Add zero, zero, negative infinity before softmax. "
                    "Four, three, two become four, three, negative infinity. Softmax gives the future "
                    "position zero weight. Causal attention uses the present and past, without looking ahead."
                )
            project = await request(
                "POST",
                "/v1/projects",
                json={
                    "title": "Causal attention — voice and visual quality retest",
                    "request": prompt,
                    "target_duration_minutes": 1,
                    "mode": "chat",
                    "text_language": "English",
                    "voice_language": "English",
                },
            )
            project_id = project["id"]
            save("live-project.json", {"project_id": project_id})
            state["projects"]["quality_improvements"] = project_id
            Path(".local/qa/scenario-session.json").write_text(
                json.dumps(state, indent=2), encoding="utf-8"
            )
            result = await request(
                "POST", f"/v1/projects/{project_id}/chat", json={"message": prompt}
            )
            save("live-start.json", result)
            print(f"Admin project {project_id}: {result['action']}", flush=True)
            return
        project_id = json.loads((ROOT / "live-project.json").read_text(encoding="utf-8"))[
            "project_id"
        ]
        if stage == "cancel":
            result = await request("POST", f"/v1/projects/{project_id}/cancel")
            save("cancel-" + project_id + ".json", result)
            print("Cancelled this QA run: " + project_id)
            return
        if stage == "chat":
            before = await request("GET", f"/v1/projects/{project_id}")
            reply = await request(
                "POST",
                f"/v1/projects/{project_id}/chat",
                json={
                    "message": "Why do we keep the diagonal of the causal attention mask? Keep the answer brief.",
                },
            )
            after = await request("GET", f"/v1/projects/{project_id}")
            save(
                "in-progress-chat.json",
                {
                    "reply": reply,
                    "brief_preserved": before["request"] == after["request"],
                    "status_before": before["status"],
                    "status_after": after["status"],
                    "passed": reply["action"] == "answer" and before["request"] == after["request"],
                },
            )
            print("Live brief question action: " + reply["action"])
            return
        progress = await request("GET", f"/v1/projects/{project_id}/progress")
        save("live-progress.json", progress)
        print(json.dumps({**progress, "events": progress.get("events", [])[-6:]}, indent=2))
        if progress["status"] in {"COMPLETE", "FAILED", "CANCELLED"}:
            save(
                "live-result.json",
                {
                    "progress": progress,
                    "project": await request("GET", f"/v1/projects/{project_id}"),
                    "scenes": await request("GET", f"/v1/projects/{project_id}/scenes"),
                },
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "stage",
        choices=[
            "configure",
            "intent",
            "start",
            "retest",
            "status",
            "math",
            "voice",
            "lesson_voice",
            "cancel",
            "ui",
            "chat",
        ],
    )
    asyncio.run(main(parser.parse_args().stage))
