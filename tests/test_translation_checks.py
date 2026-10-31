"""Checked translation: protected entities, spoken numbers, Bodhan routing and fallbacks."""

from __future__ import annotations

import io
import json
import wave
from types import SimpleNamespace

import httpx
import pytest

from scholarmotion.providers import bodhan
from scholarmotion.providers.bodhan import (
    BodhanTranslation,
    BodhanTranslator,
    BodhanTTSProvider,
    LanguageRoutedTTS,
    parse_voice_map,
)
from scholarmotion.schemas import SceneSpec, VisualBeat
from scholarmotion.translation.checks import (
    missing_names,
    name_present,
    translation_problems,
    unspeakable,
)
from scholarmotion.translation.entities import mask, restore_screen, restore_voice
from scholarmotion.translation.service import TranslationService
from scholarmotion.translation.spoken import number_words, spoken_quantity

LINE = (
    "In 1905, Einstein showed that light of 500 nm can eject electrons — the photoelectric effect."
)


def test_numbers_units_years_and_terms_become_placeholders():
    masked = mask(LINE, key_terms=["photoelectric effect"])
    kinds = {item.kind: item.text for item in masked.items}
    assert kinds == {"year": "1905", "quantity": "500 nm", "term": "photoelectric effect"}
    assert not any(ch.isdigit() for ch in masked.text)
    assert "Einstein" in masked.names


def test_screen_copy_keeps_digits_and_shows_the_english_term_with_its_native_name():
    masked = mask(LINE, key_terms=["photoelectric effect"])
    tamil = masked.text.replace("Einstein showed", "ஐன்ஸ்டீன் காட்டினார்")
    screen = restore_screen(tamil, masked, {"photoelectric effect": "ஒளிமின் விளைவு"})
    assert "1905" in screen and "500 nm" in screen
    assert "photoelectric effect (ஒளிமின் விளைவு)" in screen


def test_voice_copy_says_numbers_in_the_narration_language_and_terms_in_english_only():
    masked = mask(LINE, key_terms=["photoelectric effect"])
    voice = restore_voice(masked.text, masked, "Tamil")
    assert "ஆயிரத்து தொள்ளாயிரத்து ஐந்து" in voice
    assert "ஐந்நூறு nanometres" in voice
    assert "photoelectric effect" in voice
    assert "(" not in voice and "ஒளிமின்" not in voice
    assert unspeakable(voice) == []


@pytest.mark.parametrize(
    ("value", "language", "spoken"),
    [
        ("21", "Tamil", "இருபத்து ஒன்று"),
        ("150000", "Tamil", "ஒரு இலட்சத்து ஐம்பது ஆயிரம்"),
        ("3.5", "Hindi", "तीन दशमलव पाँच"),
        ("1905", "Hindi", "उन्नीस सौ पाँच"),
        ("250", "Marathi", "दोनशे पन्नास"),
        ("21", "Telugu", "ఇరవై ఒకటి"),
        ("-7", "English", "minus seven"),
    ],
)
def test_number_words(value, language, spoken):
    year = value == "1905"
    assert number_words(value, language, year=year) == spoken


def test_percent_and_powers_are_spoken():
    assert spoken_quantity("40", None, "Hindi", kind="percent") == "चालीस प्रतिशत"
    assert spoken_quantity("10³", None, "English", kind="number") == "ten to the power three"


def test_lost_placeholder_and_invented_number_are_caught():
    masked = mask("The wave travels 340 m/s in air.")
    assert translation_problems(masked, "அலை காற்றில் பயணிக்கிறது.", target_language="Tamil")
    problems = translation_problems(
        masked, f"அலை காற்றில் {masked.items[0].token} 12 பயணிக்கிறது.", target_language="Tamil"
    )
    assert any("digits 1 2 were added" in problem for problem in problems)


def test_transliterated_name_is_recognised_even_with_a_case_ending():
    assert name_present("Einstein", "ஐன்ஸ்டீன் ஒளியைப் பற்றி கூறினார்")
    assert name_present("Germany", "ஜெர்மனியில் காட்டினார்")
    assert not name_present("Einstein", "நியூட்டன் ஒளியைப் பற்றி கூறினார்")


