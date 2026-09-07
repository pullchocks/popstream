#!/usr/bin/env python3
"""Launch PopStream using the project-local PySide6 install."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / ".venv"))
sys.path.insert(0, str(ROOT / "src"))

from popstream.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
