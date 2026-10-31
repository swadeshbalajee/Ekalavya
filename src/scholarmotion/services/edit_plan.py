"""A video edit as a list of per-scene operations, passed from the chat to the edit runner.

The edit workflow's interface carries one instruction string; a structured
plan travels in it as JSON after PLAN_PREFIX, and a plain instruction (older
callers, a time-range edit) still works as a change to every scene in range.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from scholarmotion.agents.lesson_preparation import SceneEdit

PLAN_PREFIX = "EDIT_PLAN:"


class EditPlan(BaseModel):
    edits: list[SceneEdit] = Field(default_factory=list)
    pronunciations: dict[str, str] = Field(default_factory=dict)
    whole_video: bool = False
    # The learner's request in their words, for records and the reviewer.
    summary: str = ""
    # Private guidance from the learner's quizzes and earlier chats for the writers;
    # never shown, spoken or checked by the reviewer.
    teaching_notes: str = ""

    def encode(self) -> str:
        return PLAN_PREFIX + self.model_dump_json()

    @classmethod
    def decode(cls, instruction: str, scene_keys: list[str]) -> EditPlan:
        """The plan in ``instruction``; a plain instruction changes each of ``scene_keys``."""
        if instruction.startswith(PLAN_PREFIX):
            return cls.model_validate_json(instruction[len(PLAN_PREFIX) :])
        return cls(
            edits=[
                SceneEdit(op="change", scene=key, instruction=instruction) for key in scene_keys
            ],
            summary=instruction,
        )

    def touched(self) -> list[str]:
        """Existing scenes this plan names, in order of first mention."""
        names = [edit.scene for edit in self.edits] + [edit.after for edit in self.edits]
        return list(dict.fromkeys(name for name in names if name))

    def describe(self, numbers: dict[str, int]) -> str:
        """A short account of the plan in scene numbers, for the chat."""
        words = {
            "change": "update scene {n}",
            "insert_after": "add a new scene after scene {n}",
            "delete": "remove scene {n}",
            "split": "split scene {n} in two",
            "merge_with_next": "merge scene {n} with the next one",
            "move_after": "move scene {n}",
        }
        parts = []
        for edit in self.edits:
            if edit.op == "insert_after" and not edit.scene:
                parts.append("add a new scene at the start")
            elif edit.scene in numbers:
                parts.append(words[edit.op].format(n=numbers[edit.scene]))
        if self.pronunciations:
            parts.append("fix the pronunciation of " + ", ".join(self.pronunciations))
        return ", ".join(dict.fromkeys(parts))
