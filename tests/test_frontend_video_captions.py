import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frontend.components.subtitle_track import subtitle_track_markup


def test_video_subtitle_track_defaults_to_localized_vtt_captions():
    markup = subtitle_track_markup(
        "http://localhost:8000/v1/projects/abc/subtitles.vtt?access_token=qa%20token",
        "Tamil",
    )

    assert 'kind="subtitles"' in markup
    assert 'srclang="ta"' in markup
    assert 'label="Subtitles" default' in markup
    assert "/projects/abc/subtitles.vtt?access_token=qa%20token" in markup


def test_video_subtitle_track_escapes_urls_and_falls_back_to_english_locale():
    markup = subtitle_track_markup("https://example.test/captions?a=1&b=2", "unknown")

    assert 'srclang="en"' in markup
    assert "&amp;" in markup
