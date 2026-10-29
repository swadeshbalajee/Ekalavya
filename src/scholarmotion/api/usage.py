"""Usage dashboard API: spend, tokens, time and retries per chat, plus live runs.

Costs and tokens come from ``usage_events`` (one row per model or service
call). Time, retries and the live pipeline view come from the progress events
the pipeline already emits. Every user sees their own chats; usernames listed
in ``ADMIN_USERNAMES`` may also request ``scope=all``.
"""

import itertools
from collections import Counter, defaultdict
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scholarmotion.config import Settings
from scholarmotion.persistence.models import ProgressEvent, Project, UsageEvent, UserAccount
from scholarmotion.services.usage_records import module_for

ACTIVE_STATUSES = {"GENERATING", "EDITING", "LOCALIZING"}
TERMINAL_EVENTS = {
    "video.complete",
    "project.failed",
    "localization.complete",
    "localization.failed",
}
# A pause longer than this between progress events starts a new run.
RUN_GAP = timedelta(minutes=20)
RETRY_EVENTS = {"scene.rejected", "scene.timing_repaired", "scene.review_repair"}

# Lesson-level pipeline stages, in order, with the events that start and finish them.
PIPELINE = (
    ("prepare", "Prepare", {"project.started", "preparation.started"}, {"preparation.complete"}),
    ("sources", "Sources", {"retrieval.web_started"}, {"retrieval.complete"}),
    ("plan", "Lesson plan", {"curation.started"}, {"curriculum.complete"}),
    ("script", "Script + review", {"script.review"}, {"script.complete"}),
    ("storyboard", "Storyboard", set(), {"storyboard.complete", "scenes.created"}),
    ("narration", "Narration", set(), set()),
    ("scenes", "Scenes", {"scene.started"}, set()),
    ("review", "Lesson review", {"scene.review_repair"}, {"lesson.reviewed"}),
    ("assembly", "Assembly", {"video.assembling"}, {"video.complete"}),
)
SCENE_STEPS = (
    ("audio", "Voice", {"scene.started", "scene.audio_started"}),
    ("code", "Code", {"scene.code_started"}),
    ("render", "Render", {"scene.render_started", "scene.repairing", "scene.timing_repaired"}),
    ("verify", "Verify", {"scene.verifying", "scene.rejected"}),
    ("done", "Done", {"scene.complete", "scene.degraded"}),
)
SCENE_DONE = {"scene.complete", "scene.degraded"}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _round(value: float | None, digits: int = 4) -> float:
    return round(value or 0.0, digits)


def _runs(events: list[ProgressEvent]) -> list[list[ProgressEvent]]:
    """Group a chat's progress events into runs (builds, edits, localizations)."""
    runs: list[list[ProgressEvent]] = []
    for event in sorted(events, key=lambda item: _aware(item.created_at)):
        if event.event == "usage.summary":
            continue
        current = runs[-1] if runs else None
        if (
            current is None
            or current[-1].event in TERMINAL_EVENTS
            or _aware(event.created_at) - _aware(current[-1].created_at) > RUN_GAP
        ):
            runs.append([event])
        else:
            current.append(event)
    return runs


def _run_seconds(run: list[ProgressEvent], active: bool) -> float:
    end = datetime.now(UTC) if active else _aware(run[-1].created_at)
    return max(0.0, (end - _aware(run[0].created_at)).total_seconds())


def _retries(events: list[ProgressEvent]) -> int:
    count = sum(1 for event in events if event.event in RETRY_EVENTS)
    for event in events:
        if event.event == "translation.checked":
            count += len((event.detail or {}).get("findings") or [])
    return count


