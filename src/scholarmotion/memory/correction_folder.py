"""Lessons learned from past failures, kept as a folder of notes per agent, like skills.

    correction_folder/
      code/                        the scene-code writer
        CORRECTIONS.md             index of the entries, always shown (rewritten from them)
        entries/C001-....md        one lesson each, loaded when its triggers match
        checks/C001_....py         optional check(code) -> list of findings
        candidates/<key>.md        problems seen and fixed, not yet a lesson
      script/  storyboard/  review/

An entry starts with a small header:

    ---
    id: C001
    title: Value labels sit on the bars
    triggers: data_plot, bar, text_overlap
    seen: 4
    ---
    (the lesson in plain words, with a short example)

The agent always sees the index, so it knows what has gone wrong before;
an entry's full text is added only when one of its triggers matches the
scene (its tags, visual primitives or the checks it is failing), so prompts
stay short. People can edit, add or delete any file. The pipeline adds
lessons itself: when a repair fixes a problem, that is recorded as a
candidate, and a candidate fixed in PROMOTE_AFTER different scenes becomes
an entry.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

AGENTS = ("code", "script", "storyboard", "review")
PROMOTE_AFTER = 3
_EXAMPLES_KEPT = 5
_SEED = Path(__file__).with_name("correction_seed")
_HEADER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_LOCK = threading.Lock()


@dataclass
class Note:
    """An entry or candidate: its header fields and its text."""

    path: Path
    fields: dict[str, str] = field(default_factory=dict)
    body: str = ""

    @property
    def id(self) -> str:
        return self.fields.get("id", self.path.stem)

    @property
    def title(self) -> str:
        return self.fields.get("title", self.path.stem)

    @property
    def triggers(self) -> list[str]:
        return [t.strip().lower() for t in self.fields.get("triggers", "").split(",") if t.strip()]

    @property
    def seen(self) -> int:
        try:
            return int(self.fields.get("seen", "0"))
        except ValueError:
            return 0

    def text(self) -> str:
        header = "\n".join(f"{key}: {value}" for key, value in self.fields.items())
        return f"---\n{header}\n---\n{self.body.strip()}\n"


def _read(path: Path) -> Note:
    raw = path.read_text(encoding="utf-8")
    match = _HEADER.match(raw)
    fields: dict[str, str] = {}
    if match:
        for line in match.group(1).splitlines():
            key, _, value = line.partition(":")
            if key.strip():
                fields[key.strip().lower()] = value.strip()
        raw = raw[match.end() :]
    return Note(path, fields, raw.strip())


def _write(path: Path, text: str) -> None:
    """Replace a file in one step, so a reader never sees half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(handle, "w", encoding="utf-8") as file:
        file.write(text)
    os.replace(temporary, path)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:48] or "note"


