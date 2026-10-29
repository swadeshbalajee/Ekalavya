"""The lesson curator: turns everything collected for a lesson into one teaching plan.

Every later stage (script writer, storyboard) works from this plan instead of the
raw material, so the plan must carry what the lesson needs: the line of argument,
facts, worked examples with their numbers, equations, the source's own wording where
it matters, and ideas for what to show. Detail that does not fit is listed as a
reference to the passages that hold it; those passages stay in the vector store and
the writers can look them up later.

Material that fits one prompt is read in one pass. Larger material is split into
groups that are read in parallel; each group's notes are checked against its own
text, then the notes are merged into the plan. A reviewer compares the plan with
what it was made from and the curator fixes anything important that went missing.
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from scholarmotion.observability.usage import usage_stage
from scholarmotion.schemas import CurriculumItem, CurriculumPlan, SourceReference

logger = logging.getLogger(__name__)


class PlanSection(BaseModel):
    title: str
    purpose: str = ""
    # Relative share of the lesson's time.
    weight: float = 1.0
    key_points: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    equations: list[str] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)
    visual_ideas: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class PlanReference(BaseModel):
    about: str
    sources: list[str] = Field(default_factory=list)


class LessonPlan(BaseModel):
    title: str
    ask_kind: str = ""
    summary: str = ""
    sections: list[PlanSection] = Field(default_factory=list)
    references: list[PlanReference] = Field(default_factory=list)
    ending: str = ""


class SourceNote(BaseModel):
    topic: str
    content: str
    sources: list[str] = Field(default_factory=list)


class SourceNotes(BaseModel):
    notes: list[SourceNote] = Field(default_factory=list)
    references: list[PlanReference] = Field(default_factory=list)


class CoverageCheck(BaseModel):
    complete: bool = True
    missing: list[str] = Field(default_factory=list)


CURATOR_ROLE = """You are the lesson curator for a narrated teaching video. You read everything
collected for this lesson and write the one document the script writer and the visual
planner will work from; they will not see the material itself, so whatever the lesson
needs must be in your plan.
- Decide the sections and their order from the material and the request, not from a fixed
  template. Give each section a purpose and a relative weight of the lesson's time.
- Keep substance, not summaries: the line of argument, definitions, facts, every worked
  example with its exact numbers, equations unchanged, and the source's own terms.
- When the lesson is based on the learner's own source, follow that source's line of
  argument in its order, keep its examples and terminology, and copy short key passages
  word for word into quotes.
- Never invent facts, numbers or results that are not in the material; you may add the
  plain teaching steps needed to make the material understandable.
- Suggest visual ideas worth animating for each section.
- Keep the core even in a short lesson: name the central concept, law or result, and give
  the one equation, number or relation that makes it precise. Shorten everything else
  before you drop the core.
- Detail that the lesson cannot fit goes into references: one short line on what it is,
  with the ids of the passages that hold it, so a writer can look it up if needed.
  References are for genuine extras, never for the core idea.