def test_a_missing_name_is_a_note_not_a_failure():
    masked = mask("Einstein explained the effect clearly to every student.")
    translated = "அவர் விளைவை ஒவ்வொரு மாணவருக்கும் தெளிவாக விளக்கினார்."
    assert translation_problems(masked, translated, target_language="Tamil") == []
    assert missing_names(masked, translated) == ["Einstein"]


def test_untranslated_and_truncated_output_is_rejected():
    masked = mask("Light behaves both as a wave and as a particle.")
    assert "the text was not translated" in translation_problems(
        masked, masked.text, target_language="Tamil"
    )
    assert "the translation was cut off" in translation_problems(
        masked,
        "ஒளி ஒரு அலை மற்றும் துகள் போல நடந்து கொள்கிறது.",
        target_language="Tamil",
        truncated=True,
    )


class _Machine:
    """Bodhan stand-in: translates, but may drop a placeholder."""

    name = "bodhan"

    def __init__(self, drop_tokens: bool = False):
        self.drop_tokens = drop_tokens
        self.calls: list[str] = []

    def supports(self, language):
        return language == "Tamil"

    async def translate(self, text, target_language):
        self.calls.append(text)
        if text == "photoelectric effect":
            return BodhanTranslation("ஒளிமின் விளைவு", False, 3)
        out = "தமிழ்: " + text
        if self.drop_tokens:
            out = out.replace("[[ZQA]]", "")
        return BodhanTranslation(out, False, 10)


class _LLM:
    name = "test"

    def __init__(self):
        self.prompts: list[str] = []

    async def generate_text(self, prompt, *, temperature=None):
        self.prompts.append(prompt)
        return "சரி: " + prompt.split("TEXT:\n", 1)[1]


def _scene() -> SceneSpec:
    return SceneSpec(
        scene_id="S01",
        chapter_id="C01",
        title="The photoelectric effect",
        learning_objective="Explain why light of 500 nm ejects electrons.",
        duration_target_seconds=20,
        narration=LINE,
        key_terms=["photoelectric effect"],
        visual_beats=[
            VisualBeat(
                beat_id="B1",
                narration_segment="Light hits a metal plate.",
                visual="Photons of 500 nm hit a metal plate",
                start_seconds=0,
                end_seconds=10,
            )
        ],
    )


@pytest.mark.asyncio
async def test_bodhan_translates_when_it_covers_the_language():
    machine, llm = _Machine(), _LLM()
    service = TranslationService(llm, machine)
    display, narration = await service.localize_scene(
        _scene(), text_language="Tamil", voice_language="Tamil"
    )
    assert llm.prompts == []
    assert display.title.startswith("தமிழ்: The photoelectric effect (ஒளிமின் விளைவு)")
    assert "500 nm" in display.learning_objective
    assert "ஐந்நூறு nanometres" in narration
    assert service.events == []


@pytest.mark.asyncio
async def test_a_lost_placeholder_is_fixed_by_the_llm_with_the_problem_named():
    machine, llm = _Machine(drop_tokens=True), _LLM()
    service = TranslationService(llm, machine)
    display, _ = await service.localize_scene(
        _scene(), text_language="Tamil", voice_language="Tamil"
    )
    assert llm.prompts and "previous translation was rejected" in llm.prompts[0]
    assert "is missing" in llm.prompts[0]
    assert display.title.startswith("சரி: ")
    assert {event["status"] for event in service.events} == {"fixed"}


@pytest.mark.asyncio
async def test_screen_text_stays_english_when_every_attempt_fails():
    class Broken(_LLM):
        async def generate_text(self, prompt, *, temperature=None):
            return "தவறு"

    service = TranslationService(Broken(), _Machine(drop_tokens=True))
    display, _ = await service.localize_scene(
        _scene(), text_language="Tamil", voice_language="Tamil"
    )
    assert display.title == "The photoelectric effect"
    assert any(event["status"] == "fallback" for event in service.events)