class CorrectionFolder:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def ensure(self) -> None:
        """Create the agent folders; a new folder starts with the bundled lessons."""
        for agent in AGENTS:
            folder = self.root / agent
            if (folder / "CORRECTIONS.md").exists():
                continue
            seed = _SEED / agent
            if seed.is_dir():
                shutil.copytree(seed, folder, dirs_exist_ok=True)
            (folder / "entries").mkdir(parents=True, exist_ok=True)
            self._write_index(agent)

    def entries(self, agent: str) -> list[Note]:
        folder = self.root / agent / "entries"
        notes = []
        for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
            try:
                notes.append(_read(path))
            except OSError as error:
                logger.warning("Could not read correction %s: %s", path, error)
        return notes

    def context(self, agent: str, terms: set[str] | list[str], *, limit: int = 4) -> str:
        """The agent's index plus the full text of the entries that match ``terms``."""
        try:
            self.ensure()
        except OSError:
            return ""
        index = self._index_text(agent).strip()
        words = {str(term).lower() for term in terms if str(term).strip()}

        def matches(note: Note) -> int:
            return sum(1 for trigger in note.triggers if any(trigger in word for word in words))

        relevant = sorted(
            (note for note in self.entries(agent) if matches(note)),
            key=lambda note: (-matches(note), -note.seen),
        )[:limit]
        details = "\n\n".join(f"[{note.id}] {note.title}\n{note.body}" for note in relevant)
        return (
            "LESSONS FROM EARLIER FAILURES (follow them):\n"
            + index
            + (f"\n\nDETAILS THAT APPLY HERE:\n{details}" if details else "")
        )

    def run_checks(self, agent: str, source: str) -> list[tuple[str, str]]:
        """(entry id, finding) from every check in the agent's checks folder."""
        findings = []
        folder = self.root / agent / "checks"
        for path in sorted(folder.glob("*.py")) if folder.is_dir() else []:
            try:
                spec = importlib.util.spec_from_file_location(f"correction_check_{path.stem}", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                for finding in module.check(source) or []:
                    findings.append((path.stem.split("_")[0], str(finding)))
            except Exception as error:  # noqa: BLE001 - a broken check must not stop a scene
                logger.warning("Correction check %s failed: %s", path.name, error)
        return findings

    def record(
        self, agent: str, key: str, problem: str, fix: str, *, scene: str = ""
    ) -> Note | None:
        """Note that ``problem`` was fixed by ``fix``; returns the entry if this promoted it."""
        with _LOCK:
            try:
                self.ensure()
                return self._record(agent, key, problem, fix, scene)
            except OSError as error:
                logger.warning("Could not record correction evidence: %s", error)
                return None

    def _record(self, agent: str, key: str, problem: str, fix: str, scene: str) -> Note | None:
        slug = _slug(key)
        if any(note.fields.get("key") == slug for note in self.entries(agent)):
            return None  # already a lesson
        path = self.root / agent / "candidates" / f"{slug}.md"
        note = _read(path) if path.exists() else Note(path, {"key": slug, "seen": "0"})
        scenes = [item for item in note.fields.get("scenes", "").split(",") if item]
        if scene and scene in scenes:
            return None  # one scene counts once
        scenes = (scenes + [scene])[-20:] if scene else scenes
        example = f"- Problem: {problem.strip()[:400]}\n  Fix that worked: {fix.strip()[:400]}"
        examples = [part for part in note.body.split("\n- ") if part.strip()]
        examples = ([example] + ["- " + part.lstrip("- ") for part in examples])[:_EXAMPLES_KEPT]
        note.fields.update(seen=str(note.seen + 1), scenes=",".join(scenes))
        note.body = "\n".join(examples)
        if note.seen < PROMOTE_AFTER:
            _write(path, note.text())
            return None
        entry = self._promote(agent, note)
        path.unlink(missing_ok=True)
        return entry

    def _promote(self, agent: str, candidate: Note) -> Note:
        prefix = {"code": "C", "script": "S", "storyboard": "B", "review": "R"}.get(agent, "X")
        number = 1 + max(
            (
                int(match.group(1))
                for note in self.entries(agent)
                if (match := re.match(rf"{prefix}(\d+)", note.id))
            ),
            default=0,
        )
        key = candidate.fields["key"]
        entry_id = f"{prefix}{number:03d}"
        entry = Note(
            self.root / agent / "entries" / f"{entry_id}-{key}.md",
            {
                "id": entry_id,
                "title": f"Recurring problem: {key.replace('-', ' ')}",
                "triggers": ", ".join(dict.fromkeys([key, *key.split("-")[-1:]])),
                "key": key,
                "seen": str(candidate.seen),
                "learned": "automatically from repairs that fixed it",
            },
            "This went wrong in several scenes and was fixed by a repair each time. "
            "Avoid it from the start. Recent cases:\n" + candidate.body,
        )
        _write(entry.path, entry.text())
        self._write_index(agent)
        logger.info("Promoted correction %s for %s", entry_id, agent)
        return entry

    def _index_text(self, agent: str) -> str:
        """One line per entry, read from the entries themselves, so edits show at once."""
        lines = [
            f"- {note.id} {note.title} (when: {', '.join(note.triggers) or 'always'})"
            for note in self.entries(agent)
        ]
        return (
            "\n".join(lines)
            if lines
            else "(no lessons yet; they are added as repairs fix recurring problems)"
        ) + "\n"

    def _write_index(self, agent: str) -> None:
        _write(self.root / agent / "CORRECTIONS.md", self._index_text(agent))
