"""What a learner has shown so far, as private notes for the agents that teach them.

Nothing is scheduled. Each answered quiz question is indexed in Qdrant when the
attempt is submitted (and any attempt not yet indexed is indexed the next time
notes are built). Notes are assembled when a chat turn, lesson, quiz or study
plan needs them: the quiz answers closest in meaning to the current topic, each
quiz's latest score, the active study plan's progress and related concepts from
the learner's knowledge map. PostgreSQL stays authoritative; without Qdrant the
answers are ranked from freshly computed vectors.

The notes are for adapting what is taught (more care where the learner slipped,
a faster pace where they are sure). Agents are told never to mention them unless
the learner asks about their own quizzes or progress.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scholarmotion.config import Settings
from scholarmotion.persistence.models import (
    KnowledgeNode,
    Quiz,
    QuizAttempt,
    StudyPlan,
    StudyTask,
)
from scholarmotion.persistence.qdrant import QdrantConfig, QdrantUnavailable, QdrantVectorStore
from scholarmotion.services.embeddings import embed_texts

logger = logging.getLogger(__name__)

PRIVATE_USE = (
    "Use these notes quietly to shape the teaching. When the topic builds on ideas the learner "
    "is weak in (for SVD: basis, dimension, rank, orthogonality), first give a short refresher "
    "of those prerequisites, then explain more deeply and slowly wherever the topic relies on "
    "them. Give more care, steps and examples where the learner slipped, move at a quicker pace "
    "where they were sure, and link to concepts they have met. "
    "Never mention the notes, scores, quizzes, strengths or weaknesses unless the learner asks "
    "about their own quizzes, results or progress; then answer directly and honestly."
)
_ATTEMPTS_READ = 60
_CLOSEST = 8
_STORES: dict[int, QdrantVectorStore] = {}
_INDEXED: set[str] = set()


@dataclass(frozen=True)
class Evidence:
    point_id: str
    question: str
    topic: str
    quiz: str
    correct: bool
    when: datetime


def _store(settings: Settings) -> QdrantVectorStore:
    if id(settings) not in _STORES:
        _STORES[id(settings)] = QdrantVectorStore(QdrantConfig.from_settings(settings))
    return _STORES[id(settings)]


def _point_id(attempt_id: UUID, index: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"quiz-attempt:{attempt_id}:{index}"))


def attempt_evidence(quiz: Quiz, attempt: QuizAttempt) -> list[Evidence]:
    """One entry per answered question of an attempt."""
    topic = (quiz.topic or quiz.title or "").strip()
    found = []
    for index, (question, result) in enumerate(
        zip(quiz.questions or [], attempt.results or [], strict=False)
    ):
        text = str(question.get("prompt") or "").strip()
        if text:
            found.append(
                Evidence(
                    _point_id(attempt.id, index),
                    text,
                    topic,
                    quiz.title,
                    bool(result.get("correct")),
                    attempt.created_at,
                )
            )
    return found


def _embedding_text(item: Evidence) -> str:
    return f"{item.topic}: {item.question}"


async def index_evidence(settings: Settings, owner_id: UUID, items: list[Evidence]) -> bool:
    """Put answered questions in Qdrant; False when it could not be done."""
    if not items:
        return True
    vectors = await embed_texts(settings, [_embedding_text(item) for item in items])
    if not vectors:
        return False
    try:
        await _store(settings).upsert_learner_evidence(
            [
                {
                    "id": item.point_id,
                    "vector": vector,
                    "payload": {
                        "owner_id": str(owner_id),
                        "question": item.question,
                        "topic": item.topic,
                        "quiz": item.quiz,
                        "correct": item.correct,
                    },
                }
                for item, vector in zip(items, vectors, strict=True)
            ]
        )
    except QdrantUnavailable as error:
        logger.warning("Quiz answers not indexed in Qdrant: %s", error)
        return False
    return True


async def index_attempt(settings: Settings, quiz: Quiz, attempt: QuizAttempt) -> None:
    """Called when an attempt is submitted; failure only delays indexing."""
    try:
        items = attempt_evidence(quiz, attempt)
        if await index_evidence(settings, attempt.user_id, items):
            _INDEXED.update(item.point_id for item in items)
    except Exception as error:  # noqa: BLE001 - grading must never fail on indexing
        logger.warning("Quiz attempt indexing failed: %s", error)


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


async def _closest_answers(
    settings: Settings, owner_id: UUID, focus: str, items: list[Evidence]
) -> list[Evidence]:
    if not items:
        return []
    if not focus.strip():
        return items[:_CLOSEST]
    vectors = await embed_texts(settings, [focus])
    if not vectors:
        return items[:_CLOSEST]
    vector = vectors[0]
    by_id = {item.point_id: item for item in items}
    try:
        # Answers not indexed by this process yet (older attempts, an earlier
        # outage) are upserted first; upserting an indexed answer is harmless.
        pending = [item for item in items if item.point_id not in _INDEXED]
        if pending and await index_evidence(settings, owner_id, pending):
            _INDEXED.update(item.point_id for item in pending)
        hits = await _store(settings).query_learner_evidence(
            vector, owner_id=str(owner_id), limit=_CLOSEST * 3
        )
        # A hit no longer in PostgreSQL (a deleted quiz) is skipped.
        known = [by_id[hit[0]] for hit in hits if hit[0] in by_id]
        if known:
            return known[:_CLOSEST]
    except QdrantUnavailable as error:
        logger.warning("Qdrant learner search unavailable, ranking directly: %s", error)
    texts = await embed_texts(settings, [_embedding_text(item) for item in items])
    if not texts:
        return items[:_CLOSEST]
    ranked = sorted(
        zip(items, texts, strict=True), key=lambda pair: _cosine(vector, pair[1]), reverse=True
    )
    return [item for item, _ in ranked[:_CLOSEST]]


async def _concepts(
    session: AsyncSession, settings: Settings, owner_id: UUID, focus: str
) -> list[str]:
    if not focus.strip():
        return []
    vectors = await embed_texts(settings, [focus])
    if not vectors:
        return []
    try:
        hits = await _store(settings).query_knowledge(vectors[0], owner_id=str(owner_id), limit=8)
        names = [str(hit[2].get("name") or "") for hit in hits if hit[1] >= 0.35]
        return [name for name in names if name]
    except QdrantUnavailable:
        nodes = (
            await session.scalars(
                select(KnowledgeNode).where(
                    KnowledgeNode.owner_id == owner_id, KnowledgeNode.embedding.is_not(None)
                )
            )
        ).all()
        scored = sorted(
            ((_cosine(vectors[0], node.embedding), node.name) for node in nodes), reverse=True
        )
        return [name for score, name in scored[:8] if score >= 0.35]


def _summary(items: list[Evidence]) -> list[str]:
    """Stronger areas and areas needing work, by quiz topic, from every answer read."""
    by_topic: dict[str, list[bool]] = {}
    for item in items:
        by_topic.setdefault(item.topic or item.quiz, []).append(item.correct)

    def share(results: list[bool]) -> str:
        return f"{sum(results)}/{len(results)} right"

    strong = [
        f"{topic} ({share(results)})"
        for topic, results in by_topic.items()
        if len(results) >= 2 and sum(results) / len(results) >= 0.75
    ]
    weak = [
        f"{topic} ({share(results)})"
        for topic, results in by_topic.items()
        if sum(results) / len(results) <= 0.5
    ]
    lines = []
    if strong:
        lines.append("STRONGER AREAS: " + "; ".join(strong))
    if weak:
        lines.append("AREAS NEEDING WORK: " + "; ".join(weak))
    return lines


async def learner_notes(
    session: AsyncSession, settings: Settings, owner_id: UUID | None, focus: str
) -> str:
    """Private notes on this learner for ``focus`` (the current topic); empty when none."""
    if owner_id is None:
        return ""
    try:
        return await _notes(session, settings, owner_id, focus)
    except Exception as error:  # noqa: BLE001 - teaching goes on without personalisation
        logger.warning("Learner notes unavailable: %s", error)
        return ""


async def _notes(session: AsyncSession, settings: Settings, owner_id: UUID, focus: str) -> str:
    rows = (
        await session.execute(
            select(QuizAttempt, Quiz)
            .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
            .where(QuizAttempt.user_id == owner_id)
            .order_by(QuizAttempt.created_at.desc())
            .limit(_ATTEMPTS_READ)
        )
    ).all()
    lines: list[str] = []
    quizzes: dict[UUID, tuple[Quiz, QuizAttempt, int]] = {}
    items: list[Evidence] = []
    for attempt, quiz in rows:
        # Newest first, so the first attempt seen of a quiz is its latest.
        _, latest, count = quizzes.get(quiz.id, (quiz, attempt, 0))
        quizzes[quiz.id] = (quiz, latest, count + 1)
        items.extend(attempt_evidence(quiz, attempt))
    if quizzes:
        lines.append("QUIZZES TAKEN (newest first):")
        for quiz, latest, count in list(quizzes.values())[:10]:
            topic = f" on {quiz.topic}" if quiz.topic and quiz.topic != quiz.title else ""
            lines.append(
                f"- {quiz.title!r}{topic}: latest score {round(latest.score * 100)}%, "
                f"{count} attempt{'s' if count != 1 else ''}, {latest.created_at:%Y-%m-%d}"
            )
    lines.extend(_summary(items))
    closest = await _closest_answers(settings, owner_id, focus, items)
    if closest:
        lines.append("QUIZ ANSWERS CLOSEST TO THE CURRENT TOPIC:")
        lines.extend(
            f"- {'right' if item.correct else 'missed'}: {item.question[:220]} (quiz {item.quiz!r})"
            for item in closest
        )
    plan = await session.scalar(
        select(StudyPlan)
        .where(StudyPlan.owner_id == owner_id, StudyPlan.status == "active")
        .order_by(StudyPlan.updated_at.desc())
        .limit(1)
    )
    if plan is not None:
        tasks = (
            await session.scalars(
                select(StudyTask)
                .where(StudyTask.plan_id == plan.id)
                .order_by(StudyTask.due_date, StudyTask.position)
            )
        ).all()
        done = sum(task.done for task in tasks)
        upcoming = [task.title for task in tasks if not task.done][:3]
        lines.append(
            f"ACTIVE STUDY PLAN: {plan.title!r} (goal: {plan.goal[:200]}): {done} of "
            f"{len(tasks)} tasks done" + (f"; next: {'; '.join(upcoming)}" if upcoming else "")
        )
    has_concepts = await session.scalar(
        select(KnowledgeNode.id).where(KnowledgeNode.owner_id == owner_id).limit(1)
    )
    concepts = await _concepts(session, settings, owner_id, focus) if has_concepts else []
    if concepts:
        lines.append("RELATED CONCEPTS FROM THE LEARNER'S EARLIER CHATS: " + ", ".join(concepts))
    if not lines:
        return ""
    return PRIVATE_USE + "\n" + "\n".join(lines)
