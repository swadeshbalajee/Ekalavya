"""Narration must be speakable: no math markup reaches TTS or the subtitles."""

from __future__ import annotations

import re

import pytest

from scholarmotion.media.speech import to_speakable

MARKUP = re.compile(r"[$\\{}^]|(?<![A-Za-z])_")


@pytest.mark.parametrize(
    "written, expected",
    [
        ("positive $q$ and negative $q$", "positive q and negative q"),
        ("the vector $p$ equal to $q$ times $2a$", "the vector p equal to q times 2a"),
        ("one over $r^2$", "one over r squared"),
        ("scales as $r^3$", "scales as r cubed"),
        (r"energy $-p \cdot E$", "energy -p dot E"),
        (r"torque $p \times E$", "torque p times E"),
        (r"the angle $\theta$", "the angle theta"),
        (r"$\vec{p}$ and $\vec{E}$", "p and E"),
        (r"$\frac{1}{2}$ of it", "1 over 2 of it"),
        (r"$\sqrt{2}$ times", "square root of 2 times"),
    ],
)
def test_math_markup_becomes_spoken_words(written, expected):
    assert to_speakable(written) == expected


@pytest.mark.parametrize(
    "written",
    [
        r"the field $\frac{1}{4\pi\epsilon_0}$ near $\vec{p}$",
        r"where $\theta$ is between $\vec{p}$ and $\vec{E}$, and $U = -pE\cos\theta$",
        r"\(E \propto 1/r^3\) for a dipole",
        "a plain sentence with no markup at all",
    ],
)
def test_no_markup_survives(written):
    """Anything a TTS engine would mispronounce must be gone."""
    assert not MARKUP.search(to_speakable(written))


def test_punctuation_is_not_left_floating():
    assert to_speakable("charges $q$ and $-q$ , separated") == "charges q and -q, separated"


def test_plain_text_is_unchanged():
    plain = "An electric dipole is a pair of equal and opposite charges."
    assert to_speakable(plain) == plain


def test_empty_input_is_safe():
    assert to_speakable("") == ""


def test_underscores_in_prose_are_not_mangled():
    """Subscript handling must not eat ordinary words."""
    assert "naught" in to_speakable("epsilon naught is the permittivity")
