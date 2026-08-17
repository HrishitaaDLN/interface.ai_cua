"""Loads KEY=VALUE pairs from a local .env file into os.environ, if present.

No new dependency (python-dotenv) for one seven-line job. Never logs or
prints values — only this process's environment is touched.
"""
from __future__ import annotations

import os
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_dotenv(path: Path = _ENV_PATH) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip()  # .env wins over stale shell vars
