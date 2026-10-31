"""Step zero: decide what a learner's chat turn asks for, using the planner model.

Every interpretation of the learner's words (intent, language, scenes, time
ranges, duration, level) is a model decision. Code only validates the result
against facts it owns: supported languages, the video's real scenes and length.
When the model is unavailable the learner gets a plain answer with their saved
defaults; nothing is guessed from keywords.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SUPPORTED_LANGUAGES = (
    "Arabic",
    "Bengali",
    "English",
    "French",
    "Hindi",
    "Japanese",
    "Marathi",
    "Spanish",
    "Tamil",
    "Telugu",
)
MAX_LESSON_MINUTES = 30.0


class SceneEdit(BaseModel):
    """One operation of a video edit, on one scene of VIDEO SCENES."""

    op: Literal["change", "insert_after", "delete", "split", "merge_with_next", "move_after"] = (
        "change"
    )
    # The scene's id; for insert_after, the scene the new one follows ("" = at the start).
    scene: str = ""
    # For move_after: the scene to place it after ("" = at the start).
    after: str = ""
    # What to change, or what a new scene teaches; each scene can have its own.
    instruction: str = ""
    # For change: true when only the picture changes and every spoken word stays.
    visuals_only: bool = False


class LessonPreparation(BaseModel):
    """A versionable contract between chat, retrieval, editing and lesson generation."""

    action: Literal["answer", "generate_video", "edit_video", "localize_video", "restore_video"] = (
        "answer"
    )
    # whole_paper: a long lecture covering one attached paper end to end.
    lesson_scope: Literal["topic", "whole_paper"] = "topic"
    # For a whole-paper lecture: the exact title, from ATTACHED PAPERS, of the paper meant.
    paper: str = ""
    canonical_message: str = ""
    canonical_request: str = ""
    input_language: str = "English"
    # The video's on-screen text and narration languages.
    text_language: str = "English"
    voice_language: str = "English"
    # Set only when the learner asks the chat itself to use a language ("talk to me
    # in Tamil"); empty keeps the chat's current language.
    chat_language: str = ""
    retrieval_query: str = ""
    # Links (from the latest turn or earlier in the chat) the learner wants used as sources.
    source_links: list[str] = Field(default_factory=list)
    # How a lesson based on given sources may use outside material:
    # none = not based on given sources; not_said = based on them, preference unknown.
    source_scope: Literal["none", "not_said", "only_given", "given_plus_web"] = "none"
    # Video edits: which scenes (ids from VIDEO SCENES) or which time range, and what to change.
    target_scenes: list[str] = Field(default_factory=list)
    # The change or language switch applies to the whole video.
    whole_video: bool = False
    # What the planner says to the learner: a confirmation of the action, or a
    # clarifying question when it chose to ask instead of act.
    reply: str = ""
    # Which video the learner means when the chat has several (VIDEO VERSIONS); empty = current.
    target_video_version: int | None = None
    edit_start_seconds: float | None = None
    edit_end_seconds: float | None = None
    edit_instruction: str = ""
    # Video edits as per-scene operations (change, insert, delete, split, merge, move).
    edits: list[SceneEdit] = Field(default_factory=list)
    # Words the narrator says wrongly, mapped to a spelling the speech engine says right.
    pronunciations: dict[str, str] = Field(default_factory=dict)
    # Learner profile for a new lesson.
    duration_minutes: float | None = None
    topic: str = ""
    learner_level: str = "general"
    request_type: Literal[
        "concept_explanation",
        "equation_explanation",
        "figure_explanation",
        "paper_explanation",
        "paper_section_explanation",
        "comparison",
    ] = "concept_explanation"
    known_concepts: list[str] = Field(default_factory=list)
    unknown_concepts: list[str] = Field(default_factory=list)
    preferred_examples: list[str] = Field(default_factory=list)
    accessibility_needs: list[str] = Field(default_factory=list)
    content_constraints: list[str] = Field(default_factory=list)
    parental_guidance: list[str] = Field(default_factory=list)
    rationale: str = ""
    # False when the planner was unavailable and these are plain defaults.
    planned: bool = True


def _language(value: str | None, fallback: str) -> str:
    normalized = (value or "").strip().casefold()
    return next((item for item in SUPPORTED_LANGUAGES if item.casefold() == normalized), fallback)


def fallback_preparation(
    *,
    message: str,
    project_request: str,
    text_language: str,
    voice_language: str,
    input_language: str = "English",
    **_unused,
) -> LessonPreparation:
    """The planner is unavailable: answer in text with the learner's saved languages.

    No intent is inferred here. Starting or changing a video is only ever the
    planner's decision, so an outage can never trigger an unwanted render.
    """
    return LessonPreparation(
        action="answer",
        canonical_message=message.strip(),
        canonical_request=project_request.strip() or message.strip(),
        input_language=_language(input_language, "English"),
        text_language=_language(text_language, "English"),
        voice_language=_language(voice_language, "English"),
        retrieval_query=message.strip(),
        rationale="The planning model was unavailable, so this turn is answered in text.",
        planned=False,
    )


def _version_lines(video_versions: list[dict] | None) -> str:
    if not video_versions:
        return "(none)"
    return "\n".join(
        f"- version {item['version']}{' (current)' if item.get('current') else ''}: "
        f"language {item.get('language') or 'not recorded'}; made after the learner said "
        f"{item.get('after') or '(unknown)'!r}"
        for item in video_versions
    )


def _scene_lines(video_scenes: list[dict] | None) -> str:
    if not video_scenes:
        return "(no finished video in this conversation)"
    lines = []
    for index, scene in enumerate(video_scenes, 1):
        start, end = float(scene["start"]), float(scene["end"])
        lines.append(
            f"- id {scene['id']}: scene {index}, {start:.1f}s to {end:.1f}s, "
            f"title {scene.get('title', '')!r}"
        )
    return "\n".join(lines)


async def prepare_lesson(
    llm,
    *,
    message: str,
    project_request: str,
    conversation: str,
    account_text_language: str,
    account_voice_language: str,
    project_text_language: str,
    project_voice_language: str,
    learner_context: dict | None = None,
    mode: str = "both",
    attached_papers: list[str] | None = None,
    attached_sources: list[str] | None = None,
    video_scenes: list[dict] | None = None,
    video_versions: list[dict] | None = None,
    input_language_hint: str | None = None,
    video_status: str = "no video yet",
    chat_language: str = "English",
) -> LessonPreparation:
    """Let the planner model decide what this turn asks for, then validate it."""
    fallback = fallback_preparation(
        message=message,
        project_request=project_request,
        text_language=account_text_language or project_text_language,
        voice_language=account_voice_language or project_voice_language,
        input_language=input_language_hint or "English",
    )
    if getattr(llm, "name", "") == "mock":
        return fallback

    context = learner_context or {}
    prompt = f"""You are the planning agent for an educational chat and video tutor. Decide what the
