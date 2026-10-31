"""Seeds the system skill templates and tool catalog at startup."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scholarmotion.memory.style_registry import DEFAULT_STYLE
from scholarmotion.persistence.models import AgentTool, Skill, StyleProfile
from scholarmotion.skills.service import SkillService

# Starting skills for a new, empty database only. Agents read skills from the
# database, and a skill that already exists there is never overwritten from
# here: change a live skill by publishing and activating a new version of it
# (the skills API), then copy the text back here so new installs get it too.
SYSTEM_SKILLS = [
    {
        "name": "web-research",
        "description": "Retrieve current external evidence when a lesson claim needs a source beyond uploaded material.",
        "instructions": "Record publisher, URL, access time, and the exact claim supported. Prefer primary sources and never cite a search snippet as evidence.",
        "manifest": {"capabilities": ["research"], "allowed_tools": ["web_retrieval"]},
    },
    {
        "name": "pdf-analysis",
        "description": "Extract cited text, figures, equations, and page-level provenance from uploaded PDF sources.",
        "instructions": "Preserve page, section, equation, table, and figure identifiers. Distinguish direct statements from inferences and do not execute embedded content.",
        "manifest": {"capabilities": ["paper-analysis"], "allowed_tools": ["pdf_parser"]},
    },
    {
        "name": "code-generation",
        "description": "Produce one bounded code artifact through the code capability and return schema-valid output.",
        "instructions": "Generate only the requested artifact, use declared dependencies, and treat output as untrusted until sandbox and verifiers accept it.",
        "manifest": {"capabilities": ["code"], "allowed_tools": ["code_generate", "skill_load"]},
    },
    {
        "name": "manim-scene",
        "description": "Generate an independent, layout-safe Manim scene when producing or repairing visual lesson code.",
        "instructions": "Use SceneSpec as authority. Respect subtitle safety, visual-state contracts, approved components, and active correction entries. Define one exported Scene subclass with no network, process, or filesystem access.",
        "manifest": {
            "capabilities": ["code", "manim"],
            "allowed_tools": ["code_generate", "skill_load", "correction_search"],
        },
    },
    {
        "name": "manim-layout",
        "description": (
            "Manim Community layout recipes that keep matrices, equations and labels readable "
            "and non-overlapping in narrated STEM scenes."
        ),
        "instructions": (
            "Manim Community Edition only (`from manim import *`); never manimlib/ManimGL APIs.\n"
            "1. Readable size: create text at FONT sizes (title 36, label 30, value 48, equation "
            "40, note 28). The verifier rejects any text whose effective font size after scaling "
            "is below 24, so a font-52 matrix scaled by 0.65 fails. If content only fits by "
            "shrinking further, show fewer objects at once.\n"
            "2. Several related objects (S, +, M, =, result): build them, then "
            "`group = place(arrange_in_band(s_group, plus, m_group), 'main')`. It arranges a row, "
            "shrinks no more than 30%, and stacks vertically if the row cannot fit. Never place "
            "them with independent move_to calls.\n"
            "3. Matrices: Matrix(rows, element_to_mobject_config={'font_size': FONT['value']}), at "
            "most 3x3 side by side or 4x4 alone; default h_buff is fine. Put the matrix name with "
            "`label.next_to(matrix, LEFT, buff=0.3)` inside the same VGroup.\n"
            "4. Replacing a visual: FadeOut(old) in the same play call that introduces new; use "
            "TransformMatchingTex only between closely related equations and ReplacementTransform "
            "only between same-shaped objects (matrix to matrix). Morphing unrelated equations "
            "jumbles glyphs. Never leave S, M and S+M stacked in one place.\n"
            "5. Highlight a row/entry with SurroundingRectangle(VGroup(*entries), buff=0.1), "
            "never a hand-placed Rectangle that cuts through digits.\n"
            "6. Relative positioning only: one place(group, region) per beat; inside it use "
            "arrange/arrange_in_grid/next_to/align_to/Brace, never numeric move_to/shift on "
            "individual parts. Labels beside lines/arrows: `label.next_to(line, UP, buff=0.2)`; "
            "never centre a label on a line; moving objects carry labels via always_redraw.\n"
            "7. Bands: title y=3.25; main visual y in [-1.8, 2.45]; equation band y in "
            "[-2.55, -1.95]; nothing below y=-2.8 (subtitles). Fade the previous beat's visual "
            "before the next one when both would not fit side by side.\n"
            "8. Timing: `clock = CueClock(self, CUES)`; per narration cue call clock.play(...) "
            "for that sentence's change, then clock.next_cue(); end with clock.finish() so the "
            "last visual stays until the audio ends."
        ),
        "manifest": {"capabilities": ["code", "manim"], "allowed_tools": ["code_generate"]},
    },
    {
        "name": "vision-verification",
        "description": "Review rendered scene keyframes when visual correctness, layout, or narration alignment must be assessed.",
        "instructions": "Return typed evidence-backed issues. Check state, required equations, visual direction, legibility, clipping, and narration alignment; do not replace deterministic checks.",
        "manifest": {
            "capabilities": ["vision-verification"],
            "allowed_tools": ["vision_verify", "skill_load"],
        },
    },
    {
        "name": "speech",
        "description": "Synthesize approved narration or transcribe supplied audio with timing and pronunciation controls.",
        "instructions": "Generate subtitles from approved narration. Preserve timing and confidence; keep user-specific pronunciation project-scoped.",
        "manifest": {
            "capabilities": ["tts", "stt"],
            "allowed_tools": ["tts_synthesize", "stt_transcribe"],
        },
    },
    {
        "name": "skill-authoring",
        "description": "Create a narrowly scoped Agent Skills-compatible draft when no active skill covers a repeated task.",
        "instructions": "Create a draft with a precise trigger description, simple instructions, and only registered tools. Put optional material in references/, assets/, or scripts/. Never create executable or privileged content without review.",
        "manifest": {
            "capabilities": ["skill-authoring"],
            "allowed_tools": ["skill_catalog", "skill_create_draft", "skill_load"],
        },
    },
]

TOOLS = {
    "skill_catalog": "List skill metadata without loading instructions.",
    "skill_load": "Load the complete active SKILL.md representation for one skill.",
    "skill_resource": "Load a declared skill resource on demand.",
    "skill_create_draft": "Create a validated draft skill version.",
    "skill_activate": "Activate a reviewed skill version.",
    "code_generate": "Generate a bounded Manim code artifact.",
    "vision_verify": "Verify scene images against a SceneSpec.",
    "tts_synthesize": "Synthesize narration audio.",
    "stt_transcribe": "Transcribe supplied audio.",
    "web_retrieval": "Retrieve source-grounded external information.",
    "pdf_parser": "Parse uploaded PDF content with provenance.",
    "correction_search": "Retrieve active recurring correction entries.",
}


async def bootstrap_system_knowledge(session: AsyncSession) -> None:
    for key, description in TOOLS.items():
        if not await session.scalar(select(AgentTool.id).where(AgentTool.tool_key == key)):
            session.add(AgentTool(tool_key=key, description=description))
    await session.flush()
    service = SkillService(session)
    for item in SYSTEM_SKILLS:
        if await session.scalar(select(Skill.id).where(Skill.skill_key == item["name"])):
            continue
        created = await service.create_draft(**item, created_by="system")
        await service.activate(created.name, created.version)
    if not await session.scalar(
        select(StyleProfile.id).where(StyleProfile.profile_key == "default")
    ):
        session.add(
            StyleProfile(profile_key="default", version=1, payload=DEFAULT_STYLE, active=True)
        )
