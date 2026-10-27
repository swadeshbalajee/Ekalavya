"""Rebuilds Qdrant points from PostgreSQL, the source of truth."""

from __future__ import annotations

from sqlalchemy import select

from scholarmotion.config import Settings
from scholarmotion.persistence.models import (
    CorrectionEntry,
    Skill,
    SkillVersion,
    SourceChunk,
    SourceDocument,
)
from scholarmotion.persistence.qdrant import QdrantConfig, QdrantVectorStore
from scholarmotion.providers import create_embedding_provider


async def reconcile_qdrant_index(session, settings: Settings, vector_store=None) -> dict[str, int]:
    """Rebuild Qdrant from PostgreSQL, then prune rows absent from the source of truth."""
    vector_store = vector_store or QdrantVectorStore(QdrantConfig.from_settings(settings))
    embedder = create_embedding_provider(settings)

    source_rows = (
        await session.execute(
            select(SourceChunk, SourceDocument)
            .join(SourceDocument, SourceChunk.document_id == SourceDocument.id)
            .order_by(SourceChunk.id)
        )
    ).all()
    source_texts = [chunk.text for chunk, _document in source_rows]
    source_vectors = await embedder.embed(source_texts) if source_texts else []
    source_points = [
        {
            "id": str(chunk.id),
            "vector": vector,
            "payload": {
                "document_id": str(document.id),
                "project_id": str(document.project_id) if document.project_id else None,
                "class_level": chunk.class_level,
                "subject": chunk.subject.lower() if chunk.subject else None,
                "chapter": chunk.chapter,
                "content_type": chunk.content_type,
            },
        }
        for (chunk, document), vector in zip(source_rows, source_vectors, strict=True)
    ]

    active_skills = (
        await session.execute(
            select(Skill, SkillVersion)
            .join(SkillVersion, SkillVersion.skill_id == Skill.id)
            .where(
                Skill.status == "active",
                SkillVersion.version == Skill.active_version,
                SkillVersion.status == "active",
            )
            .order_by(Skill.skill_key)
        )
    ).all()
    corrections = (
        await session.scalars(
            select(CorrectionEntry)
            .where(CorrectionEntry.status == "active")
            .order_by(CorrectionEntry.correction_key)
        )
    ).all()
    skill_texts = [
        f"{skill.skill_key}: {version.description}\n{version.instructions}"
        for skill, version in active_skills
    ]
    correction_texts = [
        f"{item.correction_key} [{item.category}] {item.trigger_conditions}\n"
        f"{item.required_behavior}\n{item.recommended_fix}"
        for item in corrections
    ]
    skill_vectors = (
        await embedder.embed([*skill_texts, *correction_texts])
        if skill_texts or correction_texts
        else []
    )
    skill_points = []
    for (skill, version), vector, content in zip(
        active_skills, skill_vectors[: len(active_skills)], skill_texts, strict=True
    ):
        skill_points.append(
            {
                "id": str(version.id),
                "vector": vector,
                "payload": {"kind": "skill", "name": skill.skill_key, "text": content},
            }
        )
    correction_vectors = skill_vectors[len(active_skills) :]
    for item, vector, content in zip(
        corrections, correction_vectors, correction_texts, strict=True
    ):
        skill_points.append(
            {
                "id": str(item.id),
                "vector": vector,
                "payload": {"kind": "correction", "name": item.correction_key, "text": content},
            }
        )

    await vector_store.reconcile_collection(vector_store.config.source_collection, source_points)
    await vector_store.reconcile_collection(vector_store.config.skill_collection, skill_points)
    for chunk, _document in source_rows:
        # Reconciliation is idempotent; a repair run must not manufacture a
        # new content revision for an unchanged source chunk.
        chunk.vector_revision = max(1, chunk.vector_revision)
    return {"source_chunks": len(source_points), "skills_and_corrections": len(skill_points)}
