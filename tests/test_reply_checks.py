"""Tutor replies: numeric worked examples and source citations are verified."""

from __future__ import annotations

import pytest

from scholarmotion.api.tutor import _checked_reply
from scholarmotion.config import Settings
from scholarmotion.verification.answers import arithmetic_problems, citation_problems

CONTEXT = (
    "[Attention Is All You Need, p. 3] The model uses 8 heads and d_model = 512.\n\n"
    "[Attention Is All You Need, p. 5] Training took 3.5 days on 8 GPUs."
)


@pytest.mark.parametrize(
    "reply",
    [
        r"$$KE = \frac{1}{2} m v^2 = \frac{1}{2}\cdot 4 \cdot 3^2 = 18\text{ J}$$",
        r"Newton's law is $F = ma$; energy is $E = mc^2$.",
        r"$\sqrt{16} = 4$ and $1,000 \times 3 = 3,000$ and $2^{10} = 1024$",
        r"$c = 3 \times 10^{8}$ m/s, $\pi \approx 3.14$, and 22/7 ≈ 3.14",
        r"$0.1 + 0.2 = 0.3$ and $10 / 4 = 2.5$",
    ],
)
def test_correct_arithmetic_and_definitions_pass(reply):
    assert arithmetic_problems(reply) == []


def test_a_wrong_worked_example_is_named_with_the_right_value():
    [problem] = arithmetic_problems(r"So $\frac{1}{2}\cdot 4 \cdot 3^2 = 20$ joules.")
    assert "left side equals 18" in problem


def test_plain_text_arithmetic_is_checked():
    assert arithmetic_problems("Then 3 × 4 = 13.") == [
        "3 × 4 = 13 is wrong: the left side equals 12"
    ]


def test_citations_must_name_retrieved_pages_and_their_numbers():
    problems = citation_problems(
        "It uses 8 heads [Attention Is All You Need, p. 3]. Training took 12 days "
        "[Attention Is All You Need, p. 5]. See [Attention Is All You Need, p. 9].",
        CONTEXT,
    )
    assert problems == [
        "[Attention Is All You Need, p. 5]: the cited page does not contain 12",
        "[Attention Is All You Need, p. 9] cites a page that was not in the provided excerpts",
    ]


def test_correct_citations_pass():
    reply = "Training took 3.5 days on 8 GPUs [Attention Is All You Need, p. 5]."
    assert citation_problems(reply, CONTEXT) == []


class _LLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    async def generate_text(self, prompt, *, temperature=None):
        self.prompts.append(prompt)
        return self.reply


@pytest.mark.asyncio
async def test_a_failing_reply_is_regenerated_with_the_errors_named():
    llm = _LLM(r"So $\frac{1}{2}\cdot 4 \cdot 3^2 = 18$ joules.")
    body, checks = await _checked_reply(
        llm, Settings(), "PROMPT", r"So $\frac{1}{2}\cdot 4 \cdot 3^2 = 20$ joules.", ""
    )
    assert "left side equals 18" in llm.prompts[0]
    assert body.endswith("= 18$ joules.")
    assert checks["verified"] is True and checks["fixed"]


@pytest.mark.asyncio
async def test_an_uncorrected_reply_carries_a_note():
    wrong = r"So $\frac{1}{2}\cdot 4 \cdot 3^2 = 20$ joules."
    body, checks = await _checked_reply(_LLM(wrong), Settings(), "PROMPT", wrong, "")
    assert "**Note:** I could not verify" in body
    assert checks["verified"] is False and checks["unverified"]


@pytest.mark.asyncio
async def test_a_correct_reply_is_not_regenerated():
    llm = _LLM("unused")
    body, checks = await _checked_reply(llm, Settings(), "PROMPT", "$2 + 2 = 4$", "")
    assert llm.prompts == [] and body == "$2 + 2 = 4$" and checks == {"verified": True}
