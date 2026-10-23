"""Where the program of each version comes from.

original: the reference commit, extracted with `git archive` into a temporary folder (deleted at exit).
rebuild:  the src/ folder of the rebuild branch's working tree.
"""
from __future__ import annotations

import atexit
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
REFERENCE = "63326cbd6270e3ccbb2e28b8722bf0a4fcf374e0"

_extracted: dict[str, Path] = {}


def program_dir(version: str) -> Path:
    if version == "rebuild":
        return REPO / "src"
    if version != "original":
        raise ValueError(version)
    if "original" not in _extracted:
        tmp = Path(tempfile.mkdtemp(prefix="lt-original-"))
        atexit.register(shutil.rmtree, tmp, ignore_errors=True)
        archive = subprocess.run(["git", "-C", str(REPO), "archive", REFERENCE, "src", ".gitignore"],
                                 check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", str(tmp)], input=archive, check=True)
        _extracted["original"] = tmp / "src"
    return _extracted["original"]


def reference_file(path: str) -> bytes:
    """A file of the reference commit (e.g. glossary/glossary_it.json), read from git."""
    return subprocess.run(["git", "-C", str(REPO), "show", f"{REFERENCE}:{path}"],
                          check=True, capture_output=True).stdout
