"""Shared start of the function checks: argv = program folder, version, scratch folder.

The program folder goes first on sys.path, so `import lt_server` imports that version. Output: one
JSON document on stdout. Logging goes to stderr (not compared).
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

PROGRAM, VERSION, WORK = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
sys.path.insert(0, str(PROGRAM))
logging.basicConfig(level=logging.CRITICAL, stream=sys.stderr)


def emit(data) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, sort_keys=True, default=repr))


def attempt(fn, *args, **kw):
    """fn's result, or ["raises", exception type, message]."""
    try:
        return fn(*args, **kw)
    except Exception as exc:  # noqa: BLE001
        return ["raises", type(exc).__name__, str(exc)]
