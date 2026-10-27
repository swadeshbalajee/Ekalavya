"""Visible Edge session for acceptance testing; credentials come from the environment."""

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

root = Path(".local/qa")
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        channel="msedge", headless=False, args=["--remote-debugging-port=9222"]
    )
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:8501")
    page.get_by_role("tab", name="Sign in", exact=True).wait_for(timeout=60000)
    page.get_by_label("Username", exact=True).first.fill("admin")
    page.get_by_label("Password", exact=True).first.fill(os.environ["QA_ADMIN_PASSWORD"])
    page.get_by_role("button", name="Continue", exact=True).click()
    page.wait_for_timeout(5000)
    page.screenshot(path=str(root / "ui-admin-home.png"), full_page=True)
    (root / "ui-home-text.txt").write_text(page.locator("body").inner_text(), encoding="utf-8")
    state = json.loads((root / "scenario-session.json").read_text(encoding="utf-8"))
    project = (
        state["projects"].get("quality_improvements")
        or state["projects"].get("paper")
        or state["projects"].get("electromagnetism")
    )
    if project:
        page.goto("http://127.0.0.1:8501/?chat=" + project)
        page.wait_for_timeout(6000)
        page.screenshot(path=str(root / "ui-admin-generating.png"), full_page=True)
        (root / "ui-project-text.txt").write_text(
            page.locator("body").inner_text(), encoding="utf-8"
        )
    (root / "ui-errors.json").write_text(json.dumps(errors, indent=2), encoding="utf-8")
    print("Visible admin browser opened. Screenshots saved.", flush=True)
    # Keep the requested visible session available for the user during testing.
    page.wait_for_timeout(10800000)
