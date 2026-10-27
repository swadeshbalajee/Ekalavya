"""Interact with the visible admin browser and retain UI evidence."""

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(".local/qa")
checks = []


def record(name, passed, detail=""):
    checks.append({"name": name, "passed": passed, "detail": detail})
    (root / "ui-results.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    print(("PASS " if passed else "FAIL ") + name, flush=True)


with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    page = browser.contexts[0].pages[0]
    record("admin signed in", page.get_by_text("admin", exact=True).count() > 0)
    record("live progress visible", page.get_by_role("progressbar").count() > 0)
    page.get_by_role("button").filter(has_text="settings").click()
    page.get_by_role("dialog").wait_for()
    record(
        "settings dialog opens", page.get_by_role("tab", name="Account", exact=True).is_visible()
    )
    record("username is admin", page.get_by_label("Username", exact=True).input_value() == "admin")
    page.get_by_role("tab", name="Video", exact=True).click()
    record(
        "duration honors prompt",
        page.get_by_label("Target duration (minutes)").input_value() in {"1.00", "1.0", "1"},
    )
    page.screenshot(path=str(root / "ui-video-settings.png"), full_page=True)
    page.get_by_role("tab", name="Memory", exact=True).click()
    record(
        "memory tab opens",
        page.get_by_role("tab", name="Memory", exact=True).get_attribute("aria-selected") == "true",
    )
    page.keyboard.press("Escape")
    page.get_by_role("button").filter(has_text="New chat").first.click()
    page.get_by_text("Good to see you,", exact=False).wait_for(timeout=30000)
    page.get_by_label("Message", exact=True).fill(
        "Text only: Explain Newton second law for a beginner in two sentences."
    )
    page.get_by_label("Message", exact=True).press("Enter")
    expect(page.get_by_test_id("stChatMessage")).to_have_count(2, timeout=120000)
    expect(page.get_by_test_id("stChatMessage").nth(1)).not_to_contain_text(
        "Thinking", timeout=120000
    )
    page.wait_for_timeout(2000)
    content = page.locator("body").inner_text()
    record(
        "UI chat submits", "Newton" in content and page.get_by_test_id("stChatMessage").count() >= 2
    )
    record("text-only chat avoids video", page.get_by_role("progressbar").count() == 0)
    page.screenshot(path=str(root / "ui-admin-chat.png"), full_page=True)
    page.reload()
    page.get_by_label("Message", exact=True).wait_for(timeout=30000)
    record(
        "login and chat survive refresh",
        page.get_by_text("admin", exact=True).count() > 0
        and "Newton" in page.locator("body").inner_text(),
    )
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(1500)
    record("mobile composer visible", page.get_by_label("Message", exact=True).is_visible())
    record(
        "mobile has no horizontal overflow",
        page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"),
    )
    page.screenshot(path=str(root / "ui-admin-mobile.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 1000})
    state = json.loads((root / "scenario-session.json").read_text(encoding="utf-8"))
    page.goto("http://127.0.0.1:8501/?chat=" + state["projects"]["electromagnetism"])
    page.get_by_label("Message", exact=True).wait_for(timeout=30000)
    page.screenshot(path=str(root / "ui-admin-progress-current.png"), full_page=True)
