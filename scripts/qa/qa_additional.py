"""Additional live boundaries, upload parity, speech and race-condition checks."""

import asyncio
import base64
import json
from pathlib import Path
from uuid import uuid4

import httpx

from scholarmotion.config import get_settings


async def main():
    settings = get_settings()
    root = Path(".local/qa")
    state = json.loads((root / "scenario-session.json").read_text(encoding="utf-8"))
    rows = []

    def record(name, passed, detail=None):
        rows.append({"name": name, "passed": passed, "detail": detail})
        (root / "additional-results.json").write_text(
            json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(("PASS " if passed else "FAIL ") + name, flush=True)

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        headers={"X-API-Key": settings.app_api_key, "Authorization": "Bearer " + state["token"]},
        timeout=180,
        limits=httpx.Limits(keepalive_expiry=2),
    ) as c:
        # A same-username race must yield a conflict, never an unhandled database error.
        data = {"username": "qa_race_" + uuid4().hex[:8], "password": uuid4().hex}
        responses = await asyncio.gather(
            *(c.post("/v1/auth/register", json=data) for _ in range(6))
        )
        statuses = [r.status_code for r in responses]
        record(
            "concurrent duplicate registration",
            statuses.count(201) == 1 and statuses.count(409) == 5,
            statuses,
        )
        for name, text in [
            ("zero width", "\u200b"),
            ("whitespace", "   "),
            ("null character", "\u0000"),
        ]:
            r = await c.post(
                "/v1/projects", json={"title": text, "request": "Explain a concept", "mode": "chat"}
            )
            record("invalid title " + name, r.status_code == 422, {"status": r.status_code})
        r = await c.post(
            "/v1/projects",
            json={
                "title": "QA upload parity",
                "request": "Answer questions about attention.",
                "mode": "chat",
            },
        )
        p = r.json()["id"]
        base = "/v1/projects/" + p
        paper = state["projects"].get("paper")
        if paper:
            files = list((settings.object_storage_root / paper / "sources").glob("*.pdf"))
            if files:
                content = files[0].read_bytes()
                r = await c.post(
                    base + "/sources",
                    files={"file": ("attention-upload.pdf", content, "application/pdf")},
                )
                record(
                    "PDF upload parity",
                    r.status_code == 201 and r.json().get("title") == "Attention Is All You Need",
                    {"status": r.status_code, "response": r.json()},
                )
                r = await c.post(
                    base + "/sources",
                    files={"file": ("attention-upload.pdf", content, "application/pdf")},
                )
                record(
                    "duplicate PDF upload conflict", r.status_code == 409, {"status": r.status_code}
                )
        r = await c.post(
            base + "/sources",
            files={"file": ("oversized.pdf", b"x" * (100 * 1024 * 1024 + 1), "application/pdf")},
        )
        record("upload over 100 MB rejected", r.status_code == 413, {"status": r.status_code})
        r = await c.post(base + "/sources/link", json={"url": "https://example.com"})
        record("public non-PDF URL rejected", r.status_code == 422, {"status": r.status_code})
        for language, text, terms in (
            ("English", "The acceleration is three meters per second squared.", ["three", "3"]),
            ("Hindi", "बल द्रव्यमान और त्वरण का गुणनफल है।", ["बल", "द्रव्यमान"]),
            ("Tamil", "மின்னோட்டம் அதிகரித்தால் காந்தப்புலம் அதிகரிக்கும்.", ["மின்", "காந்த"]),
        ):
            r = await c.post(
                "/v1/capabilities/tts/synthesize", json={"text": text, "language": language}
            )
            if r.status_code != 200:
                record(
                    language + " real TTS",
                    False,
                    {"status": r.status_code, "response": r.text[:300]},
                )
                continue
            payload = r.json()
            path = Path(payload["audio_path"])
            record(
                language + " real TTS",
                path.is_file() and path.stat().st_size > 1000,
                {"duration": payload["duration_seconds"], "path": str(path)},
            )
            r = await c.post(
                "/v1/capabilities/stt/transcribe",
                json={
                    "audio_base64": base64.b64encode(path.read_bytes()).decode(),
                    "language": language,
                },
            )
            transcript = r.json().get("text", "") if r.status_code == 200 else ""
            record(
                language + " speech round trip",
                r.status_code == 200 and any(term in transcript.lower() for term in terms),
                {"status": r.status_code, "transcript": transcript},
            )
        for language in ("Tamil", "Hindi"):
            r = await c.post(
                "/v1/capabilities/translation/translate",
                json={
                    "text": "Force equals mass times acceleration. $F = ma$",
                    "source_language": "English",
                    "target_language": language,
                },
            )
            translated = r.json().get("text", "") if r.status_code == 200 else ""
            record(
                language + " translation preserves equation",
                r.status_code == 200 and "$F = ma$" in translated,
                {"status": r.status_code, "text": translated},
            )
        print(f"Additional checks: {sum(row['passed'] for row in rows)}/{len(rows)}", flush=True)


asyncio.run(main())
