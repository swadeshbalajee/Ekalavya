import ast
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.agents.code_generator import (
    _fallback_scene_code,
    normalize_manim_direction_constants,
)
from scholarmotion.agents.lesson_preparation import prepare_lesson
from scholarmotion.agents.script_writer import LessonScript, ScriptReview, write_script
from scholarmotion.api.service import _narration_profile
from scholarmotion.config import Settings
from scholarmotion.media.math_notation import canonical_to_tex
from scholarmotion.media.speech import to_speakable
from scholarmotion.persistence.models import Base, Project
from scholarmotion.providers.factory import create_tts_provider
from scholarmotion.schemas import CurriculumItem, CurriculumPlan, SceneSpec, TTSResult, VisualBeat
from scholarmotion.services.orchestration import ScholarMotionPipeline


@pytest.mark.asyncio
async def test_audio_budget_difference_does_not_generate_animation_twice(tmp_path):
    from scholarmotion.agents.manager import Manager
    from scholarmotion.manim_runtime.sandbox import RenderResult
    from scholarmotion.persistence.storage import LocalObjectStore
    from scholarmotion.providers.llm.mock import MockLLMProvider
    from scholarmotion.schemas import TimingCue

    class CodeProvider:
        name = "counting"
        calls = 0

        async def generate_code(self, prompt):
            self.calls += 1
            return "from manim import *\nclass SceneS01(Scene):\n def construct(self):\n  self.wait(20)\n"

    class Speech:
        async def synthesize(self, text, path, **kwargs):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"unit-test-audio")
            return TTSResult(
                audio_path=str(path),
                duration_seconds=20,
                sentence_timings=[TimingCue(text=text, start=0, end=20)],
            )

    class Skills:
        async def context_for(self, role):
            return ""

    pipeline = object.__new__(ScholarMotionPipeline)
    pipeline.storage = LocalObjectStore(tmp_path)
    pipeline.translator = None
    pipeline.progress_callback = None
    pipeline.code_llm = CodeProvider()
    pipeline.tts = Speech()
    pipeline.voice_language = "English"
    pipeline.skills = Skills()
    pipeline.manager = Manager()
    pipeline.vision = MockLLMProvider()
    pipeline.video_verifier = None
    pipeline.assembler = SimpleNamespace(probe_duration=lambda _: 20)
    pipeline.renderer = SimpleNamespace(
        render=lambda *_: RenderResult(
            success=True,
            video_path=str(tmp_path / "test.mp4"),
            log_path="",
            metadata_path="",
            keyframe_paths=[],
            duration_seconds=1,
            return_code=0,
        )
    )
    spec = SceneSpec(
        scene_id="S01",
        chapter_id="C01",
        title="Test",
        learning_objective="Explain a concept",
        narration="A clear explanation.",
        duration_target_seconds=30,
        visual_beats=[
            VisualBeat(beat_id="b", narration_segment="A clear explanation.", visual="Diagram")
        ],
    )
    result = await pipeline.produce_scene("test", spec, prefetched_corrections=[])
    assert result.report.passed and pipeline.code_llm.calls == 1


def test_long_spoken_phrase_gets_proportional_visual_time():
    from scholarmotion.media.audio import align_visual_beats

    narration = "The query matches all available keys. Then normalize."
    beats = [
        VisualBeat(
            beat_id="a",
            narration_segment="The query matches all available keys.",
            visual="Score row",
        ),
        VisualBeat(beat_id="b", narration_segment="Then normalize.", visual="Softmax row"),
    ]
    timed = align_visual_beats(narration, beats, 40)
    assert timed[0].start_seconds == 0 and timed[0].end_seconds == 30
    assert timed[1].start_seconds == 30 and timed[1].end_seconds == 40


def test_required_equation_accepts_split_tex_fraction_without_weakening_math_check():
    from scholarmotion.verification.math import verify_required_equations

    code = r"""equation = MathTex('S', '=', '{{QK^T}', '\\over', '\\sqrt{d_k}}')"""
    assert not verify_required_equations("S01", ["S = QK^T / sqrt(d_k)"], code)
    assert verify_required_equations("S01", ["S = QK^T / sqrt(d_v)"], code)
    assert verify_required_equations("S01", ["S = (a+b)/c"], "equation = MathTex('S=a+b/c')")
    assert verify_required_equations("S01", ["S = 1"], "equation = MathTex('S=1+')")