- Cite passage ids (like P3) for every section in sources.
- ask_kind says what kind of ask this is (for example a paper walkthrough, a doubt or
  follow-up on an earlier explanation, a new topic); ending suggests how a teacher would
  close this particular lesson, without teasing or promising later content."""

EXTRACT_ROLE = """You read one part of the material collected for a lesson and write notes that
another agent will merge into the lesson plan; it will not see this material, so keep
everything the lesson could need. Write one note per idea: definitions, facts, steps of
the argument, every worked example with its exact numbers, equations unchanged, and short
key passages word for word. Keep the material's order. Cite passage ids (like P3) in each
note. Mark the notes that carry the material's central ideas with "(core)" at the start
of their topic, so the merge keeps them. Put detail too fine for the lesson into
references (one line on what it is, with its passage ids) instead of dropping it. Never
invent anything."""

CHECK_ROLE = """Compare the document with the material it was made from. List only important
things that are missing or changed: the central concept, law or result and what makes it
precise, a step of the argument, a fact, a worked example or
its numbers, an equation, a definition the lesson needs, or something stated that the
material does not say. Ignore wording and style, and detail already listed as a
reference. Set complete=true when nothing important is missing or wrong."""


def _alias(chunks: list[dict]) -> tuple[list[tuple[str, dict]], dict[str, str]]:
    """Short passage ids (P1, P2...) for the prompts, mapped back to chunk ids."""
    named = [(f"P{index}", chunk) for index, chunk in enumerate(chunks, 1)]
    return named, {alias: str(chunk.get("id") or alias) for alias, chunk in named}


def _passage(alias: str, chunk: dict) -> str:
    where = " / ".join(
        str(part)
        for part in (chunk.get("document_title"), chunk.get("section"))
        if part and str(part).strip()
    )
    return f"[{alias}] ({where or 'source'}) {' '.join(str(chunk.get('text', '')).split())}"


def _groups(passages: list[str], budget: int) -> list[list[str]]:
    """Consecutive passages packed into groups of at most ``budget`` characters."""
    groups: list[list[str]] = [[]]
    used = 0
    for passage in passages:
        if groups[-1] and used + len(passage) > budget:
            groups.append([])
            used = 0
        groups[-1].append(passage)
        used += len(passage) + 1
    return groups


def _notes_text(notes: SourceNotes) -> str:
    lines = [f"- {note.topic}: {note.content} [{', '.join(note.sources)}]" for note in notes.notes]
    lines += [
        f"- (reference) {item.about} [{', '.join(item.sources)}]" for item in notes.references
    ]
    return "\n".join(lines)


def _plan_text(plan: LessonPlan) -> str:
    return plan.model_dump_json(indent=1)


class LessonCurator:
    """``llm`` writes the plan; the cheaper ``helper`` reads groups and checks coverage."""

    def __init__(self, llm, helper=None):
        self.llm = llm
        self.helper = helper or llm

    async def _ask(self, stage: str, prompt: str, schema, llm=None):
        with usage_stage(stage):
            return await (llm or self.llm).generate_structured(prompt, schema)

    async def _checked(self, stage: str, role: str, task: str, material: str, schema, *, llm=None):
        """Draft, compare with the material, and fix important gaps once."""
        draft = await self._ask(
            stage, f"{role}\n\n{task}\n\nMATERIAL:\n{material}", schema, llm=llm
        )
        try:
            check = await self._ask(
                "curator_check",
                f"{CHECK_ROLE}\n\nDOCUMENT:\n{draft.model_dump_json(indent=1)}\n\n"
                f"MATERIAL:\n{material}",
                CoverageCheck,
                llm=self.helper,
            )
        except Exception as error:  # noqa: BLE001 - an unchecked plan still works
            logger.warning("Plan check unavailable: %r", error)
            return draft
        if check.complete or not check.missing:
            return draft
        try:
            return await self._ask(
                stage,
                f"{role}\n\n{task}\n\nA reviewer compared your draft with the material and "
                "found these important gaps. Return the complete corrected document:\n- "
                + "\n- ".join(check.missing)
                + f"\n\nYOUR DRAFT:\n{draft.model_dump_json(indent=1)}\n\nMATERIAL:\n{material}",
                schema,
                llm=llm,
            )
        except Exception as error:  # noqa: BLE001 - keep the reviewed draft
            logger.warning("Plan revision failed; keeping the draft: %r", error)
            return draft

    async def curate(
        self,
        *,
        request: str,
        chunks: list[dict],
        minutes: float,
        level: str,
        source_scope: str,
        one_pass_chars: int,
    ) -> CurriculumPlan | None:
        if getattr(self.llm, "name", "") == "mock" or not hasattr(self.llm, "generate_structured"):
            return None
        named, ids = _alias(chunks)
        passages = [_passage(alias, chunk) for alias, chunk in named]
        context = (
            f"LEARNER'S REQUEST: {request}\nLEVEL: {level}\nLENGTH: about {minutes:g} minutes "
            f"of narration (about {round(minutes * 120)} spoken words in total).\n"
            f"SOURCE USE: {source_scope}"
        )
        task = f"{context}\nWrite the lesson plan."
        material = "\n".join(passages) or "(nothing was collected; plan from the request)"
        try:
            if len(material) <= one_pass_chars:
                plan = await self._checked("curator", CURATOR_ROLE, task, material, LessonPlan)
            else:
                plan = await self._curate_in_groups(context, passages, one_pass_chars)
        except Exception as error:  # noqa: BLE001 - the plain outline still builds a lesson
            logger.warning("Lesson curator unavailable; using the plain outline: %r", error)
            return None
        if not plan.sections:
            return None
        return to_curriculum(plan, ids, minutes=minutes)

    async def _curate_in_groups(self, context: str, passages: list[str], budget: int) -> LessonPlan:
        groups = _groups(passages, budget)
        extract_task = f"{context}\nWrite notes on this part of the material."
        notes = await asyncio.gather(
            *(
                self._checked(
                    "curator_extract",
                    EXTRACT_ROLE,
                    extract_task,
                    "\n".join(group),
                    SourceNotes,
                    llm=self.helper,
                )
                for group in groups
            )
        )
        merged = "\n\n".join(
            f"NOTES ON PART {index} OF {len(groups)}:\n{_notes_text(part)}"
            for index, part in enumerate(notes, 1)
        )
        return await self._checked(
            "curator",
            CURATOR_ROLE + "\nThe material below is notes made from the full material by "
            "other readers; their references stay valid, so carry them over.",
            f"{context}\nWrite the lesson plan.",
            merged,
            LessonPlan,
        )


def to_curriculum(plan: LessonPlan, ids: dict[str, str], *, minutes: float) -> CurriculumPlan:
    """The plan as the outline the script writer and storyboard already understand."""

    def real(aliases: list[str]) -> list[str]:
        return list(dict.fromkeys(ids[alias] for alias in aliases if alias in ids))

    def text(values: list[str]) -> list[str]:
        # Models sometimes emit separators such as ":" as list items.
        return [value.strip() for value in values if any(ch.isalnum() for ch in value)]

    total = minutes * 60
    weights = [max(section.weight, 0.1) for section in plan.sections]
    items = [
        CurriculumItem(
            title=section.title,
            objective=section.purpose or section.title,
            duration_seconds=max(8.0, total * weight / sum(weights)),
            prerequisites=[earlier.title for earlier in plan.sections[:index]],
            source_ids=real(section.sources),
            key_points=[
                *text(section.key_points),
                *(f"Example: {example}" for example in text(section.examples)),
                *(f'Source wording: "{quote}"' for quote in text(section.quotes)),
            ],
            equations=text(section.equations),
            visual_ideas=text(section.visual_ideas),
        )
        for index, (section, weight) in enumerate(zip(plan.sections, weights, strict=True))
    ]
    return CurriculumPlan(
        topic=plan.title,
        items=items,
        kind="curated",
        ask_kind=plan.ask_kind,
        ending=plan.ending,
        references=[
            SourceReference(about=item.about, source_ids=real(item.sources))
            for item in plan.references
            if item.about.strip()
        ],
    )


def plan_evidence(plan: CurriculumPlan) -> list[dict]:
    """Compact grounding for the script reviewer: the plan, not the raw material."""
    evidence = [{"kind": "lesson", "content": f"{plan.topic}. {plan.ask_kind}".strip()}]
    for item in plan.items:
        evidence.append(
            {
                "kind": "section",
                "content": f"{item.title}: {item.objective} | " + " | ".join(item.key_points),
                "equations": item.equations,
            }
        )
    return evidence
