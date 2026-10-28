"""Numbers written out as words in the narration language, for text-to-speech.

On-screen text keeps digits; the voice copy says them. num2words covers
Arabic, Bengali, English, French, Japanese, Spanish and Telugu. Tamil, Hindi
and Marathi are not in num2words, so they use the Indian place-value system
(hundred, thousand, lakh, crore) with the word tables below.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from num2words import num2words

_NUM2WORDS_CODES = {
    "arabic": "ar",
    "bengali": "bn",
    "english": "en",
    "french": "fr",
    "japanese": "ja",
    "spanish": "es",
    "telugu": "te",
}

# --- Tamil -----------------------------------------------------------------
_TA_UNITS = [
    "பூஜ்ஜியம்",
    "ஒன்று",
    "இரண்டு",
    "மூன்று",
    "நான்கு",
    "ஐந்து",
    "ஆறு",
    "ஏழு",
    "எட்டு",
    "ஒன்பது",
]
_TA_TEENS = [
    "பத்து",
    "பதினொன்று",
    "பன்னிரண்டு",
    "பதின்மூன்று",
    "பதினான்கு",
    "பதினைந்து",
    "பதினாறு",
    "பதினேழு",
    "பதினெட்டு",
    "பத்தொன்பது",
]
# Tens on their own, and the joining form used before a unit (இருபத்து ஒன்று).
_TA_TENS = {
    2: ("இருபது", "இருபத்து"),
    3: ("முப்பது", "முப்பத்து"),
    4: ("நாற்பது", "நாற்பத்து"),
    5: ("ஐம்பது", "ஐம்பத்து"),
    6: ("அறுபது", "அறுபத்து"),
    7: ("எழுபது", "எழுபத்து"),
    8: ("எண்பது", "எண்பத்து"),
    9: ("தொண்ணூறு", "தொண்ணூற்று"),
}
_TA_HUNDREDS = {
    1: ("நூறு", "நூற்று"),
    2: ("இருநூறு", "இருநூற்று"),
    3: ("முந்நூறு", "முந்நூற்று"),
    4: ("நானூறு", "நானூற்று"),
    5: ("ஐந்நூறு", "ஐந்நூற்று"),
    6: ("அறுநூறு", "அறுநூற்று"),
    7: ("எழுநூறு", "எழுநூற்று"),
    8: ("எண்ணூறு", "எண்ணூற்று"),
    9: ("தொள்ளாயிரம்", "தொள்ளாயிரத்து"),
}
_TA_THOUSANDS = {
    1: ("ஆயிரம்", "ஆயிரத்து"),
    2: ("இரண்டாயிரம்", "இரண்டாயிரத்து"),
    3: ("மூவாயிரம்", "மூவாயிரத்து"),
    4: ("நான்காயிரம்", "நான்காயிரத்து"),
    5: ("ஐயாயிரம்", "ஐயாயிரத்து"),
    6: ("ஆறாயிரம்", "ஆறாயிரத்து"),
    7: ("ஏழாயிரம்", "ஏழாயிரத்து"),
    8: ("எட்டாயிரம்", "எட்டாயிரத்து"),
    9: ("ஒன்பதாயிரம்", "ஒன்பதாயிரத்து"),
    10: ("பத்தாயிரம்", "பத்தாயிரத்து"),
}


def _ta_below_100(n: int) -> str:
    if n < 10:
        return _TA_UNITS[n]
    if n < 20:
        return _TA_TEENS[n - 10]
    tens, unit = divmod(n, 10)
    alone, joined = _TA_TENS[tens]
    return alone if unit == 0 else f"{joined} {_TA_UNITS[unit]}"


def _ta_below_1000(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    if not hundreds:
        return _ta_below_100(rest)
    alone, joined = _TA_HUNDREDS[hundreds]
    return alone if rest == 0 else f"{joined} {_ta_below_100(rest)}"


def _tamil(n: int) -> str:
    if n < 1000:
        return _ta_below_1000(n)
    for scale, alone, joined in ((10**7, "கோடி", "கோடியே"), (10**5, "இலட்சம்", "இலட்சத்து")):
        if n >= scale:
            count, rest = divmod(n, scale)
            prefix = "ஒரு" if count == 1 else _tamil(count)
            return f"{prefix} {alone}" if rest == 0 else f"{prefix} {joined} {_tamil(rest)}"
    count, rest = divmod(n, 1000)
    alone, joined = _TA_THOUSANDS.get(count, (f"{_tamil(count)} ஆயிரம்", f"{_tamil(count)} ஆயிரத்து"))
    return alone if rest == 0 else f"{joined} {_ta_below_1000(rest)}"


# --- Hindi -----------------------------------------------------------------
_HI_0_99 = [
    "शून्य",
    "एक",
    "दो",
    "तीन",
    "चार",
    "पाँच",
    "छह",
    "सात",
    "आठ",
    "नौ",
    "दस",
    "ग्यारह",
    "बारह",
    "तेरह",
    "चौदह",
    "पंद्रह",
    "सोलह",
    "सत्रह",
    "अठारह",
    "उन्नीस",
    "बीस",
    "इक्कीस",
    "बाईस",
    "तेईस",
    "चौबीस",
    "पच्चीस",
    "छब्बीस",
    "सत्ताईस",
    "अट्ठाईस",
    "उनतीस",
    "तीस",
    "इकतीस",
    "बत्तीस",
    "तैंतीस",
    "चौंतीस",
    "पैंतीस",
    "छत्तीस",
    "सैंतीस",
    "अड़तीस",
    "उनतालीस",
    "चालीस",
    "इकतालीस",
    "बयालीस",
    "तैंतालीस",
    "चवालीस",
    "पैंतालीस",
    "छियालीस",
    "सैंतालीस",
    "अड़तालीस",
    "उनचास",
    "पचास",
    "इक्यावन",
    "बावन",
    "तिरेपन",
    "चौवन",
    "पचपन",
    "छप्पन",
    "सत्तावन",
    "अट्ठावन",
    "उनसठ",
    "साठ",
    "इकसठ",
    "बासठ",
    "तिरेसठ",
    "चौंसठ",
    "पैंसठ",
    "छियासठ",
    "सड़सठ",
    "अड़सठ",
    "उनहत्तर",
    "सत्तर",
    "इकहत्तर",
    "बहत्तर",
    "तिहत्तर",
    "चौहत्तर",
    "पचहत्तर",
    "छिहत्तर",
    "सतहत्तर",
    "अठहत्तर",
    "उन्यासी",
    "अस्सी",
    "इक्यासी",
    "बयासी",
    "तिरासी",
    "चौरासी",
    "पचासी",
    "छियासी",
    "सत्तासी",
    "अट्ठासी",
    "नवासी",
    "नब्बे",
    "इक्यानबे",
    "बानबे",
    "तिरानबे",
    "चौरानबे",
    "पंचानबे",
    "छियानबे",
    "सत्तानबे",
    "अट्ठानबे",
    "निन्यानबे",
]

# --- Marathi ---------------------------------------------------------------
_MR_0_99 = [
    "शून्य",
    "एक",
    "दोन",
    "तीन",
    "चार",
    "पाच",
    "सहा",
    "सात",
    "आठ",
    "नऊ",
    "दहा",
    "अकरा",
    "बारा",
    "तेरा",
    "चौदा",
    "पंधरा",
    "सोळा",
    "सतरा",
    "अठरा",
    "एकोणीस",
    "वीस",
    "एकवीस",
    "बावीस",
    "तेवीस",
    "चोवीस",
    "पंचवीस",
    "सव्वीस",
    "सत्तावीस",
    "अठ्ठावीस",
    "एकोणतीस",
    "तीस",
    "एकतीस",
    "बत्तीस",
    "तेहेतीस",
    "चौतीस",
    "पस्तीस",
    "छत्तीस",
    "सदतीस",
    "अडतीस",
    "एकोणचाळीस",
    "चाळीस",
    "एक्केचाळीस",
    "बेचाळीस",
    "त्रेचाळीस",
    "चव्वेचाळीस",
    "पंचेचाळीस",
    "सेहेचाळीस",
    "सत्तेचाळीस",
    "अठ्ठेचाळीस",
    "एकोणपन्नास",
    "पन्नास",
    "एक्कावन्न",
    "बावन्न",
    "त्रेपन्न",
    "चोपन्न",
    "पंचावन्न",
    "छप्पन्न",
    "सत्तावन्न",
    "अठ्ठावन्न",
    "एकोणसाठ",
    "साठ",
    "एकसष्ट",
    "बासष्ट",
    "त्रेसष्ट",
    "चौसष्ट",
    "पासष्ट",
    "सहासष्ट",
    "सदुसष्ट",
    "अडुसष्ट",
    "एकोणसत्तर",
    "सत्तर",
    "एकाहत्तर",
    "बाहत्तर",
    "त्र्याहत्तर",
    "चौऱ्याहत्तर",
    "पंच्याहत्तर",
    "शहात्तर",
    "सत्याहत्तर",
    "अठ्ठ्याहत्तर",
    "एकोणऐंशी",
    "ऐंशी",
    "एक्क्याऐंशी",
    "ब्याऐंशी",
    "त्र्याऐंशी",
    "चौऱ्याऐंशी",
    "पंच्याऐंशी",
    "शहाऐंशी",
    "सत्त्याऐंशी",
    "अठ्ठ्याऐंशी",
    "एकोणनव्वद",
    "नव्वद",
    "एक्क्याण्णव",
    "ब्याण्णव",
    "त्र्याण्णव",
    "चौऱ्याण्णव",
    "पंच्याण्णव",
    "शहाण्णव",
    "सत्त्याण्णव",
    "अठ्ठ्याण्णव",
    "नव्व्याण्णव",
]


def _indian(n: int, words: list[str], hundred, scales: tuple[str, str, str]) -> str:
    """Indian place value: crore, lakh, thousand, hundred, then 0-99 from a table."""
    if n < 100:
        return words[n]
    thousand, lakh, crore = scales
    parts: list[str] = []
    for scale, name in ((10**7, crore), (10**5, lakh), (1000, thousand)):
        count, n = divmod(n, scale)
        if count:
            parts.append(f"{_indian(count, words, hundred, scales)} {name}")
    count, n = divmod(n, 100)
    if count:
        parts.append(hundred(count))
    if n:
        parts.append(words[n])
    return " ".join(parts)


def _hindi(n: int) -> str:
    return _indian(n, _HI_0_99, lambda c: f"{_HI_0_99[c]} सौ", ("हज़ार", "लाख", "करोड़"))


def _marathi(n: int) -> str:
    return _indian(
        n, _MR_0_99, lambda c: "शंभर" if c == 1 else f"{_MR_0_99[c]}शे", ("हजार", "लाख", "कोटी")
    )


_INDIAN = {"tamil": _tamil, "hindi": _hindi, "marathi": _marathi}
_UNIT_DIGITS = {"tamil": _TA_UNITS, "hindi": _HI_0_99[:10], "marathi": _MR_0_99[:10]}
_POINT = {"tamil": "புள்ளி", "hindi": "दशमलव", "marathi": "दशांश"}
_MINUS = {
    "tamil": "மைனஸ்",
    "hindi": "ऋण",
    "marathi": "ऋण",
    "bengali": "ঋণাত্মক",
    "telugu": "మైనస్",
    "english": "minus",
    "arabic": "سالب",
    "french": "moins",
    "spanish": "menos",
    "japanese": "マイナス",
}
PERCENT = {
    "tamil": "சதவீதம்",
    "hindi": "प्रतिशत",
    "marathi": "टक्के",
    "bengali": "শতাংশ",
    "telugu": "శాతం",
    "english": "percent",
    "arabic": "بالمائة",
    "french": "pour cent",
    "spanish": "por ciento",
    "japanese": "パーセント",
}


def _key(language: str) -> str:
    return (language or "english").strip().lower()


def supports(language: str) -> bool:
    key = _key(language)
    return key in _INDIAN or key in _NUM2WORDS_CODES


def number_words(value: str, language: str, *, year: bool = False) -> str:
    """``"1905"`` → the spoken words in ``language``. Raises ValueError if unsupported."""
    key = _key(language)
    text = value.replace(",", "").replace("−", "-").strip()
    try:
        number = Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"not a number: {value!r}") from error
    negative = number < 0
    number = abs(number)
    whole, _, fraction = format(number, "f").partition(".")
    if key in {"hindi", "marathi"} and year and not fraction and _hundreds_year(int(whole)):
        # Years like 1905 are read as "nineteen hundred five".
        century, rest = divmod(int(whole), 100)
        table = _HI_0_99 if key == "hindi" else _MR_0_99
        head = f"{table[century]} सौ" if key == "hindi" else f"{table[century]}शे"
        spoken = f"{head} {table[rest]}" if rest else head
    elif key in _INDIAN:
        spoken = _INDIAN[key](int(whole))
        if fraction:
            digits = " ".join(_UNIT_DIGITS[key][int(digit)] for digit in fraction)
            spoken = f"{spoken} {_POINT[key]} {digits}"
    elif key in _NUM2WORDS_CODES:
        code = _NUM2WORDS_CODES[key]
        if year and not fraction and code == "en":
            spoken = num2words(int(whole), lang=code, to="year")
        else:
            spoken = num2words(number, lang=code)
    else:
        raise ValueError(f"no number words for {language}")
    # NFC: num2words emits some Telugu vowel signs decomposed (ె + ౖ for ై).
    spoken = unicodedata.normalize("NFC", " ".join(spoken.split()))
    return f"{_MINUS.get(key, 'minus')} {spoken}" if negative else spoken


def _hundreds_year(value: int) -> bool:
    """Years read in hundreds (1100–1999); 2000s are read as ordinary numbers."""
    return 1100 <= value <= 1999


# Units spoken by name; the STEM-terms-in-English policy keeps them English.
UNIT_NAMES = {
    "nm": "nanometres",
    "μm": "micrometres",
    "um": "micrometres",
    "mm": "millimetres",
    "cm": "centimetres",
    "km": "kilometres",
    "m": "metres",
    "kg": "kilograms",
    "g": "grams",
    "mg": "milligrams",
    "s": "seconds",
    "ms": "milliseconds",
    "min": "minutes",
    "h": "hours",
    "Hz": "hertz",
    "kHz": "kilohertz",
    "MHz": "megahertz",
    "GHz": "gigahertz",
    "N": "newtons",
    "J": "joules",
    "kJ": "kilojoules",
    "eV": "electron volts",
    "W": "watts",
    "kW": "kilowatts",
    "V": "volts",
    "mV": "millivolts",
    "A": "amperes",
    "mA": "milliamperes",
    "Ω": "ohms",
    "C": "coulombs",
    "K": "kelvin",
    "°C": "degrees Celsius",
    "°F": "degrees Fahrenheit",
    "°": "degrees",
    "Pa": "pascals",
    "kPa": "kilopascals",
    "mol": "moles",
    "L": "litres",
    "mL": "millilitres",
    "m/s": "metres per second",
    "m/s²": "metres per second squared",
    "km/h": "kilometres per hour",
    "T": "tesla",
    "F": "farads",
    "μF": "microfarads",
}
_SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")


def spoken_quantity(number: str, unit: str | None, language: str, *, kind: str) -> str:
    """A whole number expression as speech: value words, then percent or unit name."""
    base, _, power = number.partition("^")
    if not power and any(ch in number for ch in "⁰¹²³⁴⁵⁶⁷⁸⁹"):
        split = re.match(r"^(.*?)([⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+)$", number)
        if split:
            base, power = split.group(1), split.group(2).translate(_SUPERSCRIPTS)
    words = number_words(base, language, year=kind == "year")
    if power:
        words += f" to the power {number_words(power, language)}"
    if kind == "percent":
        return f"{words} {PERCENT.get(_key(language), 'percent')}"
    if unit:
        return f"{words} {UNIT_NAMES.get(unit, unit)}"
    return words
