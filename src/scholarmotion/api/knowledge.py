"""Knowledge map API: each learner's concept graph, built from their chats.

PostgreSQL holds the graph (concepts, relations, the chats they came from);
each concept's vector is kept with it and indexed in Qdrant for semantic
search. A query finds the closest concepts by meaning, widens to their
neighbours, and the model answers from those facts only.
"""

import math
from collections import Counter
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from scholarmotion.agents.concept_graph import (
    GraphUnavailable,
    answer_from_graph,
    concept_key,
    extract_concepts,
)
from scholarmotion.api.learning import parse_vtt
from scholarmotion.api.service import openrouter_prices
from scholarmotion.config import Settings
from scholarmotion.observability import get_logger
from scholarmotion.observability.usage import track_usage
from scholarmotion.persistence.models import (
    KnowledgeEdge,
    KnowledgeMention,
    KnowledgeNode,
    Project,
    TutorMessage,
    UserAccount,
    update_quietly,
)
from scholarmotion.persistence.qdrant import QdrantConfig, QdrantUnavailable, QdrantVectorStore
from scholarmotion.providers import create_llm_provider
from scholarmotion.services.embeddings import embed_texts
from scholarmotion.services.usage_records import new_run_id, usage_rows

logger = get_logger(__name__)
KNOWLEDGE_PREFIX = "/v1/knowledge"


class QueryRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)


