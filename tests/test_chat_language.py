"""The chat replies in English until the learner asks for another language; videos keep their own."""

from __future__ import annotations

import re
from typing import ClassVar

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.agents.lesson_preparation import LessonPreparation
from scholarmotion.api import tutor as tutor_api
from scholarmotion.config import Settings
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.models import Project, UserAccount
from scholarmotion.schemas import TutorChatRequest
from scholarmotion.translation.entities import TOKEN
from scholarmotion.translation.service import TranslationService


class Translator:
    """Records each reply's target language and the STEM terms kept in English."""

    calls: ClassVar[list[tuple[str, tuple[str, ...]]]] = []

    def __init__(self, _provider, _machine=None):
        pass

    async def to_english(self, text, source_language=None):
        return text

    async def stem_terms(self, text):
        return ["Rayleigh scattering"] if "Rayleigh" in text else []

    async def translate(self, text, source_language=None, target_language=None, key_terms=()):
        Translator.calls.append((target_language, tuple(key_terms)))
        return text


class Planner:
    name = "openai_compatible"

    def __init__(self, decisions):
        self.decisions = list(decisions)

    async def generate_structured(self, prompt, schema, **_):
        return self.decisions.pop(0)


@pytest.mark.asyncio
async def test_the_chat_stays_english_for_a_tamil_video_and_keeps_an_asked_language(monkeypatch):
    planner = Planner(
        [
            # 1. "Make a video in Tamil": the video is Tamil, the chat is not.
            LessonPreparation(
                action="generate_video",
                text_language="Tamil",
                voice_language="Tamil",
                reply="I will make a 2 minute video in Tamil.",
                duration_minutes=2,
            ),
            # 2. "Talk to me in Tamil": the chat switches.
            LessonPreparation(
                action="answer",
                chat_language="Tamil",
                text_language="Tamil",
                voice_language="Tamil",
                reply="Sure. Rayleigh scattering makes the sky blue.",
            ),
            # 3. A message written in English: the chat stays Tamil.
            LessonPreparation(
                action="answer",
                text_language="Tamil",
                voice_language="Tamil",
                reply="Sunsets are red because of Rayleigh scattering.",
            ),
        ]
    )
    monkeypatch.setattr(tutor_api, "TranslationService", Translator)
    monkeypatch.setattr(tutor_api, "create_translation_provider", lambda _settings: object())
    monkeypatch.setattr(tutor_api, "create_machine_translator", lambda _settings: None)
    monkeypatch.setattr(tutor_api, "create_llm_provider", lambda _settings: planner)
    Translator.calls = []

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        user = UserAccount(username="chat-language", password_hash="x", display_name="Learner")
        session.add(user)
        await session.flush()
        project = Project(owner_id=user.id, title="Sky", request="Why is the sky blue?")
        session.add(project)
        await session.flush()

        for message in ("Make a video in Tamil", "Talk to me in Tamil", "And sunsets?"):
            await tutor_api.respond(session, Settings(), project, TutorChatRequest(message=message))
            await session.flush()

    await engine.dispose()
    assert (project.text_language, project.voice_language) == ("Tamil", "Tamil")
    assert project.chat_language == "Tamil"
    assert Translator.calls == [
        ("English", ()),
        ("Tamil", ("Rayleigh scattering",)),
        ("Tamil", ("Rayleigh scattering",)),
    ]


class _Translated:
    def __init__(self, text):
        self.text, self.truncated = text, False


class _Machine:
    """A machine translator that keeps placeholders and knows one term."""

    name = "bodhan"

    def supports(self, language):
        return language == "Tamil"

    async def translate(self, text, language):
        if text == "Rayleigh scattering":
            return _Translated("ரேலே சிதறல்")
        tokens = " ".join(TOKEN.findall(text))
        return _Translated(f"{tokens} வானத்தை நீலமாக்குகிறது.")


@pytest.mark.asyncio
async def test_a_chat_reply_keeps_stem_terms_in_english_with_the_native_name_in_brackets():
    service = TranslationService(object(), _Machine())
    shown = await service.translate(
        "Rayleigh scattering makes the sky blue.",
        source_language="English",
        target_language="Tamil",
        key_terms=["Rayleigh scattering"],
    )
    assert re.match(r"^Rayleigh scattering \(ரேலே சிதறல்\) வானத்தை", shown), shown


class _Namer:
    """A model that names every glossary term in one request."""

    name = "openai_compatible"

    def __init__(self):
        self.requests = 0

    async def generate_structured(self, prompt, schema, **_):
        self.requests += 1
        return schema(terms=[{"english": "wavelength", "native": "அலைநீளம்"}])


class _CountingMachine(_Machine):
    def __init__(self):
        self.terms_asked = []

    async def translate(self, text, language):
        if "[[" not in text:
            self.terms_asked.append(text)
        return await super().translate(text, language)


@pytest.mark.asyncio
async def test_glossary_terms_are_named_in_one_request_not_one_machine_call_each():
    namer, machine = _Namer(), _CountingMachine()
    service = TranslationService(namer, machine)
    shown = await service.translate(
        "Shorter wavelength light scatters more.",
        source_language="English",
        target_language="Tamil",
        key_terms=["wavelength"],
    )
    assert "wavelength (அலைநீளம்)" in shown
    assert namer.requests == 1 and machine.terms_asked == []


@pytest.mark.asyncio
async def test_a_reply_already_in_the_chat_language_is_not_translated_again(monkeypatch):
    planner = Planner(
        [
            LessonPreparation(
                action="answer",
                chat_language="Tamil",
                reply="பட்டாம்பூச்சி விளைவு (Butterfly Effect) என்பது ஒரு கருத்து.",
            )
        ]
    )
    monkeypatch.setattr(tutor_api, "TranslationService", Translator)
    monkeypatch.setattr(tutor_api, "create_translation_provider", lambda _settings: object())
    monkeypatch.setattr(tutor_api, "create_machine_translator", lambda _settings: None)
    monkeypatch.setattr(tutor_api, "create_llm_provider", lambda _settings: planner)
    Translator.calls = []
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        user = UserAccount(username="already-tamil", password_hash="x", display_name="Learner")
        session.add(user)
        await session.flush()
        project = Project(owner_id=user.id, title="Chaos", request="Butterfly effect")
        session.add(project)
        await session.flush()
        decision = await tutor_api.respond(
            session, Settings(), project, TutorChatRequest(message="explain in Tamil")
        )
    await engine.dispose()
    assert Translator.calls == []
    assert decision.message.startswith("பட்டாம்பூச்சி விளைவு")


class _DigitMachine(_Machine):
    """Translates well but writes a digit of its own, which the checks reject."""

    async def translate(self, text, language):
        tokens = " ".join(TOKEN.findall(text))
        return _Translated(f"1. {tokens} வானத்தை நீலமாக்குகிறது.")


class _SameMistake:
    name = "openai_compatible"

    async def generate_text(self, prompt, **_):
        return "1. [[ZQA]] வானத்தை நீலமாக்குகிறது."


@pytest.mark.asyncio
async def test_a_chat_reply_keeps_its_best_translation_rather_than_falling_back_to_english():
    # Seen live: Bodhan added list digits, both attempts failed the checks and the
    # learner who asked for Tamil got the English original.
    service = TranslationService(_SameMistake(), _DigitMachine())
    shown = await service.translate(
        "Rayleigh scattering makes the sky blue.",
        source_language="English",
        target_language="Tamil",
        key_terms=["Rayleigh scattering"],
    )
    assert "வானத்தை" in shown and shown.startswith("1. Rayleigh scattering")