def _cost_totals(rows: list[UsageEvent]) -> dict[str, Any]:
    tokens = sum(row.input_tokens + row.output_tokens for row in rows)
    latencies = [row.latency_ms for row in rows if row.latency_ms is not None]
    return {
        "cost_usd": _round(sum(row.cost_usd or 0 for row in rows)),
        "calls": sum(row.calls for row in rows),
        "tokens": tokens,
        "input_tokens": sum(row.input_tokens for row in rows),
        "output_tokens": sum(row.output_tokens for row in rows),
        "characters": sum(row.characters for row in rows),
        "failed_calls": sum(row.calls for row in rows if row.status == "error"),
        "fallback_calls": sum(row.calls for row in rows if row.status == "fallback"),
        "avg_latency_ms": _round(sum(latencies) / len(latencies), 1) if latencies else None,
        "unpriced_calls": sum(row.calls for row in rows if row.cost_usd is None),
    }


def _modules(rows: list[UsageEvent]) -> list[dict[str, Any]]:
    groups: dict[str, list[UsageEvent]] = defaultdict(list)
    for row in rows:
        groups[module_for(row.purpose, row.stage)].append(row)
    return sorted(
        ({"module": name, **_cost_totals(items)} for name, items in groups.items()),
        key=lambda item: -item["cost_usd"],
    )


def _scene_steps(events: list[ProgressEvent], key: str) -> dict[str, Any]:
    """Where one scene is: each step done, running or queued, and its attempt."""
    own = sorted(
        (event for event in events if (event.detail or {}).get("scene_key") == key),
        key=lambda event: _aware(event.created_at),
    )
    names = [event.event for event in own]
    # The latest event says where the scene is now: a repair sends it back to rendering.
    reached = -1
    if names:
        reached = next(
            (index for index, (_, _, triggers) in enumerate(SCENE_STEPS) if names[-1] in triggers),
            -1,
        )
    finished = any(name in SCENE_DONE for name in names)
    blocked = "scene.blocked" in names
    steps = []
    for index, (step, label, _) in enumerate(SCENE_STEPS):
        if finished or index < reached:
            state = "done"
        elif blocked and index == max(reached, 0):
            state = "failed"
        elif index == reached:
            state = "running"
        else:
            state = "queued"
        steps.append({"key": step, "label": label, "state": state})
    degraded = "scene.degraded" in names
    return {
        "scene": key,
        "attempt": names.count("scene.rejected") + 1,
        "state": "failed"
        if blocked
        else ("degraded" if degraded else "done" if finished else "running" if own else "queued"),
        "steps": steps,
    }


def pipeline_state(run: list[ProgressEvent], *, active: bool, failed: bool) -> dict[str, Any]:
    """The run as a graph: lesson stages in order, then one lane per scene."""
    names = [event.event for event in run]
    seen = set(names)
    times: dict[str, datetime] = {}
    for event in run:
        times.setdefault(event.event, _aware(event.created_at))
    scene_list: list[str] = []
    for event in run:
        if event.event == "scenes.created":
            scene_list = [
                str(item).split(":", 1)[0] for item in (event.detail or {}).get("scenes") or []
            ]
    for event in run:
        key = (event.detail or {}).get("scene_key")
        if key and event.event.startswith("scene.") and key not in scene_list:
            scene_list.append(key)
    scenes = [_scene_steps(run, key) for key in scene_list]
    scenes_done = bool(scenes) and all(item["state"] in {"done", "degraded"} for item in scenes)

    def finished(key: str, ends: set[str], index: int) -> bool:
        if ends & seen:
            return True
        if key == "narration":
            return "scene.started" in seen
        if key == "scenes":
            return scenes_done
        # A stage is complete once any later stage has started or finished.
        return any(
            (starts | later_ends) & seen for _, _, starts, later_ends in PIPELINE[index + 1 :]
        )

    stages: list[dict[str, Any]] = []
    running_marked = False
    for index, (key, label, starts, ends) in enumerate(PIPELINE):
        if finished(key, ends, index):
            state = "done"
        elif not running_marked and (active or failed):
            state = "failed" if failed and not active else "running"
            running_marked = True
        else:
            state = "queued" if active else "skipped"
        started = min((times[name] for name in starts if name in times), default=None)
        stages.append(
            {
                "key": key,
                "label": label,
                "state": state,
                "started_at": started.isoformat() if started else None,
            }
        )
    if names and names[-1] == "video.complete":
        for stage in stages:
            if stage["state"] in {"queued", "running"}:
                stage["state"] = "skipped"
    return {"stages": stages, "scenes": scenes, "next": _next_step(stages, scenes)}


