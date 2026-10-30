from scholarmotion.retrieval.paper_figures import _CAPTION


def labels(text):
    return [f"Figure {match.group(2)}" for match in _CAPTION.finditer(text)]


def test_caption_styles_used_by_paper_templates_are_found():
    text = (
        "Figure 1: Overview.\n"
        "Fig. 2. Results.\n"
        "Figure 3 | Prompts sorted into six buckets.\n"
        "Figure 4 — Workflow.\n"
        "Figure 5 – Training dynamics.\n"
    )
    assert labels(text) == [f"Figure {n}" for n in range(1, 6)]


def test_references_in_running_text_are_not_captions():
    text = "as shown in Figure 3. We then\nFigure 3 shows the trend\n(Figure 8). Next"
    assert labels(text) == []
