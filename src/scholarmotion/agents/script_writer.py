"""Writes and reviews the whole-lesson narration script, scene by scene."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field

from scholarmotion.media.pauses import speakable_with_pauses, with_markers
from scholarmotion.media.speech import to_speakable
from scholarmotion.schemas import CurriculumItem, CurriculumPlan, NarrationBlock

logger = logging.getLogger(__name__)


class ScriptSegment(BaseModel):
    narration: str = ""
    equations: list[str] = Field(default_factory=list)
    # The STEM terms this segment names, in English (e.g. "photoelectric effect").
    key_terms: list[str] = Field(default_factory=list)
    title: str = ""
    learning_objective: str = ""
    # Paper lectures: ids of the chapter's data plots this segment narrates.
    plot_ids: list[str] = Field(default_factory=list)
    # Paper lectures: labels of the paper's own figures shown while this segment speaks.
    figure_labels: list[str] = Field(default_factory=list)


class LessonScript(BaseModel):
    segments: list[ScriptSegment]


class ScriptReview(BaseModel):
    approved: bool
    problems: list[str] = Field(default_factory=list)
    # True only for problems that make the lesson wrong or incoherent; minor
    # notes do not stop a build after the revision rounds are used.
    blocking: bool = False


SCRIPT_REVIEW_ROUNDS = 3

# How a lesson may use material beyond the learner's own source, by the
# planner's source_scope. Evidence whose source id starts with "web-" came
# from a web search; every other piece is from the learner's source.
SOURCE_POLICIES = {
    "only_given": (
        "SOURCE POLICY: the learner asked for this lesson to be made ONLY from the source they "
        "provided. Teach what that source says: its ideas, arguments, examples and conclusions, "
        "following its line of reasoning. Make clear once, early on, which source this lesson "
        "explains; after that, teach its ideas in your own voice as the teacher, the way a "
        "teacher walks a class through a text, instead of prefacing sentences with 'the author "
        "says'. Mention the author again only where it helps, such as a claim that is "
        "specifically the author's interpretation. Use nothing beyond the Grounding evidence "
        "except plain explanations of everyday words needed to follow it. Do not judge, verify "
        "or correct the source."
    ),
    "given_plus_web": (
        "SOURCE POLICY: centre the lesson on the learner's own source (evidence whose source id "
        "does not start with 'web-'): explain its ideas first, in your own voice as the teacher, "
        "making clear once which source they come from. Web evidence may only add background "
        "or context, and must be presented as separate from what the source says."
    ),
}

PAPER_LECTURE_RULES = """This is a detailed lecture on one research paper, chapter by chapter.
Follow the chapter plan: teach every listed key point of a segment's chapter in that chapter's
segments, in a logical order, and explain WHY each design choice was made. Open the lecture by
naming the paper, its authors and the problem it solves. Use the paper's notation. When a
chapter lists equations, put each one, unchanged, in the `equations` field of the segment that
explains it and walk through every symbol in words. When a chapter lists plots, set `plot_ids`
on the segment that discusses those results and say the actual numbers from the plot. When a
chapter lists figures, set `figure_labels` on the segment that walks through that figure (the
student sees the original image) and refer to its parts in the order of its notes. Never
invent results or numbers that are not in the plan. Say clearly when an example is illustrative.
Near the end, cover the paper's contribution, its limitations and what the student should
remember."""

CURATED_PLAN_RULES = """Follow the LESSON PLAN, written by a curator who read all the material
for this lesson; you do not see that material, so the plan is your grounding. Teach each
section's key points in that section's segments, in order. Use its worked examples with
their exact numbers, its equations unchanged, and its source wording where given. Never add
facts, numbers or results that are not in the plan or in passages you looked up."""

TEACHER_VOICE = """VOICE: You are the learner's teacher, speaking to them directly in this video.
Speak in the first person ("I", "let me show you", "we can see") and address the learner as
"you", the way a teacher talks: use "I" where a teacher naturally would, and never open
sentences with filler such as "I see" or "I show you"; most sentences simply state the idea. Never talk about yourself, a narrator, a presenter or the video in the third person, and
never describe the learner as "the student" or "the viewer". If the learner asked for a
particular voice, person, tone or style, follow it exactly."""

NATURAL_ENDING = """ENDING: Before closing, recap the lesson's main ideas in a few sentences. Finish the way a good
teacher would end this particular conversation,
judging from what the learner asked. First complete the explanation; then close naturally. For
example, after walking through a paper you might sum up its main points and thank them or invite
questions; after answering a doubt or a follow-up you might check whether that answers their
question; after a new topic you might recap the key idea and invite them to ask if anything is
unclear. These are examples, not a template: choose and word the ending yourself so it fits the
ask. Do not tease new content or promise another video."""


PACING = """PACING AND FLOW: Teach unhurried, the way a good teacher talks a student through an
idea, not the way a summary lists facts. Give each idea room: say it, show why it is true, connect
it to something the learner already knows, then move on; fewer ideas explained well beat many
mentioned. Be creative: open with a situation or question that makes the learner curious, use
vivid concrete examples, ask the learner small questions, and vary the rhythm of the sentences.
Leave breathing space: where a teacher would really stop, after a question you put to the
learner, before revealing its answer, or after a key result, write a pause marker such as
[pause 2s] (1 to 4 seconds; never after every sentence). A pause counts toward its segment's time.
Every segment after the first opens with a short bridge from where the previous one left off
("So far we have seen ...; now ..."), and ends so the next one follows naturally."""


def _constrain_spoken_word_budget(text: str, target_seconds: float) -> str:
    """Keep a provider response feasible for its allocated narration slot.

    Text models sometimes return a polished paragraph twice the requested
    size.  That used to turn a one-minute lesson into a 90-second export even
    though the project setting was correct.  Retain complete sentences rather
    than cutting audio mid-thought; the TTS timing normalizer handles the small
    remaining rate difference.
    """
    budget = max(18, round(target_seconds * 2.0))
    words = text.split()
    if len(words) <= budget:
        return text.strip()
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chosen: list[str] = []
    for sentence in sentences:
        if chosen and len(" ".join([*chosen, sentence]).split()) > budget:
            break
        chosen.append(sentence)
    if chosen:
        return " ".join(chosen)
    return " ".join(words[:budget]).rstrip(".,;: ") + "."


RULES = """You are writing spoken narration for a STEM teaching video.
The learner may have asked only for a topic. Make the teaching choices they should not have to
specify: default to an interested high-school beginner unless the request names another level.
Explain the formal idea and work through a concrete example. Do not introduce or prescribe an
analogy unless the learner explicitly requests one. Connect every symbol to its meaning and units
when relevant. Preserve any audience, language, analogy, depth, voice, tone, or format the learner
explicitly requested. Do not pad the lesson by repeating generic definitions; each segment must teach a new
step. Prefer a progressively revealed mechanism, a toy worked example, and a misconception check
over a string of definitions. If the learner explicitly asks for an analogy, keep it accurate and
state where it stops matching the real system.
Write the canonical narration in English only, regardless of the requested output language: no
headers, no bullet points, no stage directions. Any language preference in the learner request is
presentation metadata and must not change this English source script.
State the actual physics — the definition, the equation, the reasoning, a concrete worked example —
using the supplied textbook facts as grounding. Teach the concept directly, as a teacher
explaining it out loud: do not spend sentences announcing what the lesson will cover ("in this
video we will explore...") or describing the screen layout.
This narration is read aloud by a speech engine and shown as subtitles, so write it as plain
spoken words only. Never use LaTeX or any math markup: no $...$, no backslash commands, no
^ or _ for powers and subscripts. Say "p equals q times two a", "one over r cubed",
"epsilon naught" — spell the mathematics out the way you would say it in a lecture.
If the learner asks for a named equation, theorem, or a specific number of laws, treat that scope
as mandatory: explain the requested items across the complete lesson, and include each visual
equation in the structured `equations` field using conventional STEM notation. Put only equations
that this segment teaches in that list. Keep their symbols, signs, indices, and constants canonical.
The narration must explain each listed equation and what its symbols mean in natural spoken words.
Write each equation as valid MathTex-ready LaTeX, with proper fractions and grouped indices.
Return the `narration` and `equations` fields; equations are visual notation and must not be spoken
as raw markup. In `key_terms`, list up to six technical STEM terms this segment names, exactly as
written in the narration (e.g. "photoelectric effect", "work function"); not people or places."""


def _key_terms(values: list[str], limit: int = 6) -> list[str]:
    """Distinct, non-empty key terms in the order the model gave them."""
    seen: dict[str, str] = {}
    for value in values:
        term = " ".join(value.split())
        if term and term.casefold() not in seen and len(term) <= 60:
            seen[term.casefold()] = term
    return list(seen.values())[:limit]


def _fallback_narration(
    item: CurriculumItem,
    *,
    target_words: int,
    is_closing: bool,
    segment_index: int | None = None,
) -> str:
    """Deterministic narration used verbatim by the mock provider, and offered to real
    providers as a baseline they are free to improve on using the grounding facts."""
    sentences = [
        f"{item.title}. {item.objective}",
        "Start with the core relationship, then connect each quantity to a concrete situation.",
        "Work through one small example and check that the result matches the physical intuition.",
    ]
    if is_closing:
        sentences.append(f"The key takeaway is the relationship that defines {item.title}.")

    # Keep deterministic fallbacks close to the provider word budget so TTS
    # needs only a modest, intelligibility-preserving rate adjustment.
    selected: list[str] = []
    for sentence in sentences:
        if selected and len(" ".join([*selected, sentence]).split()) > target_words:
            break
        selected.append(sentence)
    if not selected:
        selected.append(sentences[0])
    text = " ".join(selected)
    fillers = (
        "Use the equation to predict how changing one quantity changes the others.",
        "Use the equation to check how the physical quantities change together.",
    )
    for filler in fillers:
        if len(text.split()) + len(filler.split()) <= target_words:
            text = f"{text} {filler}"
    return text.strip()


def _build_prompt(
    item: CurriculumItem,
    evidence: list[dict],
    *,
    target_seconds: float,
    is_opening: bool,
    is_closing: bool,
    segment_index: int = 1,
    segment_count: int = 1,
    earlier_segments: list[str] | None = None,
    skill_context: str = "",
    learner_request: str = "",
    learner_level: str = "general",
) -> str:
    target_words = max(18, round(target_seconds * 2.0))
    facts = "\n".join(
        f"- ({card.get('kind', 'fact')}) {card.get('content', '')}" for card in evidence[:8]
    )
    position = (
        "This is the opening segment of the video."
        if is_opening
        else "This continues directly from the previous segment; do not re-introduce the topic."
    )
    closing = f"\n{NATURAL_ENDING}" if is_closing else ""
    teaching_moves = [
        "Start with the learner's concrete question and an observable physical setup. State the intuition this segment establishes without prescribing an analogy.",
        "Build the mechanism from that intuition. Define the important quantities and show how they interact; connect the mechanism to the exact formal relationship when one is supplied.",
        "Work one small example from explicit values to a result. Say each meaningful step and check the result against the intuition. Clearly identify invented toy values as an illustration, not as source data.",
        "Synthesize the idea and test a boundary, misconception, implication, or limitation. Use evidence for claims about a source; finish with what the learner can now explain.",
    ]
    move_index = min(segment_index - 1, len(teaching_moves) - 1)
    # A planned section says what it teaches; the generic moves are only for a bare outline.
    teaching_move = (
        "teach this section's planned points: " + "; ".join(item.key_points)
        if item.key_points
        else teaching_moves[move_index]
    )
    earlier = (
        "\n".join(f"- {text[:700]}" for text in (earlier_segments or [])[-3:])
        or "(none; this is the first segment)"
    )
    audience = (
        "an interested high-school beginner (assume no prior knowledge unless the request says otherwise)"
        if learner_level == "general"
        else learner_level
    )
    fallback = _fallback_narration(item, target_words=target_words, is_closing=is_closing)
    return (
        f"{RULES}\n{TEACHER_VOICE}\n{PACING}\n\n{skill_context}\n\n"
        f"Learner level: {audience}.\n"
        f"Learner's original request: {learner_request or '(a short request for this topic)'}\n"
        "STEM EQUATIONS: use exact notation from the retrieved evidence when available.\n"
        f"Segment subject: {item.title}\n"
        f"Learning objective: {item.objective}\n"
        f"Segment {segment_index} of {segment_count}. Distinct instructional purpose: {teaching_move}\n"
        "The segment must add a new step to the lesson. Do not repeat an earlier example or explanation;"
        " briefly refer back only when needed to make the new step understandable.\n"
        f"Earlier narration in this lesson (avoid repeating it):\n{earlier}\n"
        f"{position}{closing}\n"
        f"Target length: about {target_words} words.\n"
        f"Relevant textbook facts to ground the explanation in:\n{facts or '(none retrieved)'}\n\n"
        "Write a substantially better, more specific narration than the placeholder below, "
        "grounded in the facts above — do not just return it unchanged. Return a structured object "
        "with narration and equations fields.\n\n"
        f"TASK:{fallback}"
    )


def _chapter_plan(index: int, item: CurriculumItem) -> str:
    lines = [f"Chapter C{index:02d} {item.title}: {item.objective}"]
    lines += [f"  - {point}" for point in item.key_points]
    lines += [f"  equation: {equation}" for equation in item.equations]
    for plot in item.plots:
        values = "; ".join(
            f"{series.name}: "
            + ", ".join(
                f"{category}={value:g}"
                for category, value in zip(plot.categories, series.values, strict=False)
            )
            for series in plot.series
        )
        lines.append(f"  plot {plot.plot_id} ({plot.kind}, {plot.source}): {plot.title}. {values}")
    for figure in item.figures:
        lines.append(
            f"  figure {figure.label} (original image shown): {figure.caption}"
            + (f" Point at: {figure.focus}" if figure.focus else "")
        )
    return "\n".join(lines)


def _attach_unclaimed_plots(blocks: list[NarrationBlock], curriculum: CurriculumPlan) -> None:
    """A chapter plot no segment claimed goes to that chapter's last segment."""
    for chapter, item in enumerate(curriculum.items, 1):
        chapter_blocks = [block for block in blocks if block.chapter_id == f"C{chapter:02d}"]
        if not chapter_blocks:
            continue
        claimed = {plot.plot_id for block in chapter_blocks for plot in block.data_plots}
        for plot in item.plots:
            if plot.plot_id not in claimed:
                chapter_blocks[-1].data_plots.append(plot)
        # A figure nobody claimed is shown where the chapter starts explaining.
        shown = {figure.label for block in chapter_blocks for figure in block.paper_figures}
        for figure in item.figures:
            if figure.label not in shown:
                chapter_blocks[0].paper_figures.append(figure)


async def write_script(
    provider,
    curriculum: CurriculumPlan,
    dossier: dict,
    *,
    skill_context: str = "",
    learner_request: str = "",
    learner_level: str = "general",
    review_callback=None,
    source_scope: str = "none",
    lookup: Callable[[str, str], Awaitable[str]] | None = None,
) -> list[NarrationBlock]:
    """Outline -> per-segment LLM narration grounded in retrieved evidence.

    For a curated lesson the writer works from the curator's plan only;
    ``lookup(task, plan)`` lets it pull passages the plan left out.

    ``review_callback(round, review)`` is awaited after each script review so the
    learner can see why a draft was revised.
    """
    evidence = dossier.get("evidence", [])
    blocks: list[NarrationBlock] = []
    scene_counts = [max(1, math.ceil(item.duration_seconds / 45)) for item in curriculum.items]
    total_segments = sum(scene_counts)
    # Write with the whole lesson in view. Independent scene prompts otherwise
    # repeat introductions and lose the final conclusion.
    if getattr(provider, "name", "") != "mock":
        slots = [
            (chapter, item, item.duration_seconds / count)
            for chapter, (item, count) in enumerate(zip(curriculum.items, scene_counts), 1)
            for _ in range(count)
        ]
        # Older paper plans carry no kind; their chapters are recognised by their content.
        paper_lecture = curriculum.kind == "paper" or (
            curriculum.kind == "outline"
            and any(item.key_points or item.plots or item.equations for item in curriculum.items)
        )
        curated = curriculum.kind == "curated"
        outline = "\n".join(
            f"Segment {i}: {item.title}; {item.objective}; {seconds:.1f} seconds, "
            f"at most {max(18, round(seconds * 2))} spoken words."
            for i, (_, item, seconds) in enumerate(slots, 1)
        )
        plan_text = ""
        if paper_lecture or curated:
            plan_text = "\n".join(
                _chapter_plan(chapter, item) for chapter, item in enumerate(curriculum.items, 1)
            )
            outline += f"\n\n{'CHAPTER' if paper_lecture else 'LESSON'} PLAN:\n{plan_text}"
        looked_up = (
            await lookup("write the narration for this lesson", plan_text)
            if lookup is not None and curated
            else ""
        )
        plan_notes = (
            f"{CURATED_PLAN_RULES}\n"
            + (f"Kind of ask: {curriculum.ask_kind}\n" if curriculum.ask_kind else "")
            + (f"The curator suggests ending: {curriculum.ending}\n" if curriculum.ending else "")
            + (f"PASSAGES YOU LOOKED UP:\n{looked_up}\n" if looked_up else "")
            if curated
            else ""
        )
        prompt = (
            f"{RULES}\n{TEACHER_VOICE}\n{PACING}\n{NATURAL_ENDING}\n"
            f"{PAPER_LECTURE_RULES if paper_lecture else ''}{plan_notes}\n"
            f"{SOURCE_POLICIES.get(source_scope, '')}\n{skill_context}\n"
            f"Learner: {learner_request}\nLevel: {learner_level}\n"
            + ("" if curated else f"Grounding: {evidence}\n")
            + f"{outline}\n"
            f"Return exactly {len(slots)} ordered segments. Write one coherent lesson: "
            "Give every segment a concise title and a specific learning_objective covering only "
            "what that scene actually teaches, rather than copying the full lesson objective. "
            + (
                "Follow the plan's sections in order. "
                if curated or paper_lecture
                else "Introduce the problem, develop its mechanism, then resolve it with a "
                "meaningful takeaway. "
            )
            + "Each scene continues the previous one, with consistent symbols, numbers "
            "and example. Never announce a next scene unless that scene actually follows. "
            "The final segment must finish the explanation and then close naturally as ENDING "
            "describes, not with a teaser, a new topic, or an unfinished calculation. Respect "
            "every word budget."
        )

        def accept(
            review: ScriptReview, revision: int, draft: LessonScript
        ) -> list[NarrationBlock]:
            dossier["script_review"] = {
                **review.model_dump(),
                "revision_count": revision,
                "checks": [
                    "scope",
                    "mathematics",
                    "scene continuity",
                    "STEM speech",
                    "natural ending",
                ],
            }
            spoken = [speakable_with_pauses(s.narration, to_speakable) for s in draft.segments]
            blocks = [
                NarrationBlock(
                    block_id=f"N{i:02d}",
                    chapter_id=f"C{chapter:02d}",
                    text=spoken[i - 1][0],
                    pauses=spoken[i - 1][1],
                    title=segment.title,
                    learning_objective=segment.learning_objective or item.objective,
                    source_ids=item.source_ids,
                    estimated_duration_seconds=seconds,
                    defined_symbols={},
                    equations=segment.equations,
                    key_terms=_key_terms(segment.key_terms),
                    data_plots=[plot for plot in item.plots if plot.plot_id in segment.plot_ids],
                    paper_figures=[
                        figure
                        for figure in item.figures
                        if figure.label.casefold()
                        in {label.strip().casefold() for label in segment.figure_labels}
                    ],
                )
                for i, ((chapter, item, seconds), segment) in enumerate(
                    zip(slots, draft.segments, strict=True), 1
                )
            ]
            _attach_unclaimed_plots(blocks, curriculum)
            return blocks

        try:
            draft = await provider.generate_structured(prompt, LessonScript)
            review = ScriptReview(approved=False)
            polished = False
            for revision in range(SCRIPT_REVIEW_ROUNDS):
                if len(draft.segments) != len(slots) or any(
                    not s.narration.strip() for s in draft.segments
                ):
                    raise ValueError("Script does not cover all planned segments")
                review = await provider.generate_structured(
                    "Review this entire STEM lesson against the request and source evidence. "
                    "Judge it as a lesson of the stated length: every segment has a strict word "
                    "budget, so do not demand background or extra concepts that cannot fit; a "
                    "short lesson should teach the core idea well. Set blocking=true only for "
                    "wrong mathematics or facts, inconsistent worked values, a missing core idea "
                    "the learner asked about, symbols used but never explained, broken scene "
                    "transitions, or an ending that stops mid-thought, teases more content, or "
                    "never resolves the explanation. A closing that speaks to the learner (a "
                    "recap, thanks, inviting questions, asking whether it answered their "
                    "question) is good teaching, not a note about the video. Set blocking=true "
                    "when the narration talks about the teacher, a narrator or the video in the "
                    "third person instead of speaking as the teacher in the first person, or "
                    "ignores a voice or style the learner asked for. Also set blocking=true for "
                    "narration that talks about the "
                    "sources or how the lesson was made instead of teaching: whether a source "
                    "could be verified, found or accessed, what was searched, remarks such as "
                    "'as an AI', or notes to the viewer about the video itself. When a SOURCE "
                    "POLICY is given, set blocking=true for content it does not allow. "
                    "Narration must verbalize STEM notation naturally, never "
                    "read asterisks or LaTeX commands. Judge the flow as a student would hear it, "
                    "and set blocking=true when the lesson feels rushed or disjointed: a segment "
                    "that jumps to a new idea with no bridge from the previous one, several ideas "
                    "named in a row without any of them being explained, or no recap of the main "
                    "ideas before the ending. Pause markers such as [pause 2s] are intended "
                    "silences; report only ones that interrupt a sentence or appear after nearly "
                    "every sentence. List specific problems; approve when nothing blocking "
                    "remains.\n" + prompt + "\nSCRIPT:\n" + draft.model_dump_json(),
                    ScriptReview,
                )
                if review_callback is not None:
                    await review_callback(revision + 1, review)
                minor_only = not review.blocking
                # Minor notes get one polishing revision; after that they ship as notes.
                if not review.problems or (minor_only and polished):
                    return accept(review, revision, draft)
                if revision < SCRIPT_REVIEW_ROUNDS - 1:
                    polished = polished or minor_only
                    draft = await provider.generate_structured(
                        prompt
                        + "\nRevise this draft:\n"
                        + draft.model_dump_json()
                        + "\nFix these review problems within the word budgets:\n"
                        + "\n".join(review.problems),
                        LessonScript,
                    )
            if not review.blocking:
                # Only minor notes remain: keep the reviewed draft and surface the
                # notes; rendered scenes still pass their own verification gates.
                return accept(review, SCRIPT_REVIEW_ROUNDS - 1, draft)
            raise ValueError("Lesson script failed coherence review: " + "; ".join(review.problems))
        except (AttributeError, NotImplementedError):
            # Lightweight offline adapters may only implement per-segment text.
            pass
    for chapter_index, item in enumerate(curriculum.items, 1):
        scene_count = scene_counts[chapter_index - 1]
        duration = item.duration_seconds / scene_count
        for part in range(1, scene_count + 1):
            prompt = _build_prompt(
                item,
                evidence,
                target_seconds=duration,
                is_opening=not blocks,
                is_closing=(chapter_index == len(curriculum.items) and part == scene_count),
                segment_index=len(blocks) + 1,
                segment_count=total_segments,
                earlier_segments=[block.text for block in blocks],
                skill_context=skill_context,
                learner_request=learner_request,
                learner_level=learner_level,
            )
            # Models reach for LaTeX even when told not to, and this same string
            # is both spoken and subtitled, so normalise it rather than trust it.
            equations: list[str] = []
            key_terms: list[str] = []
            pauses = []
            if getattr(provider, "name", "") == "mock":
                text = _fallback_narration(
                    item,
                    target_words=max(20, round(duration * 2.1)),
                    is_closing=(chapter_index == len(curriculum.items) and part == scene_count),
                    segment_index=len(blocks) + 1,
                )
            else:
                try:
                    structured = await provider.generate_structured(prompt, ScriptSegment)
                    text, pauses = speakable_with_pauses(structured.narration.strip(), to_speakable)
                    equations = [value.strip() for value in structured.equations if value.strip()]
                    key_terms = _key_terms(structured.key_terms)
                except (AttributeError, NotImplementedError):
                    text = to_speakable((await provider.generate_text(prompt)).strip())
                except Exception as error:  # noqa: BLE001 - malformed structured output is recoverable
                    logger.warning(
                        "Structured narration failed for %s; using the local narration template: %s",
                        item.title,
                        error,
                    )
                    text = _fallback_narration(
                        item,
                        target_words=max(20, round(duration * 2.1)),
                        is_closing=(chapter_index == len(curriculum.items) and part == scene_count),
                        segment_index=len(blocks) + 1,
                    )
            if not text:
                text = f"{item.title}. {item.objective}"
            text = _constrain_spoken_word_budget(text, duration)
            kept = len(re.split(r"(?<=[.!?])\s+", text.strip()))
            blocks.append(
                NarrationBlock(
                    block_id=f"N{len(blocks) + 1:02d}",
                    chapter_id=f"C{chapter_index:02d}",
                    text=text,
                    learning_objective=item.objective,
                    source_ids=item.source_ids,
                    estimated_duration_seconds=duration,
                    defined_symbols={},
                    equations=equations,
                    key_terms=key_terms,
                    pauses=[pause for pause in pauses if pause.after_sentence < kept],
                )
            )
    _attach_unclaimed_plots(blocks, curriculum)
    for chapter, item in enumerate(curriculum.items, 1):
        # Offline segments carry their chapter's source equations.
        first = next((b for b in blocks if b.chapter_id == f"C{chapter:02d}"), None)
        if first is not None and item.equations and not first.equations:
            first.equations = list(item.equations)
    return blocks


class BridgeFix(BaseModel):
    scene: str
    fix: str


class TransitionReview(BaseModel):
    fixes: list[BridgeFix] = Field(default_factory=list)


async def write_new_scene(
    provider, instruction: str, *, before: str = "", after: str = "", seconds: float = 40
) -> ScriptSegment:
    """Narration for a scene inserted into a finished lesson, between two existing ones."""
    return await provider.generate_structured(
        f"{RULES}\n{TEACHER_VOICE}\n{PACING}\n\n"
        "A learner asked for a NEW scene to be added to their finished lesson. Write only that "
        f"scene: about {max(18, round(seconds * 2))} spoken words. It must follow on from the "
        "scene before it and lead into the scene after it, without repeating them.\n"
        f"WHAT THE NEW SCENE SHOULD TEACH: {instruction}\n"
        f"SCENE BEFORE IT: {before or '(none; this opens the lesson)'}\n"
        f"SCENE AFTER IT: {after or '(none; this ends the lesson, so close it naturally)'}\n"
        "Return the narration, a short title, a learning_objective, equations and key_terms.",
        ScriptSegment,
    )


async def review_transitions(
    provider, scenes: list[tuple[str, str]], boundaries: list[int]
) -> dict[str, str]:
    """Scenes whose opening no longer follows the scene before, with how to fix each.

    ``scenes`` are (id, narration) in their new order; ``boundaries`` are the
    indexes i where scene i follows scene i-1 after an edit and should be checked.
    """
    pairs = [index for index in sorted(set(boundaries)) if 0 < index < len(scenes)]
    if not pairs or getattr(provider, "name", "") == "mock":
        return {}
    listed = "\n\n".join(
        f"BOUNDARY {number}:\nSCENE {scenes[index - 1][0]} ENDS: ...{scenes[index - 1][1][-500:]}\n"
        f"SCENE {scenes[index][0]} BEGINS: {scenes[index][1][:500]}..."
        for number, index in enumerate(pairs, 1)
    )
    try:
        review = await provider.generate_structured(
            "A narrated lesson was just edited (scenes changed, added, removed or moved). For "
            "each boundary below, decide whether the second scene still follows naturally from "
            "the first as the learner hears them in a row. Report only real breaks: an opening "
            "that refers to something no longer said before it, a jump with no bridge, or a "
            "repeat of what was just said. For each, give scene (the second scene's id) and fix: "
            "a precise instruction to rewrite that scene's opening sentence or two so it bridges "
            "from the scene before. Return no fixes when every boundary flows.\n\n" + listed,
            TransitionReview,
        )
    except Exception as error:  # noqa: BLE001 - a missing check must not stop an edit
        logger.warning("Transition review unavailable: %s", error)
        return {}
    allowed = {scenes[index][0] for index in pairs}
    return {fix.scene: fix.fix for fix in review.fixes if fix.scene in allowed and fix.fix.strip()}


async def rewrite_opening(provider, opening: str, fix: str, *, before: str = "") -> str:
    """A new first sentence or two for a scene, bridging from the scene before; "" if unavailable.

    Only the opening is rewritten, so a bridge fix never changes, lengthens or
    re-explains the rest of the scene.
    """
    prompt = (
        f"{TEACHER_VOICE}\n\n"
        "A scene in a narrated lesson must now follow a different scene. Rewrite ONLY its "
        "opening (given below) so it bridges from the scene before. Keep what the opening "
        "teaches, keep roughly the same length (one or two sentences), and write natural "
        "spoken English with no markup. Return ONLY the new opening.\n\n"
        f"WHAT TO FIX: {fix}\n"
        f"THE SCENE BEFORE ENDS: ...{before[-400:]}\n"
        f"CURRENT OPENING: {opening}\n\nNEW OPENING:"
    )
    try:
        return (await provider.generate_text(prompt)).strip()
    except Exception as error:  # noqa: BLE001 - provider SDK failures vary by adapter
        logger.warning("Opening rewrite unavailable: %s", error)
        return ""


REVISION_RULES = """You are revising the spoken narration for one segment of a physics teaching video,
because a student said they did not understand this part.

Rewrite the narration so it addresses their request. Keep the same physics and the same learning
objective, but change how it is explained. Stay roughly the same length unless the request implies
otherwise, for example asking for a slower or more detailed explanation.

You are the learner's teacher: speak in the first person to "you", never about a narrator, a
presenter or the video in the third person.
A marker such as [pause 3s] is a silence the teacher leaves (for example before revealing an
answer). Keep the markers in the narration unless the request is about them, and add one where
the request asks for a pause, e.g. after each question and before its answer.
Write natural spoken English only. Never acknowledge the request, never say "as you asked" or
"let me explain again", and never mention that this is a revision. The student should simply hear a
better explanation. Never use LaTeX or math markup: no $...$, no backslash commands, no ^ or _.
Spell mathematics out the way you would say it aloud.
Return ONLY the revised narration text, nothing else."""


async def revise_narration(
    provider,
    narration: str,
    instruction: str,
    *,
    learning_objective: str = "",
    before: str = "",
    after: str = "",
) -> str:
    """Rewrite narration to satisfy a student's clarification request.

    The naive alternative -- appending the instruction to the narration -- makes the
    speech engine read the request itself out loud, so the learner hears
    "Edit requested: explain this more slowly" instead of a better explanation.
    """
    prompt = (
        f"{REVISION_RULES}\n\n"
        f"Learning objective: {learning_objective or '(unchanged)'}\n\n"
        f"STUDENT'S REQUEST:\n{instruction}\n\n"
        + (
            "The lesson around this scene (keep it connected: follow on from the first and "
            f"lead into the second):\nBEFORE: ...{before[-400:]}\nAFTER: {after[:400]}...\n\n"
            if before or after
            else ""
        )
        + f"CURRENT NARRATION:\n{narration}\n\n"
        "REVISED NARRATION:"
    )
    try:
        revised = (await provider.generate_text(prompt)).strip()
    except Exception as error:  # noqa: BLE001 - provider SDK failures vary by adapter
        logger.warning(
            "Narration revision model unavailable; using a local worked example: %s", error
        )
        revised = ""
    if not revised:
        return narration
    # Pause markers the reviser kept or added survive the speech clean-up.
    return with_markers(*speakable_with_pauses(revised, to_speakable))
