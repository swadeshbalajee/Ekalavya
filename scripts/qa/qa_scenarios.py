"""Real-provider acceptance scenarios against the local running stack.

State with credentials stays under ignored .local/qa; results contain no credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx

from scholarmotion.config import get_settings

ROOT = Path(".local/qa")
STATE = ROOT / "scenario-session.json"
RESULTS = ROOT / "scenario-results.json"


def save(path, value):
    if path == RESULTS and path.exists():
        current = json.loads(path.read_text(encoding="utf-8"))
        current.update(value)
        value = current
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


async def main(stage, project_name=None, workflow_id=None):
    settings = get_settings()
    key = {"X-API-Key": settings.app_api_key or ""}
    results = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {}
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        timeout=180,
        headers=key,
        limits=httpx.Limits(keepalive_expiry=2),
    ) as c:

        async def request(method, path, **kwargs):
            r = await c.request(method, path, **kwargs)
            if r.is_error:
                raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:800]}")
            return r

        if not state:
            r = await request(
                "POST",
                "/v1/auth/register",
                json={
                    "username": "qa_accept_" + uuid4().hex[:8],
                    "password": uuid4().hex,
                    "display_name": "Acceptance QA",
                },
            )
            state = {"token": r.json()["token"], "projects": {}}
            save(STATE, state)
        c.headers["Authorization"] = "Bearer " + state["token"]

        async def wait_for(name, p, limit=1200, workflow_id=None):
            started = time.monotonic()
            last = None
            while time.monotonic() - started < limit:
                try:
                    r = (await request("GET", f"/v1/projects/{p}/progress")).json()
                except httpx.TransportError as error:
                    print(
                        f"{name}: poll transport error {type(error).__name__}; retrying GET",
                        flush=True,
                    )
                    await asyncio.sleep(2)
                    continue
                marker = (r["status"], r["scene_count"], len(r.get("events", [])))
                if marker != last:
                    print(f"{name}: {marker} ({time.monotonic() - started:.0f}s)", flush=True)
                    last = marker
                workflow_finished = True
                workflow_status_name = None
                if workflow_id:
                    from scholarmotion.workflows import get_workflow_status

                    workflow_status = await get_workflow_status(settings, workflow_id)
                    workflow_status_name = getattr(workflow_status, "name", None)
                    workflow_finished = workflow_status_name != "RUNNING"
                if r["status"] in {"COMPLETE", "FAILED", "CANCELLED"} and workflow_finished:
                    results[name] = {
                        "project_id": p,
                        "progress": r,
                        "workflow_status": workflow_status_name,
                        "project": (await request("GET", f"/v1/projects/{p}")).json(),
                        "scenes": (await request("GET", f"/v1/projects/{p}/scenes")).json(),
                        "seconds": round(time.monotonic() - started, 2),
                    }
                    save(RESULTS, results)
                    return workflow_status_name or r["status"]
                await asyncio.sleep(5)
            results[name] = {"project_id": p, "timeout": limit, "last_progress": r}
            save(RESULTS, results)
            print(f"{name}: acceptance wait timed out after {limit}s", flush=True)
            return "TIMEOUT"

        if stage == "wait":
            await wait_for(
                "wait_" + project_name, state["projects"][project_name], workflow_id=workflow_id
            )

        elif stage == "retest":
            name = "sonnet_retest"
            p = state["projects"].get(name)
            if not p:
                prompt = (
                    "Generate a 1-minute beginner video explaining why a compass turns beside "
                    "a current-carrying straight wire. Teach the right-hand rule, then calculate "
                    "B for I=2 A and r=1 cm. Use exactly two short scenes."
                )
                p = (
                    await request(
                        "POST",
                        "/v1/projects",
                        json={
                            "title": "QA one-minute Sonnet retest",
                            "request": prompt,
                            "target_duration_minutes": 3,
                            "mode": "video",
                        },
                    )
                ).json()["id"]
                state["projects"][name] = p
                save(STATE, state)
                r = await request("POST", f"/v1/projects/{p}/chat", json={"message": prompt})
                print("Retest queued: " + p + " action=" + r.json()["action"], flush=True)
            await wait_for(name, p)

        elif stage == "build":
            lessons = {
                "electromagnetism": "Generate a 1-minute beginner video explaining why a compass turns beside a current-carrying straight wire. Show the right-hand rule, and calculate B for I=2 A and r=1 cm.",
                "mechanics": "Generate a 1-minute beginner video explaining Newton second law. Use a 2 kg cart and a 6 N net force to calculate acceleration. Keep equations and diagram labels separated.",
            }
            for name, prompt in lessons.items():
                if name in state["projects"]:
                    continue
                p = (
                    await request(
                        "POST",
                        "/v1/projects",
                        json={
                            "title": "QA " + name,
                            "request": prompt,
                            "target_duration_minutes": 3,
                            "mode": "video",
                        },
                    )
                ).json()["id"]
                state["projects"][name] = p
                save(STATE, state)
                r = await request("POST", f"/v1/projects/{p}/generate")
                print(name + " queued: " + p, flush=True)
            await asyncio.gather(
                *(wait_for(name, p) for name, p in state["projects"].items() if name in lessons)
            )

        elif stage == "paper":
            p = state["projects"].get("paper")
            if not p:
                p = (
                    await request(
                        "POST",
                        "/v1/projects",
                        json={
                            "title": "QA Attention paper",
                            "request": "Generate a 1-minute video explaining scaled dot-product attention from the uploaded Attention Is All You Need paper. Preserve source page references and check numerical examples.",
                            "target_duration_minutes": 1,
                            "mode": "chat",
                        },
                    )
                ).json()["id"]
                state["projects"]["paper"] = p
                save(STATE, state)
                r = await request(
                    "POST",
                    f"/v1/projects/{p}/sources/link",
                    json={"url": "https://arxiv.org/pdf/1706.03762"},
                )
                results["paper_import"] = {"project_id": p, "source": r.json()}
                save(RESULTS, results)
                for message in (
                    "Text only: On which page is Figure 2 and what does it show?",
                    "How does the decoder prevent future-token information?",
                ):
                    r = await request("POST", f"/v1/projects/{p}/chat", json={"message": message})
                    results.setdefault("paper_chat", []).append(r.json())
                    save(RESULTS, results)
                await request("POST", f"/v1/projects/{p}/generate")
            await wait_for("paper", p)

        elif stage == "edit":
            p = (
                state["projects"][project_name]
                if project_name
                else (
                    state["projects"].get("sonnet_retest") or state["projects"]["electromagnetism"]
                )
            )
            base = f"/v1/projects/{p}"
            scenes = (await request("GET", base + "/scenes")).json()
            if not scenes:
                raise RuntimeError("No completed scene is available for edit acceptance")
            snapshots = {}
            for scene in scenes[1:]:
                folder = settings.object_storage_root / p / "scenes" / scene["scene_id"]
                snapshots.update(
                    {
                        str(f): hashlib.sha256(f.read_bytes()).hexdigest()
                        for f in folder.rglob("*")
                        if f.is_file()
                    }
                )
            r = await request(
                "POST",
                base + "/edit-range",
                json={
                    "start_time": 0,
                    "end_time": 5,
                    "instruction": "Make the right-hand rule explanation clearer. Keep the equation and numerical answer correct.",
                },
            )
            print("edit queued: " + r.json()["workflow_id"], flush=True)
            await wait_for("scene_edit", p, workflow_id=r.json()["workflow_id"])
            changed = [
                f
                for f, digest in snapshots.items()
                if not Path(f).exists()
                or hashlib.sha256(Path(f).read_bytes()).hexdigest() != digest
            ]
            results["untouched_scene_hashes"] = {"count": len(snapshots), "changed": changed}
            save(RESULTS, results)

        elif stage == "tamil":
            p = state["projects"][project_name or "paper"]
            project = (await request("GET", f"/v1/projects/{p}")).json()
            if project["status"] != "COMPLETE":
                results["tamil_one_scene"] = {
                    "project_id": p,
                    "blocked_by_status": project["status"],
                    "reason": "The prerequisite completed lesson/localization is unavailable.",
                }
                save(RESULTS, results)
                print("Tamil one-scene scenario blocked by " + project["status"], flush=True)
                return
            folder = settings.object_storage_root / p / "scenes" / "S02"
            before = {
                str(f): hashlib.sha256(f.read_bytes()).hexdigest()
                for f in folder.rglob("*")
                if f.is_file()
            }
            save(ROOT / "tamil-untouched-before.json", before)
            versioned = await c.post(
                f"/v1/projects/{p}/scenes/S01/localize",
                json={"text_language": "Tamil", "voice_language": "Tamil"},
            )
            results["versioned_scene_localization"] = {"status": versioned.status_code}
            save(RESULTS, results)
            r = await request(
                "POST",
                f"/projects/{p}/scenes/S01/localize",
                json={"text_language": "Tamil", "voice_language": "Tamil"},
            )
            outcome = await wait_for("tamil_one_scene", p, workflow_id=r.json()["workflow_id"])
            after = {
                str(f): hashlib.sha256(f.read_bytes()).hexdigest()
                for f in folder.rglob("*")
                if f.is_file()
            }
            changed = [f for f in set(before) | set(after) if before.get(f) != after.get(f)]
            save(
                ROOT / "tamil-selectivity.json",
                {
                    "workflow_outcome": outcome,
                    "files_checked": len(before),
                    "changed": sorted(changed),
                },
            )

        elif stage == "localize":
            p = (
                state["projects"][project_name]
                if project_name
                else (state["projects"].get("sonnet_retest") or state["projects"]["mechanics"])
            )
            r = await request(
                "PATCH",
                f"/v1/projects/{p}/settings",
                json={"text_language": "Hindi", "voice_language": "Hindi"},
            )
            print("localization workflow: " + str(r.json().get("workflow_id")), flush=True)
            await wait_for("hindi_localization", p, workflow_id=r.json().get("workflow_id"))

        elif stage == "cancel":
            p = (
                await request(
                    "POST",
                    "/v1/projects",
                    json={
                        "title": "QA cancellation",
                        "request": "Generate a 1-minute video about gravitational acceleration.",
                        "target_duration_minutes": 1,
                    },
                )
            ).json()["id"]
            await request("POST", f"/v1/projects/{p}/generate")
            duplicate = await c.post(f"/v1/projects/{p}/generate")
            results["duplicate_generation"] = {
                "status": duplicate.status_code,
                "passed": duplicate.status_code == 409,
            }
            r = await request("POST", f"/v1/projects/{p}/cancel")
            results["cancel_request"] = {"status": r.status_code, "response": r.json()}
            save(RESULTS, results)
            await wait_for("cancellation", p, 120)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "stage", choices=["build", "retest", "paper", "edit", "localize", "tamil", "cancel", "wait"]
    )
    parser.add_argument("--project-name")
    parser.add_argument("--workflow-id")
    args = parser.parse_args()
    asyncio.run(main(args.stage, args.project_name, args.workflow_id))
