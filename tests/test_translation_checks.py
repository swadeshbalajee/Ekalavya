"""Checked translation: protected entities, spoken numbers, Bodhan routing and fallbacks."""

from __future__ import annotations

import io
import json
import wave

import httpx
import pytest

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
