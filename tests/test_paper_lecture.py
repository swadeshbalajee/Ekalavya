"""Whole-paper lectures: outline, plots, chapter-wise narration and intent routing."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.agents.code_generator import generate_scene_code
from scholarmotion.agents.lesson_preparation import fallback_preparation
from scholarmotion.agents.paper_outline import (
    PaperChapter,
    PaperOutline,
    _clean,
    curriculum_from_outline,
    fallback_outline,
    lecture_minutes,
    outline_paper,
)
from scholarmotion.agents.scene_compiler import compile_scene_specs
from scholarmotion.agents.script_writer import write_script
from scholarmotion.agents.storyboard_agent import create_storyboard
from scholarmotion.manim_runtime.sandbox import SandboxRenderer
from scholarmotion.manim_runtime.templates import choose_template
from scholarmotion.media.ffmpeg import FFmpegAssembler
from scholarmotion.memory.correction_memory import CorrectionMemory
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.storage import LocalObjectStore
from scholarmotion.providers.llm.mock import MockLLMProvider
from scholarmotion.providers.llm.openai_compatible import OpenAICompatibleProvider
from scholarmotion.providers.tts.mock import MockTTSProvider
from scholarmotion.schemas import DataPlot, PlotSeries
from scholarmotion.services import ScholarMotionPipeline
from scholarmotion.services.orchestration import plan_narration_takes
from scholarmotion.skills import SkillService
from scholarmotion.skills.bootstrap import bootstrap_system_knowledge

BLEU = DataPlot(
    plot_id="P1",
    title="BLEU on WMT14 EN-DE",
    categories=["ConvS2S", "Transformer base", "Transformer big"],
    series=[PlotSeries(name="BLEU", values=[25.16, 27.3, 28.4])],
    source="Table 2",
)

OUTLINE = PaperOutline(
    title="Attention Is All You Need",
    authors=["Vaswani", "Shazeer"],
    one_sentence_summary="A sequence model built only from attention.",
    recommended_minutes=14,
    chapters=[
        PaperChapter(
            title="Why drop recurrence",
            objective="See why recurrence limits parallel training.",
            key_points=["RNNs process tokens sequentially", "Attention connects any two tokens"],
        ),
        PaperChapter(
            title="Scaled dot-product attention",
            objective="Compute attention step by step.",
            key_points=["Scale by the square root of d_k to keep softmax gradients useful"],
            equations=[
                r"\mathrm{Attention}(Q,K,V)=\mathrm{softmax}\left(\frac{QK^T}{\sqrt{d_k}}\right)V"
            ],
            weight=2,
        ),
        PaperChapter(
            title="Results",
            objective="Read the translation results.",
            key_points=["Transformer big reaches 28.4 BLEU"],
            plots=[BLEU],
        ),
    ],
)


def test_outline_cleaning_drops_inconsistent_plots_and_dedupes_ids():
    broken = DataPlot(
        plot_id="P1", title="bad", categories=["a", "b"], series=[PlotSeries(name="s", values=[1])]
    )
    outline = OUTLINE.model_copy(deep=True)
    outline.chapters[0].plots = [BLEU.model_copy(), broken]
    cleaned = _clean(outline)
    ids = [plot.plot_id for chapter in cleaned.chapters for plot in chapter.plots]
    assert len(ids) == 2 and len(set(ids)) == 2  # broken one dropped, duplicate P1 renamed


def test_lecture_length_prefers_the_students_request_within_bounds():
    assert lecture_minutes(OUTLINE, None) == 14
    assert lecture_minutes(OUTLINE, 8) == 8
    assert lecture_minutes(OUTLINE, 90) == 30
    assert lecture_minutes(OUTLINE, 1) == 6


def test_curriculum_follows_chapters_and_weights():
    plan = curriculum_from_outline(OUTLINE, minutes=12)
    assert [item.title for item in plan.items] == [c.title for c in OUTLINE.chapters]
    assert sum(item.duration_seconds for item in plan.items) == pytest.approx(720)
    assert plan.items[1].duration_seconds == pytest.approx(2 * plan.items[0].duration_seconds)
    assert plan.items[1].equations and plan.items[2].plots[0].plot_id == "P1"


@pytest.mark.asyncio
async def test_offline_script_carries_paper_equations_and_plots_to_scene_specs():
    plan = curriculum_from_outline(OUTLINE, minutes=6)
    blocks = await write_script(MockLLMProvider(), plan, {"evidence": []})
    specs = compile_scene_specs(blocks, create_storyboard(blocks))
    plotted = [spec for spec in specs if spec.data_plots]
    assert plotted and plotted[-1].data_plots[0].series[0].values == [25.16, 27.3, 28.4]
    assert "data_plot" in plotted[-1].tags
    assert any(r"\sqrt{d_k}" in eq for spec in specs for eq in spec.equations)


@pytest.mark.asyncio
async def test_plot_scene_uses_plot_template_and_exact_values_in_prompt():
    plan = curriculum_from_outline(OUTLINE, minutes=6)
    blocks = await write_script(MockLLMProvider(), plan, {"evidence": []})
    spec = next(s for s in compile_scene_specs(blocks, create_storyboard(blocks)) if s.data_plots)
    assert choose_template("anything", has_data_plot=True).name == "data_plot"
    seen = {}

    class Provider:
        name = "openai_compatible"

        async def generate_code(self, prompt, *, cached_prefix=None, purpose="code"):
            seen.update(prompt=prompt, prefix=cached_prefix)
            return "from manim import *\nclass SceneS01(Scene):\n    def construct(self):\n        self.wait(1)\n"

    await generate_scene_code(Provider(), spec, sentence_durations=[3.0])
    assert "WORKING TEMPLATE (data_plot" in seen["prefix"][1]
    assert "[25.16, 27.3, 28.4]" in seen["prompt"] and "Table 2" in seen["prompt"]


def test_narration_takes_respect_limit_and_prefer_chapter_breaks():
    chapters = ["C01", "C01", "C02", "C02", "C03"]
    texts = ["x" * 1500] * 5
    takes = plan_narration_takes(chapters, texts, 4500)
    assert takes == [[0, 1], [2, 3], [4]]
    assert plan_narration_takes(["C01"] * 3, ["short"] * 3, 4500) == [[0, 1, 2]]
    assert all(sum(len(texts[i]) for i in take) <= 4500 for take in takes)


def test_a_whole_paper_lecture_is_never_started_without_the_planner():
    offline = fallback_preparation(
        message="explain this paper to me",
        project_request="explain this paper to me",
        text_language="English",
        voice_language="English",
        attached_papers=["Attention Is All You Need"],
    )
    assert (offline.action, offline.lesson_scope) == ("answer", "topic")


@pytest.mark.asyncio
async def test_outline_reads_the_pdf_natively_when_supported(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")

    class Reader:
        name = "openai_compatible"

        async def generate_structured_from_pdf(self, prompt, data, schema, *, filename):
            assert data.startswith(b"%PDF") and filename == "paper.pdf"
            assert "STUDENT REQUEST: walk me through it" in prompt
            return OUTLINE

    outline = await outline_paper(
        Reader(), title="t", request="walk me through it", pdf_path=pdf, chunks=[]
    )
    assert outline.title == "Attention Is All You Need"


def test_fallback_outline_uses_section_headings_and_skips_references():
    chunks = [
        {"section": "1 Introduction", "content_type": "paragraph", "text": "Problem."},
        {"section": "3 Model", "content_type": "paragraph", "text": "Encoder and decoder."},
        {"section": "References", "content_type": "paragraph", "text": "[1] ..."},
    ]
    outline = fallback_outline("Paper", chunks)
    assert [chapter.title for chapter in outline.chapters] == ["1 Introduction", "3 Model"]


@pytest.mark.asyncio
async def test_pdf_request_sends_file_with_native_parser():
    provider = OpenAICompatibleProvider(
        api_key="test", model="anthropic/x", base_url="https://openrouter.ai/api/v1"
    )
    captured = {}

    async def create(**kwargs):
        captured.update(kwargs)
        message = SimpleNamespace(content="Here it is:\n" + OUTLINE.model_dump_json())
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None
        )

    provider.client = SimpleNamespace(
        base_url="https://openrouter.ai/api/v1",
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    outline = await provider.generate_structured_from_pdf("plan", b"%PDF", PaperOutline)
    file_part = captured["messages"][0]["content"][0]
    assert file_part["type"] == "file"
    assert file_part["file"]["file_data"].startswith("data:application/pdf;base64,")
    assert captured["extra_body"]["plugins"][0]["pdf"]["engine"] == "native"
    assert outline.chapters[2].plots[0].plot_id == "P1"


@pytest.mark.asyncio
async def test_mock_pipeline_builds_a_paper_lecture(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        await bootstrap_system_knowledge(session)
        await session.commit()
        pipeline = ScholarMotionPipeline(
            llm=MockLLMProvider(),
            code_llm=MockLLMProvider(),
            tts=MockTTSProvider(),
            embeddings=SimpleNamespace(),
            storage=LocalObjectStore(tmp_path),
            renderer=SandboxRenderer(manim_binary="__missing_manim__", mock_missing=True),
            assembler=FFmpegAssembler("__missing_ffmpeg__", mock_missing=True),
            correction_memory=CorrectionMemory(session),
            skill_service=SkillService(session),
        )
        result = await pipeline.build(
            "paper-lecture", "explain this paper", duration_minutes=6, paper_outline=OUTLINE
        )
        chapters = {scene.spec.chapter_id for scene in result.scenes}
        assert chapters == {"C01", "C02", "C03"}
        assert any(scene.spec.data_plots for scene in result.scenes)
        curriculum = json.loads(
            (tmp_path / "paper-lecture/artifacts/curriculum/v1.json").read_text(encoding="utf-8")
        )
        assert curriculum["topic"] == "Attention Is All You Need"
    await engine.dispose()


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        ("https://arxiv.org/abs/1706.03762", "https://arxiv.org/pdf/1706.03762"),
        ("arxiv.org/abs/1706.03762v7", "https://arxiv.org/pdf/1706.03762v7"),
        ("https://arxiv.org/pdf/1706.03762.pdf", "https://arxiv.org/pdf/1706.03762"),
        ("https://example.org/paper.pdf", "https://example.org/paper.pdf"),
    ],
)
def test_arxiv_landing_pages_resolve_to_pdf(link, expected):
    from scholarmotion.retrieval.paper_download import pdf_link

    assert pdf_link(link) == expected


def _tiny_pdf(path):
    """One page: a filled vector box (the figure) above a 'Figure 1:' caption."""
    stream = (
        b"0 0 1 rg 150 420 300 200 re f\n"
        b"BT /F1 12 Tf 150 400 Td (Figure 1: A blue box.) Tj ET\n"
        b"BT /F1 10 Tf 72 300 Td (Body text that is not part of the figure.) Tj ET\n"
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    path.write_bytes(bytes(out))


def test_figures_are_cropped_from_the_pdf_by_caption(tmp_path):
    from scholarmotion.retrieval.paper_figures import extract_figures, figure_label

    pdf = tmp_path / "paper.pdf"
    _tiny_pdf(pdf)
    crops = extract_figures(pdf, tmp_path / "figures")
    assert [crop.label for crop in crops] == ["Figure 1"]
    crop = crops[0]
    assert crop.caption.startswith("Figure 1: A blue box")
    # 300 x ~220 pt at 3x, i.e. the box plus padding, not the whole page.
    assert 850 < crop.width < 960 and 550 < crop.height < 700
    assert extract_figures(pdf, tmp_path / "none", labels={"Figure 2"}) == []
    assert figure_label("see fig. 1(b)") == "Figure 1"


def _figure_plan(tmp_path):
    from scholarmotion.agents.paper_outline import referenced_figures
    from scholarmotion.schemas import PaperFigure

    outline = OUTLINE.model_copy(deep=True)
    outline.chapters[1].show_figures = ["Figure 1"]
    outline.chapters[1].figure_notes = ["Figure 1(a): attention network, then the weighted sum"]
    assert referenced_figures(outline) == {"Figure 1"}
    image = tmp_path / "figure-1.png"
    image.write_bytes(b"png")
    outline.figure_crops = [
        PaperFigure(
            label="Figure 1",
            caption="Figure 1: A2T.",
            filename="figure-1.png",
            path=str(image),
            width=900,
            height=400,
        )
    ]
    return curriculum_from_outline(outline, minutes=6)


def test_outline_figures_reach_chapters_with_focus_notes(tmp_path):
    plan = _figure_plan(tmp_path)
    assert plan.items[1].figures[0].focus.startswith("Figure 1(a)")


@pytest.mark.asyncio
async def test_figure_scene_uses_figure_template_assets_and_sends_the_image(tmp_path):
    from scholarmotion.services.orchestration import _render_assets

    plan = _figure_plan(tmp_path)
    blocks = await write_script(MockLLMProvider(), plan, {"evidence": []})
    specs = compile_scene_specs(blocks, create_storyboard(blocks))
    spec = next(s for s in specs if s.paper_figures)
    assert "paper_figure" in spec.tags
    assert _render_assets(spec) == {"assets_dir": str(tmp_path)}
    seen = {}

    class Provider:
        name = "openai_compatible"

        async def generate_code(self, prompt, *, cached_prefix=None, purpose="code", images=None):
            seen.update(prompt=prompt, prefix=cached_prefix, images=images)
            return "from manim import *\nclass SceneS01(Scene):\n    def construct(self):\n        self.wait(1)\n"

    await generate_scene_code(Provider(), spec, sentence_durations=[3.0])
    assert "WORKING TEMPLATE (paper_figure" in seen["prefix"][1]
    assert "paper_figure('figure-1.png')" in seen["prompt"]
    assert seen["images"] == [b"png"]  # unreadable bytes are passed through unchanged


def _resumable_pipeline(tmp_path):
    from scholarmotion.services.orchestration import ScholarMotionPipeline as Pipeline

    pipeline = object.__new__(Pipeline)
    pipeline.storage = LocalObjectStore(tmp_path)
    pipeline.assembler = SimpleNamespace(probe_duration=lambda _path: 12.0)
    return pipeline


def test_resume_reuses_a_scene_that_passed_in_an_earlier_attempt(tmp_path):
    from scholarmotion.schemas import SceneSpec, TTSResult, VerificationReport, VisualBeat

    pipeline = _resumable_pipeline(tmp_path)
    spec = SceneSpec(
        scene_id="S03",
        chapter_id="C01",
        title="t",
        learning_objective="o",
        narration="Narration.",
        duration_target_seconds=10,
        visual_beats=[VisualBeat(beat_id="b", narration_segment="n", visual="v")],
    )
    base = "p/scenes/S03"
    pipeline._json(f"{base}/spec/v1.json", spec)
    wav = tmp_path / "p/scenes/S03/audio/v1.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    wav.write_bytes(b"wav")
    pipeline._json(
        f"{base}/audio/v1.json",
        TTSResult(audio_path=str(wav), duration_seconds=12, sentence_timings=[]),
    )
    pipeline._json(
        f"{base}/verification/v1.json", VerificationReport(scene_id="S03", passed=False, score=0.2)
    )
    pipeline._json(
        f"{base}/verification/v2.json", VerificationReport(scene_id="S03", passed=True, score=1)
    )
    video = tmp_path / "p/scenes/S03/render/v2/videos/scene/480p15/SceneS03.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"mp4")
    for version in (1, 2):
        pipeline.storage.put_bytes(f"{base}/code/v{version}.py", b"code", overwrite=True)
    scene = pipeline._resume_scene("p", spec)
    assert scene is not None and scene.render_version == 2 and scene.render_path == str(video)
    assert scene.code_version == 2 and scene.audio.duration_seconds == 12
    # A changed narration means the old render no longer matches.
    assert pipeline._resume_scene("p", spec.model_copy(update={"narration": "New."})) is None


def test_build_state_marks_unfinished_builds_resumable(tmp_path):
    pipeline = _resumable_pipeline(tmp_path)
    assert not pipeline.resumable("p", "explain this paper")
    pipeline._json(
        "p/artifacts/build_state.json", {"request": "explain this paper", "complete": False}
    )
    assert pipeline.resumable("p", "explain this paper")
    assert not pipeline.resumable("p", "a different request")
    pipeline._json(
        "p/artifacts/build_state.json", {"request": "explain this paper", "complete": True}
    )
    assert not pipeline.resumable("p", "explain this paper")


def test_render_errors_lead_with_the_exception_and_failing_line():
    from scholarmotion.manim_runtime.sandbox import _tail_error

    log = (
        "│ C:\tmp\\scene.py:49 in construct │\n"
        "│    48 │   │   boxes = VGroup()                  │\n"
        "│ >  49 │   │   node.set_y(boxes.get_center()[1][1]) │\n"
        "│ legacy entry noise │\n"
        "IndexError: invalid index to scalar variable.\n"
    )
    assert _tail_error(log).splitlines()[0] == (
        "IndexError: invalid index to scalar variable. | "
        "scene.py line 49: node.set_y(boxes.get_center()[1][1])"
    )