@pytest.mark.asyncio
async def test_narration_keeps_its_best_attempt_and_is_never_english():
    class Broken(_LLM):
        async def generate_text(self, prompt, *, temperature=None):
            return "தவறு"

    service = TranslationService(Broken(), _Machine(drop_tokens=True))
    _, narration = await service.localize_scene(
        _scene(), text_language="Tamil", voice_language="Tamil"
    )
    assert narration.startswith("தமிழ்: ")
    assert any(event["status"] == "kept_with_problems" for event in service.events)


@pytest.mark.asyncio
async def test_languages_bodhan_does_not_cover_use_the_llm():
    machine, llm = _Machine(), _LLM()
    service = TranslationService(llm, machine)
    result = await service.translate(
        "Water boils at 100 °C.", source_language="English", target_language="Spanish"
    )
    assert machine.calls == []
    assert result.startswith("சரி: ") and "100 °C" in result


def _wav(seconds: float) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24_000)
        audio.writeframes(b"\x01\x00" * int(24_000 * seconds))
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_bodhan_tts_speaks_sentence_chunks_with_the_language_voice(tmp_path):
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        assert request.headers["Authorization"] == "Bearer key"
        return httpx.Response(200, content=_wav(1.0), headers={"Content-Type": "audio/wav"})

    tts = BodhanTTSProvider(
        api_key="key",
        voices=parse_voice_map("ta:Tamilvoice"),
        transport=httpx.MockTransport(handler),
    )
    text = "First sentence here. " * 15 + "\n\nA second paragraph."
    result = await tts.synthesize(text, tmp_path / "out.wav", language="Tamil")
    assert len(requests) >= 3
    assert all(len(body["input"]) <= 220 for body in requests)
    assert {body["voice"] for body in requests} == {"Tamilvoice"}
    assert json.loads(requests[0]["instructions"]) == {
        "lang": "ta",
        "style": "educational lecture",
    }
    assert result.provider == "bodhan" and result.voice_id == "Tamilvoice"
    # Chunks plus the pauses between sentences and paragraphs.
    assert result.duration_seconds > len(requests)


@pytest.mark.asyncio
async def test_unsupported_languages_go_to_the_configured_tts(tmp_path):
    class Fallback:
        name = "openai_compatible"
        model = "tts"

        def __init__(self):
            self.languages = []

        async def synthesize(self, text, path, *, language="English"):
            self.languages.append(language)
            return "fallback"

    fallback = Fallback()
    routed = LanguageRoutedTTS(BodhanTTSProvider(api_key="key"), fallback)
    assert await routed.synthesize("Hola", tmp_path / "a.wav", language="Spanish") == "fallback"
    assert fallback.languages == ["Spanish"]


@pytest.mark.asyncio
async def test_bodhan_translation_errors_surface_as_bodhan_error():
    def handler(_request):
        return httpx.Response(
            400, json={"error": {"code": "unsupported_language", "message": "no"}}
        )

    translator = BodhanTranslator(api_key="key", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="unsupported_language"):
        await translator.translate("Hello", "Tamil")


@pytest.mark.asyncio
async def test_bodhan_translation_uses_the_chat_route_its_keys_reach():
    # Per-model virtual keys get 404 on /translate; only the chat route answers.
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": " அலை "}, "finish_reason": "length"}],
                "usage": {"completion_tokens": 7},
            },
        )

    translator = BodhanTranslator(api_key="key", transport=httpx.MockTransport(handler))
    result = await translator.translate("A wave.", "Tamil")

    path, body = seen[0]
    assert path == "/v1/chat/completions"
    assert body == {
        "model": "indic-translate",
        "target_language": "ta",
        "messages": [{"role": "user", "content": "A wave."}],
    }
    assert (result.text, result.truncated, result.output_tokens) == ("அலை", True, 7)


