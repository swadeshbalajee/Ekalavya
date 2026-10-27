"""Verify actual browser playback, seeking, subtitle and chapter cue loading."""

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

root = Path(".local/qa")
state = json.loads((root / "scenario-session.json").read_text(encoding="utf-8"))
project_id = state["projects"].get("quality_improvements") or state["projects"]["paper"]
quality = project_id == state["projects"].get("quality_improvements")
with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    page = next(page for page in browser.contexts[0].pages if project_id in page.url)
    page.reload()
    page.wait_for_timeout(2000)
    if page.get_by_role("tab", name="Sign in", exact=True).count():
        page.get_by_label("Username", exact=True).first.fill("admin")
        page.get_by_label("Password", exact=True).first.fill(os.environ["QA_ADMIN_PASSWORD"])
        page.get_by_role("button", name="Continue", exact=True).click()
    page.get_by_text("Your lesson video", exact=True).wait_for(timeout=30000)
    frame = next(f for f in page.frames if f.locator("video").count())
    frame.wait_for_function(
        '()=>[...document.querySelectorAll("video track")].every(t=>t.readyState===2)',
        timeout=30000,
    )
    video = frame.locator("video")
    metadata = video.evaluate(
        '(v)=>({duration:v.duration,crossOrigin:v.crossOrigin,tracks:[...v.querySelectorAll("track")].map(t=>({kind:t.kind,state:t.readyState,language:t.track.language,cues:t.track.cues?.length}))})'
    )
    video.evaluate("(v)=>{v.muted=true;return v.play()}")
    page.wait_for_timeout(2000)
    played = video.evaluate("(v)=>v.currentTime")
    video.evaluate("(v)=>{v.pause();v.currentTime=35}")
    page.wait_for_timeout(1000)
    seek = video.evaluate("(v)=>({time:v.currentTime,paused:v.paused,error:v.error?.code})")
    result = {
        "metadata": metadata,
        "played_to": played,
        "seek": seek,
        "passed": played > 0
        and abs(seek["time"] - 35) < 1
        and any(t["kind"] == "chapters" and t["cues"] >= 2 for t in metadata["tracks"])
        and any(t["kind"] == "subtitles" and t["cues"] > 0 for t in metadata["tracks"])
        and all(t["cues"] > 0 for t in metadata["tracks"]),
    }
    result_path = (
        root / "improvements/browser-player.json"
        if quality
        else root / "browser-player-after-fix.json"
    )
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)
    screenshot_path = (
        root / "improvements/admin-quality-player.png"
        if quality
        else root / "ui-paper-captions-after-fix.png"
    )
    page.screenshot(path=str(screenshot_path), full_page=True)
