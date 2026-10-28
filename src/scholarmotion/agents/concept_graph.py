"""Read a chat into a concept graph, and answer questions from that graph.

The extractor decides which ideas a conversation (and its lesson video) taught
and how they relate, in its own words: relation labels are free text, not a
fixed vocabulary. It is shown the learner's existing concept names so the same
idea keeps one node across chats. The answerer replies only from the facts of
the subgraph it is given, naming the concepts it used.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from scholarmotion.observability.usage import usage_stage

logger = logging.getLogger(__name__)


class Concept(BaseModel):
    name: str = Field(description="Canonical English name, short (e.g. 'wavelength')")
    description: str = Field(default="", description="One sentence: what it is")
    kind: str = Field(
        default="", description="One word for what sort of thing it is, e.g. concept, law, person"
    )


class Relation(BaseModel):
    source: str = Field(description="Name of a concept in this map")
    target: str = Field(description="Name of a concept in this map")
    relation: str = Field(description="Short verb phrase from source to target")
    detail: str = ""


class ConceptMap(BaseModel):
    concepts: list[Concept] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)


class GraphAnswer(BaseModel):
    answer: str
    concepts: list[str] = Field(default_factory=list, description="Concept names the answer used")


class GraphUnavailable(RuntimeError):
    pass


def concept_key(name: str) -> str:
    return " ".join(name.casefold().split())[:200]


async def extract_concepts(
    llm, *, title: str, conversation: str, lesson: str, known: list[str]
) -> ConceptMap:
    if llm is None or getattr(llm, "name", "") == "mock":
        raise GraphUnavailable("the model is not configured")
    prompt = (
        "Map what a learner studied in this chat as a knowledge graph. Choose the concepts "
        "that matter (ideas, laws, quantities, methods, people, examples) and the relations "
        "between them, in your own short labels (for example 'is measured in', 'is a "
        "prerequisite for', 'is an example of'). Use English names. When a concept is the "
        "same idea as one in KNOWN CONCEPTS, use that exact name.\n\n"
        f"KNOWN CONCEPTS: {', '.join(known[:400]) or '(none yet)'}\n\n"
        f"CHAT: {title}\n{conversation[:16000]}\n"
    )
    if lesson:
        prompt += f"\nLESSON VIDEO NARRATION:\n{lesson[:8000]}\n"
    try:
        with usage_stage("knowledge_graph"):
            result = await llm.generate_structured(prompt, ConceptMap)
    except Exception as error:
        logger.warning("Concept extraction unavailable: %s", error)
        raise GraphUnavailable(str(error)) from error
    names = {concept_key(concept.name) for concept in result.concepts if concept.name.strip()}
    result.concepts = [concept for concept in result.concepts if concept.name.strip()]
    # A relation may only join concepts the map defines.
    result.relations = [
        relation
        for relation in result.relations
        if relation.relation.strip()
        and concept_key(relation.source) in names
        and concept_key(relation.target) in names
        and concept_key(relation.source) != concept_key(relation.target)
    ]
    return result


async def answer_from_graph(llm, *, question: str, facts: list[str], language: str) -> GraphAnswer:
    if llm is None or getattr(llm, "name", "") == "mock":
        raise GraphUnavailable("the model is not configured")
    prompt = (
        "Answer the learner's question using only these facts from their own knowledge "
        "graph (what they have studied). If the facts do not cover it, say what is missing "
        f"and which related concepts they have studied. Reply in {language}, briefly. List "
        "the concept names you used.\n\n"
        "FACTS:\n" + "\n".join(f"- {fact}" for fact in facts) + f"\n\nQUESTION: {question}"
    )
    try:
        with usage_stage("knowledge_graph"):
            return await llm.generate_structured(prompt, GraphAnswer)
    except Exception as error:
        logger.warning("Graph answer unavailable: %s", error)
        raise GraphUnavailable(str(error)) from error