@pytest.mark.asyncio
async def test_bodhan_waits_as_long_as_its_rate_limit_asks(monkeypatch):

    answers = [
        httpx.Response(429, headers={"retry-after": "18"}, json={"error": {"message": "slow"}}),
        httpx.Response(200, json={"choices": [{"message": {"content": "அலை"}}]}),
    ]
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(bodhan.asyncio, "sleep", sleep)
    translator = BodhanTranslator(
        api_key="limited", transport=httpx.MockTransport(lambda _request: answers.pop(0))
    )
    assert (await translator.translate("A wave.", "Tamil")).text == "அலை"
    assert waits == [18.0]


def test_links_are_protected_whole_and_come_back_unchanged():
    # Seen live: a cited page's "%20" became a number placeholder and Bodhan's
    # copied link digits failed the translation as "added digits".
    link = "https://labs.phys.utk.edu/mbreinig/phys421/modules/AI%20Tutor.html"
    masked = mask(f"See [the notes]({link}). The sky is blue.")
    assert [item.kind for item in masked.items] == ["url"]
    assert link not in masked.text
    translated = masked.text.replace("The sky is blue.", "வானம் நீலமானது.")
    assert translation_problems(masked, translated, target_language="Tamil") == []
    assert link in restore_screen(translated, masked, {})


def test_digits_the_english_already_showed_are_not_counted_as_added():
    masked = mask("Model GPT-4o explains it.")
    leftover = masked.text.replace("explains it.", "இதை விளக்குகிறது.")
    assert not any(
        "were added" in p for p in translation_problems(masked, leftover, target_language="Tamil")
    )


@pytest.mark.asyncio
async def test_the_service_reports_which_translator_wrote_the_text_it_returned():
    class Machine:
        name = "bodhan"

        def supports(self, language):
            return True

        async def translate(self, text, language):
            return SimpleNamespace(text="வானம் நீலமானது.", truncated=False)

    service = TranslationService(object(), Machine())
    await service.translate("The sky is blue.", source_language="English", target_language="Tamil")
    assert service.last_translator == "bodhan"


def test_a_cited_markdown_link_keeps_its_title_and_address_untranslated():
    # Seen live: link 5's title held STEM terms, the translation moved words around
    # them and the learner saw broken link text instead of a link.
    link = (
        "[AI tutoring for STEM: from explanation to application | Tutezy]"
        "(https://tutezy.ai/blog/ai-tutoring-for-stem-education/)"
    )
    masked = mask(f"5. {link}", key_terms=["STEM", "AI"])
    assert [item.text for item in masked.items if item.kind == "url"] == [link]
    assert "STEM" not in masked.text and "tutezy" not in masked.text
    assert restore_screen(masked.text, masked, {"stem": "அறிவியல்"}) == f"5. {link}"


@pytest.mark.asyncio
async def test_a_busy_minute_is_waited_out_rather_than_dropping_a_scene(monkeypatch):
    # Seen live: Bodhan answered 46 rate-limit 429s (no retry-after) while 17 scenes
    # were narrated at once; three tries over 4.5 s gave up and two scenes were lost.
    answers = [httpx.Response(429, json={"detail": "Maximum rate limit reached"})] * 8 + [
        httpx.Response(200, json={"choices": [{"message": {"content": "அலை"}}]})
    ]
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(bodhan.asyncio, "sleep", sleep)
    bodhan._cooldown_until.clear()
    translator = BodhanTranslator(
        api_key="busy", transport=httpx.MockTransport(lambda _request: answers.pop(0))
    )
    assert (await translator.translate("A wave.", "Tamil")).text == "அலை"
    assert waits == [3.0, 6.0, 12.0, 24.0, 30.0, 30.0, 30.0, 30.0]


@pytest.mark.asyncio
async def test_server_errors_still_give_up_after_three_tries(monkeypatch):
    calls = []

    def handler(_request):
        calls.append(1)
        return httpx.Response(503, text="down")

    async def sleep(_seconds):
        pass

    monkeypatch.setattr(bodhan.asyncio, "sleep", sleep)
    translator = BodhanTranslator(api_key="down", transport=httpx.MockTransport(handler))
    with pytest.raises(bodhan.BodhanError):
        await translator.translate("A wave.", "Tamil")
    assert len(calls) == 3