learner's latest turn asks for. Read meaning, not keywords: handle any wording, typos, informal
student language and any input language.

Return these decisions:
- action, exactly one of:
  * answer: reply in the chat. A plain request to explain a concept ('Explain QKV attention',
    'whats the formula for kinetic energy', 'why is the sky blue?') or any question, including a
    question ABOUT the existing video ('why does scene 2 show two skaters?').
  * generate_video: make a NEW narrated video lesson. Only when the learner asks, in any wording or
    language, to see, watch, visualize or animate something, or for a narrated walkthrough. The word
    'video' is not required ('help me picture how backprop flows', 'I learn better by seeing').
    Never treat an old request in the conversation as a new one. When unsure, prefer answer.
  * edit_video: change the content of the EXISTING video (see VIDEO SCENES): 'make scene 3
    simpler', 'redo the last scene', '0:31 to 1:02 use a smaller example', 'the part about torque
    is too fast'. Only possible when VIDEO SCENES lists scenes.
  * localize_video: change the language of the existing video or of some of its scenes: 'make it
    Tamil', 'explain scene 2 in Hindi', 'change only scene 2 to Tamil'.
  * restore_video: go back to an earlier video ('undo that', 'go back to the previous version',
    'I liked the first one better'). Set target_video_version to the version meant; for 'undo',
    the version just before the current one in VIDEO VERSIONS.
