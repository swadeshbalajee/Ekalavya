"""Run regression tests without reading the operator's real provider credentials."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from scholarmotion.config import Settings

# Apply before pytest collects modules and imports the API's global settings.
# Tests that need a provider explicitly construct their own test settings.
Settings.model_config["env_file"] = None
raise SystemExit(pytest.main(sys.argv[1:] or ["-q"]))
