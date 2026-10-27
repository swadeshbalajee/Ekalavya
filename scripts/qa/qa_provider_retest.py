"""Recheck provider boundaries after speech filename and fallback configuration fixes."""

import asyncio
import base64
import json
from pathlib import Path

import httpx

from scholarmotion.config import get_settings


async def main():
    root = Path(".local/qa")
    s = get_settings()
    rows = []
    originals = json.loads((root / "additional-results.json").read_text(encoding="utf-8"))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        headers={"X-API-Key": s.app_api_key},
        timeout=180,
        limits=httpx.Limits(keepalive_expiry=2),
    ) as c:
        for row in originals:
            if not row["name"].endswith("real TTS"):
                continue
            language = row["name"].split()[0]
            r = await c.post(
                "/v1/capabilities/stt/transcribe",
                json={
                    "audio_base64": base64.b64encode(
                        Path(row["detail"]["path"]).read_bytes()
                    ).decode(),
                    "language": language,
                },
            )
            data = r.json() if r.status_code == 200 else {"error": r.text[:200]}
            rows.append({"check": language + " STT", "status": r.status_code, "data": data})
            print(language, "STT", r.status_code, data, flush=True)
        for language in ("Tamil", "Hindi"):
            r = await c.post(
                "/v1/capabilities/translation/translate",
                json={
                    "text": "Force equals mass times acceleration. $F = ma$",
                    "source_language": "English",
                    "target_language": language,
                },
            )
            data = r.json() if r.status_code == 200 else {"error": r.text[:200]}
            rows.append({"check": language + " translation", "status": r.status_code, "data": data})
            print(language, "translation", r.status_code, data, flush=True)
    (root / "provider-retest-results.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
    )


asyncio.run(main())