- target_scenes: for edit_video and localize_video, the ids from VIDEO SCENES the learner means
  (by number, position such as 'the last one', title, content or time). Use an empty list when
  the request is about the whole video or is given only as a time range.
- whole_video: true when an edit_video or localize_video request is about the entire video
  ('edit the whole video', 'redo everything', 'make it all Tamil').
- reply: what you say to the learner, in English, in one or two sentences.
  * For generate_video, edit_video and localize_video: confirm what will happen (which scenes or
    the whole video, the change, the language, and for a new video its length), taking VIDEO
    STATUS into account: while a video is still being produced, say the change can be made once
    it is ready.
  * When you cannot tell what the learner wants changed, or a lesson from given material has
    source_scope not_said, choose action answer and ask a short clarifying question here.
  * Leave it empty for any other answer; the tutor writes that reply.
- target_video_version: for edit_video and localize_video, the version from VIDEO VERSIONS the
  learner means when it is not the current one ('the previous video', 'the English one', 'the
  first version'); empty for the current video. VIDEO SCENES describes the current video; an
  earlier version has the same scenes in the same order.
- edit_start_seconds / edit_end_seconds: for edit_video given as a time range (convert '1:05' to
  65). Leave empty when target_scenes identifies the part.
- edit_instruction: for edit_video, the requested change as a clear English instruction.
- edits: for edit_video, the changes as a list, one entry per scene and operation, in the order
  the learner gave them. Each entry has op, scene (an id from VIDEO SCENES), after and
  instruction:
  * change: rebuild that scene following its own instruction (its narration, its visuals or
    both). Different scenes can get different instructions, and scenes in between stay as they
    are ('simplify scene 2 and add pauses before the answers in scene 5' is two entries).
    Set visuals_only true when the request is only about what is shown (the animation, a
    diagram, colours, layout, a missing picture) or says to keep the narration; the spoken
    words and audio then stay exactly as they are.
  * insert_after: add a NEW scene after scene ('' = at the very start); instruction says what
    it teaches.
  * delete: remove the scene.
  * split: split the scene in two; instruction may say where or what each part covers.
  * merge_with_next: combine the scene with the one after it.
  * move_after: move the scene to just after the scene named in after ('' = to the start).
  Leave edits empty for a change to the whole video (use whole_video and edit_instruction) or a
  change given only as a time range.
- pronunciations: for edit_video when the learner's LATEST message says the narrator says a
  word wrongly (never carry one over from earlier messages, it was already applied): the
  word as written mapped to a spelling the speech engine will say correctly ('Euler': 'Oiler').
  Subtitles keep the original word. Do not add change entries for it: the scenes whose
  narration says the word are found and rebuilt automatically.
- LARGE EDIT CHECK: when an edit_video would rebuild 5 or more scenes, or the whole video, and the
  learner has not already confirmed it, choose action answer, keep the edit fields, and in reply
  say how many scenes will be rebuilt with the time and cost estimate from VIDEO STATUS, then ask
  whether to go ahead. When they confirm, choose edit_video with the same changes.
- lesson_scope: whole_paper when a paper is attached (see ATTACHED PAPERS) and the learner wants
  that paper itself explained as a video lecture ('explain this paper', 'walk me through the
  pdf'). A whole-paper lecture is always generate_video. Otherwise topic; a written summary of
  the paper is answer with scope topic.
- paper: for a whole-paper lecture, the exact title from ATTACHED PAPERS of the paper the learner
  means; empty when only one paper is attached or it is unclear.
- source_links: the http(s) links, copied exactly from the conversation, whose content the
  learner wants used for this reply or video ('take this blog and make a video', 'summarize this
  page', or a link pasted earlier for the lesson being requested now). Empty otherwise.
- source_scope: whether a lesson based on given material (source_links or ATTACHED SOURCES) may
  use outside material:
  * none: the request is not based on any given link or attached source.
  * only_given: the learner wants it made only from their source(s) ('only from the blog',
    'just use the pdf', 'nothing else').
  * given_plus_web: the learner allows outside sources too ('sure, add more', 'use the web as
    well').
  * not_said: it is based on given material but the learner has not said. Look at the whole
    conversation: a preference stated earlier still applies, and a reply to the question 'only
    this source, or add outside sources?' is that preference. When the reply answers that
    question, keep the original lesson request (action, canonical_request, source_links) from the
    earlier turn.
- canonical_message: the latest learner turn translated into concise canonical English.
- canonical_request: for generate_video, a concise accurate English lesson brief from the latest
  turn and relevant context. Keep topic and scope, and keep every instruction about how to teach
  it (voice, person, tone, style, level, analogies, what to emphasise). Say what kind of ask it is
  when the conversation shows it, for example a doubt or follow-up on an earlier explanation, a
  walkthrough of a paper, or a new topic. Leave out output-language instructions (those belong in
  text_language/voice_language) and duration instructions; do not invent facts.
- input_language: the language the learner wrote in.
- text_language and voice_language: the language(s) of the VIDEO (on-screen text and
  narration), never of the chat. For edit_video, the language of the video being edited (see
  VIDEO VERSIONS). Take an explicit request in the latest turn first (for localize_video, the
  requested language), then a preference stated earlier in the conversation ('same language as
  before'), then the account defaults. Text and voice can differ.
- chat_language: the language the chat replies in (see CHAT LANGUAGE). Set it only when the
  latest turn asks the chat itself to use a language ('talk to me in Tamil', 'reply in
  English from now on'); otherwise leave it empty and the chat keeps its language. Asking for a
  video in a language is not such a request, and neither is the language the learner happens
  to write in. Write the reply field in English; it is translated into the chat language.
- duration_minutes: for generate_video, in minutes (1 to {MAX_LESSON_MINUTES:g}). The length the
  learner asked for, or one they agreed to earlier in the conversation. Otherwise choose the
  length this request needs to be taught well and unhurried: a quick doubt or one formula 1-3, a
  typical concept 4-8, a broad topic with several ideas 8-15, a whole chapter 15-30. Empty for a
  whole-paper lecture with no length given (its outline sets the length).
- LENGTH CHECK: when you would start a topic video whose length you chose yourself, and the
  learner has neither given a length, agreed to one you proposed, nor told you to decide, choose
  action answer instead, keep the lesson fields (canonical_request, topic, duration_minutes, ...)
  and in reply propose the length and what it will cover, then ask whether to start or make it
  shorter or longer ('I would make this about 7 minutes: what X is, why Y, and a worked
  example. Shall I start, or would you like it shorter or longer?'). When the learner answers
  that question ('yes', 'go ahead', 'make it 5'), choose generate_video with the original lesson
  request and the agreed length.
- topic: for generate_video, the subject in a few words ('Newton's third law').
- learner_level: e.g. 'NCERT Class 9', 'high school', 'college', or 'general' when not stated.
- request_type: concept_explanation, equation_explanation, figure_explanation,
  paper_explanation, paper_section_explanation or comparison.
- retrieval_query: a focused search query for attached or library sources.
- known_concepts, unknown_concepts, preferred_examples, accessibility_needs,
  content_constraints, parental_guidance: only what the context supports; never guess.
- rationale: one short sentence explaining the decision.

Supported text and narration languages: {", ".join(SUPPORTED_LANGUAGES)}.

ACCOUNT DEFAULTS (use when the learner gives no preference):
text={account_text_language!r}; voice={account_voice_language!r}
CURRENT PROJECT SETTINGS (the video's languages):
text={project_text_language!r}; voice={project_voice_language!r}
CHAT LANGUAGE (what the chat replies in now): {chat_language!r}
SAVED LEARNER CONTEXT (empty means none has been configured):
{context}
RECENT CONVERSATION (oldest first):
{conversation or "(none)"}
VIDEO STATUS: {video_status}
VIDEO SCENES (the finished video this conversation currently shows):
{_scene_lines(video_scenes)}
VIDEO VERSIONS (every video made in this conversation, oldest first):
{_version_lines(video_versions)}
CURRENT LEARNER TURN:
{message}
EXISTING CANONICAL PROJECT REQUEST:
{project_request}
WORKSPACE MODE: {mode}
ATTACHED PAPERS: {", ".join(attached_papers or []) or "(none)"}
ATTACHED SOURCES (papers and web pages already read into this chat):
{", ".join(attached_sources or []) or "(none)"}
"""
    try:
        result = await llm.generate_structured(prompt, LessonPreparation)
    except Exception:  # noqa: BLE001 - an outage must leave the safe text-answer path usable
        return fallback
    result = _validated(
        result, fallback, video_scenes or [], attached_papers or [], video_versions or []
    )
    return _validated_sources(
        result, conversation + "\n" + message, bool(attached_sources or attached_papers)
    )


def _validated_sources(
    result: LessonPreparation, transcript: str, has_attached: bool
) -> LessonPreparation:
    """Links must really appear in the chat; a scope needs given material to apply to."""
    links = []
    for link in result.source_links:
        link = link.strip().rstrip(".,;:)]}>\"'")
        if link.startswith(("https://", "http://")) and link in transcript and link not in links:
            links.append(link)
    result.source_links = links[:5]
    if not links and not has_attached:
        result.source_scope = "none"
    elif links and result.source_scope == "none":
        # The learner pointed at this material, so its use is not "none".
        result.source_scope = "not_said"
    return result


def _validated(
    result: LessonPreparation,
    fallback: LessonPreparation,
    video_scenes: list[dict],
    attached_papers: list[str],
    video_versions: list[dict] | None = None,
) -> LessonPreparation:
    """Keep the planner's decision, constrained to what this conversation can actually do."""
    result.text_language = _language(result.text_language, fallback.text_language)
    result.voice_language = _language(result.voice_language, fallback.voice_language)
    result.input_language = _language(result.input_language, fallback.input_language)
    # An unsupported or empty choice keeps the chat's current language.
    result.chat_language = _language(result.chat_language, "")
    result.canonical_message = result.canonical_message.strip() or fallback.canonical_message
    result.canonical_request = result.canonical_request.strip() or fallback.canonical_request
    result.retrieval_query = result.retrieval_query.strip() or fallback.retrieval_query
    known = {str(scene["id"]) for scene in video_scenes}
    result.target_scenes = [item for item in result.target_scenes if item in known]
    # An operation must name real scenes; only an insert or move may point at the start.
    result.edits = [
        edit
        for edit in result.edits
        if (edit.scene in known or (edit.op == "insert_after" and not edit.scene))
        and (edit.op != "move_after" or not edit.after or edit.after in known)
        and (edit.op != "insert_after" or edit.instruction.strip())
    ]
    result.pronunciations = {
        word.strip(): spoken.strip()
        for word, spoken in result.pronunciations.items()
        if word.strip() and spoken.strip() and word.strip() != spoken.strip()
    }
    result.reply = result.reply.strip()
    if result.action in {"edit_video", "localize_video"} and not video_scenes:
        result.action = "answer"
    if result.duration_minutes is not None:
        result.duration_minutes = min(max(result.duration_minutes, 1.0), MAX_LESSON_MINUTES)
    earlier = {item["version"] for item in video_versions or [] if not item.get("current")}
    if result.target_video_version not in earlier:
        result.target_video_version = None
    if result.action == "restore_video" and result.target_video_version is None:
        # Nothing earlier to go back to (or no such version): answer instead.
        result.action = "answer"
    if result.paper not in attached_papers:
        result.paper = ""
    if not attached_papers:
        result.lesson_scope = "topic"
    elif result.lesson_scope == "whole_paper" and result.action in {"answer", "generate_video"}:
        # A request for the paper's lecture makes one; a change to the lecture
        # already made (edit, language, earlier version) stays that change.
        result.action = "generate_video"
    return result
