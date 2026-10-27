"""Three-turn Tamil tutor check with explicit text-only routing."""

import asyncio
import json
from pathlib import Path

import httpx

from scholarmotion.config import get_settings


async def main():
    root = Path(".local/qa")
    s = get_settings()
    state = json.loads((root / "scenario-session.json").read_text(encoding="utf-8"))
    rows = []
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        headers={"X-API-Key": s.app_api_key, "Authorization": "Bearer " + state["token"]},
        timeout=180,
        limits=httpx.Limits(keepalive_expiry=2),
    ) as c:
        r = await c.post(
            "/v1/projects",
            json={
                "title": "QA Tamil tutor chat",
                "request": "Explain Ohm law in text only.",
                "mode": "chat",
                "text_language": "Tamil",
                "voice_language": "Tamil",
            },
        )
        r.raise_for_status()
        project = r.json()["id"]
        for message, language in (
            ("Text only: Explain Ohm's law in Tamil, with V = IR.", "English"),
            ("எதிர்ப்பு அதிகரித்தால் மின்னோட்டம் ஏன் குறைகிறது?", "Tamil"),
            ("மின்னழுத்தம் மாறாமல் இருப்பதாக எடுத்துக்கொள்ளுங்கள். இரண்டு எண் உதாரணங்களைக் கொடுங்கள்.", "Tamil"),
        ):
            r = await c.post(
                f"/v1/projects/{project}/chat",
                json={
                    "message": message,
                    "language": language,
                    "text_language": "Tamil",
                    "voice_language": "Tamil",
                },
            )
            data = r.json() if r.status_code == 200 else {"error": r.text[:300]}
            passed = (
                r.status_code == 200
                and data.get("action") == "answer"
                and not data.get("workflow_id")
                and any("\u0b80" <= ch <= "\u0bff" for ch in data.get("message", ""))
            )
            rows.append({"status": r.status_code, "passed": passed, "response": data})
            print("Tamil turn", len(rows), "passed:", passed, flush=True)
        final = (await c.get(f"/v1/projects/{project}")).json()
        result = {"project_id": project, "turns": rows, "final_status": final["status"]}
        (root / "tamil-chat-results.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        for project_id in (
            "f02bf9a2-a6e5-49df-a5fe-51429ff7982f",
            "fad73c0f-008a-45a7-ab2c-9de877db4bc8",
        ):
            await c.patch("/v1/projects/" + project_id, json={"archived": True})


asyncio.run(main())
