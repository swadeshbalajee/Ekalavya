"""Works out which scene artifacts become stale after an edit."""

from __future__ import annotations

from dataclasses import dataclass

from scholarmotion.schemas import ArtifactKind, FeedbackType

DEPENDENCIES: dict[ArtifactKind, set[ArtifactKind]] = {
    ArtifactKind.PROFILE: {ArtifactKind.CURRICULUM},
    ArtifactKind.RETRIEVAL: {ArtifactKind.CURRICULUM},
    ArtifactKind.CURRICULUM: {ArtifactKind.PEDAGOGY},
    ArtifactKind.PEDAGOGY: {ArtifactKind.SCRIPT},
    ArtifactKind.SCRIPT: {ArtifactKind.STORYBOARD, ArtifactKind.AUDIO, ArtifactKind.SUBTITLES},
    ArtifactKind.STORYBOARD: {ArtifactKind.SPEC},
    ArtifactKind.SPEC: {ArtifactKind.CODE, ArtifactKind.AUDIO},
    ArtifactKind.CODE: {ArtifactKind.TIMING, ArtifactKind.RENDER},
    ArtifactKind.AUDIO: {ArtifactKind.TIMING, ArtifactKind.SUBTITLES, ArtifactKind.RENDER},
    ArtifactKind.TIMING: {ArtifactKind.RENDER},
    ArtifactKind.RENDER: {ArtifactKind.VERIFICATION, ArtifactKind.VIDEO},
    ArtifactKind.VERIFICATION: {ArtifactKind.VIDEO},
    ArtifactKind.VIDEO: {ArtifactKind.TIMELINE},
}


@dataclass(frozen=True)
class InvalidationPlan:
    root: ArtifactKind
    stale: frozenset[ArtifactKind]
    regenerate_audio: bool


def propagate_staleness(roots: set[ArtifactKind]) -> set[ArtifactKind]:
    stale = set(roots)
    frontier = list(roots)
    while frontier:
        kind = frontier.pop()
        for dependent in DEPENDENCIES.get(kind, set()):
            if dependent not in stale:
                stale.add(dependent)
                frontier.append(dependent)
    return stale


def invalidation_for(feedback_type: FeedbackType, instruction: str = "") -> InvalidationPlan:
    if feedback_type == FeedbackType.VISUAL_DEFECT:
        root = ArtifactKind.CODE
    elif feedback_type == FeedbackType.MATH_DEFECT:
        root = ArtifactKind.SPEC
    elif feedback_type in {FeedbackType.AUDIO_DEFECT, FeedbackType.TIMING_DEFECT}:
        root = ArtifactKind.AUDIO
    elif feedback_type == FeedbackType.STYLE_PREFERENCE:
        root = ArtifactKind.CODE
    elif feedback_type in {FeedbackType.CONTENT_EDIT, FeedbackType.SEMANTIC_DEFECT}:
        root = ArtifactKind.PEDAGOGY
    else:
        root = ArtifactKind.PROFILE
    stale = propagate_staleness({root})
    return InvalidationPlan(root, frozenset(stale), ArtifactKind.AUDIO in stale)
