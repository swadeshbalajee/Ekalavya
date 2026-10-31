"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def _no_guardrail_calls(monkeypatch):
    # Settings() reads the developer's .env; tests must never call the real screen model.
    # Tests of the guardrail pass their own model.
    monkeypatch.setenv("GUARDRAIL_MODEL", "")
