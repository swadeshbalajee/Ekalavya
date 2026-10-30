"""Catch a point from get_top()/get_center() indexed twice, which crashes the render."""

import re

_TWICE = re.compile(r"get_(?:top|bottom|left|right|center)\(\)\s*\[\s*\d\s*\]\s*\[")


def check(source: str) -> list[str]:
    return [
        f"Line {number}: a point is indexed twice ({line.strip()[:80]}); get_top()[1] is "
        "already a number."
        for number, line in enumerate(source.splitlines(), 1)
        if _TWICE.search(line)
    ]
