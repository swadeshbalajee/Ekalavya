"""Regression tests for the narration, voice, glyph, progress and intent fixes."""

from __future__ import annotations

import wave

import numpy as np
import pytest

from scholarmotion.agents.lesson_preparation import fallback_preparation
from scholarmotion.manim_runtime.sandbox import _GLYPH_GUARD
from scholarmotion.media.narration import find_silences, plan_cuts, split_lesson_wav
from scholarmotion.media.speech import to_speakable


@pytest.mark.parametrize(
    ("written", "spoken"),
    [
        ("Q*K", "Q times K"),
        ("softmax(QK^T/√d_k)", "softmax(Q times K transpose divided by square root of d sub k)"),
        ("dy/dx", "d y by d x"),
        ("x ≤ y", "x is less than or equal to y"),
        ("a - b", "a minus b"),
        ("θ ≈ 0.5", "theta is approximately 0.5"),
        ("10 m/s", "10 meters per second"),
        ("W^{-1}", "W inverse"),
        ("x^n", "x to the power n"),
        ("M_ij", "M sub i j"),
        ("ε₀", "epsilon naught"),
        ("**Key idea**: F = ma", "Key idea: F equals ma"),
        ("λ = 500 nm", "lambda equals 500 nm"),
        ("Σ x_i", "sigma x sub i"),
        ("50%", "50 percent"),
        ("KE = (1/2) m v^2", "KE equals (one half) m v squared"),
        ("x/2", "x divided by 2"),
    ],
)
def test_stem_notation_is_verbalized(written, spoken):
    assert to_speakable(written) == spoken


@pytest.mark.parametrize(
    "prose",
    [
        "Students can use pen and/or paper.",
        "A well-known result - and an important one.",
        "The x-axis shows time.",
        "This is a self-contained example.",
    ],
)
def test_prose_is_not_turned_into_math(prose):
    spoken = to_speakable(prose)
    assert "divided by" not in spoken
    assert "minus" not in spoken
    assert "*" not in spoken


def test_no_asterisk_ever_reaches_speech():
    for text in ("Q*", "*", "a * b", "the *key* step", "Q * K * V"):
        assert "*" not in to_speakable(text)


def _write_lesson(path, segments, rate=16000):
    """Speech-like tone bursts separated by short sentence pauses and long scene pauses."""
    audio = []
    for index, (speech_seconds, pause_seconds) in enumerate(segments):
        t = np.arange(int(rate * speech_seconds)) / rate
        audio.append((0.4 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2"))
        if index < len(segments) - 1:
            audio.append(np.zeros(int(rate * pause_seconds), dtype="<i2"))
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(np.concatenate(audio).tobytes())


def test_single_take_lesson_is_split_at_scene_pauses(tmp_path):
    lesson = tmp_path / "lesson.wav"
    # Scene 1: two sentences (short pause), scene pause, scene 2: one sentence.
    _write_lesson(lesson, [(4.0, 0.3), (4.0, 1.0), (6.0, 0.0)])
    outputs = [tmp_path / "s1.wav", tmp_path / "s2.wav"]
    durations = split_lesson_wav(lesson, [40, 30], outputs)
    assert durations is not None
    assert abs(durations[0] - 8.8) < 0.3  # cut in the middle of the 1.0 s scene pause
    assert abs(sum(durations) - 15.3) < 0.05
    for path in outputs:
        with wave.open(str(path), "rb") as audio:
            assert audio.getframerate() == 16000


def test_unreliable_split_falls_back(tmp_path):
    lesson = tmp_path / "lesson.wav"
    _write_lesson(lesson, [(12.0, 0.0)])  # one long take, no pauses at all
    assert split_lesson_wav(lesson, [10, 10], [tmp_path / "a.wav", tmp_path / "b.wav"]) is None


def test_silence_detection_and_cut_plan():
    rate = 1000
    samples = np.concatenate([np.ones(3000) * 9000, np.zeros(800), np.ones(3000) * 9000])
    silences = find_silences(samples.astype(np.float32), rate)
    assert len(silences) == 1 and abs(silences[0].middle - 3.4) < 0.05
    assert plan_cuts([1, 1], 6.8, silences) == [pytest.approx(silences[0].middle)]


def test_glyph_guard_removes_tofu_prone_scripts():
    namespace: dict = {}
    table_source = _GLYPH_GUARD.split("def _sm_guard_text")[0]
    exec(table_source.replace("import manim as _sm_manim", ""), namespace)  # noqa: S102
    table = namespace["_SM_GLYPHS"]
    assert "softmax(QKᵀ/√dₖ)".translate(table) == "softmax(QK^T/√d_k)"


@pytest.mark.parametrize(
    "message",
    [
        "Explain entropy",
        "explain fourier transform visually",
        "can I watch how a transistor switches",
        "help me picture how backprop flows",
    ],
)
def test_without_the_planner_nothing_is_inferred_from_keywords(message):
    # Intent is the planner's decision; an outage only ever gives a text answer.
    result = fallback_preparation(
        message=message, project_request=message, text_language="English", voice_language="English"
    )
    assert result.action == "answer"


def test_progress_log_explains_rejections():
    from pathlib import Path

    # Load only the pure progress helpers; importing app.py would start Streamlit.
    source = (Path(__file__).parents[1] / "frontend" / "app.py").read_text(encoding="utf-8")
    helpers = source[
        source.index("_PROGRESS_LABELS = {") : source.index("_PROGRESS_MILESTONES = {")
    ]
    namespace: dict = {"Any": object}
    exec(helpers, namespace)  # noqa: S102 - trusted repository source, avoids starting Streamlit
    describe = namespace["describe_progress_event"]
    line = describe(
        {
            "event": "scene.rejected",
            "detail": {
                "scene_key": "S01",
                "attempt": 1,
                "max_attempts": 6,
                "reasons": ["layout.overlap (high): title overlaps the diagram"],
                "next_action": "patch_code",
            },
        }
    )
    assert line == (
        "S01 rejected (attempt 1 of 6): layout.overlap (high): title overlaps the diagram"
        " → patching the animation code"
    )
    assert describe({"event": "scene.verifying", "detail": {"scene_key": "S01"}}) is None
    cost = describe(
        {
            "event": "usage.summary",
            "detail": {
                "operation": "build",
                "cost_usd": 1.234,
                "calls": 17,
                "cached_tokens": 42000,
                "by_purpose": [{"purpose": "code", "cost_usd": 0.8}],
            },
        }
    )
    assert cost == (
        "Model cost for this build: $1.23 over 17 calls"
        " (42,000 input tokens served from cache) — code $0.80"
    )
