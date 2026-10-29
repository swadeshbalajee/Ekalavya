"""Context-management check: does shrinking the context keep the core explanation?

Two things are checked, with whatever models the API is configured for (use
docker-compose.qa-free.yml for a $0 run):

1. Curation of a real paper, "Attention Is All You Need", read two ways: in one pass,
   and split into small groups that are extracted in parallel and merged. Each plan
   is scored on whether it still carries the paper's core ideas.
2. Chat compaction of a long chat whose standing preferences were stated early and
   then buried under filler turns. The summary is scored on what it kept.

Nothing is written to the database (compaction runs on an in-memory SQLite copy).
Results go to stdout and .local/qa/context/.

The paper must already be uploaded to some chat. Run inside the api container:
    docker compose cp scripts/qa/qa_context_management.py api:/tmp/qa_context.py
    docker compose exec api python /tmp/qa_context.py [--step all|curate|memory]
                                                      [--chat <chat id>]
See docs/context-management-test-2026-10-28.md for the first run's report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.agents.lesson_curator import LessonCurator
from scholarmotion.api.main import database, settings
from scholarmotion.observability.usage import track_usage
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.models import Project, SourceChunk, SourceDocument, TutorMessage
from scholarmotion.providers import create_llm_provider, create_planner_provider
from scholarmotion.services import chat_memory

ROOT = Path(".local/qa/context")
PAPER_TITLE = "%attention is all you need%"
REQUEST = "Explain the main idea of the paper 'Attention Is All You Need' in a 3 minute lesson."

# The paper's core, as patterns any faithful plan must contain.
CORE = {
    "Transformer, no recurrence/convolution": r"recurren|convolution",
    "self-attention": r"self-attention",
    "scaled dot-product attention": r"scaled dot.product",
    "softmax(QK^T / sqrt(d_k)) V": r"sqrt|√|d_k|d\_k",
    "query/key/value": r"quer(y|ies).*key|key.*value",
    "multi-head attention": r"multi-head",
    "positional encoding": r"positional",
    "encoder-decoder stacks": r"encoder.*decoder|decoder.*encoder",
    "BLEU result (28.4 / 41.8)": r"28\.4|41\.8|41\.0",
    "parallelism / training cost": r"parallel",
}

# One pass reads everything at once; 10,000-character groups force grouped reading.
BUDGETS = {"one_pass": 80_000, "grouped": 10_000}


def save(name: str, data) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / name).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def coverage(plan) -> dict[str, bool]:
    text = json.dumps(plan.model_dump(), ensure_ascii=False)
    return {
        name: bool(re.search(pattern, text, re.IGNORECASE | re.DOTALL))
        for name, pattern in CORE.items()
    }


def show_plan(title: str, plan) -> dict[str, bool]:
    print(f"\n=== {title} ===")
    if plan is None:
        print("NO PLAN")
        return {name: False for name in CORE}
    for index, item in enumerate(plan.items, 1):
        print(f"[{index}] {item.title} ({item.duration_seconds:.0f}s): {item.objective}")
        for point in item.key_points:
            print(f"     - {point[:220]}")
        for equation in item.equations:
            print(f"     eq: {equation}")
    print("references:", [ref.about[:90] for ref in plan.references])
    hits = coverage(plan)
    print(f"CORE KEPT {sum(hits.values())}/{len(hits)}:", hits)
    return hits


async def paper_chunks(chat: UUID | None) -> list[dict]:
    query = (
        select(SourceChunk, SourceDocument)
        .join(SourceDocument, SourceChunk.document_id == SourceDocument.id)
        .order_by(SourceChunk.page, SourceChunk.created_at)
    )
    if chat:
        query = query.where(SourceDocument.project_id == chat)
    else:
        async with database.sessions() as session:
            document = (
                await session.execute(
                    select(SourceDocument)
                    .where(SourceDocument.title.ilike(PAPER_TITLE))
                    .order_by(SourceDocument.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if document is None:
            sys.exit("Upload 'Attention Is All You Need' to a chat first, or pass --chat.")
        query = query.where(SourceDocument.id == document.id)
    async with database.sessions() as session:
        rows = (await session.execute(query)).all()
    return [
        {"id": str(c.id), "document_title": d.title, "section": c.section, "text": c.text}
        for c, d in rows
    ]


async def compaction_test(planner) -> dict[str, bool]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    turns = [
        (
            "user",
            (
                "Please always narrate as my teacher in first person, never third person. "
                "Also I want Tamil subtitles but English narration from now on."
            ),
        ),
        ("assistant", "Understood: first-person teacher narration, Tamil text, English voice."),
        ("user", "Make a video on Newton's second law, F equals m a, with a car example."),
        (
            "assistant",
            (
                "Video 1 ready: Newton's second law with a 1200 kg car accelerating at "
                "2 metres per second squared, so the net force is 2400 newtons."
            ),
        ),
        (
            "user",
            "I am confused why mass and acceleration are inversely related for a fixed force.",
        ),
        (
            "assistant",
            (
                "For a fixed force, doubling the mass halves the acceleration, since "
                "a equals F over m. A heavier car speeds up more slowly with the same push."
            ),
        ),
        ("user", "Only use my uploaded NCERT chapter, no web sources please."),
        ("assistant", "Noted: only your NCERT chapter from now on."),
    ] + [
        (role, f"{role} filler turn {index}: " + "we talked about units and examples. " * 12)
        for index in range(10)
        for role in ("user", "assistant")
    ]
    async with sessions() as session:
        project = Project(title="Newton", request="Newton's second law")
        session.add(project)
        await session.flush()
        start = datetime(2026, 10, 1, tzinfo=UTC)
        messages = [
            TutorMessage(
                project_id=project.id,
                role=role,
                canonical_text=text,
                display_text=text,
                created_at=start + timedelta(minutes=index),
            )
            for index, (role, text) in enumerate(turns)
        ]
        session.add_all(messages)
        await session.flush()
        changed = await chat_memory.compact(session, planner, project, messages, 7)
        summary = project.chat_summary or ""
    await engine.dispose()
    print("\n=== chat compaction ===")
    print("compacted:", changed)
    print(summary)
    lowered = summary.lower()
    checks = {
        "first-person teacher voice": "first" in lowered and "person" in lowered,
        "Tamil text + English voice": "tamil" in lowered and "english" in lowered,
        "only NCERT, no web": "ncert" in lowered,
        "video 1 and its example (1200 kg / 2400 N)": "2400" in lowered or "1200" in lowered,
        "the confusion (mass vs acceleration)": "mass" in lowered and "acceleration" in lowered,
    }
    print(f"KEPT {sum(checks.values())}/{len(checks)}:", checks)
    save("compaction.json", {"compacted": changed, "summary": summary, "checks": checks})
    return checks


async def main(step: str, chat: UUID | None) -> int:
    main_llm, planner = create_llm_provider(settings), create_planner_provider(settings)
    print("models:", settings.main_llm_model, "|", settings.planner_llm_model)
    results: dict[str, dict[str, bool]] = {}
    with track_usage() as ledger:
        jobs = BUDGETS if step in {"all", "curate"} else {}
        chunks = await paper_chunks(chat) if jobs else []
        if jobs:
            print("paper:", len(chunks), "chunks,", sum(len(c["text"]) for c in chunks), "chars")
        curator = LessonCurator(main_llm, helper=planner)
        plans = await asyncio.gather(
            *(
                curator.curate(
                    request=REQUEST,
                    chunks=chunks,
                    minutes=3,
                    level="general",
                    source_scope="only_given",
                    one_pass_chars=budget,
                )
                for budget in jobs.values()
            ),
            compaction_test(planner) if step in {"all", "memory"} else asyncio.sleep(0),
        )
        for (name, budget), plan in zip(jobs.items(), plans, strict=False):
            results[name] = show_plan(f"{name} ({budget:,}-character limit)", plan)
            if plan is not None:
                save(f"plan_{name}.json", plan.model_dump())
        if step in {"all", "memory"}:
            results["compaction"] = plans[-1]
        cost = sum(entry.cost_usd or 0 for entry in ledger.entries)
        print("\ncalls:", len(ledger.entries), "cost: $", round(cost, 4))
    save("scores.json", {"results": results, "calls": len(ledger.entries), "cost_usd": cost})
    passed = all(all(hits.values()) for hits in results.values())
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--step", choices=["all", "curate", "memory"], default="all")
    parser.add_argument("--chat", type=UUID, help="chat that holds the paper (default: search)")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.step, args.chat)))
