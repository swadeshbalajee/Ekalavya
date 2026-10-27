"""Papers a learner attached to one chat, for whole-paper lectures."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from scholarmotion.persistence.models import SourceDocument


async def project_papers(session, project_id: UUID) -> list[SourceDocument]:
    """PDF papers attached to this project that parsed successfully, newest first."""
    rows = (
        await session.scalars(
            select(SourceDocument)
            .where(SourceDocument.project_id == project_id, SourceDocument.kind == "paper")
            .order_by(SourceDocument.created_at.desc())
        )
    ).all()
    return [
        row
        for row in rows
        if row.uri.lower().endswith(".pdf")
        and (row.metadata_json or {}).get("ingestion_status") != "failed"
    ]


def choose_paper(papers: list[SourceDocument], title: str = "") -> SourceDocument | None:
    """The paper the planner named by its exact title, else the most recently attached one."""
    return next((paper for paper in papers if title and paper.title == title), None) or (
        papers[0] if papers else None
    )


async def project_sources(session, project_id: UUID) -> list[SourceDocument]:
    """Every source given to this chat (papers and web pages) that was read successfully."""
    rows = (
        await session.scalars(
            select(SourceDocument)
            .where(SourceDocument.project_id == project_id)
            .order_by(SourceDocument.created_at)
        )
    ).all()
    return [row for row in rows if (row.metadata_json or {}).get("ingestion_status") == "complete"]
