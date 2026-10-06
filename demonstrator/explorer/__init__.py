"""Local, read-only research replay with isolated live branches."""

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = APP_ROOT.parent / "experiment"
# The experiment is a script project, not an installable Python package.
sys.path.insert(0, str(EXPERIMENT))
