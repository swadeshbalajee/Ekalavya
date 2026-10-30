"""Merges scene caption cues and writes SRT/WebVTT files."""

from __future__ import annotations

from scholarmotion.schemas import TimingCue


def merge_scene_cues(items: list[tuple[float, list[TimingCue]]]) -> list[TimingCue]:
    return [
        TimingCue(text=cue.text, start=offset + cue.start, end=offset + cue.end)
        for offset, cues in items
        for cue in cues
    ]


def _stamp(value: float, separator: str) -> str:
    milliseconds = round(value * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{milliseconds:03}"


def cues_to_srt(cues: list[TimingCue]) -> str:
    return "\n\n".join(
        f"{index}\n{_stamp(cue.start, ',')} --> {_stamp(cue.end, ',')}\n{cue.text}"
        for index, cue in enumerate(cues, 1)
    ) + ("\n" if cues else "")


def cues_to_vtt(cues: list[TimingCue]) -> str:
    return (
        "WEBVTT\n\n"
        + "\n\n".join(
            f"{_stamp(cue.start, '.')} --> {_stamp(cue.end, '.')}\n{cue.text}" for cue in cues
        )
        + ("\n" if cues else "")
    )