def test_causal_mask_definition_can_be_typeset_on_separate_lines_but_not_omit_a_branch():
    from scholarmotion.verification.math import verify_required_equations

    equation = "M_ij = 0 for j ≤ i; M_ij = -∞ for j > i"
    code = r"""a = MathTex('M_{ij}=0', '\\text{ for } j \\le i')
b = MathTex('M_{ij}=-\\infty', '\\text{ for } j > i')"""
    assert not verify_required_equations("S02", [equation], code)
    assert verify_required_equations("S02", [equation], code.splitlines()[0])


def test_nested_latex_fraction_does_not_leak_command_names_into_speech():
    spoken = to_speakable(r"\frac{QK^T}{\sqrt{d_k}}")
    assert spoken == "Q times K transpose over square root of d sub k"


def test_the_planners_topic_is_the_lesson_topic():
    from scholarmotion.agents.learner_profiler import profile_request
    from scholarmotion.agents.lesson_preparation import LessonPreparation

    profile = profile_request(
        "Produce a lesson about causal masking in attention. Scene two should explain why "
        "masked weights are zero.",
        preparation=LessonPreparation(topic="causal masking in attention"),
    )
    assert profile.topic == "causal masking in attention"


@pytest.mark.asyncio
async def test_pcm_tts_passes_voice_style_and_spoken_math_to_provider(tmp_path, monkeypatch):
    import json
    import wave

    import httpx

    from scholarmotion.providers.tts.openai_compatible import OpenAICompatibleTTSProvider

    original_client = httpx.AsyncClient
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, content=b"\x00\x00" * 24000)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )
    provider = OpenAICompatibleTTSProvider(
        api_key="test",
        model="google/test-tts",
        voice="Kore",
        response_format="pcm",
        instructions="Warm STEM tutor",
    )
    result = await provider.synthesize("Q*K^T", tmp_path / "speech.wav")
    assert captured["input"] == "Q times K transpose"
    assert captured["voice"] == "Kore"
    assert "Warm STEM tutor" in captured["instructions"]
    assert result.voice_id == "Kore" and result.duration_seconds == 1
    with wave.open(result.audio_path) as wav:
        assert wav.getframerate() == 24000 and wav.getnchannels() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "action"),
    [
        ("Let me watch each forbidden position disappear as you explain it.", "generate_video"),
        ("Why is the diagonal kept?", "answer"),
    ],
)
@pytest.mark.parametrize("status", ["CREATED", "GENERATING"])
async def test_tutor_uses_semantic_decision_without_keyword_overrides(
    message, action, status, monkeypatch
):
    from scholarmotion.api import tutor
    from scholarmotion.schemas import TutorChatRequest

    class SemanticProvider:
        name = "semantic-test"

        async def generate_structured(self, prompt, schema, **kwargs):
            return schema(
                action=action, canonical_message=message, canonical_request="Causal masking"
            )

        async def generate_text(self, prompt, **kwargs):
            return "The diagonal keeps the current position available."

    monkeypatch.setattr(tutor, "create_llm_provider", lambda _: SemanticProvider())
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine)() as session:
        project = Project(title="Attention", request="Original brief", mode="chat", status=status)
        session.add(project)
        await session.flush()
        settings = Settings(main_llm_provider="mock", translation_provider="mock")
        decision = await tutor.respond(
            session, settings, project, TutorChatRequest(message=message)
        )
        assert decision.action == (
            "wait" if status == "GENERATING" and action == "generate_video" else action
        )
        if action == "answer":
            assert project.request == "Original brief"
    await engine.dispose()


@pytest.mark.parametrize(
    ("notation", "spoken"),
    [
        ("Q*K", "Q times K"),
        ("QKᵀ", "Q times K transpose"),
        (r"QK^T / \sqrt{d_k}", "Q times K transpose divided by square root of d sub k"),
        ("Q * K", "Q times K"),
        ("−∞", "negative infinity"),
    ],
)
def test_stem_notation_is_spoken_as_mathematics(notation, spoken):
    assert to_speakable(notation) == spoken


def test_math_scripts_are_typeset_without_unicode_font_dependency():
    tex = canonical_to_tex("Attention(Q, K, V) = softmax(QKᵀ / √dₖ + M)V")
    assert r"^{\mathsf{T}}" in tex
    assert r"\sqrt{d_{k}}" in tex
    assert "ᵀ" not in tex and "ₖ" not in tex
    assert canonical_to_tex("10⁻⁵") == "10^{-5}"
    assert canonical_to_tex(tex) == tex
    mask = canonical_to_tex("M_ij = 0 for j ≤ i; M_ij = -∞ for j > i")
    assert "M_{ij}" in mask and r"\leq" in mask and r"\infty" in mask