def _cosine(left: list[float] | None, right: list[float] | None) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def create_knowledge_router(
    settings: Settings,
    session_dependency: Callable,
    current_user_id: ContextVar[UUID | None],
    vector_store: QdrantVectorStore | None = None,
) -> APIRouter:
    router = APIRouter(prefix=KNOWLEDGE_PREFIX, tags=["knowledge"])
    Session = Annotated[AsyncSession, Depends(session_dependency)]
    store = vector_store or QdrantVectorStore(QdrantConfig.from_settings(settings))

    def require_user() -> UUID:
        user_id = current_user_id.get()
        if user_id is None:
            raise HTTPException(401, "authentication required")
        return user_id

    async def save_usage(session: AsyncSession, ledger, project_id: UUID | None) -> None:
        if not ledger.entries:
            return
        try:
            if any(entry.cost_usd is None for entry in ledger.entries):
                ledger.estimate_missing_costs(await openrouter_prices(settings))
            session.add_all(
                usage_rows(
                    ledger,
                    user_id=current_user_id.get(),
                    project_id=project_id,
                    operation="knowledge",
                    run_id=new_run_id(),
                )
            )
        except Exception as error:  # noqa: BLE001 - accounting must never fail a request
            logger.warning("Could not record knowledge usage: %s", error)

    async def last_activity(session: AsyncSession, user_id: UUID) -> dict[UUID, datetime]:
        rows = await session.execute(
            select(TutorMessage.project_id, func.max(TutorMessage.created_at))
            .join(Project, Project.id == TutorMessage.project_id)
            .where(Project.owner_id == user_id)
            .group_by(TutorMessage.project_id)
        )
        return {project_id: _aware(at) for project_id, at in rows.all()}

    async def pending_chats(session: AsyncSession, user_id: UUID) -> list[Project]:
        """Chats with messages the graph has not read yet, most recent first."""
        activity = await last_activity(session, user_id)
        projects = (
            await session.scalars(select(Project).where(Project.id.in_(list(activity))))
        ).all()
        waiting = []
        for project in projects:
            # New messages (a finished video adds one too) are what change a chat's ideas.
            changed = activity.get(project.id)
            synced = _aware(project.graph_synced_at)
            if synced is None or (changed and changed > synced):
                waiting.append(project)
        return sorted(
            waiting,
            key=lambda item: activity.get(item.id) or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )

    async def chat_text(session: AsyncSession, project: Project) -> tuple[str, str]:
        messages = (
            await session.scalars(
                select(TutorMessage)
                .where(TutorMessage.project_id == project.id)
                .order_by(TutorMessage.created_at)
            )
        ).all()
        conversation = "\n".join(
            f"{message.role}: {message.canonical_text}"
            for message in messages
            if not (message.metadata_json or {}).get("superseded")
        )
        lesson = ""
        if project.active_video_path:
            subtitles = Path(project.active_video_path).with_name("subtitles.vtt")
            if subtitles.is_file():
                lesson = " ".join(
                    cue["text"]
                    for cue in parse_vtt(subtitles.read_text(encoding="utf-8", errors="replace"))
                )
        return conversation, lesson

    async def index_nodes(nodes: list[KnowledgeNode]) -> None:
        vectors = await embed_texts(
            settings, [f"{node.name}: {node.description or ''}" for node in nodes]
        )
        if not vectors:
            return
        for node, vector in zip(nodes, vectors, strict=True):
            node.embedding = vector
        try:
            await store.upsert_knowledge_nodes(
                [
                    {
                        "id": str(node.id),
                        "vector": node.embedding,
                        "payload": {"owner_id": str(node.owner_id), "name": node.name},
                    }
                    for node in nodes
                ]
            )
        except QdrantUnavailable as error:
            # PostgreSQL keeps the vectors; search falls back to them.
            logger.warning("Knowledge vectors not indexed in Qdrant: %s", error)

    async def read_chat(session: AsyncSession, user_id: UUID, project: Project) -> None:
        conversation, lesson = await chat_text(session, project)
        existing = {
            node.key: node
            for node in (
                await session.scalars(
                    select(KnowledgeNode).where(KnowledgeNode.owner_id == user_id)
                )
            ).all()
        }
        with track_usage() as ledger:
            graph = await extract_concepts(
                create_llm_provider(settings),
                title=project.title,
                conversation=conversation,
                lesson=lesson,
                known=[node.name for node in existing.values()],
            )
        await save_usage(session, ledger, project.id)
        # This chat's earlier reading is replaced, not added to.
        await session.execute(delete(KnowledgeEdge).where(KnowledgeEdge.project_id == project.id))
        await session.execute(
            delete(KnowledgeMention).where(KnowledgeMention.project_id == project.id)
        )
        fresh: list[KnowledgeNode] = []
        here: dict[str, KnowledgeNode] = {}
        for concept in graph.concepts:
            key = concept_key(concept.name)
            node = existing.get(key) or here.get(key)
            if node is None:
                node = KnowledgeNode(owner_id=user_id, key=key, name=concept.name.strip()[:200])
                session.add(node)
                existing[key] = node
                fresh.append(node)
            if concept.description.strip() and not node.description:
                node.description = concept.description.strip()
                if node not in fresh:
                    fresh.append(node)
            if concept.kind.strip() and not node.kind:
                node.kind = concept.kind.strip().lower()[:60]
            here[key] = node
        await session.flush()
        for node in here.values():
            session.add(KnowledgeMention(node_id=node.id, project_id=project.id))
        seen = set()
        for relation in graph.relations:
            source = here[concept_key(relation.source)]
            target = here[concept_key(relation.target)]
            label = relation.relation.strip()[:80]
            if (source.id, target.id, label.casefold()) in seen:
                continue
            seen.add((source.id, target.id, label.casefold()))
            session.add(
                KnowledgeEdge(
                    owner_id=user_id,
                    project_id=project.id,
                    source_id=source.id,
                    target_id=target.id,
                    relation=label,
                    detail=relation.detail.strip() or None,
                )
            )
        await index_nodes(fresh)
        await update_quietly(session, project, graph_synced_at=datetime.now(UTC))

    async def drop_orphans(session: AsyncSession, user_id: UUID) -> None:
        orphans = (
            await session.scalars(
                select(KnowledgeNode.id).where(
                    KnowledgeNode.owner_id == user_id,
                    ~KnowledgeNode.id.in_(select(KnowledgeMention.node_id)),
                )
            )
        ).all()
        if not orphans:
            return
        await session.execute(delete(KnowledgeNode).where(KnowledgeNode.id.in_(orphans)))
        try:
            await store.delete_knowledge_nodes([str(node_id) for node_id in orphans])
        except QdrantUnavailable as error:
            logger.warning("Stale knowledge vectors left in Qdrant: %s", error)

    @router.post("/sync")
    async def sync(session: Session, limit: int = Query(default=6, ge=1, le=25)):
        """Read the chats that changed since the last update into the graph."""
        user_id = require_user()
        waiting = await pending_chats(session, user_id)
        read, failed = 0, 0
        for project in waiting[:limit]:
            try:
                await read_chat(session, user_id, project)
                await session.commit()
                read += 1
            except GraphUnavailable:
                await session.rollback()
                failed += 1
        await drop_orphans(session, user_id)
        await session.commit()
        if failed and not read:
            raise HTTPException(503, "The model could not read your chats right now; try again.")
        return {"read": read, "failed": failed, "pending": max(len(waiting) - read - failed, 0)}

    @router.get("/graph")
    async def graph(session: Session):
        user_id = require_user()
        nodes = (
            await session.scalars(select(KnowledgeNode).where(KnowledgeNode.owner_id == user_id))
        ).all()
        mentions = dict(
            (
                await session.execute(
                    select(KnowledgeMention.node_id, func.count(KnowledgeMention.id))
                    .join(KnowledgeNode, KnowledgeNode.id == KnowledgeMention.node_id)
                    .where(KnowledgeNode.owner_id == user_id)
                    .group_by(KnowledgeMention.node_id)
                )
            ).all()
        )
        edges = (
            await session.scalars(select(KnowledgeEdge).where(KnowledgeEdge.owner_id == user_id))
        ).all()
        merged: Counter = Counter()
        for edge in edges:
            merged[(edge.source_id, edge.target_id, edge.relation)] += 1
        degree: Counter = Counter()
        for source, target, _ in merged:
            degree[source] += 1
            degree[target] += 1
        waiting = await pending_chats(session, user_id)
        mapped = await session.scalar(
            select(func.count(Project.id)).where(
                Project.owner_id == user_id, Project.graph_synced_at.is_not(None)
            )
        )
        return {
            "nodes": [
                {
                    "id": str(node.id),
                    "name": node.name,
                    "kind": node.kind or "concept",
                    "description": node.description,
                    "mentions": mentions.get(node.id, 0),
                    "degree": degree.get(node.id, 0),
                }
                for node in nodes
            ],
            "edges": [
                {"source": str(source), "target": str(target), "relation": relation, "count": count}
                for (source, target, relation), count in merged.items()
            ],
            "pending": len(waiting),
            "chats_mapped": mapped or 0,
        }

    async def owned_node(session: AsyncSession, node_id: UUID) -> KnowledgeNode:
        node = await session.get(KnowledgeNode, node_id)
        if node is None or node.owner_id != require_user():
            raise HTTPException(404, "concept not found")
        return node

    async def neighbourhood(
        session: AsyncSession, ids: list[UUID]
    ) -> tuple[list[KnowledgeEdge], dict[UUID, KnowledgeNode]]:
        edges = (
            await session.scalars(
                select(KnowledgeEdge).where(
                    or_(KnowledgeEdge.source_id.in_(ids), KnowledgeEdge.target_id.in_(ids))
                )
            )
        ).all()
        node_ids = (
            set(ids) | {edge.source_id for edge in edges} | {edge.target_id for edge in edges}
        )
        nodes = {
            node.id: node
            for node in (
                await session.scalars(select(KnowledgeNode).where(KnowledgeNode.id.in_(node_ids)))
            ).all()
        }
        return list(edges), nodes

    async def chats_for(session: AsyncSession, ids: list[UUID]) -> list[dict[str, Any]]:
        rows = await session.execute(
            select(Project, func.count(KnowledgeMention.id))
            .join(KnowledgeMention, KnowledgeMention.project_id == Project.id)
            .where(KnowledgeMention.node_id.in_(ids))
            .group_by(Project.id)
            .order_by(func.count(KnowledgeMention.id).desc(), Project.updated_at.desc())
        )
        return [
            {
                "id": str(project.id),
                "title": project.title,
                "has_video": bool(project.active_video_path),
            }
            for project, _ in rows.all()
        ]

    @router.get("/nodes/{node_id}")
    async def node_detail(node_id: UUID, session: Session):
        node = await owned_node(session, node_id)
        edges, nodes = await neighbourhood(session, [node.id])
        relations: dict[tuple, dict[str, Any]] = {}
        for edge in edges:
            outgoing = edge.source_id == node.id
            other = nodes.get(edge.target_id if outgoing else edge.source_id)
            if other is None:
                continue
            key = (outgoing, other.id, edge.relation)
            relations.setdefault(
                key,
                {
                    "direction": "out" if outgoing else "in",
                    "relation": edge.relation,
                    "node": {"id": str(other.id), "name": other.name, "kind": other.kind},
                    "detail": edge.detail,
                },
            )
        return {
            "id": str(node.id),
            "name": node.name,
            "kind": node.kind or "concept",
            "description": node.description,
            "relations": list(relations.values()),
            "chats": await chats_for(session, [node.id]),
        }

    async def closest(
        session: AsyncSession, user_id: UUID, text: str, limit: int
    ) -> list[tuple[KnowledgeNode, float]]:
        vectors = await embed_texts(settings, [text])
        if not vectors:
            return []
        vector = vectors[0]
        try:
            hits = await store.query_knowledge(vector, owner_id=str(user_id), limit=limit)
            found = {
                node.id: node
                for node in (
                    await session.scalars(
                        select(KnowledgeNode).where(
                            KnowledgeNode.id.in_([UUID(hit[0]) for hit in hits]),
                            KnowledgeNode.owner_id == user_id,
                        )
                    )
                ).all()
            }
            ranked = [(found[UUID(hit[0])], hit[1]) for hit in hits if UUID(hit[0]) in found]
            if ranked:
                return ranked
        except (QdrantUnavailable, ValueError) as error:
            logger.warning("Qdrant knowledge search unavailable, using stored vectors: %s", error)
        nodes = (
            await session.scalars(select(KnowledgeNode).where(KnowledgeNode.owner_id == user_id))
        ).all()
        scored = sorted(
            ((node, _cosine(vector, node.embedding)) for node in nodes),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return scored[:limit]

    @router.get("/search")
    async def search(session: Session, q: str = Query(min_length=1, max_length=200)):
        """Concepts closest in meaning to ``q`` (exact name matches first)."""
        user_id = require_user()
        ranked = await closest(session, user_id, q, 12)
        exact = concept_key(q)
        ranked.sort(key=lambda pair: (pair[0].key != exact, -pair[1]))
        return [
            {"id": str(node.id), "name": node.name, "kind": node.kind, "score": round(score, 3)}
            for node, score in ranked
        ]

    @router.post("/query")
    async def query(payload: QueryRequest, session: Session):
        """Answer a question from the learner's own graph, citing concepts and chats."""
        user_id = require_user()
        account = await session.get(UserAccount, user_id)
        seeds = [
            node
            for node, score in await closest(session, user_id, payload.question, 6)
            if score > 0.2
        ]
        if not seeds:
            return {
                "answer": "Your knowledge map has nothing close to that yet. Learn about it in a chat, then update the map.",
                "nodes": [],
                "chats": [],
            }
        edges, nodes = await neighbourhood(session, [node.id for node in seeds])
        facts = [f"{node.name}: {node.description}" for node in nodes.values() if node.description]
        facts += [
            f"{nodes[edge.source_id].name} {edge.relation} {nodes[edge.target_id].name}"
            + (f" ({edge.detail})" if edge.detail else "")
            for edge in edges
            if edge.source_id in nodes and edge.target_id in nodes
        ]
        facts = list(dict.fromkeys(facts))[:120]
        with track_usage() as ledger:
            try:
                answer = await answer_from_graph(
                    create_llm_provider(settings),
                    question=payload.question.strip(),
                    facts=facts,
                    language=account.default_text_language if account else "English",
                )
            except GraphUnavailable as error:
                raise HTTPException(503, f"The model is unavailable: {error}") from error
        await save_usage(session, ledger, None)
        await session.commit()
        by_key = {node.key: node for node in nodes.values()}
        used = [
            by_key[concept_key(name)] for name in answer.concepts if concept_key(name) in by_key
        ]
        highlighted = used or seeds
        return {
            "answer": answer.answer,
            "nodes": [
                str(node.id) for node in {node.id: node for node in [*highlighted, *seeds]}.values()
            ],
            "chats": (await chats_for(session, [node.id for node in highlighted]))[:8],
        }

    @router.delete("", status_code=204)
    async def reset(session: Session):
        """Forget the whole graph; the next update rebuilds it from the chats."""
        user_id = require_user()
        ids = (
            await session.scalars(select(KnowledgeNode.id).where(KnowledgeNode.owner_id == user_id))
        ).all()
        await session.execute(delete(KnowledgeNode).where(KnowledgeNode.owner_id == user_id))
        await session.execute(
            update(Project)
            .where(Project.owner_id == user_id)
            .values(graph_synced_at=None, updated_at=Project.updated_at)
        )
        await session.commit()
        try:
            await store.delete_knowledge_nodes([str(node_id) for node_id in ids])
        except QdrantUnavailable as error:
            logger.warning("Stale knowledge vectors left in Qdrant: %s", error)

    return router