def _next_step(stages: list[dict[str, Any]], scenes: list[dict[str, Any]]) -> str | None:
    running = next((stage for stage in stages if stage["state"] == "running"), None)
    if running is None:
        return None
    if running["key"] == "scenes" and scenes:
        busy = [item for item in scenes if item["state"] == "running"]
        waiting = [item for item in scenes if item["state"] == "queued"]
        parts = []
        if busy:
            current = [
                f"{item['scene']} {next(s['label'].lower() for s in item['steps'] if s['state'] == 'running')}"
                + (f" (attempt {item['attempt']})" if item["attempt"] > 1 else "")
                for item in busy
                if any(s["state"] == "running" for s in item["steps"])
            ]
            parts.append("Working on " + ", ".join(current))
        if waiting:
            parts.append(f"then {len(waiting)} more scene(s)")
        parts.append("then lesson review and assembly")
        return "; ".join(parts) + "."
    queued = [stage["label"] for stage in stages if stage["state"] == "queued"]
    return f"Now: {running['label']}." + (f" Next: {', '.join(queued[:3])}." if queued else "")


def create_usage_router(
    settings: Settings,
    session_dependency: Callable,
    current_user_id: ContextVar[UUID | None],
) -> APIRouter:
    router = APIRouter(prefix="/v1/usage", tags=["usage"])
    Session = Annotated[AsyncSession, Depends(session_dependency)]
    admins = {
        name.strip().casefold() for name in settings.admin_usernames.split(",") if name.strip()
    }

    async def viewer(session: AsyncSession) -> UserAccount:
        user_id = current_user_id.get()
        user = await session.get(UserAccount, user_id) if user_id else None
        if user is None:
            raise HTTPException(401, "authentication required")
        return user

    def is_admin(user: UserAccount) -> bool:
        return user.username.casefold() in admins

    async def scoped_projects(
        session: AsyncSession, user: UserAccount, scope: str
    ) -> list[Project]:
        if scope == "all" and not is_admin(user):
            raise HTTPException(403, "only administrators can view all users")
        statement = select(Project)
        if scope != "all":
            statement = statement.where(Project.owner_id == user.id)
        return list((await session.scalars(statement)).all())

    async def progress_for(session, project_ids, since=None) -> dict[UUID, list[ProgressEvent]]:
        if not project_ids:
            return {}
        statement = select(ProgressEvent).where(ProgressEvent.project_id.in_(project_ids))
        if since is not None:
            statement = statement.where(ProgressEvent.created_at >= since)
        grouped: dict[UUID, list[ProgressEvent]] = defaultdict(list)
        for event in (await session.scalars(statement)).all():
            grouped[event.project_id].append(event)
        return grouped

    @router.get("/summary")
    async def summary(
        session: Session,
        days: int = Query(default=30, ge=1, le=3650),
        scope: str = Query(default="me", pattern="^(me|all)$"),
    ):
        user = await viewer(session)
        projects = await scoped_projects(session, user, scope)
        since = datetime.now(UTC) - timedelta(days=days)
        statement = select(UsageEvent).where(UsageEvent.created_at >= since)
        if scope != "all":
            statement = statement.where(UsageEvent.user_id == user.id)
        rows = list((await session.scalars(statement)).all())
        events = await progress_for(session, [project.id for project in projects], since)
        owners = {}
        if scope == "all":
            owners = {
                account.id: account
                for account in (await session.scalars(select(UserAccount))).all()
            }
        by_project: dict[UUID | None, list[UsageEvent]] = defaultdict(list)
        for row in rows:
            by_project[row.project_id].append(row)
        chats = []
        all_runs: list[float] = []
        videos = retries = failed_runs = 0
        for project in projects:
            project_events = events.get(project.id, [])
            project_rows = by_project.get(project.id, [])
            if not project_events and not project_rows:
                continue
            runs = _runs(project_events)
            active = project.status in ACTIVE_STATUSES
            seconds = [
                _run_seconds(run, active and index == len(runs) - 1)
                for index, run in enumerate(runs)
            ]
            built = sum(1 for event in project_events if event.event == "video.complete")
            failed = sum(1 for event in project_events if event.event == "project.failed")
            chat_retries = _retries(project_events) + sum(
                row.calls for row in project_rows if row.status == "error"
            )
            videos += built
            failed_runs += failed
            retries += chat_retries
            all_runs += [value for value in seconds if value > 0]
            last = max(
                [_aware(event.created_at) for event in project_events]
                + [_aware(row.created_at) for row in project_rows],
            )
            owner = owners.get(project.owner_id)
            chats.append(
                {
                    "project_id": str(project.id),
                    "title": project.title,
                    "status": project.status,
                    "owner": (owner.display_name or owner.username) if owner else None,
                    **_cost_totals(project_rows),
                    "videos": built,
                    "runs": len(runs),
                    "time_seconds": round(sum(seconds), 1),
                    "retries": chat_retries,
                    "last_activity": last.isoformat(),
                }
            )
        daily: dict[str, dict[str, float]] = defaultdict(lambda: {"cost_usd": 0.0, "calls": 0})
        for row in rows:
            day = _aware(row.created_at).date().isoformat()
            daily[day]["cost_usd"] += row.cost_usd or 0
            daily[day]["calls"] += row.calls
        totals = {
            **_cost_totals(rows),
            "videos": videos,
            "chats": len(chats),
            "build_seconds_total": round(sum(all_runs), 1),
            "build_seconds_avg": round(sum(all_runs) / len(all_runs), 1) if all_runs else None,
            "retries": retries,
            "success_rate": round(videos / (videos + failed_runs), 3)
            if videos + failed_runs
            else None,
            "temporary_chat_cost_usd": _round(
                sum(row.cost_usd or 0 for row in by_project.get(None, []))
            ),
        }
        users = []
        if scope == "all":
            per_user: dict[UUID | None, list[UsageEvent]] = defaultdict(list)
            for row in rows:
                per_user[row.user_id].append(row)
            for user_id, user_rows in per_user.items():
                account = owners.get(user_id)
                users.append(
                    {
                        "user": (account.display_name or account.username) if account else "—",
                        "username": account.username if account else None,
                        **_cost_totals(user_rows),
                        "chats": len({row.project_id for row in user_rows if row.project_id}),
                    }
                )
            users.sort(key=lambda item: -item["cost_usd"])
        return {
            "scope": scope,
            "is_admin": is_admin(user),
            "days": days,
            "totals": totals,
            "daily": [{"date": day, **values} for day, values in sorted(daily.items())],
            "modules": _modules(rows),
            "chats": sorted(chats, key=lambda item: item["last_activity"], reverse=True),
            "users": users,
        }

    @router.get("/chats/{project_id}")
    async def chat_detail(project_id: UUID, session: Session):
        user = await viewer(session)
        project = await session.get(Project, project_id)
        if project is None or (project.owner_id != user.id and not is_admin(user)):
            raise HTTPException(404, "chat not found")
        rows = list(
            (
                await session.scalars(
                    select(UsageEvent)
                    .where(UsageEvent.project_id == project_id)
                    .order_by(UsageEvent.created_at)
                )
            ).all()
        )
        events = (await progress_for(session, [project_id])).get(project_id, [])
        runs = _runs(events)
        active = project.status in ACTIVE_STATUSES
        scene_rows: dict[str, list[UsageEvent]] = defaultdict(list)
        for row in rows:
            if row.scene_key:
                scene_rows[row.scene_key].append(row)
        scene_retries = Counter(
            (event.detail or {}).get("scene_key") for event in events if event.event in RETRY_EVENTS
        )
        scenes = [
            {
                "scene": key,
                **_cost_totals(items),
                "retries": scene_retries.get(key, 0)
                + sum(row.calls for row in items if row.status == "error"),
                "modules": _modules(items),
            }
            for key, items in sorted(scene_rows.items())
        ]
        run_list = []
        for index, run in enumerate(runs):
            is_active = active and index == len(runs) - 1
            stage_times = _stage_timeline(run, is_active)
            run_list.append(
                {
                    "index": index + 1,
                    "started_at": _aware(run[0].created_at).isoformat(),
                    "seconds": round(_run_seconds(run, is_active), 1),
                    "outcome": "running"
                    if is_active
                    else (
                        "complete"
                        if run[-1].event in {"video.complete", "localization.complete"}
                        else "failed"
                        if run[-1].event in {"project.failed", "localization.failed"}
                        else "stopped"
                    ),
                    "retries": _retries(run),
                    "timeline": stage_times,
                }
            )
        return {
            "project": {"id": str(project.id), "title": project.title, "status": project.status},
            "totals": {**_cost_totals(rows), "retries": _retries(events)},
            "modules": _modules(rows),
            "scenes": scenes,
            "runs": run_list,
            "calls": [
                {
                    "time": _aware(row.created_at).isoformat(),
                    "module": module_for(row.purpose, row.stage),
                    "model": row.model,
                    "provider": row.provider,
                    "scene": row.scene_key,
                    "operation": row.operation,
                    "tokens": row.input_tokens + row.output_tokens,
                    "characters": row.characters,
                    "cost_usd": row.cost_usd,
                    "latency_ms": row.latency_ms,
                    "status": row.status,
                    "calls": row.calls,
                }
                for row in rows[-300:]
            ],
        }

    @router.get("/live")
    async def live(session: Session, scope: str = Query(default="me", pattern="^(me|all)$")):
        user = await viewer(session)
        projects = [
            project
            for project in await scoped_projects(session, user, scope)
            if project.status in ACTIVE_STATUSES
        ]
        events = await progress_for(
            session, [project.id for project in projects], datetime.now(UTC) - timedelta(days=2)
        )
        owners = {}
        if scope == "all" and projects:
            owners = {
                account.id: account
                for account in (await session.scalars(select(UserAccount))).all()
            }
        live_runs = []
        for project in projects:
            runs = _runs(events.get(project.id, []))
            run = runs[-1] if runs else []
            owner = owners.get(project.owner_id)
            live_runs.append(
                {
                    "project_id": str(project.id),
                    "title": project.title,
                    "status": project.status,
                    "owner": (owner.display_name or owner.username) if owner else None,
                    "started_at": _aware(run[0].created_at).isoformat() if run else None,
                    "elapsed_seconds": round(_run_seconds(run, True), 1) if run else 0,
                    "last_event": run[-1].event if run else None,
                    "pipeline": pipeline_state(run, active=True, failed=False),
                }
            )
        return {"runs": live_runs}

    return router


