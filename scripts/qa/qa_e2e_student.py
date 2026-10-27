"""Live end-to-end check from a student's point of view (real, billable providers).

Usage (scholarmotion env, stack running):  python scripts/qa/qa_e2e_student.py [phase]
Phases: intent | answer | video | edit | localize | paper [link] [prompt]
        | lesson <label> <minutes> <language> <prompt> | scene_edit <label> <scene> <instruction>
        | scene_localize <label> <scene> <language> | all (default: intent, answer, video).
Results go to .local/qa/e2e-2026-10-25/.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx

from scholarmotion.config import get_settings

OUT = Path(".local/qa/e2e-2026-10-25")
API = "http://127.0.0.1:8000"
STATE = OUT / "state.json"


def save(name: str, payload) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def load_state() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


async def session(client: httpx.AsyncClient, key: dict) -> dict:
    state = load_state()
    if state.get("token"):
        return {**key, "Authorization": "Bearer " + state["token"]}
    user = {"username": f"student-{uuid4().hex[:8]}", "password": "Student-pass-2026"}
    response = await client.post("/v1/auth/register", json=user, headers=key)
    response.raise_for_status()
    state.update(token=response.json()["token"], username=user["username"])
    save("state.json", state)
    return {**key, "Authorization": "Bearer " + state["token"]}


async def new_chat(
    client, headers, prompt: str, minutes: float = 1, language: str = "English"
) -> dict:
    project = await client.post(
        "/v1/projects",
        headers=headers,
        json={
            "title": prompt[:80],
            "request": prompt,
            "mode": "chat",
            "target_duration_minutes": minutes,
            "text_language": language,
            "voice_language": language,
        },
    )
    project.raise_for_status()
    return project.json()


async def chat(
    client, headers, project_id: str, message: str, effort: str = "medium", language: str = "English"
) -> dict:
    started = time.monotonic()
    response = await client.post(
        f"/v1/projects/{project_id}/chat",
        headers=headers,
        json={"message": message, "language": language, "reasoning_effort": effort},
        timeout=400,
    )
    response.raise_for_status()
    return {**response.json(), "seconds": round(time.monotonic() - started, 1)}


INTENT_CASES = [
    ("Explain QKV attention", "answer"),
    ("whats a derivative", "answer"),
    ("explain backprop visually pls", "generate_video"),
    ("can u make me something i can watch about how torque works", "generate_video"),
    ("i learn better by seeing, how does a transistor switch", "generate_video"),
    ("just tell me the formula for kinetic energy", "answer"),
]


async def phase_intent(client, headers) -> None:
    rows = []
    for prompt, expected in INTENT_CASES:
        project = await new_chat(client, headers, prompt)
        reply = await chat(client, headers, project["id"], prompt)
        if reply["action"] == "generate_video":
            await client.post(f"/v1/projects/{project['id']}/cancel", headers=headers)
        rows.append(
            {
                "prompt": prompt,
                "expected": expected,
                "action": reply["action"],
                "pass": reply["action"] == expected,
                "seconds": reply["seconds"],
            }
        )
        print(rows[-1])
    save("intent.json", rows)


async def phase_answer(client, headers) -> None:
    rows = []
    for prompt, effort in (("Explain QKV attention", "high"), ("explain entropy in thermodynamics", "low")):
        project = await new_chat(client, headers, prompt)
        reply = await chat(client, headers, project["id"], prompt, effort)
        message = reply["message"]
        rows.append(
            {
                "prompt": prompt,
                "effort": effort,
                "action": reply["action"],
                "seconds": reply["seconds"],
                "chars": len(message),
                "has_sources": "**Sources**" in message,
                "has_math": "$" in message,
                "message": message,
            }
        )
        print({k: v for k, v in rows[-1].items() if k != "message"})
    save("answer.json", rows)


async def phase_video(client, headers) -> None:
    # Optional second argument overrides the default lesson prompt.
    prompt = (
        sys.argv[2]
        if len(sys.argv) > 2
        else "explain how causal masking in attention works, visually"
    )
    project = await new_chat(client, headers, prompt)
    state = load_state()
    state["video_project"] = project["id"]
    save("state.json", state)
    reply = await chat(client, headers, project["id"], prompt)
    print("chat:", reply["action"], reply["message"][:160])
    if reply["action"] != "generate_video":
        save("video.json", {"error": "not routed to video", "reply": reply})
        return
    await wait_for(client, headers, project["id"], "video")


async def phase_paper(client, headers) -> None:
    """Attach a paper by link and ask for a full lecture, as a student would."""
    link = sys.argv[2] if len(sys.argv) > 2 else "https://arxiv.org/abs/1706.03762"
    prompt = sys.argv[3] if len(sys.argv) > 3 else "can you explain this paper to me in a video"
    project = await new_chat(client, headers, prompt)
    state = load_state()
    state["paper_project"] = project["id"]
    save("state.json", state)
    attached = await client.post(
        f"/v1/projects/{project['id']}/sources/link",
        headers=headers,
        json={"url": link},
        timeout=300,
    )
    attached.raise_for_status()
    print("attached:", attached.json().get("title"))
    reply = await chat(client, headers, project["id"], prompt)
    print("chat:", reply["action"], reply["message"][:160])
    if reply["action"] != "generate_video":
        save("paper.json", {"error": "not routed to video", "reply": reply})
        return
    await wait_for(client, headers, project["id"], "paper", limit_minutes=120)


async def phase_lesson(client, headers) -> None:
    """lesson <label> <minutes> <language> <prompt>: a lesson video of a set length and language."""
    label, minutes, language, prompt = sys.argv[2], float(sys.argv[3]), sys.argv[4], sys.argv[5]
    project = await new_chat(client, headers, prompt, minutes, language)
    state = load_state()
    state[f"{label}_project"] = project["id"]
    save("state.json", state)
    reply = await chat(client, headers, project["id"], prompt, language=language)
    print("chat:", reply["action"], reply["message"][:160])
    if reply["action"] != "generate_video":
        save(f"{label}.json", {"error": "not routed to video", "reply": reply})
        return
    await wait_for(client, headers, project["id"], label, limit_minutes=90)


async def phase_retry(client, headers) -> None:
    """retry <label>: press Retry on a failed lesson (reuses its saved plan)."""
    label = sys.argv[2]
    project_id = load_state()[f"{label}_project"]
    queued = await client.post(f"/v1/projects/{project_id}/generate", headers=headers)
    queued.raise_for_status()
    await wait_for(client, headers, project_id, label, limit_minutes=90)


async def phase_scene_edit(client, headers) -> None:
    """scene_edit <label> <scene_id> <instruction>: the learner picks a scene and asks for a change."""
    label, scene_id, instruction = sys.argv[2], sys.argv[3], sys.argv[4]
    project_id = load_state()[f"{label}_project"]
    queued = await client.post(
        f"/v1/projects/{project_id}/scenes/{scene_id}/edit",
        headers=headers,
        json={"instruction": instruction},
    )
    queued.raise_for_status()
    print("scene edit:", queued.json())
    await wait_for(client, headers, project_id, f"{label}-edit-{scene_id}")


async def phase_scene_localize(client, headers) -> None:
    """scene_localize <label> <scene_id> <language>: re-explain one scene in another language."""
    label, scene_id, language = sys.argv[2], sys.argv[3], sys.argv[4]
    project_id = load_state()[f"{label}_project"]
    queued = await client.post(
        f"/projects/{project_id}/scenes/{scene_id}/localize",
        headers=headers,
        json={"text_language": language, "voice_language": language},
    )
    queued.raise_for_status()
    print("scene localize:", queued.json())
    await wait_for(client, headers, project_id, f"{label}-localize-{scene_id}")


async def wait_for(client, headers, project_id: str, label: str, limit_minutes: int = 60) -> dict:
    deadline = time.monotonic() + limit_minutes * 60
    seen = 0
    progress = {}
    while time.monotonic() < deadline:
        progress = (await client.get(f"/v1/projects/{project_id}/progress", headers=headers)).json()
        events = progress.get("events", [])
        for event in events[seen:]:
            detail = {k: v for k, v in (event.get("detail") or {}).items() if k != "scenes"}
            scene = detail.pop("scene_key", "")
            print(f"  [{label}] {event['event']} {scene} {json.dumps(detail, default=str)[:400]}")
        seen = len(events)
        if progress.get("status") in {"COMPLETE", "FAILED", "CANCELLED"} and events:
            break
        await asyncio.sleep(10)
    save(f"{label}-progress.json", progress)
    print(f"[{label}] status:", progress.get("status"), progress.get("failure_message"))
    if progress.get("status") == "COMPLETE":
        await download(client, headers, project_id, label)
    return progress


async def download(client, headers, project_id: str, label: str) -> None:
    for resource, name in (("video", "final.mp4"), ("subtitles.vtt", "subtitles.vtt")):
        response = await client.get(f"/v1/projects/{project_id}/{resource}", headers=headers)
        if response.status_code == 200:
            (OUT / f"{label}-{name}").write_bytes(response.content)
    chat_log = (await client.get(f"/v1/projects/{project_id}/chat", headers=headers)).json()
    scenes = (await client.get(f"/v1/projects/{project_id}/scenes", headers=headers)).json()
    save(f"{label}-chat.json", chat_log)
    save(f"{label}-scenes.json", scenes)


async def phase_edit(client, headers) -> None:
    project_id = load_state()["video_project"]
    reply = await chat(
        client, headers, project_id, "0:05 to 0:15, use a simpler example with only three tokens"
    )
    print("edit chat:", reply["action"], reply["message"][:160])
    await wait_for(client, headers, project_id, "edit")


async def phase_localize(client, headers) -> None:
    project_id = load_state()["video_project"]
    reply = await chat(client, headers, project_id, "change the language to Hindi")
    print("localize chat:", reply["action"], reply["message"][:160])
    await wait_for(client, headers, project_id, "localize", limit_minutes=60)


async def main() -> None:
    phase = sys.argv[1] if len(sys.argv) > 1 else "default"
    key = {"X-API-Key": get_settings().app_api_key or ""}
    async with httpx.AsyncClient(base_url=API, timeout=120) as client:
        headers = await session(client, key)
        phases = {
            "intent": [phase_intent],
            "answer": [phase_answer],
            "video": [phase_video],
            "edit": [phase_edit],
            "localize": [phase_localize],
            "paper": [phase_paper],
            "lesson": [phase_lesson],
            "retry": [phase_retry],
            "scene_edit": [phase_scene_edit],
            "scene_localize": [phase_scene_localize],
            "default": [phase_intent, phase_answer, phase_video],
            "all": [phase_intent, phase_answer, phase_video, phase_edit, phase_localize],
        }[phase]
        for run in phases:
            print(f"== {run.__name__}")
            await run(client, headers)


if __name__ == "__main__":
    asyncio.run(main())
