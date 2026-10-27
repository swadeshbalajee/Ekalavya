from scholarmotion.schemas import SceneSpec, VisualBeat
from scholarmotion.verification.math import verify_required_equations
from scholarmotion.verification.semantic import verify_source_grounding


def _scene() -> SceneSpec:
    return SceneSpec(
        scene_id="S01",
        chapter_id="C01",
        title="Test",
        learning_objective="Test",
        duration_target_seconds=20,
        narration="Test narration.",
        visual_beats=[VisualBeat(beat_id="b1", narration_segment="Test", visual="Show test")],
        equations=["E=mc^2"],
        source_ids=["source-1"],
    )


def test_math_and_source_verifiers_produce_repairable_issues():
    scene = _scene()
    assert verify_required_equations(scene.scene_id, scene.equations, "print('x')")
    assert verify_source_grounding(scene, {"source-2"})
    assert not verify_source_grounding(scene, {"source-1"})


def test_required_latex_equation_accepts_its_unicode_rendered_form():
    equation = (
        r"\text{Attention}(Q, K, V) = "
        r"\text{softmax}\left(\frac{QK^T}{\sqrt{d_k}}\right)V"
    )
    code = 'formula = Text("Attention(Q, K, V) = softmax(QKᵀ / √dₖ)V")'

    assert not verify_required_equations("S01", [equation], code)