def _stage_timeline(run: list[ProgressEvent], active: bool) -> list[dict[str, Any]]:
    """Start and end of each lesson stage in one run, for a Gantt-style chart."""
    marks: list[tuple[str, datetime]] = []
    for key, _, starts, ends in PIPELINE:
        for event in run:
            if event.event in starts | ends or (
                key == "scenes" and event.event.startswith("scene.")
            ):
                marks.append((key, _aware(event.created_at)))
    spans: dict[str, list[datetime]] = defaultdict(list)
    for key, moment in marks:
        spans[key].append(moment)
    order = [key for key, *_ in PIPELINE]
    timeline = []
    start = _aware(run[0].created_at)
    for key in order:
        if key not in spans:
            continue
        begin, end = min(spans[key]), max(spans[key])
        timeline.append(
            {
                "stage": {k: label for k, label, *_ in PIPELINE}[key],
                "start_seconds": round((begin - start).total_seconds(), 1),
                "end_seconds": round((end - start).total_seconds(), 1),
            }
        )
    # Each stage runs until the next one starts.
    for current, following in itertools.pairwise(timeline):
        current["end_seconds"] = max(current["end_seconds"], following["start_seconds"])
    if timeline:
        tail = (
            (datetime.now(UTC) if active else _aware(run[-1].created_at)) - start
        ).total_seconds()
        timeline[-1]["end_seconds"] = round(max(timeline[-1]["end_seconds"], tail), 1)
    return timeline
