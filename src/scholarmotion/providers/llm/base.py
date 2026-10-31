"""LLM provider protocol and capability flags."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# A model that writes LaTeX into JSON without doubling its backslashes turns
# "\text" into a tab and "ext", "\frac" into a form feed and "rac", and so on.
# These are the LaTeX commands whose first letter is a JSON escape.
_ESCAPED_COMMANDS = {
    "\t": "textbf textit text times theta tanh tan tau tilde triangle top to",
    "\f": "frac forall flat",
    "\b": "boldsymbol bigg big binom begin beta bar bf bot bullet",
    "\r": "rightarrow right rangle rceil rfloor rho rm",
    "\n": "nabla neq newline neg not nu ne",
}
# The decoded control character stands for the command's first letter, so
# "\text" arrives as a tab followed by "ext".
_ESCAPED = {
    char: (
        words[0],
        re.compile(
            re.escape(char) + "(" + "|".join(w[1:] for w in words.split()) + ")(?![A-Za-z])"
        ),
    )
    for char, words in _ESCAPED_COMMANDS.items()
}


def _restored(text: str) -> str:
    if not any(char in text for char in _ESCAPED):
        return text
    for char, (letter, pattern) in _ESCAPED.items():

        def restore(
            match: re.Match, char: str = char, letter: str = letter, source: str = text
        ) -> str:
            # A newline is usually a real line break; restore it only inside $...$ math.
            if char == "\n" and not source.count("$", 0, match.start()) % 2:
                return match.group(0)
            return "\\" + letter + match.group(1)

        text = pattern.sub(restore, text)
    return text


def restore_latex_escapes(value: Any) -> Any:
    """Put back the LaTeX backslashes that JSON decoding turned into control characters."""
    if isinstance(value, str):
        return _restored(value)
    if isinstance(value, list):
        return [restore_latex_escapes(item) for item in value]
    if isinstance(value, dict):
        return {key: restore_latex_escapes(item) for key, item in value.items()}
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            # Written straight into __dict__ so frozen models and assignment validators are untouched.
            value.__dict__[name] = restore_latex_escapes(getattr(value, name))
    return value


_CALL_TIMEOUT: ContextVar[float | None] = ContextVar("llm_call_timeout", default=None)


@contextmanager
def call_timeout(seconds: float) -> Iterator[None]:
    """Let each model request inside this block run up to ``seconds``.

    For stages whose answers are long (a whole lesson script), where the
    default per-request limit would cut a healthy model off mid-answer.
    """
    token = _CALL_TIMEOUT.set(seconds)
    try:
        yield
    finally:
        _CALL_TIMEOUT.reset(token)


def call_timeout_seconds(default: float) -> float:
    """The per-request limit: the longer of ``default`` and the block's limit."""
    return max(default, _CALL_TIMEOUT.get() or 0)


@dataclass(frozen=True)
class LLMCapabilities:
    structured_output: bool = True
    code: bool = True
    images: bool = False
    video_frames: bool = False


class LLMProvider(Protocol):
    name: str
    model: str
    capabilities: LLMCapabilities

    async def generate_structured(
        self, prompt: str, output_schema: type[T], *, temperature: float | None = None
    ) -> T: ...

    async def generate_text(self, prompt: str, *, temperature: float | None = None) -> str: ...

    async def generate_code(self, prompt: str, *, temperature: float | None = None) -> str: ...

    async def analyze_images(
        self, prompt: str, images: list[bytes], *, temperature: float | None = None
    ) -> dict[str, Any]: ...
