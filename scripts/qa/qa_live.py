"""Repeatable local API checks. Run in the scholarmotion environment with the API up.

Writes sanitized results to .local/qa/live-results.json; uses disposable QA users.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx

from scholarmotion.config import get_settings

OUT = Path(".local/qa")
RESULTS = []


async def main():
    settings = get_settings()
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=120) as c:
        key = {"X-API-Key": settings.app_api_key or ""}

        async def check(name, method, path, expected=200, headers=None, predicate=None, **kwargs):
            started = time.monotonic()
            try:
                r = await c.request(
                    method, path, headers=key if headers is None else headers, **kwargs
                )
                valid = r.status_code in (expected if isinstance(expected, tuple) else (expected,))
                valid = valid and (predicate(r) if predicate else True)
                row = {
                    "name": name,
                    "method": method,
                    "path": path,
                    "status": r.status_code,
                    "expected": expected,
                    "passed": bool(valid),
                    "seconds": round(time.monotonic() - started, 3),
                }
                if not valid:
                    # Auth success responses contain credentials: never save response bodies.
                    row["response"] = r.text[:700] if "/auth/" not in path else "[redacted]"
                RESULTS.append(row)
                print(("PASS " if valid else "FAIL ") + name, flush=True)
                OUT.mkdir(parents=True, exist_ok=True)
                (OUT / "live-results.json").write_text(
                    json.dumps(RESULTS, indent=2), encoding="utf-8"
                )
                return r
            except Exception as e:
                RESULTS.append(
                    {"name": name, "passed": False, "error": type(e).__name__ + ": " + str(e)}
                )
                print("ERROR " + name + ": " + str(e), flush=True)
                raise

        await check("public health", "GET", "/health", headers={})
        await check("public API docs", "GET", "/docs", headers={})
        await check("public OpenAPI", "GET", "/openapi.json", headers={})
        await check("API key required", "GET", "/v1/capabilities", 401, headers={})
        await check(
            "wrong API key", "GET", "/v1/capabilities", 401, headers={"X-API-Key": "incorrect"}
        )
        await check("user token required", "GET", "/v1/projects", 401)
        await check(
            "invalid user token",
            "GET",
            "/v1/projects",
            401,
            headers={**key, "Authorization": "Bearer invalid"},
        )
        await check("capability catalog", "GET", "/v1/capabilities")
        await check(
            "metrics exported", "GET", "/metrics", predicate=lambda r: "scholarmotion_" in r.text
        )
        await check("Qdrant corpus health", "GET", "/corpus/health")
        await check(
            "reject invalid username",
            "POST",
            "/v1/auth/register",
            422,
            json={"username": "x y", "password": "password123"},
        )
        await check(
            "reject weak password",
            "POST",
            "/v1/auth/register",
            422,
            json={"username": "qa_invalid", "password": "x"},
        )
        suffix = uuid4().hex[:10]
        users = []
        for n in range(2):
            data = {
                "username": f"qa_{suffix}_{n}",
                "password": uuid4().hex,
                "display_name": f"QA {n}",
            }
            r = await check(f"register user {n}", "POST", "/v1/auth/register", 201, json=data)
            users.append((data, {**key, "Authorization": "Bearer " + r.json()["token"]}))
        data, a = users[0]
        b = users[1][1]
        await check("duplicate username", "POST", "/v1/auth/register", 409, json=data)
        await check(
            "case-insensitive login",
            "POST",
            "/v1/auth/login",
            json={"username": data["username"].upper(), "password": data["password"]},
        )
        await check(
            "wrong password",
            "POST",
            "/v1/auth/login",
            401,
            json={"username": data["username"], "password": "wrong"},
        )
        await check("account profile", "GET", "/v1/auth/me", headers=a)
        await check(
            "account preferences",
            "PATCH",
            "/v1/auth/me",
            headers=a,
            json={"default_duration_minutes": 5, "default_text_language": "English"},
        )
        for duration in (0, -1, 31):
            await check(
                f"account rejects duration {duration}",
                "PATCH",
                "/v1/auth/me",
                422,
                headers=a,
                json={"default_duration_minutes": duration},
            )
        await check(
            "invalid profile color",
            "PATCH",
            "/v1/auth/me",
            422,
            headers=a,
            json={"avatar_color": "red"},
        )
        p = (
            await check(
                "create chat project",
                "POST",
                "/v1/projects",
                201,
                headers=a,
                json={"title": "QA chat", "request": "Explain electric flux.", "mode": "chat"},
            )
        ).json()["id"]
        root = "/v1/projects/" + p
        await check(
            "project inherits account duration",
            "GET",
            root,
            headers=a,
            predicate=lambda r: r.json()["target_duration_minutes"] == 5,
        )
        await check(
            "list own projects",
            "GET",
            "/v1/projects",
            headers=a,
            predicate=lambda r: p in str(r.json()),
        )
        await check(
            "other account list isolation",
            "GET",
            "/v1/projects",
            headers=b,
            predicate=lambda r: p not in str(r.json()),
        )
        for method, endpoint, body in [
            ("GET", "", None),
            ("PATCH", "", {"title": "stolen"}),
            ("DELETE", "", None),
            ("POST", "/generate", None),
            ("POST", "/edit-range", {"start_time": 0, "end_time": 1, "instruction": "bad"}),
            ("GET", "/chat", None),
            ("GET", "/video", None),
            ("GET", "/subtitles.vtt", None),
            ("POST", "/copy", {}),
        ]:
            await check(
                "cross-owner " + method + " " + (endpoint or "project"),
                method,
                root + endpoint,
                404,
                headers=b,
                **({"json": body} if body is not None else {}),
            )
        await check("malformed project UUID", "GET", "/v1/projects/not-a-uuid", 422, headers=a)
        await check("missing project", "GET", "/v1/projects/" + str(uuid4()), 404, headers=a)
        await check(
            "reject malformed JSON",
            "POST",
            "/v1/projects",
            422,
            headers={**a, "Content-Type": "application/json"},
            content="{",
        )
        for duration in (0, -1, 31):
            await check(
                f"project rejects duration {duration}",
                "POST",
                "/v1/projects",
                422,
                headers=a,
                json={
                    "title": "bad",
                    "request": "Explain flux",
                    "target_duration_minutes": duration,
                },
            )
        await check(
            "reject unsupported mode",
            "POST",
            "/v1/projects",
            422,
            headers=a,
            json={"title": "bad", "request": "x", "mode": "invalid"},
        )
        await check(
            "reject blank project title",
            "POST",
            "/v1/projects",
            422,
            headers=a,
            json={"title": "", "request": "Explain flux"},
        )
        await check(
            "reject oversized project title",
            "POST",
            "/v1/projects",
            422,
            headers=a,
            json={"title": "X" * 301, "request": "Explain flux"},
        )
        await check(
            "reject blank project request",
            "POST",
            "/v1/projects",
            422,
            headers=a,
            json={"title": "Blank", "request": ""},
        )
        await check(
            "rename project", "PATCH", root, headers=a, json={"title": "QA renamed", "pinned": True}
        )
        await check(
            "reject whitespace project rename", "PATCH", root, 422, headers=a, json={"title": "   "}
        )
        for suffix_path in ("/video", "/subtitles.vtt"):
            await check(
                "missing artifact " + suffix_path, "GET", root + suffix_path, 404, headers=a
            )
        for suffix_path in (
            "/scenes",
            "/timeline",
            "/chapters.vtt",
            "/memory",
            "/revisions",
            "/progress",
            "/chat",
        ):
            await check("empty project " + suffix_path, "GET", root + suffix_path, headers=a)
        for start, end in ((-1, 1), (1, 1), (2, 1)):
            await check(
                f"invalid edit range {start}:{end}",
                "POST",
                root + "/edit-range",
                422,
                headers=a,
                json={"start_time": start, "end_time": end, "instruction": "change"},
            )
        await check(
            "missing scene edit",
            "POST",
            root + "/scenes/missing/edit",
            404,
            headers=a,
            json={"instruction": "change"},
        )
        await check(
            "empty chat rejected", "POST", root + "/chat", 422, headers=a, json={"message": ""}
        )
        await check(
            "oversized chat rejected",
            "POST",
            root + "/chat",
            422,
            headers=a,
            json={"message": "X" * 20001},
        )
        for i, message in enumerate(
            (
                "Explain electric flux in text only.",
                "How does it relate to area?",
                "Correct that: what if the field is parallel to the surface?",
            )
        ):
            await check(
                f"chat turn {i + 1}",
                "POST",
                root + "/chat",
                headers=a,
                json={"message": message},
                predicate=lambda r: (
                    r.json()["action"] == "answer" and not r.json().get("workflow_id")
                ),
            )
        await check(
            "chat history persisted",
            "GET",
            root + "/chat",
            headers=a,
            predicate=lambda r: len(r.json()) >= 6,
        )
        for url in (
            "http://example.com/a.pdf",
            "https://127.0.0.1/a.pdf",
            "https://localhost/a.pdf",
            "https://192.168.1.1/a.pdf",
            "https://user:password@example.com/a.pdf",
            "file:///etc/passwd",
        ):
            await check(
                "unsafe URL " + url.split("/")[2],
                "POST",
                root + "/sources/link",
                422,
                headers=a,
                json={"url": url},
            )
        await check(
            "reject corrupt PDF",
            "POST",
            root + "/sources",
            422,
            headers=a,
            files={"file": ("corrupt.pdf", b"%PDF-1.4 not a real PDF", "application/pdf")},
        )
        await check(
            "reject non-PDF upload",
            "POST",
            root + "/sources",
            422,
            headers=a,
            files={"file": ("notes.txt", b"not a PDF", "text/plain")},
        )
        await check(
            "reject traversal filename",
            "POST",
            root + "/sources",
            (400, 422),
            headers=a,
            files={"file": ("../../escape.txt", b"QA harmless text", "text/plain")},
        )
        f = (
            await check(
                "create folder",
                "POST",
                "/v1/project-folders",
                201,
                headers=a,
                json={"name": "QA " + suffix},
            )
        ).json()["id"]
        await check(
            "duplicate folder",
            "POST",
            "/v1/project-folders",
            409,
            headers=a,
            json={"name": "QA " + suffix},
        )
        await check(
            "reject whitespace folder",
            "POST",
            "/v1/project-folders",
            422,
            headers=a,
            json={"name": "   "},
        )
        await check(
            "folder owner isolation",
            "PATCH",
            "/v1/project-folders/" + f,
            404,
            headers=b,
            json={"name": "stolen"},
        )
        await check("move project", "POST", root + "/move", headers=a, json={"folder_id": f})
        await check(
            "copy project", "POST", root + "/copy", 201, headers=a, json={"title": "QA copy"}
        )
        await check("folder listing", "GET", "/v1/project-folders", headers=a)
        await check("skill catalog", "GET", "/v1/skills", predicate=lambda r: len(r.json()) > 0)
        await check("skill instructions", "GET", "/v1/skills/manim-scene")
        await check("missing skill", "GET", "/v1/skills/no-such-skill", 404)
        await check("admin audit forbidden", "GET", "/admin/audit", 403)
        await check(
            "wrong admin key", "GET", "/admin/audit", 403, headers={**key, "X-Admin-Key": "wrong"}
        )
        admin = {**key, "X-Admin-Key": settings.admin_api_key or ""}
        await check("admin audit allowed", "GET", "/admin/audit", headers=admin)
        skill = "qa-" + suffix
        await check(
            "draft skill",
            "POST",
            "/v1/skills",
            201,
            json={
                "name": skill,
                "description": "QA test",
                "capabilities": ["code"],
                "instructions": "QA instructions",
                "resources": [{"path": "references/qa.md", "content": "QA reference"}],
            },
        )
        await check(
            "skill activation forbidden", "POST", f"/v1/skills/{skill}/versions/1/activate", 403
        )
        await check(
            "admin skill activation",
            "POST",
            f"/v1/skills/{skill}/versions/1/activate",
            headers=admin,
        )
        await check("skill resource", "GET", f"/v1/skills/{skill}/resources/references/qa.md")
        await check(
            "skill audit persisted",
            "GET",
            "/admin/audit",
            headers=admin,
            predicate=lambda r: skill in r.text,
        )
        await check(
            "empty TTS rejected", "POST", "/v1/capabilities/tts/synthesize", 422, json={"text": ""}
        )
        await check(
            "TTS capability",
            "POST",
            "/v1/capabilities/tts/synthesize",
            json={"text": "QA speech test."},
        )
        await check(
            "invalid STT base64",
            "POST",
            "/v1/capabilities/stt/transcribe",
            422,
            json={"audio_base64": "***invalid***"},
        )
        await check(
            "translation capability",
            "POST",
            "/v1/capabilities/translation/translate",
            json={"text": "Flux", "target_language": "English"},
        )
        await check("logout", "POST", "/v1/auth/logout", 204, headers=b)
        await check("revoked token rejected", "GET", "/v1/projects", 401, headers=b)
        print(
            json.dumps(
                {
                    "total": len(RESULTS),
                    "passed": sum(x["passed"] for x in RESULTS),
                    "failed": [x["name"] for x in RESULTS if not x["passed"]],
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