def test_generated_text_equation_uses_trusted_typesetter():
    code = normalize_manim_direction_constants(
        "from manim import *\nclass SceneS01(Scene):\n"
        ' def construct(self):\n  self.add(Text("QKᵀ / √dₖ = S", font="Arial"))\n',
        latex_enabled=True,
    )
    tree = ast.parse(code)
    constructors = [
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    ]
    assert "STEMEquation" in constructors
    assert "Text" not in constructors
    assert "from scholarmotion.manim_runtime.components import STEMEquation" in code


def test_inline_math_does_not_send_tamil_labels_through_latin_tex():
    code = normalize_manim_direction_constants(
        "from manim import *\nclass SceneS01(Scene):\n def construct(self):\n"
        '  self.add(Text("மின்னழுத்தம் V = 12 V"))\n',
        latex_enabled=True,
    )
    assert "மின்னழுத்தம்" in code and "STEMEquation" not in code


def test_masked_attention_fallback_does_not_invent_river_bank_example():
    spec = SceneSpec(
        scene_id="S01",
        chapter_id="C01",
        title="Causal attention",
        learning_objective="Explain masking future positions",
        duration_target_seconds=30,
        narration="The causal mask prevents attention to future tokens.",
        visual_beats=[
            VisualBeat(beat_id="b1", narration_segment="mask", visual="mask future cells")
        ],
    )
    assert "river" not in _fallback_scene_code(spec, "SceneS01")


def test_fish_audio_requires_a_real_voice_instead_of_silently_choosing_one():
    with pytest.raises(ValueError, match="TTS_VOICE"):
        create_tts_provider(
            Settings(
                tts_provider="openai_compatible", tts_model="fish-audio/test", tts_voice="alloy"
            )
        )


@pytest.mark.asyncio
async def test_project_narrator_survives_global_settings_change():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine)() as session:
        project = Project(id=uuid4(), title="Test", request="Explain attention")
        session.add(project)
        await session.flush()
        first = await _narration_profile(
            session, project.id, Settings(tts_voice="Kore", tts_api_key="secret")
        )
        second = await _narration_profile(session, project.id, Settings(tts_voice="Puck"))
        assert first == second and second["tts_voice"] == "Kore"
        assert all("key" not in name for name in first)
    await engine.dispose()


@pytest.mark.asyncio
async def test_semantic_visual_intent_does_not_require_video_keyword():
    class Planner:
        async def generate_structured(self, prompt, schema, **kwargs):
            assert "WORKSPACE MODE: chat" in prompt
            return schema(
                action="generate_video",
                canonical_message="Show the causal mask in motion",
                canonical_request="Causal masking",
            )

    result = await prepare_lesson(
        Planner(),
        message="I would understand this if I could watch each future position disappear.",
        project_request="Causal masking",
        mode="chat",
        account_text_language="English",
        account_voice_language="English",
        conversation="",
        project_text_language="English",
        project_voice_language="English",
    )
    assert result.action == "generate_video"


@pytest.mark.asyncio
async def test_entire_script_is_reviewed_and_a_bad_ending_is_repaired():
    class Planner:
        draft_count = 0
        review_count = 0

        async def generate_structured(self, prompt, schema, **kwargs):
            if schema is LessonScript:
                self.draft_count += 1
                return schema(
                    segments=[
                        {
                            "narration": "The mask blocks future tokens. The result uses only the available context.",
                            "equations": [],
                        }
                    ]
                )
            assert schema is ScriptReview
            self.review_count += 1
            return schema(
                approved=self.review_count > 1,
                problems=[] if self.review_count > 1 else ["Missing closing takeaway"],
            )

    provider = Planner()
    blocks = await write_script(
        provider,
        CurriculumPlan(
            topic="Causal attention",
            items=[
                CurriculumItem(
                    title="Mask", objective="Explain the causal mask", duration_seconds=30
                ),
            ],
        ),
        {},
        learner_request="Explain causal masking in attention",
    )
    assert provider.draft_count == 2 and provider.review_count == 2
    assert "available context" in blocks[-1].text and "river" not in blocks[-1].text


def test_large_audio_tempo_change_is_not_used_to_force_duration(tmp_path):
    pipeline = object.__new__(ScholarMotionPipeline)
    audio = TTSResult(
        audio_path=str(Path(tmp_path) / "voice.wav"), duration_seconds=20, sentence_timings=[]
    )
    # No assembler is installed: this must return before attempting to retime.
    assert (
        pipeline._fit_narration_to_scene_budget(audio, target_seconds=40, artifact_version=1)
        is audio
    )
