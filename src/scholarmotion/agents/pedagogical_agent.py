"""Builds the teaching dossier (motivation, prerequisites, derivations, examples) for a lesson."""

from __future__ import annotations

from typing import Any

from scholarmotion.schemas import CurriculumPlan, KnowledgeCard, LearnerProfile


def build_teaching_dossier(
    profile: LearnerProfile, curriculum: CurriculumPlan, cards: list[KnowledgeCard]
) -> dict[str, Any]:
    evidence = [card.model_dump() for card in cards]
    return {
        "topic": profile.topic,
        "motivation": f"Connect {profile.topic} to an observable question before introducing formal notation.",
        "prerequisites": [item.title for item in curriculum.items[:-1]],
        "intuition": [
            f"Use a concrete visual model for {item.title}." for item in curriculum.items
        ],
        "formal_explanation": [item.objective for item in curriculum.items],
        "derivations": [card.model_dump() for card in cards if card.kind == "equation"],
        "examples": [card.model_dump() for card in cards if card.kind == "example"]
        or [{"content": f"A simple two-dimensional example of {profile.topic}.", "source_ids": []}],
        "counterexamples": [f"Show what is not an instance of {profile.topic}."],
        "common_mistakes": [
            f"Do not use {item.title} before defining it." for item in curriculum.items
        ],
        "analogies": [
            f"Treat each step in {profile.topic} as a visible change, not a paragraph of text."
        ],
        "expected_visuals": ["coordinate system", "highlighted quantities", "step transformation"],
        "transitions": [
            f"Now that {item.title} is clear, connect it to the next idea."
            for item in curriculum.items
        ],
        "recap": [item.objective for item in curriculum.items],
        "evidence": evidence,
    }
